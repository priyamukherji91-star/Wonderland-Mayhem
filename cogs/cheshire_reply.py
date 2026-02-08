import re
import random
import discord
from discord.ext import commands
from discord import app_commands

# ─────────────────────────────────────────────
# TRIGGERS
# ─────────────────────────────────────────────

# 0) Lea "wolf" trigger (any text channel)
TRIGGER_LEA_WOLF = re.compile(r"\blea\s+is\s+a\s+wolf\b", re.IGNORECASE)

# 1) Classic Cheshire text trigger
TRIGGER_MAD = re.compile(r"\beveryone is mad here\b", re.IGNORECASE)

# 2) Zyphie "imma be honest" style trigger
HONEST_USER_ID: int = 358235180563038209  # Zyphie

TRIGGER_HONEST = re.compile(
    r"\b("
    r"imma\s+be\s+honest"
    r"|i['’]m\s+gonna\s+be\s+honest"
    r"|gonna\s+be\s+honest"
    r"|to\s+be\s+honest"
    r")\b",
    re.IGNORECASE,
)

# Channels where the Zyphie + Parade behaviour is active
HONEST_CHANNEL_IDS: set[int] = {
    1251693839962607672,  # birthdays set
    1251693839962607675,  # 💩 shitposting
    1254688995221176365,  # 👀🤡 sussy humour
    1251693840365125693,  # extra chaos channel
    1251693839962607674,  # extra chaos channel
    1428118992215609354,  # staff/side channel
    1251693840365125701,  # NEW channel you asked to include
}

# 3) Parade / Black Parade trigger
TRIGGER_PARADE = re.compile(r"\b(black\s+parade|parade)\b", re.IGNORECASE)

# ─────────────────────────────────────────────
# RESPONSES
# ─────────────────────────────────────────────

LEA_REPLY = "No, Lea is a kitty cat"

# Zyphie roast lines – only used when HONEST_USER_ID talks in HONEST_CHANNEL_IDS
ZYPHIE_LINES: list[str] = [
    "Zyphie, imma be honest, even Wonderland’s tired of that opener.",
    "Imma be honest, Zyphie, the queue isn’t the only thing that’s lost.",
    "Zyphie, every time you’re ‘gonna be honest’, the tea gets colder.",
    "Imma be honest, Zyphie — even the Cheshire Cat needs a cigarette now.",
    "Zyphie, to be honest, the Fool’s Gallery is taking notes.",
]

# Black Parade lyric slots – fill these with your own lyric chunks locally.
PARADE_SNIPPETS: list[str] = [
    "When the drums hit, the parade starts in your chest.",
    "We’ll carry on — even if the world refuses to get the memo.",
    "Give a cheer for all the broken; this anthem was always for you.",
    "Paint it black, take it back, and march like you mean it.",
]

# Fallback if you forget to fill PARADE_SNIPPETS
DEFAULT_PARADE_FALLBACK = "We’ll carry on. That’s it. That’s the parade."


class CheshireReply(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    # ─────────────────────────────────────────
    # Message listener
    # ─────────────────────────────────────────
    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        # ignore bots (including yourself)
        if message.author.bot:
            return

        content = (message.content or "").strip()
        channel = message.channel

        # Only respond in guild text channels
        if not isinstance(channel, discord.TextChannel):
            return

        # 0) Lea "wolf" trigger (ANY channel)
        if TRIGGER_LEA_WOLF.search(content):
            try:
                await channel.send(LEA_REPLY)
            except discord.Forbidden:
                pass
            return

        # 1) Classic Cheshire trigger: "everyone is mad here" → "I agree."
        #    (AutoClean is already configured to spare this exact line.)
        if TRIGGER_MAD.search(content):
            try:
                await channel.send("I agree.")
            except discord.Forbidden:
                pass
            return

        # 2) Zyphie "imma be honest" roast (only in configured channels)
        if (
            message.author.id == HONEST_USER_ID
            and channel.id in HONEST_CHANNEL_IDS
            and TRIGGER_HONEST.search(content)
        ):
            line = random.choice(ZYPHIE_LINES)
            try:
                await channel.send(line)
            except discord.Forbidden:
                pass
            return

        # 3) Black Parade trigger (same channels as Zyphie logic)
        if channel.id in HONEST_CHANNEL_IDS and TRIGGER_PARADE.search(content):
            snippets = PARADE_SNIPPETS or [DEFAULT_PARADE_FALLBACK]
            line = random.choice(snippets)
            try:
                await channel.send(line)
            except discord.Forbidden:
                pass
            return

    # Tiny test slash command (kept from original file)
    @app_commands.command(name="cheshire", description="Test that the Cheshire cog is loaded")
    async def cheshire(self, interaction: discord.Interaction):
        await interaction.response.send_message(
            "The Cheshire cog is loaded. 🐱", ephemeral=True
        )


async def setup(bot: commands.Bot):
    await bot.add_cog(CheshireReply(bot))
