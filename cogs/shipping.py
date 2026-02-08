# -*- coding: utf-8 -*-
import random
import datetime
from urllib.parse import quote

import discord
from discord import app_commands
from discord.ext import commands

# ───────────────────────────────────────────────
# CONFIG
# ───────────────────────────────────────────────
SHITPOSTING_CHANNEL_ID = 1251693839962607675  # only allowed here

# Use a webhook to post results (needs "Manage Webhooks")
USE_WEBHOOK = True
WEBHOOK_NAME = "Cheshire Cat Ship"

class ShipChaos(commands.Cog):
    """💘 Ship command — chaotic, daily, and hardened (Cheshire Cat edition)."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    # ───────────────────────────────────────────────
    # /ship
    # ───────────────────────────────────────────────
    @app_commands.command(
        name="ship",
        description="Ship two users and let Cheshire Cat deliver the judgment 💞"
    )
    @app_commands.describe(user1="First user", user2="Second user")
    async def ship(self, interaction: discord.Interaction, user1: discord.User, user2: discord.User):
        await self._run_ship(interaction, user1, user2)

    # ───────────────────────────────────────────────
    # /shiprandom
    # ───────────────────────────────────────────────
    @app_commands.command(
        name="shiprandom",
        description="Randomly ship two random server members 💘"
    )
    async def shiprandom(self, interaction: discord.Interaction):
        if not interaction.channel or interaction.channel.id != SHITPOSTING_CHANNEL_ID:
            return await interaction.response.send_message(
                "🚫 This command only works in <#1251693839962607675> — go cause chaos there.",
                ephemeral=True
            )

        guild = interaction.guild
        if not guild:
            return await interaction.response.send_message(
                "⚠️ This command can only be used in a server.",
                ephemeral=True
            )

        members = [m for m in guild.members if not m.bot]
        if len(members) < 2:
            return await interaction.response.send_message("❌ Not enough members to ship!", ephemeral=True)

        user1, user2 = random.sample(members, 2)
        await self._run_ship(interaction, user1, user2)

    # ───────────────────────────────────────────────
    # Internal shared logic
    # ───────────────────────────────────────────────
    async def _run_ship(self, interaction: discord.Interaction, user1: discord.User, user2: discord.User):
        # Channel gate
        if not interaction.channel or interaction.channel.id != SHITPOSTING_CHANNEL_ID:
            return await interaction.response.send_message(
                "🚫 This command only works in <#1251693839962607675> — go cause chaos there.",
                ephemeral=True
            )

        # Deterministic score (daily)
        today = datetime.date.today().toordinal()
        combo = tuple(sorted([user1.id, user2.id]))
        rng = random.Random(int(f"{combo[0]}{combo[1]}{today}"))
        score = rng.randint(0, 100)

        # Extra stat: Stability (peaks around 50/50 couples)
        stability = max(0, min(100, int(100 - abs(score - 50) * 2)))

        # Couple mash name (self-ship handled)
        n1, n2 = user1.display_name, user2.display_name
        couple_name = (n1[:3] + (n1 if user1.id == user2.id else n2)[-3:]).capitalize()

        # Flavor lines
        pure_love = [
            "💞 Their chemistry could power a star.",
            "💍 Destined to outlive every ship war.",
            "🌹 A love story strong enough to crash servers.",
            "🔥 One look and Cheshire grinned wider.",
            "💘 Canon since day one.",
        ]
        mild_chaos = [
            "🎢 70% flirting, 30% existential dread.",
            "🧩 Puzzle pieces that kinda fit but also fight.",
            "🛠️ Patch notes: communication fixes pending.",
            "🎭 Enemies to lovers speedrun category.",
            "💅 Spicy energy; HR would have questions.",
        ]
        dramatic = [
            "🍿 Drama so juicy it gets its own recap thread.",
            "🎬 One’s a romcom, the other’s a horror — and it works.",
            "💅 Scandalous levels of chemistry.",
            "🧃 Mutual chaos, zero regrets.",
            "🔥 A love story written in caps lock.",
        ]
        doomed = [
            "💀 Relationship.exe has crashed.",
            "🥴 One ghosted mid-typing.",
            "🪦 Compatibility not found (404).",
            "📉 Stock fell faster than Bitcoin 2018.",
            "💣 Love bombed then rage quit.",
        ]
        tragic_comedy = [
            "🤣 Pure comedy gold — the universe’s favorite bit.",
            "🫣 They’d roast each other daily but secretly adore it.",
            "😂 Even Cheshire can’t look away.",
            "🎢 Ride or die — mostly ride.",
            "💫 Their love language is sarcasm.",
        ]
        self_love = [
            "🪞 Self-love speedrun WR holder.",
            "✨ Flirting with destiny (and yourself).",
            "💅 Main character energy: renewable.",
            "🧠 Knows exactly what they want — themselves.",
            "🌟 Solo queue to happily ever after.",
        ]

        if user1.id == user2.id:
            comment = rng.choice(self_love)
        elif score >= 85:
            comment = rng.choice(pure_love)
        elif score >= 65:
            comment = rng.choice(dramatic)
        elif score >= 45:
            comment = rng.choice(mild_chaos)
        elif score >= 25:
            comment = rng.choice(tragic_comedy)
        else:
            comment = rng.choice(doomed)

        adjectives = [
            "chaotic", "forbidden", "galactic", "unholy",
            "divine", "mildly concerning", "dramatic", "feral",
            "cat-approved", "AI-rejected", "server-breaking",
        ]
        comment = f"{comment} ({rng.choice(adjectives)} energy detected.)"

        # ── Visual helpers ──────────────────────────
        def bar(val: int) -> str:
            filled = max(0, min(10, val // 10))
            return "█" * filled + "░" * (10 - filled)

        # score color: red→green
        hue = (score / 100) * (120 / 360)  # 0..1 range for 0°..120°
        color = discord.Color.from_hsv(hue, 0.85, 0.95)

        # Avatar helpers — force static PNG + size, then URL-encode
        def avatar_png(u: discord.User | discord.Member) -> str:
            return u.display_avatar.replace(size=512, static_format="png").url

        u1_png = avatar_png(user1)
        u2_png = avatar_png(user2)
        u1_q = quote(u1_png, safe="")
        u2_q = quote(u2_png, safe="")

        # Luminabot composite (two avatars merged)
        ship_img = f"https://api.luminabot.xyz/image/ship?user1={u1_q}&user2={u2_q}"

        # Build embed
        embed = discord.Embed(
            title=f"💘 {user1.display_name} × {user2.display_name}",
            description=(
                f"**Compatibility:** `{score}%`  `[{bar(score)}]`\n"
                f"**Stability:**     `{stability}%`  `[{bar(stability)}]`\n"
                f"**Couple Name:** `{couple_name}`\n\n{comment}\n\n"
                f"{user1.mention}  ×  {user2.mention}"
            ),
            color=color,
        )
        # Show both avatars (author icon = user1, thumbnail = user2)
        embed.set_author(name="Cheshire’s Ship Report", icon_url=u1_png)
        embed.set_thumbnail(url=u2_png)
        embed.set_image(url=ship_img)
        embed.set_footer(text="Results reset daily ❤️")
        embed.timestamp = datetime.datetime.utcnow()

        # ── Send (webhook preferred) ─────────────────
        message = None
        channel = interaction.channel
        if USE_WEBHOOK and isinstance(channel, discord.TextChannel):
            try:
                webhooks = await channel.webhooks()
                webhook = discord.utils.find(lambda w: w.name == WEBHOOK_NAME, webhooks)
                if webhook is None:
                    webhook = await channel.create_webhook(name=WEBHOOK_NAME, reason="Ship results (anti-purge)")
                message = await webhook.send(
                    embed=embed,
                    username="Cheshire Cat",
                    avatar_url=self.bot.user.display_avatar.url if self.bot.user else discord.Embed.Empty,
                    wait=True,
                )
            except Exception:
                message = None  # fall back

        if message is None:
            await interaction.response.send_message(embed=embed)

        # Silent ack if webhook used
        if not interaction.response.is_done():
            try:
                await interaction.response.send_message("💌 Shipped.", ephemeral=True)
            except Exception:
                pass


async def setup(bot: commands.Bot):
    await bot.add_cog(ShipChaos(bot))
