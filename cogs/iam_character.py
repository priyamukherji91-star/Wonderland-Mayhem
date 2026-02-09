from __future__ import annotations

import logging
from typing import Optional
from urllib.parse import quote

import aiohttp
import discord
from bs4 import BeautifulSoup
from discord.ext import commands
from discord import app_commands

LOG = logging.getLogger(__name__)

TOMESTONE_RESOLVER = "https://tomestone.gg/character-name/{world}/{name}"
LODestone_SEARCH = "https://na.finalfantasyxiv.com/lodestone/character/?q={name}&worldname={world}"


def lodestone_search_url(name: str, world: str) -> str:
    return LODestone_SEARCH.format(name=quote(name, safe=""), world=quote(world, safe=""))


def _meta(soup: BeautifulSoup, *, prop: str) -> Optional[str]:
    tag = soup.find("meta", attrs={"property": prop})
    if tag and tag.get("content"):
        return str(tag["content"]).strip()
    return None


def _meta_name(soup: BeautifulSoup, *, name: str) -> Optional[str]:
    tag = soup.find("meta", attrs={"name": name})
    if tag and tag.get("content"):
        return str(tag["content"]).strip()
    return None


class IAmCharacter(commands.Cog):
    """
    !iam <Character Name> <World>
    /iam name:<Character Name> world:<World>

    Tomestone-first:
      - Resolve via /character-name/<world>/<name> (redirects to canonical profile)
      - Parse OpenGraph meta tags for title/description/image
      - Fallback to Lodestone search URL if anything fails
    """

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.session = aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=12),
            headers={
                "User-Agent": "Mozilla/5.0 (compatible; CheshireBot/1.0; +https://tomestone.gg/)"
            },
        )

    async def cog_unload(self) -> None:
        try:
            await self.session.close()
        except Exception:
            pass

    async def _fetch_tomestone_profile(self, name: str, world: str) -> tuple[Optional[discord.Embed], Optional[str]]:
        """
        Returns (embed, final_url). If it fails, returns (None, None).
        """
        # Tomestone expects world in URL; lowercase works (site redirects properly).
        world_slug = (world or "").strip().lower()
        char_name = (name or "").strip()

        if not world_slug or not char_name:
            return None, None

        resolver_url = TOMESTONE_RESOLVER.format(
            world=quote(world_slug, safe=""),
            name=quote(char_name, safe=""),
        )

        try:
            async with self.session.get(resolver_url, allow_redirects=True) as resp:
                if resp.status != 200:
                    return None, None

                final_url = str(resp.url)
                html = await resp.text()

            soup = BeautifulSoup(html, "html.parser")

            og_title = _meta(soup, prop="og:title") or f"{char_name} — {world}"
            og_desc = _meta(soup, prop="og:description") or _meta_name(soup, name="description")
            og_image = _meta(soup, prop="og:image")

            embed = discord.Embed(
                title=og_title,
                url=final_url,
                description=og_desc or "",
                color=discord.Color.blurple(),
            )

            # If og:image exists, show it as the card visual
            if og_image:
                embed.set_image(url=og_image)

            embed.set_footer(text="Source: tomestone.gg")
            return embed, final_url

        except asyncio.TimeoutError:
            return None, None
        except Exception:
            LOG.exception("Tomestone lookup failed for %s (%s)", char_name, world)
            return None, None

    async def _send_result(self, where, *, name: str, world: str) -> None:
        """
        where: Context (prefix) OR Interaction (slash)
        """
        name_clean = " ".join((name or "").strip().split())
        world_clean = (world or "").strip()

        # Tomestone first
        embed, _url = await self._fetch_tomestone_profile(name_clean, world_clean)
        if embed:
            # Send embed
            if isinstance(where, commands.Context):
                await where.reply(embed=embed, mention_author=False)
            else:
                # interaction
                if where.response.is_done():
                    await where.followup.send(embed=embed)
                else:
                    await where.response.send_message(embed=embed)
            return

        # Fallback: Lodestone search link
        lodestone = lodestone_search_url(name_clean, world_clean)
        msg = (
            f"**{name_clean} — {world_clean}**\n"
            "I couldn’t pull a Tomestone profile right now. Here’s a Lodestone search link instead.\n"
            f"{lodestone}"
        )

        if isinstance(where, commands.Context):
            await where.reply(msg, mention_author=False)
        else:
            if where.response.is_done():
                await where.followup.send(msg)
            else:
                await where.response.send_message(msg)

    # ─────────────────────────────────────────────
    # Prefix command: !iam
    # Usage: !iam Cookie Chan ragnarok
    # (Character names can include spaces; we’ll treat last token as world)
    # ─────────────────────────────────────────────
    @commands.command(name="iam")
    async def iam_prefix(self, ctx: commands.Context, *, query: str) -> None:
        query = (query or "").strip()
        if not query:
            return await ctx.reply("Usage: `!iam <Character Name> <World>`", mention_author=False)

        parts = query.split()
        if len(parts) < 2:
            return await ctx.reply("Usage: `!iam <Character Name> <World>`", mention_author=False)

        world = parts[-1]
        name = " ".join(parts[:-1])
        await self._send_result(ctx, name=name, world=world)

    # ─────────────────────────────────────────────
    # Slash command: /iam
    # ─────────────────────────────────────────────
    @app_commands.command(name="iam", description="Show an FFXIV character profile (Tomestone-first).")
    @app_commands.describe(name="Character name (e.g. Cookie Chan)", world="World/server (e.g. Ragnarok)")
    async def iam_slash(self, interaction: discord.Interaction, name: str, world: str) -> None:
        await interaction.response.defer(thinking=False)
        await self._send_result(interaction, name=name, world=world)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(IAmCharacter(bot))
