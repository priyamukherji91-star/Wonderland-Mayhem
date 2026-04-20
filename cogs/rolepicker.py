import logging
from typing import Optional

import discord
from discord.ext import commands
from discord import app_commands

import config
import permissions

LOG = logging.getLogger(__name__)

# Channel where the role menus live
ROLES_CHANNEL_ID = config.ROLES_CHANNEL_ID

# ── Reaction → Role IDs ────────────────────────────────────────────
# Ping roles (reaction based) – these use ROLE IDS
PING_REACTIONS: dict[str, int] = {
    "<:trials:1255114824518598656>": 1421818453689630730,      # mountfarm
    "🎥": 1421818572849680497,                                  # movies
    "<:dig:1421926886547787916>": 1421818529010679938,          # maps
    "🦎": 1421818621973499995,                                  # unreal
    "<:deepdungeon:1255114903082106902>": 1421943829757431939,  # Deep Dungeon
    "⏲️": 1452047355552727040,                                  # Daily Roulettes
    "⚔️": 1452048195944448040,                                  # Duty Helper
    "🪓": 1452048006424559807,                                  # Crafter/Gatherer
}

# Optional roles (reaction based) – these use ROLE NAMES (resolved at runtime)
OPTIONAL_REACTIONS: dict[str, str] = {
    "<:kekw:1259303576233054289>": "sussy-humour",
    "🔞": "NSFW",
}

# Gender dropdown roles
GENDER_ROLES: dict[str, int] = config.GENDER_ROLE_IDS

# Roles allowed to run /post_roles
ADMIN_ROLE_NAMES = list(config.ADMIN_ROLE_NAMES)


# ── Gender dropdown components ─────────────────────────────────────
class GenderSelect(discord.ui.Select):
    def __init__(self):
        options = [
            discord.SelectOption(label=label, value=str(role_id))
            for label, role_id in GENDER_ROLES.items()
        ]
        super().__init__(
            placeholder="Select your pronouns…",
            min_values=1,
            max_values=1,
            options=options,
            custom_id="gender_select_v1",
        )

    async def callback(self, interaction: discord.Interaction):
        member = interaction.user
        if not isinstance(member, discord.Member):
            await interaction.response.send_message(
                "This can only be used inside the server.", ephemeral=True
            )
            return

        try:
            role_id = int(self.values[0])
        except (ValueError, TypeError):
            await interaction.response.send_message(
                "Something went wrong with the selected role.", ephemeral=True
            )
            return

        role = member.guild.get_role(role_id)
        if role is None:
            await interaction.response.send_message(
                "That role doesn’t exist anymore.", ephemeral=True
            )
            return

        me = member.guild.me
        if me is None or not me.guild_permissions.manage_roles:
            await interaction.response.send_message(
                "I don’t have permission to manage roles.", ephemeral=True
            )
            return

        if role >= me.top_role:
            await interaction.response.send_message(
                "I can’t assign that role because it is above my highest role.",
                ephemeral=True,
            )
            return

        gender_role_ids = set(GENDER_ROLES.values())
        roles_to_remove = [
            r for r in member.roles if r.id in gender_role_ids and r != role
        ]

        try:
            if roles_to_remove:
                await member.remove_roles(*roles_to_remove, reason="Updating pronoun role")
            if role not in member.roles:
                await member.add_roles(role, reason="Selected pronoun role")
        except discord.Forbidden:
            LOG.exception("Forbidden while updating pronoun role for %s", member.id)
            await interaction.response.send_message(
                "I don’t have permission to change your roles.", ephemeral=True
            )
            return
        except Exception:
            LOG.exception("Unexpected error while updating pronoun role for %s", member.id)
            await interaction.response.send_message(
                "Something went wrong while updating your roles.", ephemeral=True
            )
            return

        await interaction.response.send_message("Pronouns updated.", ephemeral=True)


class GenderView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)
        self.add_item(GenderSelect())


# ── Cog ────────────────────────────────────────────────────────────
class RolePicker(commands.Cog):
    """Ping roles, optional roles, and pronoun dropdown."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    async def cog_load(self):
        self.bot.add_view(GenderView())

    @app_commands.command(
        name="post_roles",
        description="Post the ping roles, optional roles, and pronoun selector.",
    )
    @permissions.mod_slash_only()
    async def post_roles(self, interaction: discord.Interaction):
        if interaction.channel_id != ROLES_CHANNEL_ID:
            return await interaction.response.send_message(
                "Run this in the pick-your-roles channel.", ephemeral=True
            )

        await interaction.response.defer(ephemeral=True, thinking=False)

        channel = interaction.channel
        if not isinstance(channel, discord.TextChannel):
            return await interaction.followup.send(
                "This command must be used in a text channel.", ephemeral=True
            )

        embed = discord.Embed(
            title="Choose your chaos",
            description=(
                "React below to opt into various pings / channels.\n\n"
                "**Ping roles** (get notified when people run stuff)\n"
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
                "Use the dropdown below to set pronouns."
            ),
            color=0x5865F2,
        )

        msg = await channel.send(embed=embed)

        for emoji in list(PING_REACTIONS.keys()) + list(OPTIONAL_REACTIONS.keys()):
            try:
                await msg.add_reaction(emoji)
            except Exception:
                LOG.exception("Failed to add reaction %s to rolepicker message %s", emoji, msg.id)

        await channel.send(view=GenderView())
        await interaction.followup.send("Role menus posted.", ephemeral=True)

    async def _resolve_member(
        self, guild: discord.Guild, payload: discord.RawReactionActionEvent
    ) -> Optional[discord.Member]:
        member = guild.get_member(payload.user_id)
        if member is not None:
            return member

        payload_member = getattr(payload, "member", None)
        if isinstance(payload_member, discord.Member):
            return payload_member

        try:
            return await guild.fetch_member(payload.user_id)
        except discord.NotFound:
            LOG.warning("Could not find member %s in guild %s", payload.user_id, guild.id)
            return None
        except discord.Forbidden:
            LOG.exception("Missing permission to fetch member %s in guild %s", payload.user_id, guild.id)
            return None
        except Exception:
            LOG.exception("Unexpected error fetching member %s in guild %s", payload.user_id, guild.id)
            return None

    async def _apply_role_change(
        self,
        member: discord.Member,
        role: discord.Role,
        add: bool,
        reason: str,
    ) -> None:
        me = member.guild.me
        if me is None:
            LOG.warning("guild.me is None in guild %s", member.guild.id)
            return

        if not me.guild_permissions.manage_roles:
            LOG.warning("Bot lacks Manage Roles in guild %s", member.guild.id)
            return

        if role >= me.top_role:
            LOG.warning(
                "Cannot manage role '%s' (%s); it is >= bot top role in guild %s",
                role.name,
                role.id,
                member.guild.id,
            )
            return

        try:
            if add:
                await member.add_roles(role, reason=reason)
                LOG.info("Added role %s to member %s", role.id, member.id)
            else:
                await member.remove_roles(role, reason=reason)
                LOG.info("Removed role %s from member %s", role.id, member.id)
        except discord.Forbidden:
            LOG.exception(
                "Forbidden while %s role %s for member %s",
                "adding" if add else "removing",
                role.id,
                member.id,
            )
        except Exception:
            LOG.exception(
                "Unexpected error while %s role %s for member %s",
                "adding" if add else "removing",
                role.id,
                member.id,
            )

    async def _handle_react(self, payload: discord.RawReactionActionEvent, add: bool):
        if payload.guild_id is None or payload.channel_id != ROLES_CHANNEL_ID:
            return

        if self.bot.user is not None and payload.user_id == self.bot.user.id:
            return

        guild = self.bot.get_guild(payload.guild_id)
        if guild is None:
            LOG.warning("Guild %s not found in cache for reaction event", payload.guild_id)
            return

        member = await self._resolve_member(guild, payload)
        if member is None:
            return

        emoji_str = str(payload.emoji)
        LOG.info(
            "Rolepicker reaction: guild=%s channel=%s user=%s emoji=%s add=%s",
            payload.guild_id,
            payload.channel_id,
            payload.user_id,
            emoji_str,
            add,
        )

        if emoji_str in PING_REACTIONS:
            role_id = PING_REACTIONS[emoji_str]
            role = guild.get_role(role_id)
            if role is None:
                LOG.warning("Ping role %s not found in guild %s", role_id, guild.id)
                return

            await self._apply_role_change(
                member,
                role,
                add,
                reason="Ping role opt-in" if add else "Ping role opt-out",
            )
            return

        if emoji_str in OPTIONAL_REACTIONS:
            role_name = OPTIONAL_REACTIONS[emoji_str]
            role = discord.utils.get(guild.roles, name=role_name)
            if role is None:
                LOG.warning("Optional role '%s' not found in guild %s", role_name, guild.id)
                return

            await self._apply_role_change(
                member,
                role,
                add,
                reason="Optional role opt-in" if add else "Optional role opt-out",
            )
            return

        LOG.info("Reaction emoji %s is not mapped to a role", emoji_str)

    @commands.Cog.listener()
    async def on_raw_reaction_add(self, payload: discord.RawReactionActionEvent):
        await self._handle_react(payload, add=True)

    @commands.Cog.listener()
    async def on_raw_reaction_remove(self, payload: discord.RawReactionActionEvent):
        await self._handle_react(payload, add=False)


async def setup(bot: commands.Bot):
    await bot.add_cog(RolePicker(bot))