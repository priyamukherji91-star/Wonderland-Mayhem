from __future__ import annotations

import asyncio
import logging
import random
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands

import permissions

LOG = logging.getLogger(__name__)

# Rotate every 5 minutes
STATUS_ROTATE_SECONDS = 300

# NOTE: This list is the user-approved canonical status pool.
SHORT_QUOTES = [
    "We’re all mad here",
    "Always tea time",
    "Lost in Wonderland",
    "Follow the rabbit",
    "Down the hole",
    "Not all there",
    "Stuck in the void.",
    "Unfortunate.",
    "Chido vanished again",
    "Moreno clicking extreme",
    "Shitty Boi",
    "I'm not a cat",
    "Senri fading softly",
    "Feed the Pepper",
    "Lea progging Limsa",
    "Bundy is baked",
    "Food coma extreme",
    "Cookie reset mid-sentence",
    "Lost but committed",
    "Chido chose violence",
    "Elva touched nothing (suspicious)",
    "Inaeli pressed the button",
    "Kotetsu went AFK mentally",
    "Shari sighed deeply",
    "Zyphie tapped my shoulder",
    "Brain cell on cooldown",
    "Emotionally AFK",
    "Coping, poorly",
    "Here, unfortunately",
    "Inaeli knew better",
    "Lemonardo monologuing",
    "Cookie thinking about snacks",
    "Existing is laggy",
    "Patch broke me",
    "Celes took notes",
    "Cookie forgot why",
    "Cookie rebooting brain",
    "Senri took aggro accidentally",
    "Moreno didn’t read patch notes",
    "Vibes unstable",
    "Meetings broke me",
    "Work? No.",

    # ── Newly added statuses ──
    "Show me your footwork!",
    "Chido lost in Factorio again",
    "Lea, a cat in disguise",
    "Yun Yun - Lucky Lala Foot",
    "Origar book pile diving",
    "Zyphie lost in Forbidden Door",
    "Pepper is doodling",
    "Lea looking for hotties in the area",
    "Tocuhes grass in Azeroth",
    "Shari being feeshspecious",
    "Sleeping the day away",
    "Sloppy",
    "Undressing Viera's",
    "Wolf roleplay, cat reality",
    "Prog before Hoes",
    "WoL - Warrior of Light(less)",
    "This cutscene could’ve been an email",
    "I sense a surge in the aether!",
    "Only I can protect her!",
    "Trust system carries my social anxiety",
    "If not glam, why live?",
    "Such devastation… this was not my intention.",
    "All creation bends to my will!",
    "Behold: mechanics I will ignore.",
    "My rotation is a suggestion.",
    "WHM freecure fishing addiction",
    "Duty Complete: trust issues",
    "Helpers welcome (please carry)",
    "Someone emoted at me, I logged off",
    "We cleared once, now chasing that high",
    "Small indie company",
    "Lore skippers spotted",
]


class CheshireStatus(commands.Cog):
    """Rotating Discord presence for the Cheshire Cat bot,
    with a one-off manual override via /setstatus.
    """

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.override: Optional[str] = None
        self.task: Optional[asyncio.Task] = bot.loop.create_task(self.status_task())
        LOG.info("CheshireStatus rotation task started.")

    async def status_task(self) -> None:
        await self.bot.wait_until_ready()
        while not self.bot.is_closed():
            try:
                if self.override is not None:
                    quote = self.override
                    self.override = None
                else:
                    quote = random.choice(SHORT_QUOTES)

                activity = discord.Game(name=quote)
                await self.bot.change_presence(activity=activity)
                LOG.info("Set status to: %s", quote)
            except asyncio.CancelledError:
                LOG.info("CheshireStatus rotation task cancelled.")
                break
            except Exception:
                LOG.exception("Failed to set status")

            await asyncio.sleep(STATUS_ROTATE_SECONDS)

    def cog_unload(self) -> None:
        if self.task and not self.task.done():
            self.task.cancel()
            LOG.info("CheshireStatus rotation task stopped.")

    # ── Slash command: /setstatus ──

    @app_commands.command(
        name="setstatus",
        description="Set a one-off custom status for the Cheshire bot.",
    )
    @permissions.mod_slash_only()
    @app_commands.describe(text="The short status text to show.")
    async def setstatus(self, interaction: discord.Interaction, text: str) -> None:
        text = (text or "").strip()
        if not text:
            await interaction.response.send_message(
                "Status cannot be empty.",
                ephemeral=True,
            )
            return

        if len(text) > 128:
            text = text[:128]

        self.override = text

        try:
            await self.bot.change_presence(activity=discord.Game(name=text))
        except Exception:
            LOG.exception("Failed to set manual status")
            await interaction.response.send_message(
                "Could not update status right now.",
                ephemeral=True,
            )
            return

        await interaction.response.send_message(
            f"Status updated to:\n`{text}`\n\n"
            "Rotation will resume automatically on the next cycle.",
            ephemeral=True,
        )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(CheshireStatus(bot))
