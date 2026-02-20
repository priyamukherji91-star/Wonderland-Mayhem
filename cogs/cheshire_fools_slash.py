from __future__ import annotations

import re
import random
from typing import Optional

import discord
from discord.ext import commands
from discord import app_commands

import config
import permissions


# ── FLAVOUR LINES ─────────────────────────────────────────────────

FOOLS_FOOTERS: tuple[str, ...] = (
    "You’ve reached the bottom of the rabbit hole of good judgement.",
    "Recorded for future bullying (with love).",
    "Somewhere, the Cheshire Cat is wheezing.",
    "The tea’s gone cold and so has this take.",
    "Another masterpiece of poor decision-making.",
)

# Hard-pin the destination channel to avoid config drift / wrong env config
FORCED_FOOLS_CHANNEL_ID = 1251693840365125698


# ── HELPERS ────────────────────────────────────────────────────────


def _can_use_fools(member: discord.Member) -> bool:
    """Return True if a member is allowed to file fools entries."""
    # Mods/admins (admins already covered by is_mod_member)
    if permissions.is_mod_member(member):
        return True
    # FC / Friend roles allowed to use the gallery
    return permissions.has_any_role(member, config.FOOLS_ALLOWED_ROLE_NAMES)


def _short(text: str, limit: int = 1024) -> str:
    text = text.strip()
    if not text:
        return "[no text, just vibes]"
    if len(text) <= limit:
        return text
    return text[: limit - 1] + "…"


def _extract_ids_from_link(link: str) -> Optional[tuple[int, int, int]]:
    """
    Parse a Discord message link into (guild_id, channel_id, message_id).

    Accepts:
      - https://discord.com/channels/...
      - https://discordapp.com/channels/...
    """
    m = re.search(
        r"/channels/(?P<guild>\d+)/(?P<channel>\d+)/(?P<message>\d+)",
        link,
    )
    if not m:
        return None
    return (int(m.group("guild")), int(m.group("channel")), int(m.group("message")))


async def _send_fool_embed(
    origin_message: discord.Message,
    invoked_by: discord.abc.User,
    reason: Optional[str],
) -> discord.Message:
    """Build and send the fools embed to the configured gallery channel."""
    guild = origin_message.guild
    if guild is None:
        raise RuntimeError("This can only be used in a server, not in DMs.")

    channel = (
        guild.get_channel(FORCED_FOOLS_CHANNEL_ID)
        or guild.get_thread(FORCED_FOOLS_CHANNEL_ID)
    )
    if channel is None:
        raise RuntimeError(
            "Fool’s Gallery channel not found. Check FOOLS_CHANNEL_ID in config.py."
        )

    suspect = origin_message.author
    reporter = invoked_by

    # --------- message text (grey box) -----------------------------
    raw_content = (origin_message.content or "").strip()
    content_display = _short(raw_content)
    code_block = f"```{content_display}```"

    # --------- embed skeleton --------------------------------------
    embed = discord.Embed(
        title="Rabbit Hole of Regret",
        description=code_block,
        color=discord.Color.pink(),
    )

    # Suspect / Channel / Reporter / Jump
    embed.add_field(
        name="Suspect",
        value=suspect.mention,
        inline=True,
    )
    embed.add_field(
        name="Channel",
        value=origin_message.channel.mention,
        inline=True,
    )
    embed.add_field(
        name="Reporter",
        value=reporter.mention,
        inline=True,
    )

    if origin_message.jump_url:
        embed.add_field(
            name="Jump",
            value=f"[Go to message]({origin_message.jump_url})",
            inline=False,
        )

    # Attachments summary (first one linked) – works for picture-only posts too
    if origin_message.attachments:
        first = origin_message.attachments[0]
        attach_text = f"[{first.filename}]({first.url})"
        if len(origin_message.attachments) > 1:
            attach_text += f"\n(+{len(origin_message.attachments) - 1} more attachment(s))"

        embed.add_field(
            name="Attachment",
            value=attach_text,
            inline=False,
        )

        # Show the image inside the embed if it’s an image
        if first.content_type and first.content_type.startswith("image/"):
            embed.set_image(url=first.url)

    # Avatar + rotating footer lines
    if suspect.display_avatar:
        embed.set_thumbnail(url=suspect.display_avatar.url)

    embed.set_footer(text=random.choice(FOOLS_FOOTERS))

    # ---- force a real ping for the suspect ------------------------
    # Even if the bot's global allowed_mentions blocks pings,
    # this per-message override will ping the user.
    allowed = discord.AllowedMentions(
        users=True,
        roles=False,
        everyone=False,
        replied_user=False,
    )
    content = suspect.mention

    return await channel.send(
        content=content,
        embed=embed,
        allowed_mentions=allowed,
    )  # type: ignore[return-value]


# ── COG ────────────────────────────────────────────────────────────


class CheshireFools(commands.Cog):
    """Send particularly cursed takes to the Fool's Gallery."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    # --------- Slash command: /fool --------------------------------

    @app_commands.command(
        name="fool",
        description="Send a message (by link) to the Rabbit Hole of Regret.",
    )
    @app_commands.describe(
        message_link="Link to the message you want to file",
        reason="Optional note to store along with it",
    )
    async def fool_slash(
        self,
        interaction: discord.Interaction,
        message_link: str,
        reason: Optional[str] = None,
    ) -> None:
        if not isinstance(interaction.user, discord.Member):
            await interaction.response.send_message(
                "This can only be used in a server.",
                ephemeral=True,
            )
            return

        if not _can_use_fools(interaction.user):
            await interaction.response.send_message(
                "You’re not allowed to file things in the Fool’s Gallery.",
                ephemeral=True,
            )
            return

        ids = _extract_ids_from_link(message_link)
        if not ids:
            await interaction.response.send_message(
                "That doesn’t look like a valid message link.",
                ephemeral=True,
            )
            return

        guild_id, channel_id, message_id = ids
        if interaction.guild is None or interaction.guild.id != guild_id:
            await interaction.response.send_message(
                "You can only file messages from this server.",
                ephemeral=True,
            )
            return

        await interaction.response.defer(ephemeral=True)

        channel = interaction.guild.get_channel(channel_id)
        if channel is None or not isinstance(channel, (discord.TextChannel, discord.Thread)):
            await interaction.followup.send(
                "I couldn’t find that channel or it’s not a text channel.",
                ephemeral=True,
            )
            return

        try:
            message = await channel.fetch_message(message_id)
        except discord.NotFound:
            await interaction.followup.send(
                "I couldn’t find that message. It may have been deleted.",
                ephemeral=True,
            )
            return
        except discord.Forbidden:
            await interaction.followup.send(
                "I don’t have permission to view that message.",
                ephemeral=True,
            )
            return
        except Exception:
            await interaction.followup.send(
                "Something went wrong while fetching that message.",
                ephemeral=True,
            )
            return

        try:
            await _send_fool_embed(
                origin_message=message,
                invoked_by=interaction.user,
                reason=reason,
            )
        except RuntimeError as exc:
            await interaction.followup.send(str(exc), ephemeral=True)
            return

        await interaction.followup.send("Noted in the Rabbit Hole of Regret.", ephemeral=True)


# ── CONTEXT MENU (TOP-LEVEL) ──────────────────────────────────────


@app_commands.context_menu(name="Send to Fool’s Gallery")
async def send_to_fools_context(
    interaction: discord.Interaction,
    message: discord.Message,
) -> None:
    """Right-click → Apps → Send to Fool’s Gallery."""
    if not isinstance(interaction.user, discord.Member):
        await interaction.response.send_message(
            "This can only be used in a server.",
            ephemeral=True,
        )
        return

    if not _can_use_fools(interaction.user):
        await interaction.response.send_message(
            "You’re not allowed to file things in the Fool’s Gallery.",
            ephemeral=True,
        )
        return

    await interaction.response.defer(ephemeral=True)

    try:
        await _send_fool_embed(
            origin_message=message,
            invoked_by=interaction.user,
            reason=None,
        )
    except RuntimeError as exc:
            await interaction.followup.send(str(exc), ephemeral=True)
            return

    await interaction.followup.send("Noted in the Rabbit Hole of Regret.", ephemeral=True)


# ── SETUP ──────────────────────────────────────────────────────────


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(CheshireFools(bot))
    # Register guild-scoped context menu
    bot.tree.add_command(
        send_to_fools_context,
        guild=discord.Object(config.GUILD_ID),
    )