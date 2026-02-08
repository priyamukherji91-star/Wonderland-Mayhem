"""
Moderation / AutoMod cog

Key points:
- The channels listed in EXEMPT_CHANNEL_IDS are *whitelisted*: no AutoMod deletes there.
  Add your wiki channel ID here to ensure NOTHING is deleted in that channel.
- No pin logic exists here; if you still see pins, it’s from another cog or manual pinning.
"""

import re
import json
import asyncio
import logging
from datetime import timedelta
from typing import Optional, Dict, Any, List, Set

import discord
from discord.ext import commands
from discord import app_commands

import config
import permissions

# ── CONFIG ─────────────────────────────────────────────────────────

# Where to persist warn notes (tiny JSON store)
WARN_DB_PATH = "data/modnotes.json"

# AutoMod toggles & thresholds
BLOCK_INVITES = True
BLOCK_MASS_MENTIONS = True
MAX_MENTIONS = 6

ANTISPAM_ENABLED = True
SPAM_WINDOW_SECONDS = 6
SPAM_MAX_MESSAGES = 6

REPEAT_ENABLED = True
REPEAT_WINDOW_SECONDS = 10

# Channels exempt from ALL AutoMod checks (NO deletes, NO spam checks, etc.)
EXEMPT_CHANNEL_IDS: Set[int] = {
    config.FFXIV_WIKI_CHANNEL_ID,  # #ffxiv-wiki
    # config.GIVEAWAYS_CHANNEL_ID,  # (optional) giveaways channel, if you want it exempt as well
}

# Roles that should always be considered immune (optional)
IMMUNE_ROLE_NAMES: List[str] = list(config.ADMIN_ROLE_NAMES)

# Buttons (if any) should be usable only by these users/roles
OWNER_USER_IDS: Set[int] = set()          # e.g. {123456789012345678}
PRIVATE_ROLE_NAMES: Set[str] = {config.DOOMED_RABBIT_ROLE_NAME}

INVITE_RE = re.compile(
    r"(?:discord\.gg/|discord\.com/invite/)[A-Za-z0-9-]+",
    re.IGNORECASE,
)

# Limit for how much message content we log in embeds
LOG_MESSAGE_CONTENT_MAX = 1900

# ── PUBLIC MESSAGES (flair) ────────────────────────────────────────
TIMEOUT_PUBLIC_TEMPLATE = "🫖 Time-out tea is served, {member}. Back in {duration}."
WARN_PUBLIC_TEMPLATE    = "🌹 Careful where you paint, {member}. Warning noted. {reason}"


# ── UTILS: tiny JSON "DB" for warnings ─────────────────────────────
def _load_warns() -> Dict[str, Any]:
    try:
        with open(WARN_DB_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return {}
    except Exception:
        logging.exception("Failed to read warn DB")
        return {}


def _save_warns(data: Dict[str, Any]) -> None:
    try:
        import os
        os.makedirs(os.path.dirname(WARN_DB_PATH), exist_ok=True)
        with open(WARN_DB_PATH, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception:
        logging.exception("Failed to write warn DB")


def _shorten(text: Optional[str], limit: int = 1024) -> str:
    if text is None:
        return ""
    s = str(text)
    if len(s) <= limit:
        return s
    return s[: limit - 3] + "..."


# ── PERM CHECKS ────────────────────────────────────────────────────
def is_mod():
    """Wrapper so existing decorators continue working, using shared permissions."""
    return permissions.mod_slash_only()


def _is_immune(member: discord.Member) -> bool:
    """Return True if member should be skipped by AutoMod checks."""
    return permissions.is_mod_member(member)


# ── MOD LOG HELPER ─────────────────────────────────────────────────
async def modlog(guild: discord.Guild, embed: discord.Embed):
    """Send an embed to the configured mod-log channel, if any."""
    if config.MODLOG_CHANNEL_ID is None:
        return
    ch = guild.get_channel(config.MODLOG_CHANNEL_ID)
    if isinstance(ch, discord.TextChannel):
        try:
            await ch.send(embed=embed)
        except Exception:
            logging.exception("Failed to post modlog")


def action_embed(
    user: discord.abc.User,
    actor: discord.abc.User,
    action: str,
    reason: Optional[str] = None,
) -> discord.Embed:
    e = discord.Embed(title=f"{action}", color=0xED4245)
    e.add_field(name="User", value=f"{user.mention} ({user.id})", inline=False)
    e.add_field(name="By", value=f"{actor.mention} ({actor.id})", inline=False)
    if reason:
        e.add_field(name="Reason", value=reason[:1000], inline=False)
    return e


# ── TRACKERS FOR AUTOMOD ───────────────────────────────────────────
class BurstTracker:
    """Track messages per user for spam and repeats."""

    def __init__(self):
        self.history: Dict[int, List[tuple[int, str]]] = {}  # user_id -> list[(ts, content)]

    def add(self, user_id: int, ts: int, content: str):
        bucket = self.history.setdefault(user_id, [])
        bucket.append((ts, content))

    def recent(self, user_id: int, ts: int, window: int) -> List[tuple[int, str]]:
        bucket = self.history.get(user_id, [])
        # Keep only entries within the window
        filtered = [(t, c) for (t, c) in bucket if ts - t <= window]
        self.history[user_id] = filtered
        return filtered


class Moderation(commands.Cog):
    """Moderation commands, AutoMod, and enhanced message logs."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.burst_tracker = BurstTracker()

    # ── helpers (audit-log based) ──────────────────────────────────

    async def _find_message_deleter(
        self,
        guild: discord.Guild,
        message: discord.Message,
    ) -> Optional[discord.Member | discord.User]:
        """
        Best-effort audit-log lookup for who deleted a message.
        Based on Mittens' mod_logs logic.
        """
        me = guild.me
        if not me or not me.guild_permissions.view_audit_log:
            return None

        try:
            now = discord.utils.utcnow()
            async for entry in guild.audit_logs(
                limit=6,
                action=discord.AuditLogAction.message_delete,
            ):
                # Only consider very recent entries
                if (now - entry.created_at).total_seconds() > 15:
                    continue

                target = entry.target
                extra = entry.extra

                # Type check target (member/user)
                if not isinstance(target, (discord.Member, discord.User)):
                    continue
                if target.id != message.author.id:
                    continue

                # Check channel match if present
                if getattr(extra, "channel", None) and getattr(extra.channel, "id", None) != message.channel.id:
                    continue

                return entry.user
        except Exception:
            # Silent: logging failures shouldn't break anything
            return None

        return None

    # ── ON MESSAGE (AUTOMOD) ───────────────────────────────────────
    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        # Ignore DMs / bot messages
        if not message.guild or message.author.bot:
            return

        # Exempt channels entirely
        if message.channel and message.channel.id in EXEMPT_CHANNEL_IDS:
            return

        # Don’t touch immune users
        if isinstance(message.author, discord.Member) and _is_immune(message.author):
            return

        # Basic invite blocking
        if BLOCK_INVITES and INVITE_RE.search(message.content or ""):
            try:
                await message.delete()
                await message.channel.send(
                    f"{message.author.mention} Discord invites aren’t allowed here.",
                    delete_after=10,
                )
                await modlog(
                    message.guild,
                    action_embed(message.author, self.bot.user, "Blocked invite"),
                )
            except discord.Forbidden:
                logging.warning(
                    "Moderation: missing permission to delete invite message"
                )
            except Exception:
                logging.exception("Error while blocking invite")
            return

        # Mass mention protection
        if BLOCK_MASS_MENTIONS and message.mentions:
            total_mentions = len(message.mentions) + len(message.role_mentions)
            if total_mentions >= MAX_MENTIONS:
                try:
                    await message.delete()
                    await message.channel.send(
                        f"{message.author.mention} that’s a few too many mentions.",
                        delete_after=10,
                    )
                    await modlog(
                        message.guild,
                        action_embed(message.author, self.bot.user, "Mass mention"),
                    )
                except discord.Forbidden:
                    logging.warning(
                        "Moderation: missing permission to delete mass mention message"
                    )
                except Exception:
                    logging.exception("Error while blocking mass mention")
                return

        # Antispam / repeat checks (now SILENT in-channel)
        if ANTISPAM_ENABLED or REPEAT_ENABLED:
            await self._check_spam_and_repeats(message)

    async def _check_spam_and_repeats(self, message: discord.Message):
        if not isinstance(message.author, discord.Member):
            return

        now_ts = int(message.created_at.timestamp())
        content = message.content or ""

        # Track recent messages for this user
        recent = self.burst_tracker.recent(
            message.author.id,
            now_ts,
            max(SPAM_WINDOW_SECONDS, REPEAT_WINDOW_SECONDS),
        )
        self.burst_tracker.add(message.author.id, now_ts, content)

        # Spam: too many messages in a short window
        if ANTISPAM_ENABLED:
            spam_recent = [t for (t, _) in recent if now_ts - t <= SPAM_WINDOW_SECONDS]
            if len(spam_recent) >= SPAM_MAX_MESSAGES:
                # Removed public "slow down / take a breather" message.
                try:
                    await modlog(
                        message.guild,
                        action_embed(message.author, self.bot.user, "Spam burst"),
                    )
                except Exception:
                    logging.exception("Error while handling spam burst (modlog)")
                return

        # Repeat detection: same message over and over
        if REPEAT_ENABLED and content:
            recent_texts = [c for (_, c) in recent]
            repeats = [c for c in recent_texts if c == content]
            if len(repeats) >= 3:
                # Removed public "we got the message" message.
                try:
                    await modlog(
                        message.guild,
                        action_embed(message.author, self.bot.user, "Repeated content"),
                    )
                except Exception:
                    logging.exception("Error while handling repeat spam (modlog)")

    # ── MESSAGE LOGGING (delete / edit, Mittens-style) ─────────────
    @commands.Cog.listener()
    async def on_message_delete(self, message: discord.Message):
        """
        Log non-bot message deletions to the mod-log channel,
        with audit-log best-effort 'Deleted by' field.
        """
        if config.MODLOG_CHANNEL_ID is None:
            return
        if not message.guild:
            return
        if message.guild.id != config.GUILD_ID:
            return
        if message.author.bot:
            return

        # Ignore deletions *from* the log channel itself, to avoid recursion.
        if isinstance(message.channel, discord.TextChannel) and message.channel.id == config.MODLOG_CHANNEL_ID:
            return
        if isinstance(message.channel, discord.Thread) and message.channel.parent_id == config.MODLOG_CHANNEL_ID:
            return

        if not isinstance(message.channel, (discord.TextChannel, discord.Thread)):
            return

        content = message.content or "*no content*"

        attach_info = ""
        if message.attachments:
            names = ", ".join(a.filename for a in message.attachments[:5])
            more = len(message.attachments) - 5
            if more > 0:
                names += f" (+{more} more)"
            attach_info = names

        deleter = await self._find_message_deleter(message.guild, message)
        if isinstance(deleter, (discord.Member, discord.User)):
            deleter_str = f"{deleter.mention} ({deleter.id})"
        else:
            deleter_str = "Unknown / self-delete"

        embed = discord.Embed(
            title="🗑 Message deleted",
            description=f"```{_shorten(content, LOG_MESSAGE_CONTENT_MAX)}```",
            color=discord.Color.red(),
        )
        embed.add_field(
            name="Author",
            value=f"{message.author.mention} (`{message.author.id}`)",
            inline=True,
        )
        embed.add_field(
            name="Channel",
            value=message.channel.mention,
            inline=True,
        )
        embed.add_field(
            name="Message ID",
            value=f"`{message.id}`",
            inline=True,
        )

        if attach_info:
            embed.add_field(
                name="Attachments",
                value=_shorten(attach_info, 1024),
                inline=False,
            )

        embed.add_field(
            name="Deleted by",
            value=deleter_str,
            inline=True,
        )
        embed.add_field(
            name="Created at",
            value=discord.utils.format_dt(message.created_at, style="F"),
            inline=True,
        )

        embed.timestamp = discord.utils.utcnow()

        await modlog(message.guild, embed)

    @commands.Cog.listener()
    async def on_message_edit(self, before: discord.Message, after: discord.Message):
        """
        Log meaningful edits (content changed) in a Mittens-style embed.
        """
        if config.MODLOG_CHANNEL_ID is None:
            return
        if not after.guild:
            return
        if after.guild.id != config.GUILD_ID:
            return
        if before.author.bot:
            return
        if before.content == after.content:
            return
        if not isinstance(after.channel, (discord.TextChannel, discord.Thread)):
            return

        # Ignore edits inside the log channel itself.
        if isinstance(after.channel, discord.TextChannel) and after.channel.id == config.MODLOG_CHANNEL_ID:
            return
        if isinstance(after.channel, discord.Thread) and after.channel.parent_id == config.MODLOG_CHANNEL_ID:
            return

        before_text = before.content or "*no content*"
        after_text = after.content or "*no content*"

        before_snip = _shorten(before_text, 900)
        after_snip = _shorten(after_text, 900)

        desc = (
            f"**Before:**\n```{before_snip}```\n"
            f"**After:**\n```{after_snip}```"
        )

        embed = discord.Embed(
            title="✏️ Message edited",
            description=desc,
            color=discord.Color.orange(),
        )
        embed.add_field(
            name="Author",
            value=f"{before.author.mention} (`{before.author.id}`)",
            inline=True,
        )
        embed.add_field(
            name="Channel",
            value=after.channel.mention,
            inline=True,
        )
        embed.add_field(
            name="Message ID",
            value=f"`{before.id}`",
            inline=True,
        )
        if after.jump_url:
            embed.add_field(
                name="Jump",
                value=f"[Jump to message]({after.jump_url})",
                inline=False,
            )

        embed.timestamp = discord.utils.utcnow()

        await modlog(after.guild, embed)

    # ── WARN SYSTEM ────────────────────────────────────────────────
    async def _add_warn(
        self,
        guild_id: int,
        user_id: int,
        reason: str,
        actor_id: int,
    ) -> int:
        data = _load_warns()
        g_key = str(guild_id)
        u_key = str(user_id)
        guild_data = data.setdefault(g_key, {})
        user_data = guild_data.setdefault(u_key, {"warns": []})
        warns = user_data["warns"]
        warns.append({"reason": reason, "actor": actor_id})
        _save_warns(data)
        return len(warns)

    async def _get_warns(
        self,
        guild_id: int,
        user_id: int,
    ) -> List[Dict[str, Any]]:
        data = _load_warns()
        return data.get(str(guild_id), {}).get(str(user_id), {}).get("warns", [])

    async def _clear_warns(
        self,
        guild_id: int,
        user_id: int,
    ) -> int:
        data = _load_warns()
        g_key = str(guild_id)
        u_key = str(user_id)
        guild_data = data.get(g_key)
        if not isinstance(guild_data, dict):
            return 0
        user_data = guild_data.get(u_key)
        if not isinstance(user_data, dict):
            return 0
        warns = user_data.get("warns")
        count = len(warns) if isinstance(warns, list) else 0
        user_data["warns"] = []
        _save_warns(data)
        return count

    # ── COMMANDS ───────────────────────────────────────────────────
    @app_commands.command(
        name="purge",
        description="Delete recent messages (optionally filter by user or text).",
    )
    @is_mod()
    @app_commands.describe(
        count="How many to scan (max 200).",
        user="Only delete messages from this user.",
        contains="Only delete messages containing this text.",
    )
    async def purge(
        self,
        interaction: discord.Interaction,
        count: app_commands.Range[int, 1, 200],
        user: Optional[discord.User] = None,
        contains: Optional[str] = None,
    ):
        if not isinstance(interaction.channel, discord.TextChannel):
            await interaction.response.send_message(
                "This command must be used in a text channel.",
                ephemeral=True,
            )
            return

        await interaction.response.defer(ephemeral=True, thinking=False)

        def _predicate(msg: discord.Message) -> bool:
            if user and msg.author.id != user.id:
                return False
            if contains and (contains.lower() not in (msg.content or "").lower()):
                return False
            return True

        deleted: List[discord.Message] = []
        try:
            async for msg in interaction.channel.history(limit=count):
                if _predicate(msg):
                    deleted.append(msg)
            await interaction.channel.delete_messages(deleted)  # type: ignore[arg-type]
        except discord.Forbidden:
            await interaction.followup.send(
                "I don’t have permission to delete messages here.",
                ephemeral=True,
            )
            return
        except Exception:
            logging.exception("Error during purge")
            await interaction.followup.send(
                "Something went wrong during purge.",
                ephemeral=True,
            )
            return

        await interaction.followup.send(
            f"Deleted {len(deleted)} messages.",
            ephemeral=True,
        )

    @app_commands.command(
        name="slowmode",
        description="Set channel slowmode (seconds). 0 to clear.",
    )
    @is_mod()
    async def slowmode(
        self,
        interaction: discord.Interaction,
        seconds: app_commands.Range[int, 0, 21600],  # up to 6 hours
    ):
        if not isinstance(interaction.channel, discord.TextChannel):
            await interaction.response.send_message(
                "This command must be used in a text channel.",
                ephemeral=True,
            )
            return

        try:
            await interaction.channel.edit(slowmode_delay=seconds)  # type: ignore[call-arg]
        except discord.Forbidden:
            await interaction.response.send_message(
                "I don’t have permission to change slowmode here.",
                ephemeral=True,
            )
            return
        except Exception:
            logging.exception("Error setting slowmode")
            await interaction.response.send_message(
                "Something went wrong while setting slowmode.",
                ephemeral=True,
            )
            return

        if seconds == 0:
            await interaction.response.send_message(
                "Slowmode cleared.",
                ephemeral=True,
            )
        else:
            await interaction.response.send_message(
                f"Slowmode set to {seconds} seconds.",
                ephemeral=True,
            )

    @app_commands.command(
        name="lock",
        description="Lock this channel for @everyone.",
    )
    @is_mod()
    async def lock(self, interaction: discord.Interaction):
        await self._set_lock(interaction, lock=True)

    @app_commands.command(
        name="unlock",
        description="Unlock this channel for @everyone.",
    )
    @is_mod()
    async def unlock(self, interaction: discord.Interaction):
        await self._set_lock(interaction, lock=False)

    async def _set_lock(self, interaction: discord.Interaction, lock: bool):
        ch: discord.TextChannel = interaction.channel  # type: ignore
        everyone = interaction.guild.default_role  # type: ignore
        perms = ch.overwrites_for(everyone)
        perms.send_messages = not lock
        try:
            await ch.set_permissions(everyone, overwrite=perms)
        except discord.Forbidden:
            await interaction.response.send_message(
                "I don’t have permission to adjust channel permissions.",
                ephemeral=True,
            )
            return
        except Exception:
            logging.exception("Error while toggling channel lock")
            await interaction.response.send_message(
                "Something went wrong while toggling the lock.",
                ephemeral=True,
            )
            return

        if lock:
            await interaction.response.send_message(
                "Channel locked for @everyone.",
                ephemeral=True,
            )
        else:
            await interaction.response.send_message(
                "Channel unlocked for @everyone.",
                ephemeral=True,
            )

    # ── MEMBER DISCIPLINE (quick timeout via slash) ────────────────
    async def _quick_timeout_callback(
        self,
        interaction: discord.Interaction,
        member: discord.Member,
    ):
        try:
            duration = timedelta(minutes=10)
            await member.timeout(
                until=discord.utils.utcnow() + duration,
                reason="Quick 10m timeout",
            )
            await interaction.response.send_message(
                TIMEOUT_PUBLIC_TEMPLATE.format(
                    member=member.mention,
                    duration="10m",
                ),
            )
            await modlog(
                interaction.guild,
                action_embed(member, interaction.user, "Timeout 10m"),  # type: ignore[arg-type]
            )
        except discord.Forbidden:
            await interaction.response.send_message(
                "I lack permission to timeout that member.",
                ephemeral=True,
            )

    @app_commands.command(
        name="quick_timeout",
        description="Timeout a member for 10 minutes.",
    )
    @is_mod()
    @app_commands.describe(
        member="Member to put on a 10 minute timeout.",
    )
    async def quick_timeout_cmd(
        self,
        interaction: discord.Interaction,
        member: discord.Member,
    ):
        await self._quick_timeout_callback(interaction, member)

    # ── WARN SLASH COMMANDS ────────────────────────────────────────
    @app_commands.command(
        name="warn",
        description="Warn a member and store it in the warn database.",
    )
    @is_mod()
    @app_commands.describe(
        member="Member to warn.",
        reason="Reason for the warning.",
    )
    async def warn_cmd(
        self,
        interaction: discord.Interaction,
        member: discord.Member,
        reason: str,
    ):
        if member.bot:
            await interaction.response.send_message(
                "You can’t warn bots.",
                ephemeral=True,
            )
            return

        if _is_immune(member):
            await interaction.response.send_message(
                "That member is considered staff / immune and cannot be warned.",
                ephemeral=True,
            )
            return

        if member == interaction.user:
            await interaction.response.send_message(
                "You can’t warn yourself.",
                ephemeral=True,
            )
            return

        await interaction.response.defer(ephemeral=False, thinking=False)

        reason = reason.strip()
        if not reason:
            reason = "No reason provided."

        count = await self._add_warn(
            guild_id=interaction.guild.id,   # type: ignore[arg-type]
            user_id=member.id,
            reason=reason,
            actor_id=interaction.user.id,
        )

        # Public confirmation in the channel
        msg = WARN_PUBLIC_TEMPLATE.format(
            member=member.mention,
            reason=f"(Warn #{count}: {reason})",
        )
        await interaction.followup.send(msg)

        # DM the user (best effort)
        try:
            await member.send(
                f"You have received a warning in **{interaction.guild.name}**:\n"
                f"Reason: {reason}\n"
                f"Total warns: {count}"
            )
        except Exception:
            # DM failures aren’t fatal
            pass

        # Log to mod log
        await modlog(
            interaction.guild,  # type: ignore[arg-type]
            action_embed(member, interaction.user, f"Warn #{count}", reason),
        )

    @app_commands.command(
        name="warnings",
        description="Show warnings for a member.",
    )
    @is_mod()
    @app_commands.describe(
        member="Member whose warns you want to see.",
    )
    async def warnings_cmd(
        self,
        interaction: discord.Interaction,
        member: discord.Member,
    ):
        warns = await self._get_warns(
            guild_id=interaction.guild.id,   # type: ignore[arg-type]
            user_id=member.id,
        )

        if not warns:
            await interaction.response.send_message(
                f"{member.mention} has no stored warnings.",
                ephemeral=True,
            )
            return

        # Build a compact embed listing
        lines: List[str] = []
        guild = interaction.guild
        for idx, w in enumerate(warns, start=1):
            reason = str(w.get("reason", "No reason"))
            actor_id = w.get("actor")
            actor_text = f"<@{actor_id}>" if isinstance(actor_id, int) else "Unknown"
            if guild is not None and isinstance(actor_id, int):
                actor_member = guild.get_member(actor_id)
                if actor_member is not None:
                    actor_text = f"{actor_member.mention} ({actor_member.id})"
            lines.append(f"**#{idx}** – {reason} *(by {actor_text})*")

        desc = "\n".join(lines)
        embed = discord.Embed(
            title=f"Warnings for {member}",
            description=_shorten(desc, 4000),
            color=discord.Color.orange(),
        )
        embed.set_footer(text=f"Total warns: {len(warns)}")

        await interaction.response.send_message(
            embed=embed,
            ephemeral=True,
        )

    @app_commands.command(
        name="clearwarns",
        description="Clear all warnings for a member.",
    )
    @is_mod()
    @app_commands.describe(
        member="Member whose warnings you want to clear.",
    )
    async def clearwarns_cmd(
        self,
        interaction: discord.Interaction,
        member: discord.Member,
    ):
        count = await self._clear_warns(
            guild_id=interaction.guild.id,   # type: ignore[arg-type]
            user_id=member.id,
        )

        if count == 0:
            await interaction.response.send_message(
                f"{member.mention} has no stored warnings.",
                ephemeral=True,
            )
            return

        await interaction.response.send_message(
            f"Cleared **{count}** warning(s) for {member.mention}.",
            ephemeral=True,
        )

        await modlog(
            interaction.guild,  # type: ignore[arg-type]
            action_embed(member, interaction.user, "Cleared warns", f"{count} warn(s) cleared."),
        )


# ── SETUP ──────────────────────────────────────────────────────────
async def setup(bot: commands.Bot):
    await bot.add_cog(Moderation(bot))
