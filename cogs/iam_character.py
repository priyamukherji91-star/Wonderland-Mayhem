from __future__ import annotations

from urllib.parse import quote

import discord
from discord.ext import commands
from discord import app_commands


# Tomestone resolver: redirects to the canonical character page if it exists.
# Discord will unfurl it into a rich embed/card.
TOMESTONE_RESOLVER = "https://tomestone.gg/character-name/{world}/{name}"

# Lodestone search fallback (always works)
LODESTONE_SEARCH = "https://na.finalfantasyxiv.com/lodestone/character/?q={name}&worldname={world}"


def _clean_spaces(s: str) -> str:
    return " ".join((s or "").strip().split())


def tomestone_url(name: str, world: str) -> str:
    # Tomestone is fine with lowercase worlds; it redirects properly.
    w = quote(_clean_spaces(world).lower(), safe="")
    n = quote(_clean_spaces(name), safe="")
    return TOMESTONE_RESOLVER.format(world=w, name=n)


def lodestone_url(name: str, world: str) -> str:
    w = quote(_clean_spaces(world), safe="")
    n = quote(_clean_spaces(name), safe="")
    return LODESTONE_SEARCH.format(name=n, world=w)


class IAmCharacter(commands.Cog):
    """
    Tomestone-first without server-side scraping.

    We post the Tomestone resolver link and let Discord generate the embed/card.
    This avoids Cloudflare/anti-bot blocks against your host.
    """

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    async def _send(self, target, *, name: str, world: str) -> None:
        name = _clean_spaces(name)
        world = _clean_spaces(world)

        if not name or not world:
            msg = "Usage: `!iam <Character Name> <World>` or `/iam name:<name> world:<world>`"
            if isinstance(target, commands.Context):
                await target.reply(msg, mention_author=False)
            else:
                await target.response.send_message(msg, ephemeral=True)
            return

        t_url = tomestone_url(name, world)
        l_url = lodestone_url(name, world)

        # NOTE:
        # - Tomestone link is NOT wrapped in <>, so Discord WILL embed it (your “card”).
        # - Lodestone link is wrapped in <> to avoid double embeds (optional, cleaner UX).
        content = (
            f"**{name} — {world}**\n"
            f"{t_url}\n"
            f"Lodestone search: <{l_url}>"
        )

        if isinstance(target, commands.Context):
            await target.reply(content, mention_author=False)
        else:
            # interaction
            if target.response.is_done():
                await target.followup.send(content)
            else:
                await target.response.send_message(content)

    # ── Prefix: !iam Cookie Chan Ragnarok (last token = world)
    @commands.command(name="iam")
    async def iam_prefix(self, ctx: commands.Context, *, query: str) -> None:
        query = _clean_spaces(query)
        parts = query.split()
        if len(parts) < 2:
            return await ctx.reply("Usage: `!iam <Character Name> <World>`", mention_author=False)

        world = parts[-1]
        name = " ".join(parts[:-1])
        await self._send(ctx, name=name, world=world)

    # ── Slash: /iam name: Cookie Chan world: Ragnarok
    @app_commands.command(name="iam", description="Show an FFXIV character (Tomestone embed + Lodestone fallback).")
    @app_commands.describe(name="Character name", world="World/server")
    async def iam_slash(self, interaction: discord.Interaction, name: str, world: str) -> None:
        await self._send(interaction, name=name, world=world)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(IAmCharacter(bot))
