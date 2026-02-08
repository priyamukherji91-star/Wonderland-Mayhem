import discord
from discord.ext import commands
from discord import app_commands
import config
import permissions
from typing import Union

# Channel where the role menus live
ROLES_CHANNEL_ID = config.ROLES_CHANNEL_ID  # from config

# ── Reaction → Role IDs ────────────────────────────────────────────
# Ping roles (reaction based) – these use ROLE IDS
PING_REACTIONS: dict[str, int] = {
    "<:trials:1255114824518598656>": 1421818453689630730,      # mountfarm
    "🎥": 1421818572849680497,                                  # movies
    "<:dig:1421926886547787916>": 1421818529010679938,          # maps
    "🦎": 1421818621973499995,                                  # unreal
    "<:deepdungeon:1255114903082106902>": 1421943829757431939,  # Deep Dungeon

    # ── NEW ─────────────────────────────────────────────
    "⏲️": 1452047355552727040,                                  # Daily Roulettes
    "⚔️": 1452048195944448040,                                  # Duty Helper
    "🪓": 1452048006424559807,                                  # Crafter/Gatherer
}

# Optional roles (reaction based) – these use ROLE NAMES (resolved at runtime)
OPTIONAL_REACTIONS: dict[str, str] = {
    "<:kekw:1259303576233054289>": "sussy-humour",  # sussy-humour role
    "🔞": "NSFW",                                    # NSFW role (unlocks #forbidden-door via perms)
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
            custom_id="gender_select_v1",  # fixed ID for persistent view
        )

    async def callback(self, interaction: discord.Interaction):
        member = interaction.user
        if not isinstance(member, discord.Member):
            await interaction.response.send_message(
                "This can only be used inside the server.", ephemeral=True
            )
            return

        # Resolve role by ID from the selected value
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

        # Remove any other gender roles from the member, then add the selected one
        gender_role_ids = set(GENDER_ROLES.values())
        roles_to_remove = [r for r in member.roles if r.id in gender_role_ids]

        try:
            if roles_to_remove:
                await member.remove_roles(*roles_to_remove, reason="Updating pronoun role")
            if role not in member.roles:
                await member.add_roles(role, reason="Selected pronoun role")
        except discord.Forbidden:
            await interaction.response.send_message(
                "I don’t have permission to change your roles.", ephemeral=True
            )
            return
        except Exception:
            await interaction.response.send_message(
                "Something went wrong while updating your roles.", ephemeral=True
            )
            return

        await interaction.response.send_message("Pronouns updated.", ephemeral=True)


class GenderView(discord.ui.View):
    def __init__(self):
        # timeout=None + fixed custom_id on the Select → persistent view
        super().__init__(timeout=None)
        self.add_item(GenderSelect())


# ── Cog ────────────────────────────────────────────────────────────
class RolePicker(commands.Cog):
    """Ping roles, optional roles, and pronoun dropdown."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    async def cog_load(self):
        # Register the persistent gender view so interactions keep working
        # after reloads/restarts.
        self.bot.add_view(GenderView())

    # ── Slash: post role menus ─────────────────────────────────────
    @app_commands.command(
        name="post_roles",
        description="Post the ping roles, optional roles, and pronoun selector."
    )
    @permissions.mod_slash_only()
    async def post_roles(self, interaction: discord.Interaction):
        # Must be in the configured roles channel
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

        # Build the embed
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

        # Send embed + add reactions
        msg = await channel.send(embed=embed)

        for emoji in list(PING_REACTIONS.keys()) + list(OPTIONAL_REACTIONS.keys()):
            try:
                await msg.add_reaction(emoji)
            except Exception:
                pass

        # Add the pronoun dropdown view beneath the embed
        await channel.send(view=GenderView())

        await interaction.followup.send("Role menus posted.", ephemeral=True)

    # ── Internal reaction handling ─────────────────────────────────
    async def _handle_react(self, payload: discord.RawReactionActionEvent, add: bool):
        if payload.guild_id is None or payload.channel_id != ROLES_CHANNEL_ID:
            return

        if payload.user_id == self.bot.user.id:
            return

        guild = self.bot.get_guild(payload.guild_id)
        if guild is None:
            return

        member = guild.get_member(payload.user_id)
        if member is None:
            return

        emoji_str = str(payload.emoji)

        if emoji_str in PING_REACTIONS:
            role_id = PING_REACTIONS[emoji_str]
            role = guild.get_role(role_id)
            if role is None:
                return

            try:
                if add:
                    await member.add_roles(role, reason="Ping role opt-in")
                else:
                    await member.remove_roles(role, reason="Ping role opt-out")
            except Exception:
                pass
            return

        if emoji_str in OPTIONAL_REACTIONS:
            role_name = OPTIONAL_REACTIONS[emoji_str]
            role = discord.utils.get(guild.roles, name=role_name)
            if role is None:
                return

            try:
                if add:
                    await member.add_roles(role, reason="Optional role opt-in")
                else:
                    await member.remove_roles(role, reason="Optional role opt-out")
            except Exception:
                pass

    @commands.Cog.listener()
    async def on_raw_reaction_add(self, payload: discord.RawReactionActionEvent):
        await self._handle_react(payload, add=True)

    @commands.Cog.listener()
    async def on_raw_reaction_remove(self, payload: discord.RawReactionActionEvent):
        await self._handle_react(payload, add=False)


async def setup(bot: commands.Bot):
    await bot.add_cog(RolePicker(bot))
