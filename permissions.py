# permissions.py
from __future__ import annotations

from typing import Iterable, Callable, TypeVar, Awaitable

import discord
from discord import app_commands
from discord.ext import commands

import config

T = TypeVar("T")


def has_any_role(member: discord.Member, role_names: Iterable[str]) -> bool:
    """Return True if the member has at least one of the given role names."""
    member_role_names = {r.name for r in member.roles}
    return any(name in member_role_names for name in role_names)


def is_mod_member(member: discord.Member) -> bool:
    """
    Shared 'mod' check for the whole bot.

    A member counts as mod if:
    - They have key moderation permissions, OR
    - They have one of the ADMIN_ROLE_NAMES from config.
    """
    perms = member.guild_permissions
    if (
        perms.administrator
        or perms.manage_guild
        or perms.manage_messages
        or perms.kick_members
        or perms.ban_members
    ):
        return True

    if has_any_role(member, config.ADMIN_ROLE_NAMES):
        return True

    return False


# ─────────────────────────────────────────────
# Decorators for slash commands
# ─────────────────────────────────────────────

def mod_slash_only() -> Callable[[T], T]:
    """
    Use on slash commands that should be mod-only:

        @app_commands.command(...)
        @mod_slash_only()
        async def mycmd(...):
            ...
    """
    def check(interaction: discord.Interaction) -> bool:
        user = interaction.user
        if not isinstance(user, discord.Member):
            raise app_commands.CheckFailure("This command can only be used in a server.")

        if is_mod_member(user):
            return True

        raise app_commands.CheckFailure("You do not have permission to use this command.")

    return app_commands.check(check)


# ─────────────────────────────────────────────
# Decorators for prefix commands (if needed)
# ─────────────────────────────────────────────

def mod_command_only() -> Callable[[Callable[..., Awaitable[bool]]], Callable[..., Awaitable[bool]]]:
    """
    Use on prefix commands that should be mod-only:

        @commands.command(...)
        @mod_command_only()
        async def mycmd(ctx, ...):
            ...
    """
    def predicate(ctx: commands.Context) -> bool:
        if not isinstance(ctx.author, discord.Member):
            return False
        return is_mod_member(ctx.author)

    return commands.check(predicate)
