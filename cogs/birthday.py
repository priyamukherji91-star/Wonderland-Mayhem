# cogs/birthday.py
# Birthday cog with:
# - /birthday set/check/today
# - Daily birthday announcements at 09:00 server time
# Storage is a JSON file on disk, with a path that can live on a Railway volume.

from __future__ import annotations

import json
import logging
import os
import random
from datetime import datetime, time as dtime
from typing import Dict, Any, Optional

import discord
from discord.ext import commands, tasks
from discord import app_commands
from zoneinfo import ZoneInfo

import config

log = logging.getLogger(__name__)

# ── STORAGE PATH ─────────────────────────────────────────────────
# You can override this in Railway with an env var, e.g.:
#   BIRTHDAY_DB_PATH=/data/birthdays.json
# if your volume is mounted at /data.
BIRTHDAY_DB_PATH = os.getenv("BIRTHDAY_DB_PATH", "data/birthdays.json")

# ── CONFIG FROM CONFIG.PY ────────────────────────────────────────

BIRTHDAY_SET_CHANNEL_ID = config.BIRTHDAY_SET_CHANNEL_ID
BIRTHDAY_ANNOUNCE_CHANNEL_ID = config.BIRTHDAY_ANNOUNCE_CHANNEL_ID
BIRTHDAY_STAFF_CHANNEL_ID = config.BIRTHDAY_STAFF_CHANNEL_ID
DOOMED_RABBIT_ROLE_NAME = config.DOOMED_RABBIT_ROLE_NAME
ST_TIMEZONE = config.ST_TIMEZONE
BIRTHDAY_CHECK_TIME = dtime(hour=9, minute=0, tzinfo=ZoneInfo(ST_TIMEZONE))

# ── FLAVOUR TEXT ─────────────────────────────────────────────────

BIRTHDAY_MESSAGES = [
    "🎂 Down the rabbit hole we go! Today is {mention}’s very real birthday (not an unbirthday at all). Shower them with curious wishes! 🫖🃏",
    "🫖 A very merry birthday to {mention}! The teacups are spinning, the hatter is shouting, and the cards are cheering – may your day be wonderfully mad.",
    "⏱️ The White Rabbit checked his watch: today belongs to {mention}! Happy birthday – may your path through Wonderland be strange in all the best ways.",
    "🎉 No unbirthdays here – today is {mention}’s true day. Cake, chaos and a little bit of magic are in order.",
    "🃏 The Queen has declared: today we celebrate {mention}! Happy birthday, you delightful creature of Wonderland.",
    "🌹 The roses are painted and the table is set – happy birthday, {mention}! May your year be curiouser and curiouser.",
]

PUBLIC_ROAST_CONFIRM = [
    "🎂 Birthday saved, {mention}. Try not to embarrass Wonderland on the day.",
    "🫖 Your birthday’s logged, {mention}. Even the teacups groaned.",
    "♠️ Birthday noted, {mention}. The cards demanded hazard pay.",
    "⏱️ The Rabbit wrote your birthday down, {mention}. He immediately regretted it.",
    "🃏 Your birthday’s in the ledger, {mention}. Wonderland is bracing itself.",
    "🌙 Birthday recorded, {mention}. The Cheshire Cat muttered ‘yikes’.",
    "🎩 Birthday filed, {mention}. The hatbox asked why it had to be you.",
    "🪶 Your birthday’s inked, {mention}. The quill tried to resign.",
]

EPHEMERAL_CONFIRM_TEXT = "Done! When the clock strikes your day, I’ll shout it across Wonderland."


# ── STORAGE HELPERS ──────────────────────────────────────────────

def _load_birthdays() -> Dict[str, Any]:
    """Load the birthday DB from disk. Returns {} if missing or broken."""
    try:
        # Ensure parent dir exists (in case the file will be created later)
        parent = os.path.dirname(BIRTHDAY_DB_PATH)
        if parent:
            os.makedirs(parent, exist_ok=True)

        if not os.path.exists(BIRTHDAY_DB_PATH):
            log.info("Birthday DB not found at %s; starting empty.", BIRTHDAY_DB_PATH)
            return {}

        with open(BIRTHDAY_DB_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
            if isinstance(data, dict):
                log.info("Loaded birthday DB from %s (guilds: %d).", BIRTHDAY_DB_PATH, len(data))
                return data
            else:
                log.warning("Birthday DB at %s is not a dict; resetting.", BIRTHDAY_DB_PATH)
                return {}
    except Exception:
        log.exception("Failed to read birthday DB at %s", BIRTHDAY_DB_PATH)
        return {}


def _save_birthdays(data: Dict[str, Any]) -> None:
    """Persist the birthday DB to disk."""
    try:
        parent = os.path.dirname(BIRTHDAY_DB_PATH)
        if parent:
            os.makedirs(parent, exist_ok=True)
        with open(BIRTHDAY_DB_PATH, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        log.info("Saved birthday DB to %s", BIRTHDAY_DB_PATH)
    except Exception:
        log.exception("Failed to write birthday DB to %s", BIRTHDAY_DB_PATH)


# ── COG IMPLEMENTATION ───────────────────────────────────────────

class Birthdays(commands.Cog):
    """Birthday tracking and daily announcements."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self._db: Dict[str, Any] = _load_birthdays()
        self._tz = ZoneInfo(ST_TIMEZONE)
        self.check_birthdays.start()

    def cog_unload(self) -> None:
        self.check_birthdays.cancel()

    # ── internal helpers ─────────────────────────────────────────

    def _set_birthday(self, guild_id: int, user_id: int, mm_dd: str) -> None:
        """Set or update a user's birthday in MM-DD format."""
        g = self._db.setdefault(str(guild_id), {})
        g[str(user_id)] = mm_dd
        _save_birthdays(self._db)

    def _get_birthday(self, guild_id: int, user_id: int) -> Optional[str]:
        return self._db.get(str(guild_id), {}).get(str(user_id))

    @staticmethod
    def _has_doomed_rabbit(member: discord.Member) -> bool:
        return any(role.name == DOOMED_RABBIT_ROLE_NAME for role in member.roles)

    @staticmethod
    def _mmdd_to_ddmm(mm_dd: str) -> str:
        """Convert 'MM-DD' → 'DD/MM' for display."""
        try:
            m, d = mm_dd.split("-")
            return f"{int(d):02d}/{int(m):02d}"
        except Exception:
            return mm_dd

    # ── slash group: /birthday ───────────────────────────────────

    birthday = app_commands.Group(name="birthday", description="Manage birthdays.")

    @birthday.command(name="set", description="Set your birthday (DD/MM)")
    async def birthday_set(self, interaction: discord.Interaction, date: str) -> None:
        """User sets their birthday as DD/MM in the configured channel."""
        if interaction.channel_id != BIRTHDAY_SET_CHANNEL_ID:
            await interaction.response.send_message(
                "Use this in the birthday channel.",
                ephemeral=True,
            )
            return

        # Accept DD/MM or D/M, basic validation
        try:
            parts = date.strip().replace(" ", "").split("/")
            if len(parts) != 2:
                raise ValueError
            d, m = (int(parts[0]), int(parts[1]))
            # Validate using a dummy year
            datetime(year=2000, month=m, day=d)
        except Exception:
            await interaction.response.send_message(
                "Format must be **DD/MM** (e.g. 07/04).",
                ephemeral=True,
            )
            return

        mm_dd = f"{m:02d}-{d:02d}"
        assert interaction.guild is not None

        self._set_birthday(interaction.guild.id, interaction.user.id, mm_dd)

        public_line = random.choice(PUBLIC_ROAST_CONFIRM).format(mention=interaction.user.mention)

        await interaction.response.send_message(EPHEMERAL_CONFIRM_TEXT, ephemeral=True)

        # Public confirmation roast in the birthday channel
        try:
            channel = interaction.guild.get_channel(BIRTHDAY_SET_CHANNEL_ID)
            if isinstance(channel, discord.TextChannel):
                await channel.send(public_line)
        except Exception:
            log.exception("Failed to send public birthday confirmation")

    @birthday.command(name="check", description="List known birthdays in this server.")
    async def birthday_check(self, interaction: discord.Interaction) -> None:
        """List all stored birthdays for the current guild."""
        assert interaction.guild is not None
        guild_id = interaction.guild.id
        g = self._db.get(str(guild_id), {})

        if not g:
            await interaction.response.send_message("No birthdays are written yet.")
            return

        lines = []
        # sort by date (MM-DD)
        for uid, mm_dd in sorted(g.items(), key=lambda kv: kv[1]):
            member = interaction.guild.get_member(int(uid))
            if not member:
                continue
            name = member.display_name
            lines.append(f"• {name} — {self._mmdd_to_ddmm(mm_dd)}")

        final = "⏱️ The ledger opens:\n" + "\n".join(lines)
        await interaction.response.send_message(final)

    @birthday.command(name="today", description="Show today’s birthdays (Doomed Rabbit only).")
    async def birthday_today(self, interaction: discord.Interaction) -> None:
        """Show today's birthdays, restricted to Doomed Rabbit in the staff channel."""
        if interaction.channel_id != BIRTHDAY_STAFF_CHANNEL_ID:
            await interaction.response.send_message("Wrong channel.", ephemeral=True)
            return

        if not isinstance(interaction.user, discord.Member) or not self._has_doomed_rabbit(interaction.user):
            await interaction.response.send_message("Not enough rabbit.", ephemeral=True)
            return

        assert interaction.guild is not None
        today = datetime.now(self._tz).strftime("%m-%d")
        g = self._db.get(str(interaction.guild.id), {})

        matches: list[str] = []
        for uid, mm_dd in g.items():
            if mm_dd == today:
                member = interaction.guild.get_member(int(uid))
                if member:
                    matches.append(member.mention)

        if not matches:
            await interaction.response.send_message("No birthdays today.")
            return

        await interaction.response.send_message("Today’s tea party guests: " + " ".join(matches))

    # ── Scheduled daily announcements ─────────────────────────────

    @tasks.loop(time=BIRTHDAY_CHECK_TIME)
    async def check_birthdays(self) -> None:
        """Runs daily at BIRTHDAY_CHECK_TIME and posts birthday messages."""
        try:
            today = datetime.now(self._tz).strftime("%m-%d")

            for guild in self.bot.guilds:
                guild_data = self._db.get(str(guild.id), {})
                if not guild_data:
                    continue

                # Find members with birthdays today
                members_today: list[discord.Member] = []
                for uid, mm_dd in guild_data.items():
                    if mm_dd != today:
                        continue
                    member = guild.get_member(int(uid))
                    if member:
                        members_today.append(member)

                if not members_today:
                    continue

                channel = guild.get_channel(BIRTHDAY_ANNOUNCE_CHANNEL_ID)
                if not isinstance(channel, discord.TextChannel):
                    continue

                for member in members_today:
                    template = random.choice(BIRTHDAY_MESSAGES)
                    msg = template.format(mention=member.mention)
                    try:
                        await channel.send(msg)
                    except Exception:
                        log.exception("Failed to send birthday message in guild %s", guild.id)
        except Exception:
            log.exception("check_birthdays loop error")

    @check_birthdays.before_loop
    async def before_check_birthdays(self) -> None:
        # Ensure the bot is ready before starting the loop
        await self.bot.wait_until_ready()


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Birthdays(bot))
