# cogs/iam_character.py
from __future__ import annotations

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

LOG = logging.getLogger(__name__)

DB_PATH = "data/iam_characters.json"
WEBHOOK_NAME = "Cheshire Character Card"

# Allow self-hosting / changing provider without code edits
CHAR_CARD_BASE_URL = os.getenv("CHAR_CARD_BASE_URL", "https://xiv-character-cards.drakon.cloud")
HTTP_TIMEOUT_S = 20

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
    # Simple, reliable fallback: user can click and confirm identity
    return (
        "https://na.finalfantasyxiv.com/lodestone/character/"
        f"?q={quote(name.strip())}&worldname={quote(world.strip())}"
    )


async def _prepare_card_for_name(session: aiohttp.ClientSession, *, world: str, name: str) -> dict[str, Any]:
    url = _prepare_url_by_name(world, name)
    async with session.get(url) as r:
        text = await r.text()
        if r.status != 200:
            raise RuntimeError(f"Card API {r.status}: {text[:200]}")
        return await r.json()


def _try_extract_id(prep_json: dict[str, Any]) -> Optional[int]:
    url = prep_json.get("url")
    if not isinstance(url, str):
        return None
    m = ID_FROM_URL_RE.search(url)
    if not m:
        return None
    return int(m.group(1))


def build_character_embed(stored: StoredChar, *, requested_by: Optional[discord.Member] = None) -> discord.Embed:
    em = discord.Embed(
        title=f"{stored.name} — {stored.world}",
        color=discord.Color.dark_teal(),
    )

    if stored.lodestone_id:
        em.set_image(url=_card_url_by_id(stored.lodestone_id, lang="en"))
        em.description = "Character card generated from Lodestone data."
    else:
        em.description = "Card not cached yet."

    if requested_by:
        em.set_author(
            name=f"Requested by {requested_by.display_name}",
            icon_url=requested_by.display_avatar.url,
        )

    em.set_footer(text="Source: Character Cards (or fallback to Lodestone)")
    return em


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

    async def _send_card(self, ctx: commands.Context, embed: discord.Embed, content: Optional[str] = None) -> None:
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
                        username="Cheshire Cat",
                        avatar_url=self.bot.user.display_avatar.url if self.bot.user else None,
                        wait=False,
                    )
                    return
                except Exception:
                    LOG.exception("Webhook send failed; falling back to normal send")
        await ctx.send(content=content, embed=embed)

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
        try:
            prep = await _prepare_card_for_name(session, world=world, name=name)
            lodestone_id = _try_extract_id(prep)
            if lodestone_id:
                stored.lodestone_id = lodestone_id
                self._set_stored(ctx.guild.id, ctx.author.id, stored)
                em = build_character_embed(stored)
                return await self._send_card(ctx, em, content=_card_url_by_id(lodestone_id, "en"))
        except aiohttp.ClientConnectorError as e:
            # DNS/down → fallback to Lodestone search
            LOG.exception("Card host unreachable")
            link = _lodestone_search_url(world, name)
            em = discord.Embed(
                title=f"{name} — {world}",
                description="Card host is unreachable right now. Here’s a Lodestone search link instead.",
                color=discord.Color.dark_teal(),
            )
            em.add_field(name="Lodestone search", value=link, inline=False)
            return await ctx.send(embed=em)
        except Exception:
            LOG.exception("Card prepare failed")

        return await ctx.send("I couldn’t cache the card yet. Try `!whoami` again in a bit.")

    @commands.command(name="whoami")
    async def whoami(self, ctx: commands.Context, member: Optional[discord.Member] = None):
        if ctx.guild is None:
            return await ctx.send("This command only works in the server.")

        target = member or ctx.author
        stored = self._get_stored(ctx.guild.id, target.id)
        if not stored:
            if target.id == ctx.author.id:
                return await ctx.send("You haven’t set a character yet. Use `!iam <First Last> <World>`.")
            return await ctx.send("That user hasn’t set a character yet.")

        # If we already have an ID, embed the stable image URL
        if stored.lodestone_id:
            em = build_character_embed(stored, requested_by=ctx.author)
            return await self._send_card(ctx, em, content=_card_url_by_id(stored.lodestone_id, "en"))

        # Otherwise, try preparing again; if host unreachable, fallback link
        session = await self._session_get()
        try:
            prep = await _prepare_card_for_name(session, world=stored.world, name=stored.name)
            lodestone_id = _try_extract_id(prep)
            if lodestone_id:
                stored.lodestone_id = lodestone_id
                self._set_stored(ctx.guild.id, target.id, stored)
                em = build_character_embed(stored, requested_by=ctx.author)
                return await self._send_card(ctx, em, content=_card_url_by_id(lodestone_id, "en"))
        except aiohttp.ClientConnectorError:
            link = _lodestone_search_url(stored.world, stored.name)
            em = discord.Embed(
                title=f"{stored.name} — {stored.world}",
                description="Card host is unreachable right now. Here’s a Lodestone search link instead.",
                color=discord.Color.dark_teal(),
            )
            em.add_field(name="Lodestone search", value=link, inline=False)
            return await ctx.send(embed=em)
        except Exception:
            LOG.exception("Re-prepare failed for %s / %s", stored.world, stored.name)

        em = build_character_embed(stored, requested_by=ctx.author)
        return await self._send_card(ctx, em)

    @commands.command(name="forgetme")
    async def forgetme(self, ctx: commands.Context):
        if ctx.guild is None:
            return await ctx.send("This command only works in the server.")
        if self._del_stored(ctx.guild.id, ctx.author.id):
            return await ctx.send("Erased. 🐾")
        return await ctx.send("Nothing to forget.")


async def setup(bot: commands.Bot):
    await bot.add_cog(IAmCharacter(bot))
