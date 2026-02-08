# cogs/autoclean.py
import asyncio
import logging
from typing import Final

import discord
from discord.ext import commands

import config

# -----------------------------------------
# SETTINGS
# -----------------------------------------
AUTODELETE_SECONDS: Final[int] = 6

# Channels we never touch at all
EXEMPT_CHANNEL_IDS = set(config.AUTO_CLEAN_EXEMPT_CHANNEL_IDS)

LOG = logging.getLogger(__name__)


class AutoClean(commands.Cog):
    """
    AutoClean now ONLY deletes:
      • User prefix commands (!something)
      • User slash interaction messages

    Bot replies/results, webhook posts, embeds, and everything else
    will stay permanently.
    """

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    # -----------------------------------------
    # Helpers
    # -----------------------------------------

    def _is_exempt(self, channel: discord.abc.GuildChannel) -> bool:
        if channel.id in EXEMPT_CHANNEL_IDS:
            return True
        return False

    # -----------------------------------------
    # Delete ONLY user prefix commands
    # -----------------------------------------
    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        # ignore bot messages completely
        if message.author.bot:
            return

        # ignore DMs or missing guild
        if not message.guild:
            return

        # ignore exempt channels completely
        if self._is_exempt(message.channel):
            return

        # obtain prefix list
        prefixes = await self.bot.get_prefix(message)
        if isinstance(prefixes, str):
            prefixes = [prefixes]

        # check if it starts with a prefix command
        if any(message.content.startswith(p) for p in prefixes):
            try:
                await asyncio.sleep(AUTODELETE_SECONDS)
                await message.delete()
            except discord.NotFound:
                pass
            except discord.Forbidden:
                LOG.warning("AutoClean: missing permissions to delete a user command")
            except Exception:
                LOG.exception("AutoClean: error deleting user command")

    # -----------------------------------------
    # Delete ONLY the user's slash interaction "stub"
    # (NOT the bot's reply message)
    # -----------------------------------------
    @commands.Cog.listener()
    async def on_interaction(self, interaction: discord.Interaction):
        if interaction.type != discord.InteractionType.application_command:
            return

        # ignore if not from a guild / exempt channel
        channel = interaction.channel
        if not isinstance(channel, discord.TextChannel):
            return
        if self._is_exempt(channel):
            return

        # delete ONLY the user's interaction trigger
        try:
            await asyncio.sleep(AUTODELETE_SECONDS)
            await interaction.delete_original_response()
        except discord.NotFound:
            pass
        except discord.Forbidden:
            # some slash commands don't permit deleting the stub — ignore
            pass
        except Exception:
            LOG.exception("AutoClean: failed deleting interaction trigger")


async def setup(bot: commands.Bot):
    await bot.add_cog(AutoClean(bot))
