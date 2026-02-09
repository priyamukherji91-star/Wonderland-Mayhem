# cogs/iam_character.py
from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from typing import Any, Optional

import aiohttp
import discord
from discord.ext import commands

import config

LOG = logging.getLogger(__name__)

DB_PATH = "data/iam_characters.json"
WEBHOOK_NAME = "Cheshire Character Card"

# XIVAPI v1 base (character/lodestone endpoints live here)
XIVAPI_BASE_URL = getattr(config, "XIVAPI_BASE_URL", "https://xivapi.com")
XIVAPI_PRIVATE_KEY = getattr(config, "XIVAPI_PRIVATE_KEY", None)
HTTP_TIMEOUT_S = getattr(config, "XIVAPI_TIMEOUT_SECONDS", 15)


# ───────────────────────────────────────────────────────────────────
# tiny JSON store
# ───────────────────────────────────────────────────────────────────

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
    lodestone_id: int
    name: str
    server: str
    avatar: Optional[str] = None

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "StoredChar":
        return cls(
            lodestone_id=int(d["lodestone_id"]),
            name=str(d["name"]),
            server=str(d["server"]),
            avatar=d.get("avatar"),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "lodestone_id": self.lodestone_id,
            "name": self.name,
            "server": self.server,
            "avatar": self.avatar,
        }


# ───────────────────────────────────────────────────────────────────
# XIVAPI calls
# ───────────────────────────────────────────────────────────────────

async def _xivapi_get_json(session: aiohttp.ClientSession, path: str, params: dict[str, str]) -> dict[str, Any]:
    url = f"{XIVAPI_BASE_URL.rstrip('/')}/{path.lstrip('/')}"
    if XIVAPI_PRIVATE_KEY:
        # XIVAPI commonly accepts developer keys as `private_key` query param. :contentReference[oaicite:3]{index=3}
        params = {**params, "private_key": str(XIVAPI_PRIVATE_KEY)}

    async with session.get(url, params=params) as r:
        txt = await r.text()
        if r.status != 200:
            raise RuntimeError(f"XIVAPI {r.status}: {txt[:300]}")
        try:
            return await r.json()
        except Exception as e:
            raise RuntimeError(f"XIVAPI returned non-JSON: {txt[:300]}") from e


async def xivapi_character_search(session: aiohttp.ClientSession, *, name: str, server: str) -> StoredChar:
    # Character search flow is the standard way: search(name, server) -> get ID. :contentReference[oaicite:4]{index=4}
    data = await _xivapi_get_json(
        session,
        "/character/search",
        {"name": name, "server": server},
    )

    results = data.get("Results") or data.get("results") or []
    if not results:
        raise LookupError("No character found for that name/server.")

    first = results[0]
    lodestone_id = int(first.get("ID") or first.get("id"))
    return StoredChar(
        lodestone_id=lodestone_id,
        name=str(first.get("Name") or first.get("name") or name),
        server=str(first.get("Server") or first.get("server") or server),
        avatar=first.get("Avatar") or first.get("avatar"),
    )


async def xivapi_character_profile(session: aiohttp.ClientSession, lodestone_id: int) -> dict[str, Any]:
    # Fetch a profile by ID; libraries typically expose this as character(id, all_data: true). :contentReference[oaicite:5]{index=5}
    return await _xivapi_get_json(
        session,
        f"/character/{lodestone_id}",
        {"data": "AC,FC"},  # active class/job + free company (best-effort)
    )


# ───────────────────────────────────────────────────────────────────
# rendering
# ───────────────────────────────────────────────────────────────────

def _safe_get(d: dict[str, Any], *keys: str) -> Optional[Any]:
    cur: Any = d
    for k in keys:
        if not isinstance(cur, dict) or k not in cur:
            return None
        cur = cur[k]
    return cur


def build_character_embed(stored: StoredChar, profile: Optional[dict[str, Any]]) -> discord.Embed:
    char = (profile or {}).get("Character") or {}
    name = char.get("Name") or stored.name
    server = char.get("Server") or stored.server

    title = char.get("Title")
    fc_name = _safe_get(profile or {}, "FreeCompany", "Name")

    acj = char.get("ActiveClassJob") or {}
    job = acj.get("Job") or {}
    job_name = job.get("Name") or job.get("Abbreviation") or None
    job_level = acj.get("Level")

    portrait = char.get("Portrait") or stored.avatar
    avatar = char.get("Avatar") or stored.avatar

    em = discord.Embed(
        title=f"{name} — {server}",
        description=(f"**Title:** {title}\n" if title else "")
        + (f"**Free Company:** {fc_name}\n" if fc_name else "")
        + (f"**Main job:** {job_name} (Lv {job_level})\n" if job_name and job_level is not None else ""),
        color=discord.Color.dark_teal(),
    )

    em.add_field(name="Lodestone ID", value=str(stored.lodestone_id), inline=True)
    em.add_field(name="Saved as", value=f"{stored.name} — {stored.server}", inline=True)

    if avatar:
        em.set_thumbnail(url=avatar)
    if portrait:
        em.set_image(url=portrait)

    em.set_footer(text="Pulled from XIVAPI / Lodestone.")
    return em


# ───────────────────────────────────────────────────────────────────
# Cog
# ───────────────────────────────────────────────────────────────────

class IAmCharacter(commands.Cog):
    """Store a user's FFXIV character and show a quick card."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._db = _load_db()
        self._session: Optional[aiohttp.ClientSession] = None

    async def cog_unload(self):
        if self._session and not self._session.closed:
            await self._session.close()

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

    async def _session_get(self) -> aiohttp.ClientSession:
        if self._session and not self._session.closed:
            return self._session
        timeout = aiohttp.ClientTimeout(total=HTTP_TIMEOUT_S)
        self._session = aiohttp.ClientSession(timeout=timeout)
        return self._session

    async def _send_card(self, ctx: commands.Context, embed: discord.Embed) -> None:
        """
        Prefer a webhook message so it behaves like a “real post”
        (and won’t get caught by any “delete bot messages” cleanup logic).
        """
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
                        embed=embed,
                        username="Cheshire Cat",
                        avatar_url=self.bot.user.display_avatar.url if self.bot.user else None,
                        wait=False,
                    )
                    return
                except Exception:
                    LOG.exception("Webhook send failed; falling back to normal send")

        await ctx.send(embed=embed)

    # ── Commands ──────────────────────────────────────────────────

    @commands.command(name="iam")
    async def iam(self, ctx: commands.Context, *args: str):
        """
        !iam <First Last> <Server>
        Example: !iam Raelys Skyborn Behemoth
        """
        if ctx.guild is None:
            return await ctx.send("This command only works in the server.")

        if len(args) < 2:
            return await ctx.send("Usage: `!iam <First Last> <Server>` (server is the last word).")

        server = args[-1]
        name = " ".join(args[:-1]).strip()

        session = await self._session_get()
        try:
            stored = await xivapi_character_search(session, name=name, server=server)
        except LookupError as e:
            return await ctx.send(str(e))
        except Exception as e:
            LOG.exception("XIVAPI search failed")
            return await ctx.send(f"Couldn’t reach XIVAPI right now: `{e}`")

        self._set_stored(ctx.guild.id, ctx.author.id, stored)

        profile = None
        try:
            profile = await xivapi_character_profile(session, stored.lodestone_id)
        except Exception:
            LOG.info("Profile fetch failed for %s", stored.lodestone_id)

        em = build_character_embed(stored, profile)
        await self._send_card(ctx, em)

    @commands.command(name="whoami")
    async def whoami(self, ctx: commands.Context, member: Optional[discord.Member] = None):
        """!whoami [@user] — show the saved character card."""
        if ctx.guild is None:
            return await ctx.send("This command only works in the server.")

        target = member or ctx.author
        stored = self._get_stored(ctx.guild.id, target.id)
        if not stored:
            if target.id == ctx.author.id:
                return await ctx.send("You haven’t set a character yet. Use `!iam <First Last> <Server>`.")
            return await ctx.send("That user hasn’t set a character yet.")

        session = await self._session_get()
        profile = None
        try:
            profile = await xivapi_character_profile(session, stored.lodestone_id)
        except Exception:
            LOG.info("Profile fetch failed for %s", stored.lodestone_id)

        em = build_character_embed(stored, profile)
        em.set_author(name=f"Requested by {ctx.author.display_name}", icon_url=ctx.author.display_avatar.url)
        await self._send_card(ctx, em)

    @commands.command(name="forgetme")
    async def forgetme(self, ctx: commands.Context):
        """!forgetme — delete your stored character."""
        if ctx.guild is None:
            return await ctx.send("This command only works in the server.")
        if self._del_stored(ctx.guild.id, ctx.author.id):
            return await ctx.send("Erased. The Cat has ‘forgotten’ you. 🐾")
        return await ctx.send("Nothing to forget.")


async def setup(bot: commands.Bot):
    await bot.add_cog(IAmCharacter(bot))
