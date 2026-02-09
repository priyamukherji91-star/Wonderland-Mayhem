# cogs/autoclean.py
import asyncio
import logging
from typing import Final, Iterable

import discord
from discord import app_commands
from discord.ext import commands

import config

# —— CONFIG ————————————————————————————————————————
AUTODELETE_SECONDS: Final[int] = 6

# Channels you want to EXEMPT from any auto-deletion
EXEMPT_CHANNEL_IDS: set[int] = set(getattr(config, "AUTO_CLEAN_EXEMPT_CHANNEL_IDS", []))

# Categories to exempt (optional)
EXEMPT_CATEGORY_IDS: set[int] = set()

# Heuristics: skip messages that should persist
SKIP_IF_PINNED: Final[bool] = True
SKIP_IF_HAS_COMPONENTS: Final[bool] = True  # don’t nuke active buttons/menus

# —— WARNING-SKIP SETTINGS —————————————————————————
MOD_WARNING_CHANNEL_IDS: set[int] = set()
if getattr(config, "MODLOG_CHANNEL_ID", None) is not None:
    MOD_WARNING_CHANNEL_IDS.add(config.MODLOG_CHANNEL_ID)

WARNING_KEYWORDS: tuple[str, ...] = (
    "Warning noted.",
    "has been warned",
    "has received a warning",
)

WARNING_EMBED_TITLES: tuple[str, ...] = (
    "User Warned",
    "Warning Issued",
)

WARNING_EMBED_FOOTER_SNIPPETS: tuple[str, ...] = (
    "Warning noted",
    "Cheshire AutoMod",
)

# —— LOGGING ————————————————————————
LOG = logging.getLogger(__name__)


class AutoClean(commands.Cog):
    """Auto-deletes bot clutter and prefix invocations in most channels."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    def _is_exempt_channel(self, channel: discord.abc.GuildChannel) -> bool:
        if not isinstance(channel, (discord.TextChannel, discord.Thread)):
            return True
        if channel.id in EXEMPT_CHANNEL_IDS:
            return True
        if channel.category_id and channel.category_id in EXEMPT_CATEGORY_IDS:
            return True
        return False

    def _should_ignore_message(self, message: discord.Message) -> bool:
        if message.guild is None:
            return True
        if self._is_exempt_channel(message.channel):
            return True
        if SKIP_IF_PINNED and getattr(message, "pinned", False):
            return True
        if SKIP_IF_HAS_COMPONENTS and getattr(message, "components", None):
            return True
        return False

    def _looks_like_warning_message(self, message: discord.Message) -> bool:
        if message.channel.id not in MOD_WARNING_CHANNEL_IDS:
            return False

        text = _gather_message_text(message)
        if _contains_any(text, WARNING_KEYWORDS):
            return True
        if _contains_any(text, WARNING_EMBED_TITLES):
            return True
        if _contains_any(text, WARNING_EMBED_FOOTER_SNIPPETS):
            return True

        return False

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        if message.guild is None or message.author.bot:
            return
        if self._should_ignore_message(message):
            return

        if message.author == self.bot.user:
            if self._looks_like_warning_message(message):
                return
            if message.content and message.content.strip() == "I agree.":
                return
            await message.delete(delay=AUTODELETE_SECONDS)
            return

        prefixes = await self.bot.get_prefix(message)
        if isinstance(prefixes, str):
            prefixes = [prefixes]

        if any(message.content.startswith(p) for p in prefixes):
            try:
                await asyncio.sleep(AUTODELETE_SECONDS)
                await message.delete()
            except discord.NotFound:
                pass
            except discord.Forbidden:
                LOG.warning(
                    "AutoClean: missing permissions to delete user prefix cmd in #%s",
                    getattr(message.channel, "name", "?"),
                )
            except Exception:
                LOG.exception("AutoClean: failed to delete user prefix invocation")

    @commands.Cog.listener()
    async def on_app_command_completion(
        self,
        interaction: discord.Interaction,
        command: app_commands.Command,
    ) -> None:
        channel = interaction.channel
        if channel is None or not isinstance(channel, discord.TextChannel):
            return
        if self._is_exempt_channel(channel):
            return

        try:
            if interaction.response.is_done():
                original = await interaction.original_response()
                if original:
                    await asyncio.sleep(AUTODELETE_SECONDS)
                    await original.delete()
        except discord.NotFound:
            pass
        except discord.Forbidden:
            logging.warning(
                "AutoClean: cannot delete original response in #%s (missing perms).",
                getattr(interaction.channel, "name", "?"),
            )
        except Exception:
            logging.exception("AutoClean: on_app_command_completion failed")


def _contains_any(haystack: str, needles: Iterable[str]) -> bool:
    h = haystack.lower()
    return any(n.lower() in h for n in needles)


def _gather_message_text(message: discord.Message) -> str:
    parts: list[str] = []
    if message.content:
        parts.append(message.content)
    for e in message.embeds or ():
        if e.title:
            parts.append(e.title)
        if e.description:
            parts.append(e.description)
        if e.footer and e.footer.text:
            parts.append(e.footer.text)
    return "\n".join(parts)


async def setup(bot: commands.Bot):
    await bot.add_cog(AutoClean(bot))
