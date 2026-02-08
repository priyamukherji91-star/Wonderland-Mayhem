# cogs/help_catalog.py
from __future__ import annotations

import logging
from collections import defaultdict
from typing import Dict, Iterable, List, Optional, Tuple

import discord
from discord.ext import commands
from discord import app_commands

LOG = logging.getLogger(__name__)


# ── helpers ─────────────────────────────────────────────────────────

def _safe(text: Optional[str]) -> str:
    if not text:
        return "—"
    s = " ".join(text.strip().split())
    return s or "—"


def _chunk_text(lines: List[str], max_chars: int = 3900) -> List[str]:
    """Split into chunks under ~4k to fit embed description comfortably."""
    chunks: List[str] = []
    buf: List[str] = []
    length = 0
    for line in lines:
        ln = len(line) + 1
        if buf and length + ln > max_chars:
            chunks.append("\n".join(buf))
            buf = [line]
            length = len(line)
        else:
            buf.append(line)
            length += ln
    if buf:
        chunks.append("\n".join(buf))
    return chunks


def _fmt(cmd: str, desc: str) -> str:
    return f"• {cmd}\n  {desc}"


def _cog_title(name: Optional[str]) -> str:
    if not name:
        return "Misc"
    # Display tidy names (strip 'cogs.' and underscores)
    pretty = name.replace("cogs.", "").replace("_", " ").strip()
    return pretty.title() or "Misc"


def _walk_slash(tree: app_commands.CommandTree) -> Iterable[app_commands.Command | app_commands.ContextMenu]:
    """Yield all slash + context menu commands, flattening groups."""
    def expand(c: app_commands.Command | app_commands.Group | app_commands.ContextMenu):
        if isinstance(c, app_commands.Group):
            for child in c.commands:
                yield from expand(child)
        else:
            yield c

    for c in tree.get_commands():
        yield from expand(c)


def _module_to_cog_name(obj) -> Optional[str]:
    """Best-effort: map a callback's module (e.g., 'cogs.giveaways') to a cog title."""
    mod = getattr(getattr(obj, "__call__", obj), "__module__", None) or getattr(obj, "__module__", None)
    if not mod:
        return None
    # expect 'cogs.<name>' → '<name>'
    return mod.split(".", 1)[1] if mod.startswith("cogs.") and "." in mod else mod


# ── main cog ────────────────────────────────────────────────────────

class HelpCatalog(commands.Cog):
    """
    Unified command catalog:

    - Slash commands (including grouped ones, e.g. /duty new)
    - Context menus (user/message)
    - Prefix commands (!something and aliases)

    All grouped by cog/module.
    """

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    # --------- collectors

    def collect_slash(self, query: Optional[str]) -> Dict[str, List[Tuple[str, str, str]]]:
        """
        Returns mapping: cog_name -> list of (display, desc, kind)
        kind ∈ {'slash','ctx-user','ctx-message'}
        """
        by_cog: Dict[str, List[Tuple[str, str, str]]] = defaultdict(list)
        try:
            for c in _walk_slash(self.bot.tree):
                if isinstance(c, app_commands.Command):
                    disp = f"/{c.qualified_name}"
                    desc = _safe(c.description or getattr(c.callback, "__doc__", None))
                    kind = "slash"
                    cog_name = _module_to_cog_name(c.callback) or "Misc"
                elif isinstance(c, app_commands.ContextMenu):
                    typ = "ctx-user" if c.type is discord.AppCommandType.user else "ctx-message"
                    disp = f"[{typ}] {c.name}"
                    desc = "Context menu command"
                    kind = typ
                    cog_name = _module_to_cog_name(c.callback) or "Misc"
                else:
                    continue

                if query and query.lower() not in (disp.lower() + " " + desc.lower()):
                    continue

                by_cog[cog_name].append((disp, desc, kind))
        except Exception:
            LOG.exception("collect_slash failed")
        return by_cog

    def collect_prefix(self, query: Optional[str]) -> Dict[str, List[Tuple[str, str, str]]]:
        """
        Returns mapping: cog_name -> list of (display, desc, kind='prefix')
        """
        by_cog: Dict[str, List[Tuple[str, str, str]]] = defaultdict(list)
        try:
            for cmd in self.bot.commands:
                if getattr(cmd, "hidden", False):
                    continue
                names = [cmd.name] + list(getattr(cmd, "aliases", []) or [])
                disp = " | ".join(f"!{n}" for n in names)
                desc = _safe(
                    getattr(cmd, "brief", None)
                    or getattr(cmd, "help", None)
                    or getattr(cmd.callback, "__doc__", None)
                )

                if query and query.lower() not in (disp.lower() + " " + desc.lower()):
                    continue

                # Prefer the command's bound Cog name; fallback to callback module
                cog_name = getattr(cmd.cog, "qualified_name", None) or getattr(cmd.cog, "name", None)
                if not cog_name:
                    cog_name = _module_to_cog_name(cmd.callback) or "Misc"

                by_cog[cog_name].append((disp, desc, "prefix"))
        except Exception:
            LOG.exception("collect_prefix failed")
        return by_cog

    # --------- rendering

    def build_embeds(self, title: str, query: Optional[str]) -> List[discord.Embed]:
        # merge slash + prefix by cog
        grouped: Dict[str, List[Tuple[str, str, str]]] = defaultdict(list)

        for cog, rows in self.collect_slash(query).items():
            grouped[cog].extend(rows)
        for cog, rows in self.collect_prefix(query).items():
            grouped[cog].extend(rows)

        # sort cogs and rows
        sorted_cogs = sorted(grouped.items(), key=lambda kv: _cog_title(kv[0]).lower())

        if not sorted_cogs:
            em = discord.Embed(
                title=title,
                description="No commands matched your query.",
                color=0x5865F2,
            )
            return [em]

        KIND_ORDER = {"slash": 0, "ctx-user": 1, "ctx-message": 2, "prefix": 3}

        lines: List[str] = []
        for cog_name, rows in sorted_cogs:
            rows.sort(key=lambda r: (KIND_ORDER.get(r[2], 9), r[0].lower()))
            lines.append(f"__**{_cog_title(cog_name)}**__")
            for disp, desc, _kind in rows:
                lines.append(_fmt(disp, desc))
            lines.append("")

        chunks = _chunk_text(lines)
        embeds: List[discord.Embed] = []
        for i, ch in enumerate(chunks):
            em = discord.Embed(
                title=title if i == 0 else f"{title} (page {i+1})",
                description=ch,
                color=0x5865F2,
            )
            embeds.append(em)
        return embeds

    # --------- command

    @app_commands.command(
        name="helpall",
        description="Show a catalog of all commands, grouped by cog/module.",
    )
    @app_commands.describe(query="Optional search term to filter commands")
    async def helpall(self, interaction: discord.Interaction, query: Optional[str] = None) -> None:
        await interaction.response.defer(ephemeral=True)
        embeds = self.build_embeds("Command catalog", query)
        for em in embeds:
            await interaction.followup.send(embed=em, ephemeral=True)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(HelpCatalog(bot))
