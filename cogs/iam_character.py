from __future__ import annotations

from urllib.parse import quote

import discord
from discord.ext import commands
from discord import app_commands

WEBHOOK_NAME = "Cheshire IAm"


TOMESTONE_RESOLVER = "https://tomestone.gg/character-name/{world}/{name}"
LODESTONE_SEARCH = "https://na.finalfantasyxiv.com/lodestone/character/?q={name}&worldname={world}"


def _clean_spaces(s: str) -> str:
    return " ".join((s or "").strip().split())


def tomestone_url(name: str, world: str) -> str:
    w = quote(_clean_spaces(world).lower(), safe="")
    n = quote(_clean_spaces(name), safe="")
    return TOMESTONE_RESOLVER.format(world=w, name=n)


def lodestone_url(name: str, world: str) -> str:
    w = quote(_clean_spaces(world), safe="")
    n = quote(_clean_spaces(name), safe="")
    return LODESTONE_SEARCH.format(name=n, world=w)


class IAmCharacter(commands.Cog):
    """
    Tomestone-first WITHOUT scraping, plus webhook send so AutoClean won't delete it.
    """

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    async def _send_via_webhook(self, channel: discord.TextChannel, content: str) -> bool:
        """Return True if webhook send succeeded, else False."""
        try:
            webhooks = await channel.webhooks()
            webhook = discord.utils.find(lambda w: w.name == WEBHOOK_NAME, webhooks)
            if webhook is None:
                webhook = await channel.create_webhook(
                    name=WEBHOOK_NAME,
                    reason="IAm output (anti-autodelete)",
                )

            await webhook.send(
                content=content,
                username="Cheshire Cat",
                avatar_url=self.bot.user.display_avatar.url if self.bot.user else None,
                wait=True,
            )
            return True
        except Exception:
            return False

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

        # IMPORTANT:
        # - Tomestone link stays “raw” so Discord unfurls it into a rich preview/card.
        # - Lodestone link is wrapped to avoid double embeds.
        content = (
            f"**{name} — {world}**\n"
            f"{t_url}\n"
            f"Lodestone search: <{l_url}>"
        )

        # Prefer webhook in text channels (prevents AutoClean deletion)
        channel = None
        if isinstance(target, commands.Context) and isinstance(target.channel, discord.TextChannel):
            channel = target.channel
        elif isinstance(target, discord.Interaction) and isinstance(target.channel, discord.TextChannel):
            channel = target.channel

        if channel is not None:
            ok = await self._send_via_webhook(channel, content)
            if ok:
                # Quiet ack for slash so user sees “done”, but the real post is public
                if isinstance(target, discord.Interaction) and not target.response.is_done():
                    await target.response.send_message("Done.", ephemeral=True)
                return

        # Fallback: normal send (may be autodeleted if AutoClean hits it)
        if isinstance(target, commands.Context):
            await target.reply(content, mention_author=False)
        else:
            if target.response.is_done():
                await target.followup.send(content)
            else:
                await target.response.send_message(content)

    # Prefix: !iam Cookie Chan Ragnarok (last token = world)
    @commands.command(name="iam")
    async def iam_prefix(self, ctx: commands.Context, *, query: str) -> None:
        query = _clean_spaces(query)
        parts = query.split()
        if len(parts) < 2:
            return await ctx.reply("Usage: `!iam <Character Name> <World>`", mention_author=False)

        world = parts[-1]
        name = " ".join(parts[:-1])
        await self._send(ctx, name=name, world=world)

    # Slash: /iam name: Cookie Chan world: Ragnarok
    @app_commands.command(name="iam", description="Show an FFXIV character (Tomestone preview + Lodestone fallback).")
    @app_commands.describe(name="Character name", world="World/server")
    async def iam_slash(self, interaction: discord.Interaction, name: str, world: str) -> None:
        await self._send(interaction, name=name, world=world)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(IAmCharacter(bot))
