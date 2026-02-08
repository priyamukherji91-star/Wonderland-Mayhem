# cogs/autosync.py
import pkgutil
import logging
from typing import List

import discord
from discord import app_commands
from discord.ext import commands

LOG = logging.getLogger(__name__)

COGS_PACKAGE = "cogs"
SELF_MODULE = f"{COGS_PACKAGE}.autosync"


def discover_cog_modules() -> List[str]:
    pkg = __import__(COGS_PACKAGE, fromlist=["*"])
    modules: List[str] = []
    for mod in pkgutil.iter_modules(pkg.__path__):  # type: ignore[attr-defined]
        name = f"{COGS_PACKAGE}.{mod.name}"
        if name == SELF_MODULE or mod.name.startswith("_"):
            continue
        modules.append(name)
    return modules


def to_module_path(name_or_module: str) -> str:
    name_or_module = name_or_module.strip()
    return name_or_module if name_or_module.startswith(f"{COGS_PACKAGE}.") else f"{COGS_PACKAGE}.{name_or_module}"


class AutoSync(commands.Cog):
    """Auto-load all cogs on startup + provide /reload and /sync."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._discovered = discover_cog_modules()
        LOG.info("Discovered cogs: %s", self._discovered)

    async def cog_load(self):
        # Load every cog
        for module in self._discovered:
            if module in self.bot.extensions:
                continue
            try:
                await self.bot.load_extension(module)
                LOG.info("Loaded extension: %s", module)
            except Exception:
                LOG.exception("Failed to load extension: %s", module)

        # ---- Guild-only sync (no duplicates) ----
        try:
            guild_id = getattr(self.bot, "GUILD_ID", None)
            if guild_id:
                guild_obj = discord.Object(id=int(guild_id))

                # 1) Copy current GLOBAL commands into the guild
                self.bot.tree.copy_global_to(guild=guild_obj)

                # 2) PURGE global commands so they don't exist twice
                self.bot.tree.clear_commands(guild=None)
                try:
                    # Push the empty global set to Discord (deletes any existing globals)
                    await self.bot.tree.sync()
                    LOG.info("Purged global commands (now 0).")
                except Exception:
                    # If we can't sync global (rate-limit or lack of scope), continue — guild will still be clean
                    LOG.exception("Failed to purge global commands")

                # 3) Sync guild (only guild commands remain)
                cmds = await self.bot.tree.sync(guild=guild_obj)
                LOG.info("Auto-synced %d commands to guild %s (guild-only, no dupes).", len(cmds), guild_id)
            else:
                # No guild specified: keep classic GLOBAL mode
                cmds = await self.bot.tree.sync()
                LOG.info("Auto-synced %d commands globally.", len(cmds))
        except Exception:
            LOG.exception("Initial auto-sync failed")

    # /reload
    @app_commands.command(name="reload", description="Reload a cog by name (e.g. duty_cog).")
    @app_commands.describe(cog="Cog module name (e.g. duty_cog)")
    async def reload(self, interaction: discord.Interaction, cog: str):
        await interaction.response.defer(ephemeral=True)
        module = to_module_path(cog)
        try:
            if module in self.bot.extensions:
                await self.bot.unload_extension(module)
            await self.bot.load_extension(module)
            await interaction.followup.send(f"Reloaded: `{module}` ✅", ephemeral=True)
            LOG.info("Reloaded extension: %s", module)
        except Exception as e:
            LOG.exception("Reload failed for %s", module)
            await interaction.followup.send(f"Reload failed for `{module}`: `{e}`", ephemeral=True)

    @reload.autocomplete("cog")
    async def reload_autocomplete(self, interaction: discord.Interaction, current: str):
        cur = (current or "").lower()
        choices = []
        for module in sorted(self._discovered):
            bare = module.split(".", 1)[1]
            if not cur or cur in bare.lower() or cur in module.lower():
                choices.append(app_commands.Choice(name=bare, value=bare))
            if len(choices) >= 20:
                break
        return choices

    # /sync
    @app_commands.command(name="sync", description="Sync slash commands (guild or global).")
    @app_commands.choices(
        scope=[
            app_commands.Choice(name="guild (no dupes)", value="guild"),
            app_commands.Choice(name="global (slow)", value="global"),
        ]
    )
    async def sync(self, interaction: discord.Interaction, scope: app_commands.Choice[str]):
        await interaction.response.defer(ephemeral=True)
        try:
            if scope.value == "guild":
                guild_id = getattr(self.bot, "GUILD_ID", None)
                if not guild_id:
                    await interaction.followup.send(
                        "GUILD_ID is not set on the bot. Set `bot.GUILD_ID` in bot.py for guild sync.",
                        ephemeral=True,
                    )
                    return
                guild_obj = discord.Object(id=int(guild_id))

                # Rebuild guild-only set from current globals, then purge globals
                self.bot.tree.copy_global_to(guild=guild_obj)
                self.bot.tree.clear_commands(guild=None)
                try:
                    await self.bot.tree.sync()  # delete globals remotely
                    LOG.info("Purged global commands via /sync.")
                except Exception:
                    LOG.exception("Failed to purge global commands via /sync")

                cmds = await self.bot.tree.sync(guild=guild_obj)
                await interaction.followup.send(
                    f"Synced **{len(cmds)}** commands to guild `{guild_id}` (guild-only, no duplicates) ✅",
                    ephemeral=True,
                )
            else:
                cmds = await self.bot.tree.sync()
                await interaction.followup.send(
                    f"Synced **{len(cmds)}** commands globally ✅\n"
                    f"(Note: global + guild may show duplicates. Prefer guild mode.)",
                    ephemeral=True,
                )
        except Exception as e:
            LOG.exception("Sync failed")
            await interaction.followup.send(f"Sync failed: `{e}`", ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(AutoSync(bot))
