import os
import logging
import re
import asyncio
import tempfile
from typing import Optional, List

import discord
from discord.ext import commands
from discord import app_commands

import yt_dlp
import config
import permissions

# ───────────────────────────────────────────────────────────────────
# TOKEN (hard-coded by request — using your existing token)
# Prefer env var if present; fallback to hardcoded token.
# ───────────────────────────────────────────────────────────────────
HARDCODED_TOKEN = "YOUR_DISCORD_TOKEN_HERE"
TOKEN = os.getenv("DISCORD_TOKEN", HARDCODED_TOKEN)

# ───────────────────────────────────────────────────────────────────
# COG EXTENSIONS
#   IMPORTANT: autosync will discover + load other cogs itself.
#   So we only load autosync here to avoid double-loading.
# ───────────────────────────────────────────────────────────────────
INITIAL_EXTENSIONS: List[str] = [
    "cogs.autosync",
]

# ───────────────────────────────────────────────────────────────────
# SERVER CONFIG
# ───────────────────────────────────────────────────────────────────
# Set to your guild ID (int) if you want guild-only /sync in autosync
GUILD_ID = config.GUILD_ID

# Channels where link-fixing / media reupload is active
LINKFIX_CHANNEL_IDS = set(config.LINKFIX_CHANNEL_IDS)

# ───────────────────────────────────────────────────────────────────
# BOT SETUP
# ───────────────────────────────────────────────────────────────────
logging.basicConfig(level=logging.INFO)
intents = discord.Intents.all()  # ensure privileged intents are enabled in the Dev Portal
bot = commands.Bot(command_prefix="!", intents=intents)

# Make Guild ID available to autosync cog (so /sync guild works nicely)
bot.GUILD_ID = GUILD_ID


@bot.event
async def setup_hook():
    # Load ONLY autosync; it will handle discovering/loading other cogs.
    for ext in INITIAL_EXTENSIONS:
        try:
            await bot.load_extension(ext)
            logging.info("Loaded extension: %s", ext)
        except Exception as e:
            logging.exception("Failed to load %s: %s", ext, e)


# ───────────────────────────────────────────────────────────────────
# LIFECYCLE
# ───────────────────────────────────────────────────────────────────
@bot.event
async def on_ready():
    logging.info("✅ Logged in as %s (ID: %s)", bot.user, bot.user.id)


# ───────────────────────────────────────────────────────────────────
# SLASH: /ping (admin only)
#   — Same behaviour as before:
#     only roles in config.ADMIN_ROLE_NAMES may use this.
# ───────────────────────────────────────────────────────────────────

def _ping_admin_check(interaction: discord.Interaction) -> bool:
    user = interaction.user
    if not isinstance(user, discord.Member):
        raise app_commands.CheckFailure("This command can only be used in a server.")
    if permissions.has_any_role(user, config.ADMIN_ROLE_NAMES):
        return True
    raise app_commands.CheckFailure("You do not have permission to use this command.")


@bot.tree.command(name="ping", description="Admin ping check")
@app_commands.check(_ping_admin_check)
async def ping_command(interaction: discord.Interaction):
    await interaction.response.send_message("Pong! 🏓", ephemeral=True)


# ───────────────────────────────────────────────────────────────────
# LINK / MEDIA HANDLER
#
#  • Twitter/X → URL swap (fxtwitter / fixupx) via webhook
#  • Reddit → URL swap to rxddit.com
#  • Instagram/Facebook → download video with yt-dlp and upload as file
#  • Only runs in LINKFIX_CHANNEL_IDS
# ───────────────────────────────────────────────────────────────────

# Twitter / X
TWITTER_DOMAINS = ("twitter.com", "www.twitter.com", "mobile.twitter.com")
X_DOMAINS = ("x.com", "www.x.com", "mobile.x.com")

# Reddit
REDDIT_DOMAINS = (
    "reddit.com",
    "www.reddit.com",
    "old.reddit.com",
    "new.reddit.com",
    "redd.it",
)

# Instagram
INSTAGRAM_DOMAINS = ("instagram.com", "www.instagram.com", "m.instagram.com")

# Facebook
FACEBOOK_DOMAINS = ("facebook.com", "www.facebook.com", "m.facebook.com")

# Domains we won't touch (already "fixed" or proxies)
SKIP_DOMAINS = (
    # Twitter / X
    "fxtwitter.com",
    "vxtwitter.com",
    "fixupx.com",
    "fixvx.com",
    # Reddit
    "rxddit.com",
    "vxreddit.com",
    "rxyddit.com",
    "redditez.com",
)

# Simple URL matcher (avoids links inside <angle-brackets>, which suppress embeds)
URL_REGEX = re.compile(r"(?<!<)(https?://[^\s>]+)")


def _extract_host(url: str) -> Optional[str]:
    try:
        after_scheme = url.split("://", 1)[1]
        host = after_scheme.split("/", 1)[0]
        return host.lower()
    except Exception:
        return None


def _is_instagram(url: str) -> bool:
    host = _extract_host(url)
    return host in INSTAGRAM_DOMAINS if host else False


def _is_facebook(url: str) -> bool:
    host = _extract_host(url)
    return host in FACEBOOK_DOMAINS if host else False


def _swap_domain(url: str) -> str:
    """
    Convert Twitter/X + Reddit links to their 'fixed' counterparts
    for better Discord embeds. Instagram/Facebook are handled by
    downloading & uploading media instead (Option B).
    """
    lowered = url.lower()

    # If already a fixed/proxy domain, leave it alone
    if any(d in lowered for d in SKIP_DOMAINS):
        return url

    host = _extract_host(url)
    if not host:
        return url

    # Twitter → fxtwitter.com
    if host in TWITTER_DOMAINS:
        return url.replace(host, "fxtwitter.com", 1)

    # X → fixupx.com
    if host in X_DOMAINS:
        return url.replace(host, "fixupx.com", 1)

    # Reddit → rxddit.com
    if host in REDDIT_DOMAINS:
        return url.replace(host, "rxddit.com", 1)

    return url


def _fix_message_content_for_links(content: str) -> tuple[str, bool]:
    """
    Replace eligible Twitter/X/Reddit URLs in the content.
    Skip ones in <...>. Does NOT touch Instagram/Facebook.
    """
    changed = False

    def repl(match: re.Match) -> str:
        nonlocal changed
        url = match.group(1)
        if _is_instagram(url) or _is_facebook(url):
            # handled separately
            return url
        new_url = _swap_domain(url)
        if new_url != url:
            changed = True
        return new_url

    new_content = URL_REGEX.sub(repl, content)
    return new_content, changed


async def _get_or_create_webhook(channel: discord.TextChannel) -> Optional[discord.Webhook]:
    """
    Find or create a webhook in this channel to impersonate the user
    for reposts (so AutoClean doesn't nuke them).
    """
    try:
        hooks = await channel.webhooks()
        for wh in hooks:
            # Prefer a webhook owned by this bot to avoid permission issues
            if wh.user and wh.user.bot:
                return wh
        # create one if none available
        return await channel.create_webhook(name="FixEmbed Bridge")
    except discord.Forbidden:
        return None
    except Exception:
        logging.exception("Failed to get/create webhook in #%s", channel.name)
        return None


async def _download_media_file(url: str) -> Optional[str]:
    """
    Download a single media file using yt-dlp and return the local path.
    Limited to ~24 MB to avoid Discord size issues.
    """
    tmpdir = tempfile.mkdtemp(prefix="cheshire_media_")

    def _run() -> Optional[str]:
        ydl_opts = {
            "format": "bv*[filesize<24000000]+ba/b[filesize<24000000]/best",
            "outtmpl": os.path.join(tmpdir, "%(id)s.%(ext)s"),
            "noplaylist": True,
            "quiet": True,
            "no_warnings": True,
        }
        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(url, download=True)
                if isinstance(info, dict) and "entries" in info:
                    info = info["entries"][0]
                filename = ydl.prepare_filename(info)
                return filename
        except Exception:
            logging.exception("yt-dlp failed for %s", url)
            return None

    loop = asyncio.get_running_loop()
    path = await loop.run_in_executor(None, _run)
    return path


async def _reupload_instaface_media(
    message: discord.Message,
    urls: List[str],
    perms: discord.Permissions,
) -> bool:
    """
    For Instagram/Facebook URLs: download media and re-upload as files.
    Returns True if at least one media upload succeeded.
    """
    if not urls:
        return False

    channel = message.channel
    if not isinstance(channel, discord.TextChannel):
        return False

    webhook: Optional[discord.Webhook] = None
    if perms.manage_webhooks:
        webhook = await _get_or_create_webhook(channel)

    any_success = False

    for u in urls:
        path = await _download_media_file(u)
        if not path:
            continue

        file = discord.File(path)

        # PREVENT DISCORD FROM EMBEDDING THE FACEBOOK/INSTAGRAM LINK
        safe_url = f"<{u}>"

        # CLEAN FINAL MESSAGE
        content = f"{message.author.mention} shared: {safe_url}"

        try:
            if webhook:
                await webhook.send(
                    content,
                    username=message.author.display_name,
                    avatar_url=(
                        message.author.display_avatar.url
                        if message.author.display_avatar
                        else None
                    ),
                    file=file,
                    allowed_mentions=discord.AllowedMentions(
                        users=True, roles=False, everyone=False
                    ),
                )
            else:
                await channel.send(
                    content,
                    file=file,
                    allowed_mentions=discord.AllowedMentions(
                        users=True, roles=False, everyone=False
                    ),
                )
            any_success = True
        except discord.HTTPException:
            logging.exception("Failed to upload media for %s", u)
        finally:
            try:
                os.remove(path)
            except OSError:
                pass

    return any_success


@bot.event
async def on_message(message: discord.Message):
    # Always allow commands to work if we early-return
    async def _process_cmds():
        try:
            await bot.process_commands(message)
        except Exception:
            logging.exception("process_commands failed")

    # Ignore DM channels & non-text channels
    if not isinstance(message.channel, discord.TextChannel):
        await _process_cmds()
        return

    # Only run link/media handler in configured channels
    if message.channel.id not in LINKFIX_CHANNEL_IDS:
        await _process_cmds()
        return

    # Ignore bots and webhooks to prevent loops
    if message.author.bot or message.webhook_id is not None:
        await _process_cmds()
        return

    # We need to be able to delete; otherwise do nothing
    me = message.guild.me if message.guild else None
    if me is None:
        await _process_cmds()
        return
    perms: discord.Permissions = message.channel.permissions_for(me)
    if not perms.manage_messages:
        await _process_cmds()
        return

    content = message.content or ""
    urls = [m.group(1) for m in URL_REGEX.finditer(content)]

    insta_fb_urls = [u for u in urls if _is_instagram(u) or _is_facebook(u)]

    did_media = False
    if insta_fb_urls:
        did_media = await _reupload_instaface_media(message, insta_fb_urls, perms)

    # Fix Twitter/X/Reddit links in the text
    fixed_content, changed = _fix_message_content_for_links(content)

    did_text = False
    if changed:
        webhook = None
        if perms.manage_webhooks:
            webhook = await _get_or_create_webhook(message.channel)

        try:
            if webhook:
                await webhook.send(
                    fixed_content,
                    username=message.author.display_name,
                    avatar_url=(
                        message.author.display_avatar.url
                        if message.author.display_avatar
                        else None
                    ),
                    allowed_mentions=discord.AllowedMentions.all(),
                )
            else:
                await message.channel.send(
                    fixed_content,
                    allowed_mentions=discord.AllowedMentions.all(),
                )
            did_text = True
        except Exception:
            logging.exception("Failed to repost fixed links")

    # If we reposted anything (media or fixed text), delete the original
    if did_media or did_text:
        try:
            await message.delete()
        except Exception:
            pass

    # Continue processing commands
    await _process_cmds()


# ───────────────────────────────────────────────────────────────────
# RUN
# ───────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    bot.run(TOKEN)
