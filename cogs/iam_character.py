# cogs/iam_character.py
from __future__ import annotations

import io
import json
import logging
import os
import re
from dataclasses import dataclass
from typing import Any, Optional
from urllib.parse import quote

import aiohttp
import discord
from discord.ext import commands

# Pillow (add `pillow` to requirements.txt)
from PIL import Image, ImageDraw, ImageFont

LOG = logging.getLogger(__name__)

DB_PATH = "data/iam_characters.json"
WEBHOOK_NAME = "Cheshire Character Card"

# External card host (nice-to-have). If it dies, we generate locally.
CHAR_CARD_BASE_URL = os.getenv("CHAR_CARD_BASE_URL", "https://xiv-character-cards.drakon.cloud")
HTTP_TIMEOUT_S = 20

# XIVAPI (used for local fallback generation)
XIVAPI_BASE = os.getenv("XIVAPI_BASE_URL", "https://xivapi.com")
XIVAPI_KEY = os.getenv("XIVAPI_KEY")  # optional; but recommended for reliability/rate limits
XIVAPI_UA = os.getenv("XIVAPI_USER_AGENT", "CheshireBot/1.0 (iam_character)")

ID_FROM_URL_RE = re.compile(r"/characters/id/(\d+)\.png")


def _load_db() -> dict[str, Any]:
    try:
        with open(DB_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return {}
    except Exception:
        LOG.exception("Failed reading %s", DB_PATH)
        return {}


def _save_db(data: dict[str, Any]) -> None:
    try:
        os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
        with open(DB_PATH, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception:
        LOG.exception("Failed writing %s", DB_PATH)


@dataclass
class StoredChar:
    name: str
    world: str
    lodestone_id: Optional[int] = None

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "StoredChar":
        return cls(
            name=str(d["name"]),
            world=str(d["world"]),
            lodestone_id=int(d["lodestone_id"]) if d.get("lodestone_id") else None,
        )

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"name": self.name, "world": self.world}
        if self.lodestone_id:
            out["lodestone_id"] = self.lodestone_id
        return out


def _card_url_by_id(lodestone_id: int, lang: str = "en") -> str:
    return f"{CHAR_CARD_BASE_URL}/characters/id/{lodestone_id}.png?lang={quote(lang, safe='')}"


def _prepare_url_by_name(world: str, name: str) -> str:
    w = quote(world.strip(), safe="")
    n = quote(name.strip(), safe="")
    return f"{CHAR_CARD_BASE_URL}/prepare/name/{w}/{n}"


def _lodestone_search_url(world: str, name: str) -> str:
    return (
        "https://na.finalfantasyxiv.com/lodestone/character/"
        f"?q={quote(name.strip())}&worldname={quote(world.strip())}"
    )


def _try_extract_id(prep_json: dict[str, Any]) -> Optional[int]:
    url = prep_json.get("url")
    if not isinstance(url, str):
        return None
    m = ID_FROM_URL_RE.search(url)
    if not m:
        return None
    return int(m.group(1))


def build_character_embed(
    stored: StoredChar,
    *,
    requested_by: Optional[discord.Member] = None,
    use_attachment: bool = False,
) -> discord.Embed:
    em = discord.Embed(
        title=f"{stored.name} — {stored.world}",
        color=discord.Color.dark_teal(),
    )

    if stored.lodestone_id and not use_attachment:
        em.set_image(url=_card_url_by_id(stored.lodestone_id, lang="en"))
        em.description = "Character card generated from Lodestone data."
    elif use_attachment:
        em.set_image(url="attachment://charcard.png")
        em.description = "Character card generated from Lodestone data (local render)."
    else:
        em.description = "Card not cached yet."

    if requested_by:
        em.set_author(
            name=f"Requested by {requested_by.display_name}",
            icon_url=requested_by.display_avatar.url,
        )

    em.set_footer(text="Source: Card host (when available) + XIVAPI fallback")
    return em


async def _prepare_card_for_name(session: aiohttp.ClientSession, *, world: str, name: str) -> dict[str, Any]:
    url = _prepare_url_by_name(world, name)
    async with session.get(url) as r:
        text = await r.text()
        if r.status != 200:
            raise RuntimeError(f"Card API {r.status}: {text[:200]}")
        return await r.json()


async def _url_ok(session: aiohttp.ClientSession, url: str) -> bool:
    """Cheap availability check so we can decide between host image vs local render."""
    try:
        async with session.head(url, allow_redirects=True) as r:
            return 200 <= r.status < 300
    except Exception:
        return False


# ─────────────────────────────────────────────────────────────────────
# XIVAPI helpers (fallback) — used when the card host is down
# ─────────────────────────────────────────────────────────────────────


def _xivapi_headers() -> dict[str, str]:
    h = {"User-Agent": XIVAPI_UA}
    if XIVAPI_KEY:
        h["X-API-KEY"] = XIVAPI_KEY
    return h


async def _xivapi_search_id(session: aiohttp.ClientSession, *, world: str, name: str) -> Optional[int]:
    # https://xivapi.com/character/search?name=...&server=...
    params = {"name": name, "server": world}
    url = f"{XIVAPI_BASE.rstrip('/')}/character/search"
    async with session.get(url, params=params, headers=_xivapi_headers()) as r:
        if r.status != 200:
            return None
        data = await r.json()

    results = data.get("Results")
    if not isinstance(results, list) or not results:
        return None

    # Pick the first exact-ish match if possible
    lower_name = name.strip().lower()
    lower_world = world.strip().lower()
    for row in results:
        if not isinstance(row, dict):
            continue
        if str(row.get("Name", "")).strip().lower() == lower_name and str(row.get("Server", "")).strip().lower() == lower_world:
            try:
                return int(row.get("ID"))
            except Exception:
                pass

    # Fallback: first result
    try:
        return int(results[0].get("ID"))
    except Exception:
        return None


async def _xivapi_character(session: aiohttp.ClientSession, lodestone_id: int) -> Optional[dict[str, Any]]:
    # https://xivapi.com/character/<id>
    url = f"{XIVAPI_BASE.rstrip('/')}/character/{lodestone_id}"
    params = {
        "data": "CJ,AC"  # ClassJobs + Achievements/Character basic blocks
    }
    async with session.get(url, params=params, headers=_xivapi_headers()) as r:
        if r.status != 200:
            return None
        return await r.json()


def _safe_str(v: Any, default: str = "—") -> str:
    if v is None:
        return default
    s = str(v).strip()
    return s or default


def _job_rows(character_payload: dict[str, Any]) -> list[tuple[str, int]]:
    """Return list of (job_abbrev, level)."""
    char = character_payload.get("Character") if isinstance(character_payload, dict) else None
    if not isinstance(char, dict):
        return []

    cjs = char.get("ClassJobs")
    if not isinstance(cjs, list):
        return []

    rows: list[tuple[str, int]] = []
    for j in cjs:
        if not isinstance(j, dict):
            continue
        # XIVAPI usually gives: { "Job": {"Abbreviation": "PLD"}, "Level": 90 }
        job = j.get("Job")
        abbr = None
        if isinstance(job, dict):
            abbr = job.get("Abbreviation") or job.get("Name")
        if not abbr:
            continue
        try:
            lvl = int(j.get("Level") or 0)
        except Exception:
            lvl = 0
        # Hide 0-level entries to keep it clean
        if lvl <= 0:
            continue
        rows.append((str(abbr), lvl))

    # Sort by level desc then name
    rows.sort(key=lambda x: (-x[1], x[0]))
    return rows


def _render_card_image(
    *,
    title: str,
    name: str,
    world: str,
    portrait_img: Image.Image,
    jobs: list[tuple[str, int]],
) -> bytes:
    """Build a single PNG like your example (portrait left, stats/jobs right)."""

    # Canvas
    W, H = 900, 520
    img = Image.new("RGBA", (W, H), (12, 16, 24, 255))
    draw = ImageDraw.Draw(img)

    # Fonts (Pillow default; avoids missing font files)
    f_title = ImageFont.load_default()
    f_big = ImageFont.load_default()
    f_small = ImageFont.load_default()

    # Portrait area
    portrait = portrait_img.convert("RGBA")
    portrait = portrait.resize((360, 520))
    img.paste(portrait, (0, 0))

    # Right panel
    x0 = 380
    pad = 18

    # Header
    draw.text((x0 + pad, 18), _safe_str(title, ""), font=f_title, fill=(210, 220, 255, 255))
    draw.text((x0 + pad, 48), _safe_str(name), font=f_big, fill=(255, 255, 255, 255))
    draw.text((x0 + pad, 72), _safe_str(world), font=f_small, fill=(170, 190, 220, 255))

    # Divider
    draw.line((x0 + pad, 100, W - pad, 100), fill=(70, 90, 130, 255), width=2)

    # Jobs grid
    draw.text((x0 + pad, 116), "Jobs", font=f_title, fill=(210, 220, 255, 255))

    # Render as two-column list for readability
    col1_x = x0 + pad
    col2_x = x0 + 260
    y = 146
    row_h = 18

    # Keep it sane: show up to 28 entries
    jobs = jobs[:28]

    for idx, (abbr, lvl) in enumerate(jobs):
        cx = col1_x if idx % 2 == 0 else col2_x
        if idx % 2 == 0 and idx > 0:
            y += row_h
        line = f"{abbr:>3}  {lvl:>2}"
        draw.text((cx, y), line, font=f_small, fill=(240, 240, 240, 255))

    # Soft footer
    draw.text((x0 + pad, H - 26), "Generated by Cheshire (fallback)", font=f_small, fill=(120, 140, 170, 255))

    out = io.BytesIO()
    img.save(out, format="PNG")
    return out.getvalue()


async def _download_image(session: aiohttp.ClientSession, url: str) -> Optional[Image.Image]:
    try:
        async with session.get(url) as r:
            if r.status != 200:
                return None
            raw = await r.read()
        return Image.open(io.BytesIO(raw))
    except Exception:
        return None


async def _generate_card_png_bytes(
    session: aiohttp.ClientSession,
    *,
    stored: StoredChar,
) -> Optional[bytes]:
    """Generate a card PNG using XIVAPI, no external card-host needed."""

    if not stored.lodestone_id:
        # Try to resolve ID via XIVAPI search
        cid = await _xivapi_search_id(session, world=stored.world, name=stored.name)
        if not cid:
            return None
        stored.lodestone_id = cid

    payload = await _xivapi_character(session, stored.lodestone_id)
    if not payload:
        return None

    char = payload.get("Character")
    if not isinstance(char, dict):
        return None

    # Prefer Portrait, fallback to Avatar
    portrait_url = char.get("Portrait") or char.get("Avatar")
    if not isinstance(portrait_url, str) or not portrait_url:
        return None

    portrait_img = await _download_image(session, portrait_url)
    if portrait_img is None:
        return None

    title = _safe_str(char.get("Title"), "")
    name = _safe_str(char.get("Name"), stored.name)
    world = _safe_str(char.get("Server"), stored.world)

    jobs = _job_rows(payload)

    return _render_card_image(
        title=title,
        name=name,
        world=world,
        portrait_img=portrait_img,
        jobs=jobs,
    )


class IAmCharacter(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._db = _load_db()
        self._session: Optional[aiohttp.ClientSession] = None

    async def cog_unload(self):
        if self._session and not self._session.closed:
            await self._session.close()

    async def _session_get(self) -> aiohttp.ClientSession:
        if self._session and not self._session.closed:
            return self._session
        timeout = aiohttp.ClientTimeout(total=HTTP_TIMEOUT_S)
        self._session = aiohttp.ClientSession(timeout=timeout)
        return self._session

    def _guild_bucket(self, guild_id: int) -> dict[str, Any]:
        return self._db.setdefault(str(guild_id), {})

    def _get_stored(self, guild_id: int, user_id: int) -> Optional[StoredChar]:
        raw = self._guild_bucket(guild_id).get(str(user_id))
        return StoredChar.from_dict(raw) if isinstance(raw, dict) else None

    def _set_stored(self, guild_id: int, user_id: int, stored: StoredChar) -> None:
        self._guild_bucket(guild_id)[str(user_id)] = stored.to_dict()
        _save_db(self._db)

    def _del_stored(self, guild_id: int, user_id: int) -> bool:
        bucket = self._guild_bucket(guild_id)
        existed = str(user_id) in bucket
        bucket.pop(str(user_id), None)
        if existed:
            _save_db(self._db)
        return existed

    async def _send_card(
        self,
        ctx: commands.Context,
        *,
        embed: discord.Embed,
        content: Optional[str] = None,
        file: Optional[discord.File] = None,
    ) -> None:
        """Send via webhook when possible (so AutoClean doesn't eat it), otherwise normal send."""
        channel = ctx.channel
        if isinstance(channel, discord.TextChannel):
            perms = channel.permissions_for(channel.guild.me) if channel.guild and channel.guild.me else None
            if perms and perms.manage_webhooks:
                try:
                    hooks = await channel.webhooks()
                    hook = discord.utils.find(lambda w: w.name == WEBHOOK_NAME, hooks)
                    if hook is None:
                        hook = await channel.create_webhook(name=WEBHOOK_NAME, reason="FFXIV character cards")

                    await hook.send(
                        content=content,
                        embed=embed,
                        file=file,
                        username="Cheshire Cat",
                        avatar_url=self.bot.user.display_avatar.url if self.bot.user else None,
                        wait=False,
                    )
                    return
                except Exception:
                    LOG.exception("Webhook send failed; falling back to normal send")

        await ctx.send(content=content, embed=embed, file=file)

    async def _send_host_or_fallback(
        self,
        ctx: commands.Context,
        *,
        stored: StoredChar,
        requested_by: Optional[discord.Member] = None,
    ) -> None:
        """Try card-host image first; if it's down, render locally and upload."""
        session = await self._session_get()

        # If we have an ID and host is reachable for that image, use it.
        if stored.lodestone_id:
            host_url = _card_url_by_id(stored.lodestone_id, "en")
            if await _url_ok(session, host_url):
                em = build_character_embed(stored, requested_by=requested_by)
                return await self._send_card(ctx, embed=em, content=host_url)

        # Otherwise generate locally
        png = await _generate_card_png_bytes(session, stored=stored)
        if png is None:
            # Last resort: Lodestone search
            link = _lodestone_search_url(stored.world, stored.name)
            em = discord.Embed(
                title=f"{stored.name} — {stored.world}",
                description="I couldn’t generate a card right now. Here’s a Lodestone search link instead.",
                color=discord.Color.dark_teal(),
            )
            em.add_field(name="Lodestone search", value=link, inline=False)
            return await ctx.send(embed=em)

        # Update cached ID if we discovered it during generation
        if stored.lodestone_id:
            self._set_stored(ctx.guild.id, (requested_by or ctx.author).id, stored)  # best-effort cache

        file = discord.File(fp=io.BytesIO(png), filename="charcard.png")
        em = build_character_embed(stored, requested_by=requested_by, use_attachment=True)
        return await self._send_card(ctx, embed=em, file=file)

    # ─────────────────────────────────────────────────────────────
    # Commands
    # ─────────────────────────────────────────────────────────────

    @commands.command(name="iam")
    async def iam(self, ctx: commands.Context, *args: str):
        if ctx.guild is None:
            return await ctx.send("This command only works in the server.")
        if len(args) < 2:
            return await ctx.send("Usage: `!iam <First Last> <World>` (world is the last word).")

        world = args[-1].strip()
        name = " ".join(args[:-1]).strip()

        stored = StoredChar(name=name, world=world, lodestone_id=None)
        self._set_stored(ctx.guild.id, ctx.author.id, stored)

        session = await self._session_get()

        # Try to cache lodestone_id via card-host prepare (fast when host is up)
        try:
            prep = await _prepare_card_for_name(session, world=world, name=name)
            lodestone_id = _try_extract_id(prep)
            if lodestone_id:
                stored.lodestone_id = lodestone_id
                self._set_stored(ctx.guild.id, ctx.author.id, stored)
        except aiohttp.ClientConnectorError:
            # Host is down — that's fine; we can still work.
            LOG.warning("Card host unreachable during !iam; using fallback path")
        except Exception:
            LOG.exception("Card prepare failed")

        # Send host card if possible, otherwise local generated card
        return await self._send_host_or_fallback(ctx, stored=stored)

    @commands.command(name="whoami")
    async def whoami(self, ctx: commands.Context, member: Optional[discord.Member] = None):
        if ctx.guild is None:
            return await ctx.send("This command only works in the server.")

        target = member or ctx.author
        stored = self._get_stored(ctx.guild.id, target.id)
        if not stored:
            if target.id == ctx.author.id:
                return await ctx.send("You haven’t set a character yet. Use `!iam <First Last> <World>`. ")
            return await ctx.send("That user hasn’t set a character yet.")

        # Try host card, else local render fallback
        return await self._send_host_or_fallback(ctx, stored=stored, requested_by=ctx.author)

    @commands.command(name="forgetme")
    async def forgetme(self, ctx: commands.Context):
        if ctx.guild is None:
            return await ctx.send("This command only works in the server.")
        if self._del_stored(ctx.guild.id, ctx.author.id):
            return await ctx.send("Erased. 🐾")
        return await ctx.send("Nothing to forget.")


async def setup(bot: commands.Bot):
    await bot.add_cog(IAmCharacter(bot))
