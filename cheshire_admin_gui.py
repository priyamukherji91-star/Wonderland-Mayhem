"""
Cheshire Admin GUI – compact version

Admin panel for your FC bot project.

Tabs:
- Project   : pick project folder, run deploy.bat, see basic info + log.
- Statuses  : edit rotating statuses (data/statuses.json + SHORT_QUOTES).
- Settings  : edit UPPERCASE constants in config.py.
- Birthdays : view data/birthdays.json, load from file, sync from API.
- Moderation: basic AutoMod toggles/thresholds in cogs/moderation.py +
              view/clear warns from data/modnotes.json.

Build EXE (no console):
- Install Python 3.11, pip install customtkinter + pyinstaller.
- Run: pyinstaller --noconsole --onefile cheshire_admin_gui.py

The GUI itself stays “pure Tk” so it works with standard libraries, but it’s
laid out with a slightly Discord-ish vibe.
"""

from __future__ import annotations

import ast
import json
import os
import re
import subprocess
import sys
import threading
import urllib.request
import urllib.error
from pathlib import Path
from typing import Any, Dict, List, Tuple, Optional, Set

import tkinter as tk
from tkinter import ttk, filedialog, messagebox


APP_NAME = "Cheshire Admin GUI"
CONFIG_FILE_NAME = "cheshire_admin_config.json"

STATUSES_FILE_REL = Path("data") / "statuses.json"


def _get_statuses_path(project_path: Path) -> Path:
    return project_path / STATUSES_FILE_REL


def load_statuses_from_json(project_path: Path) -> List[str]:
    path = _get_statuses_path(project_path)
    if not path.is_file():
        return []
    try:
        with path.open("r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, list):
            return [str(x) for x in data]
    except Exception as exc:
        messagebox.showerror(APP_NAME, f"Failed to load statuses.json:\n{exc}")
    return []


def load_statuses_from_cog(project_path: Path) -> List[str]:
    cog_path = project_path / "cogs" / "cheshire_status.py"
    if not cog_path.is_file():
        return []
    try:
        src = cog_path.read_text(encoding="utf-8")
        tree = ast.parse(src, filename=str(cog_path))

        for node in tree.body:
            if isinstance(node, ast.Assign) and len(node.targets) == 1:
                target = node.targets[0]
                if isinstance(target, ast.Name) and target.id == "SHORT_QUOTES":
                    value = ast.literal_eval(node.value)
                    if isinstance(value, list):
                        return [str(x) for x in value]
    except Exception as exc:
        messagebox.showerror(
            APP_NAME,
            f"Failed to read SHORT_QUOTES from cogs/cheshire_status.py:\n{exc}",
        )
    return []


def save_statuses_to_json(project_path: Path, statuses: List[str]) -> None:
    path = _get_statuses_path(project_path)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8") as f:
            json.dump(statuses, f, ensure_ascii=False, indent=2)
    except Exception as exc:
        messagebox.showerror(APP_NAME, f"Failed to save statuses.json:\n{exc}")


# ---------------------------------------------------------------------------
# Config helpers (remember last project path, API token, etc.)
# ---------------------------------------------------------------------------


def _get_app_dir() -> Path:
    # When frozen by PyInstaller, __file__ points to the temp bundle; we want
    # the directory where the EXE lives.
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def _get_config_path() -> Path:
    return _get_app_dir() / CONFIG_FILE_NAME


def _default_project_path() -> Path:
    preferred = Path("C:/Users/Cookiechan/OneDrive/Desktop/FC Bot")
    if preferred.exists():
        return preferred
    return Path.cwd()


def load_config() -> dict:
    cfg_path = _get_config_path()
    if not cfg_path.is_file():
        return {
            "project_path": str(_default_project_path()),
            "deploy_command": "deploy.bat",
            "birthdays_api_url": "",
            "birthdays_api_token": "",
        }
    try:
        with cfg_path.open("r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            raise ValueError("bad config")
        return data
    except Exception:
        return {
            "project_path": str(_default_project_path()),
            "deploy_command": "deploy.bat",
            "birthdays_api_url": "",
            "birthdays_api_token": "",
        }


def save_config(data: dict) -> None:
    cfg_path = _get_config_path()
    try:
        with cfg_path.open("w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception:
        # If saving fails, we don't want to kill the GUI; just swallow.
        pass


# ---------------------------------------------------------------------------
# Birthdays helpers
# ---------------------------------------------------------------------------

BIRTHDAYS_FILE_REL = Path("data") / "birthdays.json"


def _get_birthdays_path(project_path: Path) -> Path:
    return project_path / BIRTHDAYS_FILE_REL


def load_birthdays_local(project_path: Path) -> Dict[str, Any]:
    path = _get_birthdays_path(project_path)
    if not path.is_file():
        return {}
    try:
        with path.open("r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            return data
    except Exception as exc:
        messagebox.showerror(APP_NAME, f"Failed to load birthdays.json:\n{exc}")
    return {}


def format_mmdd_to_ddmm(mm_dd: str) -> str:
    try:
        m, d = mm_dd.split("-")
        return f"{int(d):02d}/{int(m):02d}"
    except Exception:
        return mm_dd


# ---------------------------------------------------------------------------
# config.py constant scanning / saving
# ---------------------------------------------------------------------------


def load_config_constants(project_path: Path) -> Tuple[List[Dict[str, Any]], Set[str]]:
    cfg_path = project_path / "config.py"
    if not cfg_path.is_file():
        return [], set()

    src = cfg_path.read_text(encoding="utf-8")
    tree = ast.parse(src, filename=str(cfg_path))

    constants: List[Dict[str, Any]] = []

    for node in tree.body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1:
            target = node.targets[0]
            if isinstance(target, ast.Name) and target.id.isupper():
                try:
                    value = ast.literal_eval(node.value)
                except Exception:
                    continue
                constants.append(
                    {"name": target.id, "value": value, "lineno": node.lineno}
                )
        elif isinstance(node, ast.AnnAssign):
            # e.g. NAME: Final[int] = 123
            target = node.target
            if (
                isinstance(target, ast.Name)
                and target.id.isupper()
                and node.value is not None
            ):
                try:
                    value = ast.literal_eval(node.value)
                except Exception:
                    continue
                constants.append(
                    {"name": target.id, "value": value, "lineno": node.lineno}
                )

    original_names = {c["name"] for c in constants}
    return constants, original_names


def save_config_constants(
    project_path: Path,
    constants: List[Dict[str, Any]],
    original_names: Set[str],
) -> None:
    cfg_path = project_path / "config.py"
    if not cfg_path.is_file():
        raise FileNotFoundError("config.py not found")

    src_lines = cfg_path.read_text(encoding="utf-8").splitlines(keepends=True)
    const_map: Dict[str, Any] = {c["name"]: c["value"] for c in constants}

    def format_value(v: Any) -> str:
        return repr(v)

    written: Set[str] = set()
    name_re = re.compile(r"([A-Z][A-Z0-9_]*)\s*[:=]")

    new_lines: List[str] = []

    for line in src_lines:
        stripped = line.lstrip()
        m = name_re.match(stripped)
        if not m:
            new_lines.append(line)
            continue

        name = m.group(1)
        if name in const_map:
            value = format_value(const_map[name])
            # Try to preserve annotation if present
            if ":" in stripped.split("=", 1)[0]:
                # e.g. NAME: Final[int] = old
                prefix = (
                    stripped.split(":", 1)[0]
                    + stripped.split(":", 1)[1].split("=", 1)[0]
                )
                new_line = f"{prefix}= {value}\n"
            else:
                new_line = f"{name} = {value}\n"
            indent = line[: len(line) - len(stripped)]
            new_lines.append(indent + new_line)
            written.add(name)
        else:
            new_lines.append(line)

    for c in constants:
        if c["name"] not in written and c["name"] not in original_names:
            new_lines.append(f"{c['name']} = {format_value(c['value'])}\n")

    cfg_path.write_text("".join(new_lines), encoding="utf-8")


def _find_constant(constants: List[Dict[str, Any]], name: str) -> Optional[Dict[str, Any]]:
    for c in constants:
        if c["name"] == name:
            return c
    return None


def _set_constant(constants: List[Dict[str, Any]], name: str, value: Any) -> None:
    existing = _find_constant(constants, name)
    if existing is not None:
        existing["value"] = value
    else:
        constants.append({"name": name, "value": value, "lineno": None})


# ---------------------------------------------------------------------------
# Generic scalar helpers for cogs (e.g. moderation)
# ---------------------------------------------------------------------------


def read_scalar_constant(module_path: Path, name: str) -> Any | None:
    if not module_path.is_file():
        return None
    try:
        src = module_path.read_text(encoding="utf-8")
        tree = ast.parse(src, filename=str(module_path))
        for node in tree.body:
            if isinstance(node, ast.Assign) and len(node.targets) == 1:
                target = node.targets[0]
                if isinstance(target, ast.Name) and target.id == name:
                    return ast.literal_eval(node.value)
            elif isinstance(node, ast.AnnAssign):
                target = node.target
                if (
                    isinstance(target, ast.Name)
                    and target.id == name
                    and node.value is not None
                ):
                    return ast.literal_eval(node.value)
    except Exception:
        return None
    return None


def write_scalar_constant(module_path: Path, name: str, value: Any) -> None:
    if not module_path.is_file():
        raise FileNotFoundError(module_path)
    src_lines = module_path.read_text(encoding="utf-8").splitlines(keepends=True)
    name_re = re.compile(rf"({re.escape(name)})\s*[:=]")

    def format_value(v: Any) -> str:
        return repr(v)

    written = False
    new_lines: List[str] = []

    for line in src_lines:
        stripped = line.lstrip()
        m = name_re.match(stripped)
        if not m:
            new_lines.append(line)
            continue
        before, _sep, _rest = stripped.partition("=")
        new_line = before + "= " + format_value(value) + "\n"
        indent = line[: len(line) - len(stripped)]
        new_lines.append(indent + new_line)
        written = True

    if not written:
        new_lines.append(f"{name} = {format_value(value)}\n")

    module_path.write_text("".join(new_lines), encoding="utf-8")


# ---------------------------------------------------------------------------
# Tkinter GUI
# ---------------------------------------------------------------------------


class CheshireAdminApp(tk.Tk):
    def __init__(self) -> None:
        super().__init__()

        self.title(APP_NAME)
        self.geometry("1100x700")

        self._icon_img_ref = None
        try:
            icon_path = _get_app_dir() / "cheshire.ico"
            if icon_path.is_file():
                if sys.platform.startswith("win"):
                    self.iconbitmap(default=str(icon_path))
                else:
                    img = tk.PhotoImage(file=str(icon_path))
                    self.iconphoto(True, img)
                    self._icon_img_ref = img
        except Exception:
            pass

        self.config_data = load_config()
        self.project_path = Path(
            self.config_data.get("project_path", str(_default_project_path()))
        )

        self.project_path_var = tk.StringVar(value=str(self.project_path))
        self.deploy_cmd_var = tk.StringVar(
            value=self.config_data.get("deploy_command", "deploy.bat")
        )

        # FIXED: pass value=... instead of using the URL as master
        self.birthdays_api_url_var = tk.StringVar(
            value=self.config_data.get("birthdays_api_url", "")
        )
        self.birthdays_api_token_var = tk.StringVar(
            value=self.config_data.get("birthdays_api_token", "")
        )

        self.statuses: List[str] = []
        self.config_constants: List[Dict[str, Any]] = []
        self.config_original_names: Set[str] = set()
        self.birthdays: Dict[str, Any] = {}
        self._deploy_running: bool = False

        self.mod_block_invites_var = tk.BooleanVar(value=True)
        self.mod_block_mass_mentions_var = tk.BooleanVar(value=True)
        self.mod_max_mentions_var = tk.IntVar(value=6)
        self.mod_antispam_enabled_var = tk.BooleanVar(value=True)
        self.mod_spam_window_var = tk.IntVar(value=6)
        self.mod_spam_max_messages_var = tk.IntVar(value=6)
        self.mod_repeat_enabled_var = tk.BooleanVar(value=True)
        self.mod_repeat_window_var = tk.IntVar(value=10)
        self.mod_warns_tree: ttk.Treeview | None = None

        self.config_tree: ttk.Treeview | None = None
        self.config_name_var = tk.StringVar()
        self.config_value_entry: tk.Entry | None = None

        self.bday_tree: ttk.Treeview | None = None

        self.log_text: tk.Text

        self._tab_frames: Dict[str, ttk.Frame] = {}
        self._tab_buttons: Dict[str, ttk.Button] = {}
        self._current_tab: Optional[str] = None

        self.deploy_button: ttk.Button | None = None

        self._build_main_layout()
        self._update_project_info()

    def log(self, message: str) -> None:
        self.log_text.configure(state="normal")
        self.log_text.insert(tk.END, message + "\n")
        self.log_text.see(tk.END)
        self.log_text.configure(state="disabled")

    def log_from_thread(self, message: str) -> None:
        self.after(0, lambda: self.log(message))

    # ------------------------------------------------------------------
    # Layout
    # ------------------------------------------------------------------

    def _build_main_layout(self) -> None:
        self.columnconfigure(0, weight=1)
        self.rowconfigure(1, weight=1)

        top_frame = ttk.Frame(self)
        top_frame.grid(row=0, column=0, sticky="nsew", padx=8, pady=(8, 4))
        top_frame.columnconfigure(0, weight=1)

        tab_bar = ttk.Frame(top_frame)
        tab_bar.grid(row=0, column=0, sticky="w")

        for name in ["Project", "Statuses", "Settings", "Birthdays", "Moderation"]:
            btn = ttk.Button(
                tab_bar,
                text=name,
                command=lambda n=name: self.show_tab(n),
            )
            btn.pack(side=tk.LEFT, padx=(0, 4))
            self._tab_buttons[name] = btn

        right = ttk.Frame(top_frame)
        right.grid(row=0, column=1, sticky="e")
        right.columnconfigure(1, weight=1)

        ttk.Label(right, text="Project:").grid(row=0, column=0, sticky="w")
        entry = ttk.Entry(right, textvariable=self.project_path_var, width=60)
        entry.grid(row=0, column=1, sticky="we", padx=(2, 2))
        ttk.Button(
            right,
            text="Browse…",
            command=self.on_browse_project,
        ).grid(row=0, column=2, padx=(2, 0))

        ttk.Button(right, text="Rescan", command=self.on_rescan).grid(
            row=1, column=0, padx=0, pady=(2, 0)
        )

        ttk.Label(right, text="Deploy helper:").grid(
            row=1, column=1, sticky="w", padx=(4, 0), pady=(2, 0)
        )
        cmd_entry = ttk.Entry(right, textvariable=self.deploy_cmd_var, width=20)
        cmd_entry.grid(row=1, column=1, sticky="e", padx=(100, 4), pady=(2, 0))

        self.deploy_button = ttk.Button(
            right, text="Run deploy", command=self.on_run_deploy
        )
        self.deploy_button.grid(row=1, column=2, padx=(2, 0), pady=(2, 0))

        content = ttk.Frame(self)
        content.grid(row=1, column=0, sticky="nsew", padx=8, pady=4)
        content.columnconfigure(0, weight=1)
        content.rowconfigure(0, weight=1)

        self._content_frame = content

        log_frame = ttk.Frame(self)
        log_frame.grid(row=2, column=0, sticky="nsew", padx=8, pady=(4, 8))
        log_frame.columnconfigure(0, weight=1)
        log_frame.rowconfigure(0, weight=1)

        ttk.Label(log_frame, text="Log:").grid(row=0, column=0, sticky="w")
        self.log_text = tk.Text(log_frame, height=8, state="disabled", wrap="word")
        self.log_text.grid(row=1, column=0, sticky="nsew")
        yscroll = ttk.Scrollbar(log_frame, orient="vertical", command=self.log_text.yview)
        self.log_text.configure(yscrollcommand=yscroll.set)
        yscroll.grid(row=1, column=1, sticky="ns")

        for name in ["Project", "Statuses", "Settings", "Birthdays", "Moderation"]:
            frame = ttk.Frame(self._content_frame)
            frame.grid(row=0, column=0, sticky="nsew")
            frame.grid_remove()
            self._tab_frames[name] = frame

        self._build_project_tab(self._tab_frames["Project"])
        self._build_statuses_tab(self._tab_frames["Statuses"])
        self._build_settings_tab(self._tab_frames["Settings"])
        self._build_birthdays_tab(self._tab_frames["Birthdays"])
        self._build_moderation_tab(self._tab_frames["Moderation"])

        self.show_tab("Project")

    def show_tab(self, name: str) -> None:
        if self._current_tab == name:
            return
        self._current_tab = name
        for tab_name, frame in self._tab_frames.items():
            if tab_name == name:
                frame.grid()
            else:
                frame.grid_remove()

    # ------------- Project tab -------------

    def _build_project_tab(self, parent: ttk.Frame) -> None:
        parent.columnconfigure(0, weight=1)
        parent.rowconfigure(0, weight=1)

        upper = ttk.Frame(parent)
        upper.grid(row=0, column=0, sticky="nsew")
        upper.columnconfigure(1, weight=1)

        summary = ttk.LabelFrame(upper, text="Project summary")
        summary.grid(row=0, column=0, sticky="nsew", padx=(0, 8), pady=4)
        for i in range(3):
            summary.rowconfigure(i, weight=0)
        summary.columnconfigure(0, weight=1)

        self.project_info_label = ttk.Label(
            summary,
            text="",
            justify="left",
            anchor="w",
        )
        self.project_info_label.grid(row=0, column=0, sticky="w", padx=4, pady=4)

        help_frame = ttk.LabelFrame(upper, text="Notes")
        help_frame.grid(row=0, column=1, sticky="nsew", pady=4)
        help_frame.columnconfigure(0, weight=1)

        txt = (
            "Pick your FC bot folder and run deploy.bat from here.\n\n"
            "Checks:\n"
            " • bot.py present? (root)\n"
            " • config.py present? (root)\n"
            " • cogs/ and data/ folders present?\n"
            " • data/statuses.json + data/birthdays.json are optional; they will be created.\n\n"
            "Deploy helper simply runs deploy.bat in that folder and streams its output into the log."
        )
        lbl = ttk.Label(help_frame, text=txt, justify="left", wraplength=500)
        lbl.grid(row=0, column=0, sticky="nw", padx=4, pady=4)

    # ------------- Statuses tab -------------

    def _build_statuses_tab(self, parent: ttk.Frame) -> None:
        parent.columnconfigure(0, weight=1)
        parent.rowconfigure(1, weight=1)

        top = ttk.LabelFrame(
            parent, text="Rotating statuses (data/statuses.json / SHORT_QUOTES)"
        )
        top.grid(row=0, column=0, sticky="nsew", padx=4, pady=4)
        top.columnconfigure(1, weight=1)

        ttk.Button(
            top,
            text="Load from statuses.json",
            command=self.on_load_statuses_json,
        ).grid(row=0, column=0, padx=4, pady=2, sticky="w")
        ttk.Button(
            top,
            text="Load from SHORT_QUOTES",
            command=self.on_load_statuses_cog,
        ).grid(row=0, column=1, padx=4, pady=2, sticky="w")
        ttk.Button(
            top,
            text="Save to statuses.json",
            command=self.on_save_statuses_json,
        ).grid(row=0, column=2, padx=4, pady=2, sticky="w")

        list_frame = ttk.Frame(parent)
        list_frame.grid(row=1, column=0, sticky="nsew", padx=4, pady=(0, 4))
        list_frame.columnconfigure(0, weight=1)
        list_frame.rowconfigure(0, weight=1)

        self.status_listbox = tk.Listbox(list_frame, selectmode=tk.EXTENDED)
        self.status_listbox.grid(row=0, column=0, sticky="nsew")
        yscroll = ttk.Scrollbar(
            list_frame, orient="vertical", command=self.status_listbox.yview
        )
        self.status_listbox.configure(yscrollcommand=yscroll.set)
        yscroll.grid(row=0, column=1, sticky="ns")

        edit_frame = ttk.Frame(parent)
        edit_frame.grid(row=2, column=0, sticky="ew", padx=4, pady=(0, 4))
        edit_frame.columnconfigure(0, weight=1)

        self.status_entry = ttk.Entry(edit_frame)
        self.status_entry.grid(row=0, column=0, sticky="ew", padx=(0, 4))
        ttk.Button(
            edit_frame,
            text="Add / Update",
            command=self.on_add_update_status,
        ).grid(row=0, column=1)
        ttk.Button(
            edit_frame,
            text="Delete selected",
            command=self.on_delete_statuses,
        ).grid(row=0, column=2)

        ttk.Label(
            parent,
            text="Statuses shorter than ~30 characters work best as Discord \"Playing\" text.",
        ).grid(row=3, column=0, sticky="w", padx=4, pady=(0, 4))

    # ------------- Settings tab -------------

    def _build_settings_tab(self, parent: ttk.Frame) -> None:
        parent.columnconfigure(0, weight=2)
        parent.columnconfigure(1, weight=3)
        parent.rowconfigure(0, weight=1)

        left = ttk.LabelFrame(parent, text="config.py constants (UPPERCASE only)")
        left.grid(row=0, column=0, sticky="nsew", padx=(4, 2), pady=4)
        left.columnconfigure(0, weight=1)
        left.rowconfigure(0, weight=1)

        tree = ttk.Treeview(
            left,
            columns=("name", "value"),
            show="headings",
            selectmode="browse",
        )
        tree.heading("name", text="Name")
        tree.heading("value", text="Value (repr)")
        tree.column("name", width=220, anchor="w")
        tree.column("value", width=260, anchor="w")
        tree.grid(row=0, column=0, sticky="nsew")

        yscroll = ttk.Scrollbar(left, orient="vertical", command=tree.yview)
        tree.configure(yscrollcommand=yscroll.set)
        yscroll.grid(row=0, column=1, sticky="ns")

        self.config_tree = tree

        btns = ttk.Frame(left)
        btns.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(4, 0))
        ttk.Button(
            btns,
            text="Reload from config.py",
            command=self.on_reload_config,
        ).pack(side=tk.LEFT, padx=(0, 4))
        ttk.Button(
            btns,
            text="Save to config.py",
            command=self.on_save_config,
        ).pack(side=tk.LEFT, padx=(0, 4))

        right = ttk.LabelFrame(parent, text="Edit selected constant")
        right.grid(row=0, column=1, sticky="nsew", padx=(2, 4), pady=4)
        right.columnconfigure(1, weight=1)

        ttk.Label(right, text="Name:").grid(row=0, column=0, sticky="w", padx=4, pady=4)
        name_entry = ttk.Entry(right, textvariable=self.config_name_var, state="readonly")
        name_entry.grid(row=0, column=1, sticky="ew", padx=(0, 4), pady=4)

        ttk.Label(right, text="Value (Python literal):").grid(
            row=1, column=0, sticky="w", padx=4, pady=4
        )
        value_entry = ttk.Entry(right)
        value_entry.grid(row=1, column=1, sticky="ew", padx=(0, 4), pady=4)
        self.config_value_entry = value_entry

        ttk.Button(
            right,
            text="Apply to list (not saved)",
            command=self.on_apply_config_value,
        ).grid(row=2, column=1, sticky="e", padx=4, pady=(4, 4))

        ttk.Label(
            right,
            text=(
                "Edits here apply to the in-memory list first.\n"
                "Click \"Save to config.py\" to actually rewrite config.py.\n"
                "Only simple UPPERCASE constants are shown."
            ),
            foreground="gray",
            justify="left",
        ).grid(row=3, column=0, columnspan=2, sticky="w", padx=4, pady=(4, 4))

        def on_tree_select(event: object) -> None:
            sel = self.config_tree.selection() if self.config_tree else ()
            if not sel:
                self.config_name_var.set("")
                if self.config_value_entry:
                    self.config_value_entry.delete(0, tk.END)
                return
            item_id = sel[0]
            name, value = self.config_tree.item(item_id, "values")
            self.config_name_var.set(name)
            if self.config_value_entry:
                self.config_value_entry.delete(0, tk.END)
                self.config_value_entry.insert(0, value)

        tree.bind("<<TreeviewSelect>>", on_tree_select)

    # ------------- Birthdays tab -------------

    def _build_birthdays_tab(self, parent: ttk.Frame) -> None:
        parent.columnconfigure(0, weight=1)
        parent.rowconfigure(1, weight=1)

        top = ttk.LabelFrame(parent, text="Birthdays API")
        top.grid(row=0, column=0, sticky="ew", padx=4, pady=4)
        top.columnconfigure(1, weight=1)

        ttk.Label(top, text="API URL:").grid(row=0, column=0, sticky="w", padx=4, pady=2)
        ttk.Entry(top, textvariable=self.birthdays_api_url_var).grid(
            row=0, column=1, sticky="ew", padx=(0, 4), pady=2
        )

        ttk.Label(top, text="API token (optional):").grid(
            row=1, column=0, sticky="w", padx=4, pady=2
        )
        ttk.Entry(top, textvariable=self.birthdays_api_token_var, show="*").grid(
            row=1, column=1, sticky="ew", padx=(0, 4), pady=2
        )

        ttk.Button(
            top,
            text="Sync from server",
            command=self.on_sync_birthdays_from_server,
        ).grid(row=0, column=2, rowspan=2, sticky="ns", padx=4, pady=2)

        table_frame = ttk.LabelFrame(parent, text="Local birthdays (data/birthdays.json)")
        table_frame.grid(row=1, column=0, sticky="nsew", padx=4, pady=(0, 4))
        table_frame.columnconfigure(0, weight=1)
        table_frame.rowconfigure(0, weight=1)

        tree = ttk.Treeview(
            table_frame,
            columns=("guild", "user", "ddmm"),
            show="headings",
            selectmode="browse",
        )
        tree.heading("guild", text="Guild ID")
        tree.heading("user", text="User ID / mention")
        tree.heading("ddmm", text="DD/MM")
        tree.column("guild", width=120, anchor="w")
        tree.column("user", width=220, anchor="w")
        tree.column("ddmm", width=80, anchor="w")
        tree.grid(row=0, column=0, sticky="nsew")

        yscroll = ttk.Scrollbar(table_frame, orient="vertical", command=tree.yview)
        tree.configure(yscrollcommand=yscroll.set)
        yscroll.grid(row=0, column=1, sticky="ns")

        self.bday_tree = tree

        btns = ttk.Frame(parent)
        btns.grid(row=2, column=0, sticky="ew", padx=4, pady=(0, 4))
        ttk.Button(btns, text="Refresh local", command=self.on_refresh_birthdays).pack(
            side=tk.LEFT, padx=(0, 4)
        )
        ttk.Button(btns, text="Load file…", command=self.on_load_birthdays_file).pack(
            side=tk.LEFT, padx=(0, 4)
        )

        ttk.Label(
            parent,
            text=(
                "This tab is read-only: it shows what the bot will see in data/birthdays.json.\n"
                "Use the /birthday slash commands in Discord to actually add / edit birthdays."
            ),
            foreground="gray",
            justify="left",
        ).grid(row=3, column=0, sticky="w", padx=4, pady=(0, 4))

    # ------------- Moderation tab -------------

    def _build_moderation_tab(self, parent: ttk.Frame) -> None:
        frame = ttk.Frame(parent)

        top = ttk.LabelFrame(frame, text="AutoMod toggles & thresholds (cogs/moderation.py)")
        top.pack(fill=tk.X, padx=4, pady=4)

        row = 0
        ttk.Checkbutton(
            top,
            text="Block Discord invites",
            variable=self.mod_block_invites_var,
        ).grid(row=row, column=0, sticky="w", padx=4, pady=2)
        row += 1

        ttk.Checkbutton(
            top,
            text="Block mass mentions",
            variable=self.mod_block_mass_mentions_var,
        ).grid(row=row, column=0, sticky="w", padx=4, pady=2)
        ttk.Label(top, text="Max mentions:").grid(row=row, column=1, sticky="e", padx=4, pady=2)
        ttk.Entry(top, textvariable=self.mod_max_mentions_var, width=6).grid(
            row=row, column=2, sticky="w", padx=(0, 4), pady=2
        )
        row += 1

        ttk.Checkbutton(
            top,
            text="Enable anti-spam (burst)",
            variable=self.mod_antispam_enabled_var,
        ).grid(row=row, column=0, sticky="w", padx=4, pady=2)
        ttk.Label(top, text="Window (s):").grid(row=row, column=1, sticky="e", padx=4, pady=2)
        ttk.Entry(top, textvariable=self.mod_spam_window_var, width=6).grid(
            row=row, column=2, sticky="w", padx=(0, 4), pady=2
        )
        ttk.Label(top, text="Max msgs:").grid(row=row, column=3, sticky="e", padx=4, pady=2)
        ttk.Entry(top, textvariable=self.mod_spam_max_messages_var, width=6).grid(
            row=row, column=4, sticky="w", padx=(0, 4), pady=2
        )
        row += 1

        ttk.Checkbutton(
            top,
            text="Enable repeat detection",
            variable=self.mod_repeat_enabled_var,
        ).grid(row=row, column=0, sticky="w", padx=4, pady=2)
        ttk.Label(top, text="Repeat window (s):").grid(
            row=row, column=1, sticky="e", padx=4, pady=2
        )
        ttk.Entry(top, textvariable=self.mod_repeat_window_var, width=6).grid(
            row=row, column=2, sticky="w", padx=(0, 4), pady=2
        )

        ttk.Button(
            top,
            text="Save to moderation.py",
            command=self.on_save_moderation,
        ).grid(row=row + 1, column=0, columnspan=5, sticky="w", padx=4, pady=(4, 2))

        bottom = ttk.LabelFrame(frame, text="Warn database (data/modnotes.json)")
        bottom.pack(fill=tk.BOTH, expand=True, padx=4, pady=(0, 4))
        bottom.columnconfigure(0, weight=1)
        bottom.rowconfigure(0, weight=1)

        tree = ttk.Treeview(
            bottom,
            columns=("guild", "user", "count"),
            show="headings",
            selectmode="browse",
        )
        tree.heading("guild", text="Guild ID")
        tree.heading("user", text="User ID")
        tree.heading("count", text="Warn count")
        tree.column("guild", width=120, anchor="w")
        tree.column("user", width=140, anchor="w")
        tree.column("count", width=80, anchor="center")
        tree.grid(row=0, column=0, sticky="nsew")

        yscroll = ttk.Scrollbar(bottom, orient="vertical", command=tree.yview)
        tree.configure(yscrollcommand=yscroll.set)
        yscroll.grid(row=0, column=1, sticky="ns")

        self.mod_warns_tree = tree

        btns = ttk.Frame(bottom)
        btns.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(4, 0))
        ttk.Button(btns, text="Reload warns", command=self.on_reload_warns).pack(
            side=tk.LEFT, padx=(0, 4)
        )
        ttk.Button(
            btns,
            text="Clear selected user (all guilds)",
            command=self.on_clear_selected_warns,
        ).pack(side=tk.LEFT, padx=(0, 4))

        ttk.Label(
            bottom,
            text=(
                "The bot writes warns into data/modnotes.json.\n"
                "This viewer is read-only; clearing here only edits the JSON file."
            ),
            foreground="gray",
            justify="left",
        ).grid(row=2, column=0, columnspan=2, sticky="w", padx=4, pady=(4, 4))

        frame.pack(fill=tk.BOTH, expand=True)

    # ------------------------------------------------------------------
    # Project tab helpers
    # ------------------------------------------------------------------

    def _update_project_info(self) -> None:
        p = Path(self.project_path_var.get() or ".")
        self.project_path = p

        bot_py = (p / "bot.py").is_file()
        cfg_py = (p / "config.py").is_file()
        data_dir = (p / "data").is_dir()
        cogs_dir = (p / "cogs").is_dir()
        statuses = _get_statuses_path(p).is_file()
        birthdays = _get_birthdays_path(p).is_file()

        lines = [
            f"Folder: {p}",
            f"bot.py: {'✓' if bot_py else '✗'}",
            f"config.py: {'✓' if cfg_py else '✗'}",
            f"data/: {'✓' if data_dir else '✗'}    cogs/: {'✓' if cogs_dir else '✗'}",
            f"data/statuses.json: {'✓' if statuses else '✗'}    data/birthdays.json: {'✓' if birthdays else '✗'}",
        ]
        self.project_info_label.configure(text="\n".join(lines))

        self._load_statuses()
        self._load_config_constants()
        self.on_refresh_birthdays()
        self._load_moderation_state()

    # ------------------------------------------------------------------
    # Top bar actions
    # ------------------------------------------------------------------

    def on_browse_project(self) -> None:
        folder = filedialog.askdirectory(
            title="Select project folder", initialdir=self.project_path
        )
        if not folder:
            return
        self.project_path_var.set(folder)
        self.config_data["project_path"] = folder
        save_config(self.config_data)
        self._update_project_info()
        self.log(f"Project set to: {folder}")

    def on_rescan(self) -> None:
        self._update_project_info()
        self.log("Rescanned project files.")

    def on_run_deploy(self) -> None:
        if self._deploy_running:
            messagebox.showinfo(APP_NAME, "Deploy is already running.")
            return

        p = self.project_path
        cmd = self.deploy_cmd_var.get().strip() or "deploy.bat"
        self.config_data["deploy_command"] = cmd
        save_config(self.config_data)

        if not (p / cmd).is_file():
            messagebox.showerror(APP_NAME, f"{cmd} not found in project folder.")
            return

        self._deploy_running = True
        if self.deploy_button:
            self.deploy_button.config(state="disabled")
        self.log(f"Running {cmd} in {p} …")

        def runner():
            try:
                if os.name == "nt":
                    popen_cmd = cmd
                    use_shell = True
                else:
                    popen_cmd = [cmd]
                    use_shell = False

                proc = subprocess.Popen(
                    popen_cmd,
                    cwd=str(p),
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    shell=use_shell,
                )
                assert proc.stdout is not None
                for line in proc.stdout:
                    self.log_from_thread(line.rstrip("\n"))
                proc.wait()
                self.log_from_thread(
                    f"{cmd} finished with code {proc.returncode}."
                )
            except Exception as exc:
                self.log_from_thread(f"Deploy failed: {exc}")
            finally:
                self._deploy_running = False
                if self.deploy_button:
                    self.after(0, lambda: self.deploy_button.config(state="normal"))

        threading.Thread(target=runner, daemon=True).start()

    # ------------------------------------------------------------------
    # Statuses tab actions
    # ------------------------------------------------------------------

    def _load_statuses(self) -> None:
        p = self.project_path
        statuses = load_statuses_from_json(p)
        if not statuses:
            statuses = load_statuses_from_cog(p)
        self.statuses = statuses
        self._refresh_status_listbox()

    def _refresh_status_listbox(self) -> None:
        if not hasattr(self, "status_listbox"):
            return
        lb: tk.Listbox = self.status_listbox  # type: ignore
        lb.delete(0, tk.END)
        for s in self.statuses:
            lb.insert(tk.END, s)

    def on_load_statuses_json(self) -> None:
        self.statuses = load_statuses_from_json(self.project_path)
        self._refresh_status_listbox()
        self.log("Loaded statuses from data/statuses.json.")

    def on_load_statuses_cog(self) -> None:
        self.statuses = load_statuses_from_cog(self.project_path)
        self._refresh_status_listbox()
        self.log("Loaded statuses from cogs/cheshire_status.py (SHORT_QUOTES).")

    def on_save_statuses_json(self) -> None:
        if hasattr(self, "status_listbox"):
            lb: tk.Listbox = self.status_listbox  # type: ignore
            self.statuses = [lb.get(i) for i in range(lb.size())]
        save_statuses_to_json(self.project_path, self.statuses)
        self.log("Saved statuses to data/statuses.json.")

    def on_add_update_status(self) -> None:
        if not hasattr(self, "status_listbox"):
            return
        lb: tk.Listbox = self.status_listbox  # type: ignore
        text = self.status_entry.get().strip()
        if not text:
            return
        sel = lb.curselection()
        if sel:
            idx = sel[0]
            lb.delete(idx)
            lb.insert(idx, text)
        else:
            lb.insert(tk.END, text)
        self.status_entry.delete(0, tk.END)

    def on_delete_statuses(self) -> None:
        if not hasattr(self, "status_listbox"):
            return
        lb: tk.Listbox = self.status_listbox  # type: ignore
        sel = list(lb.curselection())
        if not sel:
            return
        for idx in reversed(sel):
            lb.delete(idx)

    # ------------------------------------------------------------------
    # Settings tab actions
    # ------------------------------------------------------------------

    def _load_config_constants(self) -> None:
        consts, original_names = load_config_constants(self.project_path)
        self.config_constants = consts
        self.config_original_names = original_names

        if not self.config_tree:
            return

        self.config_tree.delete(*self.config_tree.get_children())
        for c in sorted(self.config_constants, key=lambda c: c["name"]):
            self.config_tree.insert("", tk.END, values=(c["name"], repr(c["value"])))

    def on_reload_config(self) -> None:
        self._load_config_constants()
        self.log("Reloaded constants from config.py.")

    def on_save_config(self) -> None:
        try:
            save_config_constants(
                self.project_path, self.config_constants, self.config_original_names
            )
            self.log("Saved constants back to config.py.")
        except Exception as exc:
            messagebox.showerror(APP_NAME, f"Failed to save config.py:\n{exc}")

    def on_apply_config_value(self) -> None:
        name = self.config_name_var.get().strip()
        if not name:
            return
        if not self.config_value_entry:
            return
        raw = self.config_value_entry.get()
        try:
            value = ast.literal_eval(raw)
        except Exception as exc:
            messagebox.showerror(
                APP_NAME, f"Value is not a valid Python literal:\n{exc}"
            )
            return
        _set_constant(self.config_constants, name, value)
        self._load_config_constants()
        self.log(f"Updated constant {name} (in memory).")

    # ------------------------------------------------------------------
    # Birthdays tab actions
    # ------------------------------------------------------------------

    def _set_birthdays_table(self, data: Dict[str, Any]) -> None:
        if not self.bday_tree:
            return
        self.bday_tree.delete(*self.bday_tree.get_children())
        for g_id, users in sorted(data.items(), key=lambda kv: kv[0]):
            if not isinstance(users, dict):
                continue
            for u_id, mm_dd in sorted(users.items(), key=lambda kv: kv[1]):
                self.bday_tree.insert(
                    "",
                    tk.END,
                    values=(g_id, f"{u_id} / <@{u_id}>", format_mmdd_to_ddmm(mm_dd)),
                )

    def on_refresh_birthdays(self) -> None:
        data = load_birthdays_local(self.project_path)
        self._set_birthdays_table(data)
        if data:
            self.log("Loaded local birthdays from data/birthdays.json.")
        else:
            self.log("No local birthdays file yet (data/birthdays.json).")

    def on_load_birthdays_file(self) -> None:
        path = filedialog.askopenfilename(
            title="Select birthdays.json",
            initialdir=self.project_path,
            filetypes=[("JSON files", "*.json"), ("All files", "*.*")],
        )
        if not path:
            return
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if not isinstance(data, dict):
                raise ValueError("Expected a JSON object at top level.")
            self.birthdays = data
            self._set_birthdays_table(data)
            self.log(f"Loaded birthdays from {path}.")
        except Exception as exc:
            messagebox.showerror(APP_NAME, f"Failed to load birthday file:\n{exc}")

    def on_sync_birthdays_from_server(self) -> None:
        url = self.birthdays_api_url_var.get().strip()
        token = self.birthdays_api_token_var.get().strip()
        if not url:
            messagebox.showerror(APP_NAME, "API URL is empty.")
            return

        self.config_data["birthdays_api_url"] = url
        self.config_data["birthdays_api_token"] = token
        save_config(self.config_data)

        self.log("Syncing birthdays from API …")

        def runner():
            try:
                req = urllib.request.Request(url)
                if token:
                    req.add_header("Authorization", f"Bearer {token}")
                with urllib.request.urlopen(req, timeout=20) as resp:
                    payload = resp.read().decode("utf-8")

                raw = json.loads(payload)

                if isinstance(raw, dict):
                    data = raw
                elif isinstance(raw, list):
                    normalised: Dict[str, Dict[str, str]] = {}
                    for item in raw:
                        if not isinstance(item, dict):
                            continue
                        g = item.get("guild_id") or item.get("guild") or item.get(
                            "guildId"
                        )
                        u = item.get("user_id") or item.get("user") or item.get(
                            "userId"
                        )
                        mm_dd = (
                            item.get("mm_dd")
                            or item.get("mm-dd")
                            or item.get("date")
                            or item.get("birthday")
                        )
                        if not (g and u and mm_dd):
                            continue
                        normalised.setdefault(str(g), {})[str(u)] = str(mm_dd)
                    data = normalised
                else:
                    raise ValueError("API did not return a dict or list.")

                self.birthdays = data

                path = _get_birthdays_path(self.project_path)
                path.parent.mkdir(parents=True, exist_ok=True)
                with path.open("w", encoding="utf-8") as f:
                    json.dump(data, f, indent=2, ensure_ascii=False)

                self.after(0, lambda: self._set_birthdays_table(data))
                self.log_from_thread(
                    "Synced birthdays from API and saved to data/birthdays.json."
                )
            except urllib.error.HTTPError as exc:
                self.log_from_thread(
                    f"Birthdays API HTTP error: {exc.code} {exc.reason}"
                )
            except urllib.error.URLError as exc:
                self.log_from_thread(f"Birthdays API connection failed: {exc}")
            except Exception as exc:
                self.log_from_thread(f"Birthdays API failed: {exc}")

        threading.Thread(target=runner, daemon=True).start()

    # ------------------------------------------------------------------
    # Moderation tab actions
    # ------------------------------------------------------------------

    def _load_moderation_state(self) -> None:
        mod_path = self.project_path / "cogs" / "moderation.py"
        b = read_scalar_constant(mod_path, "BLOCK_INVITES")
        if isinstance(b, bool):
            self.mod_block_invites_var.set(b)
        b = read_scalar_constant(mod_path, "BLOCK_MASS_MENTIONS")
        if isinstance(b, bool):
            self.mod_block_mass_mentions_var.set(b)
        v = read_scalar_constant(mod_path, "MAX_MENTIONS")
        if isinstance(v, int):
            self.mod_max_mentions_var.set(v)

        b = read_scalar_constant(mod_path, "ANTISPAM_ENABLED")
        if isinstance(b, bool):
            self.mod_antispam_enabled_var.set(b)
        v = read_scalar_constant(mod_path, "SPAM_WINDOW_SECONDS")
        if isinstance(v, int):
            self.mod_spam_window_var.set(v)
        v = read_scalar_constant(mod_path, "SPAM_MAX_MESSAGES")
        if isinstance(v, int):
            self.mod_spam_max_messages_var.set(v)

        b = read_scalar_constant(mod_path, "REPEAT_ENABLED")
        if isinstance(b, bool):
            self.mod_repeat_enabled_var.set(b)
        v = read_scalar_constant(mod_path, "REPEAT_WINDOW_SECONDS")
        if isinstance(v, int):
            self.mod_repeat_window_var.set(v)

        self._reload_warns_internal()

    def on_save_moderation(self) -> None:
        mod_path = self.project_path / "cogs" / "moderation.py"
        try:
            write_scalar_constant(
                mod_path, "BLOCK_INVITES", bool(self.mod_block_invites_var.get())
            )
            write_scalar_constant(
                mod_path,
                "BLOCK_MASS_MENTIONS",
                bool(self.mod_block_mass_mentions_var.get()),
            )
            write_scalar_constant(
                mod_path, "MAX_MENTIONS", int(self.mod_max_mentions_var.get())
            )
            write_scalar_constant(
                mod_path,
                "ANTISPAM_ENABLED",
                bool(self.mod_antispam_enabled_var.get()),
            )
            write_scalar_constant(
                mod_path,
                "SPAM_WINDOW_SECONDS",
                int(self.mod_spam_window_var.get()),
            )
            write_scalar_constant(
                mod_path,
                "SPAM_MAX_MESSAGES",
                int(self.mod_spam_max_messages_var.get()),
            )
            write_scalar_constant(
                mod_path,
                "REPEAT_ENABLED",
                bool(self.mod_repeat_enabled_var.get()),
            )
            write_scalar_constant(
                mod_path,
                "REPEAT_WINDOW_SECONDS",
                int(self.mod_repeat_window_var.get()),
            )
            self.log("Saved moderation toggles/thresholds to cogs/moderation.py.")
        except Exception as exc:
            messagebox.showerror(APP_NAME, f"Failed to save cogs/moderation.py:\n{exc}")

    # ------------------------------------------------------------------
    # Warns JSON handling
    # ------------------------------------------------------------------

    def _reload_warns_internal(self) -> None:
        path = self.project_path / "data" / "modnotes.json"
        data: Dict[str, Any] = {}

        if path.is_file():
            try:
                with path.open("r", encoding="utf-8") as f:
                    data = json.load(f)
            except Exception:
                data = {}
        else:
            try:
                path.parent.mkdir(parents=True, exist_ok=True)
                with path.open("w", encoding="utf-8") as f:
                    json.dump({}, f, indent=2)
            except Exception:
                pass

        if not self.mod_warns_tree:
            return

        self.mod_warns_tree.delete(*self.mod_warns_tree.get_children())
        for g_id, users in sorted(data.items(), key=lambda kv: kv[0]):
            if not isinstance(users, dict):
                continue
            for u_id, d in sorted(users.items(), key=lambda kv: kv[0]):
                if not isinstance(d, dict):
                    continue
                warns = d.get("warns", [])
                count = len(warns) if isinstance(warns, list) else 0
                self.mod_warns_tree.insert(
                    "",
                    tk.END,
                    values=(g_id, str(u_id), count),
                )

    def on_reload_warns(self) -> None:
        self._reload_warns_internal()
        self.log("Reloaded warn database from data/modnotes.json.")

    def on_clear_selected_warns(self) -> None:
        if not self.mod_warns_tree:
            return
        sel = self.mod_warns_tree.selection()
        if not sel:
            return
        item_id = sel[0]
        values = self.mod_warns_tree.item(item_id, "values")
        if len(values) != 3:
            return
        user_id_str = values[1]
        try:
            user_id = int(user_id_str)
        except Exception:
            messagebox.showerror(APP_NAME, f"Unexpected user ID: {user_id_str}")
            return

        path = self.project_path / "data" / "modnotes.json"
        if not path.is_file():
            return
        try:
            with path.open("r", encoding="utf-8") as f:
                data = json.load(f)
            if not isinstance(data, dict):
                raise ValueError("Bad JSON structure.")
        except Exception as exc:
            messagebox.showerror(APP_NAME, f"Failed to read modnotes.json:\n{exc}")
            return

        changed = False
        for g_id, users in list(data.items()):
            if not isinstance(users, dict):
                continue
            if str(user_id) in users:
                users.pop(str(user_id), None)
                changed = True
            if not users:
                data.pop(g_id, None)

        if not changed:
            self.log(f"No warns found for user {user_id}.")
            return

        try:
            with path.open("w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            self.log(f"Cleared warns for user {user_id}.")
        except Exception as exc:
            messagebox.showerror(APP_NAME, f"Failed to write modnotes.json:\n{exc}")
            return

        self._reload_warns_internal()


def main() -> None:
    app = CheshireAdminApp()
    app.mainloop()


if __name__ == "__main__":
    main()
