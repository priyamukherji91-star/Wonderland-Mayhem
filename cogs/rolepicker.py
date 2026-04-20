import logging
from dataclasses import dataclass
from typing import Optional

import discord
from discord.ext import commands
from discord import app_commands

import config
import permissions

LOG = logging.getLogger(__name__)

ROLES_CHANNEL_ID = config.ROLES_CHANNEL_ID
GENDER_ROLES: dict[str, int] = config.GENDER_ROLE_IDS


@dataclass(frozen=True)
class ToggleRole:
    key: str
    label: str
    role_id: Optional[int] = None
    role_name: Optional[str] = None
    emoji: Optional[str] = None
    style: discord.ButtonStyle = discord.ButtonStyle.secondary


PING_BUTTONS: tuple[ToggleRole, ...] = (
    ToggleRole(
        key="mountfarm",
        label="mountfarm",
        role_id=1421818453689630730,
        emoji="<:trials:1255114824518598656>",
        style=discord.ButtonStyle.primary,
    ),
    ToggleRole(
        key="movies",
        label="movies",
        role_id=1421818572849680497,
        emoji="🎥",
        style=discord.ButtonStyle.secondary,
    ),
    ToggleRole(
        key="maps",
        label="maps",
        role_id=1421818529010679938,
        emoji="<:dig:1421926886547787916>",
        style=discord.ButtonStyle.secondary,
    ),
    ToggleRole(
        key="unreal",
        label="unreal",
        role_id=1421818621973499995,
        emoji="🦎",
        style=discord.ButtonStyle.secondary,
    ),
    ToggleRole(
        key="deepdungeon",
        label="Deep Dungeon",
        role_id=1421943829757431939,
        emoji="<:deepdungeon:1255114903082106902>",
        style=discord.ButtonStyle.secondary,
    ),
    ToggleRole(
        key="dailyroulettes",
        label="Daily Roulettes",
        role_id=1452047355552727040,
        emoji="⏲️",
        style=discord.ButtonStyle.success,
    ),
    ToggleRole(
        key="dutyhelper",
        label="Duty Helper",
        role_id=1452048195944448040,
        emoji="⚔️",
        style=discord.ButtonStyle.success,
    ),
    ToggleRole(
        key="craftergatherer",
        label="Crafter/Gatherer",
        role_id=1452048006424559807,
        emoji="🪓",
        style=discord.ButtonStyle.success,
    ),
)

OPTIONAL_BUTTONS: tuple[ToggleRole, ...] = (
    ToggleRole(
        key="sussyhumour",
        label="sussy-humour",
        role_name="sussy-humour",
        emoji="<:kekw:1259303576233054289>",
        style=discord.ButtonStyle.secondary,
    ),
    ToggleRole(
        key="nsfw",
        label="NSFW",
        role_name="NSFW",
        emoji="🔞",
        style=discord.ButtonStyle.danger,
    ),
)

PRONOUN_BUTTONS: tuple[ToggleRole, ...] = (
    ToggleRole(
        key="sheher",
        label="She/Her",
        role_id=GENDER_ROLES["She/Her"],
        style=discord.ButtonStyle.primary,
    ),
    ToggleRole(
        key="theythem",
        label="They/Them",
        role_id=GENDER_ROLES["They/Them"],
        style=discord.ButtonStyle.primary,
    ),
    ToggleRole(
        key="hehim",
        label="He/Him",
        role_id=GENDER_ROLES["He/Him"],
        style=discord.ButtonStyle.primary,
    ),
)

ALL_BUTTONS: tuple[ToggleRole, ...] = PING_BUTTONS + OPTIONAL_BUTTONS + PRONOUN_BUTTONS
BUTTONS_BY_KEY: dict[str, ToggleRole] = {button.key: button for button in ALL_BUTTONS}
PRONOUN_ROLE_IDS: set[int] = {button.role_id for button in PRONOUN_BUTTONS if button.role_id is not None}


class RoleToggleButton(discord.ui.Button):
    def __init__(self, button_cfg: ToggleRole):
        super().__init__(
            label=button_cfg.label,
            emoji=button_cfg.emoji,
            style=button_cfg.style,
            custom_id=f"rolepicker:{button_cfg.key}",
        )
        self.button_cfg = button_cfg

    async def callback(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None:
            await interaction.response.send_message(
                "This can only be used in the server.", ephemeral=True
            )
            return

        if interaction.channel_id != ROLES_CHANNEL_ID:
            await interaction.response.send_message(
                "Use this in the pick-your-roles channel.", ephemeral=True
            )
            return

        member = interaction.user
        if not isinstance(member, discord.Member):
            resolved = interaction.guild.get_member(interaction.user.id)
            if resolved is None:
                await interaction.response.send_message(
                    "Could not resolve your server member record.", ephemeral=True
                )
                return
            member = resolved

        role = resolve_role(interaction.guild, self.button_cfg)
        if role is None:
            await interaction.response.send_message(
                "That role could not be found.", ephemeral=True
            )
            return

        me = interaction.guild.me
        if me is None or not me.guild_permissions.manage_roles:
            await interaction.response.send_message(
                "I don’t have permission to manage roles.", ephemeral=True
            )
            return

        if role >= me.top_role:
            await interaction.response.send_message(
                "I can’t manage that role because it is above my highest role.",
                ephemeral=True,
            )
            return

        try:
            if role.id in PRONOUN_ROLE_IDS:
                response = await toggle_pronoun_role(member, role)
            else:
                response = await toggle_regular_role(member, role)
        except discord.Forbidden:
            LOG.exception(
                "Forbidden while toggling role %s for member %s",
                role.id,
                member.id,
            )
            await interaction.response.send_message(
                "I don’t have permission to change your roles.", ephemeral=True
            )
            return
        except Exception:
            LOG.exception(
                "Unexpected error while toggling role %s for member %s",
                role.id,
                member.id,
            )
            await interaction.response.send_message(
                "Something went wrong while changing your roles.", ephemeral=True
            )
            return

        await interaction.response.send_message(response, ephemeral=True)


class RoleButtonsView(discord.ui.View):
    def __init__(self) -> None:
        super().__init__(timeout=None)

        for button_cfg in PING_BUTTONS[:5]:
            self.add_item(RoleToggleButton(button_cfg))
        for button_cfg in PING_BUTTONS[5:]:
            button = RoleToggleButton(button_cfg)
            button.row = 1
            self.add_item(button)
        for button_cfg in OPTIONAL_BUTTONS:
            button = RoleToggleButton(button_cfg)
            button.row = 2
            self.add_item(button)
        for button_cfg in PRONOUN_BUTTONS:
            button = RoleToggleButton(button_cfg)
            button.row = 3
            self.add_item(button)


def resolve_role(guild: discord.Guild, button_cfg: ToggleRole) -> Optional[discord.Role]:
    if button_cfg.role_id is not None:
        return guild.get_role(button_cfg.role_id)
    if button_cfg.role_name is not None:
        return discord.utils.get(guild.roles, name=button_cfg.role_name)
    return None


async def toggle_regular_role(member: discord.Member, role: discord.Role) -> str:
    if role in member.roles:
        await member.remove_roles(role, reason="Self-role toggle off")
        LOG.info("Removed role %s from member %s", role.id, member.id)
        return f"Removed **{role.name}**."

    await member.add_roles(role, reason="Self-role toggle on")
    LOG.info("Added role %s to member %s", role.id, member.id)
    return f"Added **{role.name}**."


async def toggle_pronoun_role(member: discord.Member, selected_role: discord.Role) -> str:
    existing_pronouns = [role for role in member.roles if role.id in PRONOUN_ROLE_IDS]

    if selected_role in existing_pronouns:
        await member.remove_roles(selected_role, reason="Pronoun role toggle off")
        LOG.info("Removed pronoun role %s from member %s", selected_role.id, member.id)
        return f"Removed **{selected_role.name}**."

    roles_to_remove = [role for role in existing_pronouns if role != selected_role]
    if roles_to_remove:
        await member.remove_roles(*roles_to_remove, reason="Pronoun role swap")

    await member.add_roles(selected_role, reason="Pronoun role toggle on")
    LOG.info("Set pronoun role %s for member %s", selected_role.id, member.id)
    return f"Set pronouns to **{selected_role.name}**."


class RolePicker(commands.Cog):
    """Self-assign role buttons + pronoun buttons."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    async def cog_load(self) -> None:
        self.bot.add_view(RoleButtonsView())

    @app_commands.command(
        name="post_roles",
        description="Post the role buttons and pronoun buttons.",
    )
    @permissions.mod_slash_only()
    async def post_roles(self, interaction: discord.Interaction) -> None:
        if interaction.channel_id != ROLES_CHANNEL_ID:
            await interaction.response.send_message(
                "Run this in the pick-your-roles channel.", ephemeral=True
            )
            return

        channel = interaction.channel
        if not isinstance(channel, discord.TextChannel):
            await interaction.response.send_message(
                "This command must be used in a text channel.", ephemeral=True
            )
            return

        embed = discord.Embed(
            title="Choose your chaos",
            description=(
                "Click the buttons below to toggle your pings / channels.\n\n"
                "**Ping roles**\n"
                "<:trials:1255114824518598656> → mountfarm\n"
                "🎥 → movies\n"
                "<:dig:1421926886547787916> → maps\n"
                "🦎 → unreal\n"
                "<:deepdungeon:1255114903082106902> → Deep Dungeon\n"
                "⏲️ → Daily Roulettes\n"
                "⚔️ → Duty Helper\n"
                "🪓 → Crafter/Gatherer\n\n"
                "**Optional roles / channels**\n"
                "<:kekw:1259303576233054289> → sussy-humour\n"
                "🔞 → NSFW access (forbidden-door)\n\n"
                "**Pronouns**\n"
                "Use the pronoun buttons below to set or remove your pronoun role."
            ),
            color=0x5865F2,
        )

        await interaction.response.defer(ephemeral=True, thinking=False)
        await channel.send(embed=embed, view=RoleButtonsView())
        await interaction.followup.send("Role buttons posted.", ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(RolePicker(bot))
