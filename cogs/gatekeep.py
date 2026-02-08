# cogs/gatekeep.py

from __future__ import annotations

import logging
import random
from typing import Optional

import discord
from discord.ext import commands
from discord import app_commands

import config
import permissions

LOG = logging.getLogger(__name__)


# ── HELPERS ──────────────────────────────────────────────────────

def _human_member_index(guild: discord.Guild) -> int:
    """Approximate 'you are member #X' – counts non-bot members."""
    return sum(1 for m in guild.members if not m.bot)


def _here_then_gone_channel(guild: discord.Guild) -> Optional[discord.TextChannel]:
    ch = guild.get_channel(config.HERE_THEN_GONE_CHANNEL_ID)
    return ch if isinstance(ch, discord.TextChannel) else None


def _gate_role(guild: discord.Guild) -> Optional[discord.Role]:
    return guild.get_role(config.GATE_ROLE_ID)


def _fc_role(guild: discord.Guild) -> Optional[discord.Role]:
    return discord.utils.get(guild.roles, name=config.FC_ROLE_NAME)


def _friend_role(guild: discord.Guild) -> Optional[discord.Role]:
    return discord.utils.get(guild.roles, name=config.FRIEND_ROLE_NAME)


# ── GATE VIEW (FC / FRIEND BUTTONS) ───────────────────────────────

class GateView(discord.ui.View):
    """Buttons in #choose-your-door to pick FC vs Friend.

    timeout=None + static custom_id → persistent view that survives restarts.
    """

    def __init__(self) -> None:
        super().__init__(timeout=None)

    async def _apply_choice(self, interaction: discord.Interaction, make_fc: bool) -> None:
        if interaction.guild is None:
            await interaction.response.send_message(
            "This can only be used in a server.", ephemeral=True
            )
            return

        guild = interaction.guild
        user = interaction.user
        if not isinstance(user, discord.Member):
            member = guild.get_member(user.id)
            if member is None:
                await interaction.response.send_message(
                    "Could not resolve your member record.", ephemeral=True
                )
                return
        else:
            member = user

        gate_role = _gate_role(guild)
        fc_role = _fc_role(guild)
        friend_role = _friend_role(guild)

        to_add: list[discord.Role] = []
        to_remove: list[discord.Role] = []

        # Always try to remove the gate role once they choose
        if gate_role and gate_role in member.roles:
            to_remove.append(gate_role)

        if make_fc:
            # FC door picked
            if friend_role and friend_role in member.roles:
                to_remove.append(friend_role)
            if fc_role and fc_role not in member.roles:
                to_add.append(fc_role)
        else:
            # Friend door picked
            if fc_role and fc_role in member.roles:
                to_remove.append(fc_role)
            if friend_role and friend_role not in member.roles:
                to_add.append(friend_role)

        try:
            if to_remove:
                await member.remove_roles(*to_remove, reason="Gate choice swap")
            if to_add:
                await member.add_roles(*to_add, reason="Gate choice")
        except Exception:
            LOG.exception("Failed adjusting roles for member %s", member)
            if interaction.response.is_done():
                await interaction.followup.send(
                    "Something went wrong changing your roles.", ephemeral=True
                )
            else:
                await interaction.response.send_message(
                    "Something went wrong changing your roles.", ephemeral=True
                )
            return

        if make_fc:
            msg = "You’ve been marked as **FC Member**. The Tea Party is now unlocked."
        else:
            msg = "You’ve been marked as **Friend of the FC**. The Tea Party is now unlocked."

        if interaction.response.is_done():
            await interaction.followup.send(msg, ephemeral=True)
        else:
            await interaction.response.send_message(msg, ephemeral=True)

    @discord.ui.button(
        label="Hatter’s Strays (FC)",
        style=discord.ButtonStyle.primary,
        custom_id="gate_fc_button",
    )
    async def fc_button(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        await self._apply_choice(interaction, make_fc=True)

    @discord.ui.button(
        label="Tea Party VIP (Friend)",
        style=discord.ButtonStyle.secondary,
        custom_id="gate_friend_button",
    )
    async def friend_button(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        await self._apply_choice(interaction, make_fc=False)


# ── COG ────────────────────────────────────────────────────────────

class Gatekeep(commands.Cog):
    """Gate role assignment + join/leave logs."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    # ── EVENTS ────────────────────────────────────────────────────

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member) -> None:
        if member.guild.id != config.GUILD_ID:
            return

        # Give gate role on join, if present
        gate_role = _gate_role(member.guild)
        if gate_role:
            try:
                await member.add_roles(gate_role, reason="New member gate role")
            except Exception:
                LOG.exception("Failed to assign gate role to %s", member)

        ch = _here_then_gone_channel(member.guild)
        if not ch:
            return

        idx = _human_member_index(member.guild)

        # Text style: "A New Shade Appears" + rabbit-hole copy like your screenshot
        join_lines = [
            f"{member.mention} materialised with a nervous little grin.",
            f"{member.mention} stepped through an unseen door.",
            f"{member.mention} drifted in from some forgotten corner of Wonderland.",
        ]
        line = random.choice(join_lines)

        embed = discord.Embed(
            title="A New Shade Appears",
            description=(
                f"{line}\n"
                f"They are member **#{idx}** to tumble down the rabbit hole.\n\n"
                "The cat is watching."
            ),
            colour=discord.Colour.blurple(),
        )

        try:
            embed.set_thumbnail(url=member.display_avatar.url)
        except Exception:
            pass

        try:
            await ch.send(embed=embed)
        except Exception:
            LOG.exception("Failed sending join log for %s", member)

    @commands.Cog.listener()
    async def on_member_remove(self, member: discord.Member) -> None:
        if member.guild.id != config.GUILD_ID:
            return

        ch = _here_then_gone_channel(member.guild)
        if not ch:
            return

        leave_lines = [
            f"A chair sits empty where {member.mention} once was.",
            f"{member.mention} slipped back through the door.",
            f"Another page closes: {member.mention} has left the Tea Party.",
        ]
        line = random.choice(leave_lines)

        embed = discord.Embed(
            title="A door closes",
            description=line,
            colour=discord.Colour.red(),
        )

        try:
            embed.set_thumbnail(url=member.display_avatar.url)
        except Exception:
            pass

        try:
            await ch.send(embed=embed)
        except Exception:
            LOG.exception("Failed sending leave log for %s", member)

    # ── SLASH COMMANDS ────────────────────────────────────────────

    @app_commands.command(name="post_gate", description="Post the FC/Friend gate embed.")
    @permissions.mod_slash_only()
    async def post_gate(self, interaction: discord.Interaction) -> None:
        """Post the gate embed with FC/Friend buttons in the configured channel."""

        if interaction.guild is None:
            await interaction.response.send_message(
                "This command can only be used in a server.", ephemeral=True
            )
            return

        guild = interaction.guild

        gate_channel = guild.get_channel(config.GATE_CHANNEL_ID)
        if not isinstance(gate_channel, discord.TextChannel):
            gate_channel = interaction.channel

        if gate_channel is None or not isinstance(gate_channel, discord.TextChannel):
            await interaction.response.send_message(
                "Could not find a suitable channel to post the gate.",
                ephemeral=True,
            )
            return

        embed = discord.Embed(
            title="Choose your door",
            description=(
                "Welcome to the Tea Party.\n\n"
                "Use the buttons below to choose whether you are joining as "
                "a member of the Free Company or as a Friend of the FC."
            ),
            colour=discord.Colour.blurple(),
        )
        embed.add_field(
            name="Hatter’s Strays (FC)",
            value="For members inside the Free Company.",
            inline=False,
        )
        embed.add_field(
            name="Tea Party VIP (Friend)",
            value="For friends, alts, and visitors of the FC.",
            inline=False,
        )

        view = GateView()

        try:
            await gate_channel.send(embed=embed, view=view)
        except Exception:
            LOG.exception("Failed to post gate embed in %s", gate_channel)
            if interaction.response.is_done():
                await interaction.followup.send(
                    "Failed to post the gate embed.", ephemeral=True
                )
            else:
                await interaction.response.send_message(
                    "Failed to post the gate embed.", ephemeral=True
                )
            return

        if interaction.response.is_done():
            await interaction.followup.send(
                f"Gate posted in {gate_channel.mention}.", ephemeral=True
            )
        else:
            await interaction.response.send_message(
                f"Gate posted in {gate_channel.mention}.", ephemeral=True
            )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Gatekeep(bot))
    bot.add_view(GateView())
