# cogs/iam_character.py
from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from typing import Any, Optional
from urllib.parse import quote

import discord
from discord.ext import commands

LOG = logging.getLogger(__name__)

DB_PATH = "data/iam_characters.json"
WEBHOOK_NAME = "Cheshire Character Card"

# XIV Character Cards service (PNG cards)
# Docs: https://xivapi.github.io/XIV-Character-Cards/
CHAR_CARD_BASE_URL = "https://xiv-character-cards.drakon.cloud"


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
    name: str
    world: str

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "StoredChar":
        return cls(
            name=str(d["name"]),
            world=str(d["world"]),
        )

    def to_dict(self) -> dict[str, Any]:
        return {"name": self.name, "world": self.world}


def build_card_url_by_name(world: str, name: str, lang: str = "en") -> str:
    # Endpoint: /characters/name/<WORLD>/<CHARACTER NAME>.png?lang=en
    # Name + world must be URL encoded.
    w = quote(world.strip(), safe="")
    n = quote(name.strip(), safe="")
    return f"{CHAR_CARD_BASE_URL}/characters/name/{w}/{n}.png?lang={quote(lang, safe='')}"


def build_character_embed(stored: StoredChar, *, requested_by: Optional[discord.Member] = None) -> discord.Embed:
    em = discord.Embed(
        title=f"{stored.name} — {stored.world}",
        color=discord.Color.dark_teal(),
    )

    # Big card image
    em.set_image(url=build_card_url_by_name(stored.world, stored.name, lang="en"))

    if requested_by:
        em.set_author(
            name=f"Requested by {requested_by.display_name}",
            icon_url=requested_by.display_avatar.url,
        )

    em.set_footer(text="Character card generated from Lodestone data.")
    return em


class IAmCharacter(commands.Cog):
    """Store a user's FFXIV character and show a quick card."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._db = _load_db()

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

    async def _send_card(self, ctx: commands.Context, embed: discord.Embed) -> None:
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

    @commands.command(name="iam")
    async def iam(self, ctx: commands.Context, *args: str):
        """
        !iam <First Last> <World>
        Example: !iam Cookie Chan Ragnarok
        """
        if ctx.guild is None:
            return await ctx.send("This command only works in the server.")

        if len(args) < 2:
            return await ctx.send("Usage: `!iam <First Last> <World>` (world is the last word).")

        world = args[-1].strip()
        name = " ".join(args[:-1]).strip()

        stored = StoredChar(name=name, world=world)
        self._set_stored(ctx.guild.id, ctx.author.id, stored)

        em = build_character_embed(stored)
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
                return await ctx.send("You haven’t set a character yet. Use `!iam <First Last> <World>`.")
            return await ctx.send("That user hasn’t set a character yet.")

        em = build_character_embed(stored, requested_by=ctx.author)
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
