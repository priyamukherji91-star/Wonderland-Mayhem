# cogs/music.py
from __future__ import annotations

import asyncio
import base64
import binascii
import logging
import os
import re
import urllib.parse
from dataclasses import dataclass
from typing import Optional

import aiohttp
import discord
from discord.ext import commands
import yt_dlp

LOG = logging.getLogger(__name__)

MUSIC_TEXT_CHANNEL_ID = 1441863803011727380
EMBED_COLOR = 0x5865F2
FFMPEG_BEFORE_OPTIONS = "-nostdin -reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5"
FFMPEG_OPTIONS = "-vn"

YTDLP_COOKIES_ENV = os.getenv("YTDLP_COOKIES", "").strip()
YTDLP_COOKIES_B64_ENV = os.getenv("YTDLP_COOKIES_B64", "").strip()
AUTO_COOKIE_PATH = "/app/data/cookies.txt"

YTDL_BASE_OPTS = {
    "format": "bestaudio/best",
    "quiet": True,
    "no_warnings": True,
    "noplaylist": False,
    "extract_flat": False,
    "default_search": "ytsearch1",
    "source_address": "0.0.0.0",
    "skip_download": True,
    "http_headers": {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0 Safari/537.36"
        )
    },
}

YOUTUBE_HOSTS = (
    "youtube.com",
    "www.youtube.com",
    "m.youtube.com",
    "music.youtube.com",
    "youtu.be",
)
SPOTIFY_HOSTS = (
    "open.spotify.com",
    "play.spotify.com",
    "spotify.link",
)

SPOTIFY_TRACK_RE = re.compile(r"spotify\.com/track/([A-Za-z0-9]+)", re.IGNORECASE)
URL_RE = re.compile(r"^https?://", re.IGNORECASE)


@dataclass
class QueueItem:
    title: str
    stream_url: str
    webpage_url: str
    requested_by: str
    duration: Optional[int] = None
    thumbnail: Optional[str] = None


class GuildMusicState:
    def __init__(self) -> None:
        self.queue: list[QueueItem] = []
        self.voice_client: Optional[discord.VoiceClient] = None
        self.current: Optional[QueueItem] = None
        self.text_channel_id: Optional[int] = None
        self.lock = asyncio.Lock()

    def reset(self) -> None:
        self.queue.clear()
        self.current = None
        self.text_channel_id = None


class Music(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.states: dict[int, GuildMusicState] = {}
        self.session: Optional[aiohttp.ClientSession] = None
        self.cookiefile_path: Optional[str] = None

    async def cog_load(self) -> None:
        self.session = aiohttp.ClientSession()
        self.cookiefile_path = self.prepare_cookie_file()

    async def cog_unload(self) -> None:
        for state in self.states.values():
            vc = state.voice_client
            if vc and vc.is_connected():
                try:
                    await vc.disconnect(force=True)
                except Exception:
                    pass
        if self.session and not self.session.closed:
            await self.session.close()

    def prepare_cookie_file(self) -> Optional[str]:
        if YTDLP_COOKIES_B64_ENV:
            try:
                os.makedirs("/app/data", exist_ok=True)
                raw = base64.b64decode(YTDLP_COOKIES_B64_ENV)
                with open(AUTO_COOKIE_PATH, "wb") as f:
                    f.write(raw)
                LOG.info("Music: wrote cookies file from YTDLP_COOKIES_B64 to %s", AUTO_COOKIE_PATH)
                return AUTO_COOKIE_PATH
            except (binascii.Error, OSError):
                LOG.exception("Music: failed to decode/write YTDLP_COOKIES_B64")
                return None

        if YTDLP_COOKIES_ENV:
            return YTDLP_COOKIES_ENV

        return None

    def state_for(self, guild_id: int) -> GuildMusicState:
        return self.states.setdefault(guild_id, GuildMusicState())

    def is_music_channel(self, ctx: commands.Context) -> bool:
        return bool(ctx.guild and ctx.channel and ctx.channel.id == MUSIC_TEXT_CHANNEL_ID)

    async def send_music_only_notice(self, ctx: commands.Context) -> None:
        await ctx.reply(
            f"Music commands only work in <#{MUSIC_TEXT_CHANNEL_ID}>.",
            mention_author=False,
            delete_after=10,
        )

    async def send_embed(self, channel: discord.abc.Messageable, title: str, description: str) -> None:
        try:
            embed = discord.Embed(title=title, description=description, color=EMBED_COLOR)
            await channel.send(embed=embed)
        except Exception:
            LOG.exception("Music: failed to send embed")

    async def ensure_voice(self, ctx: commands.Context) -> Optional[discord.VoiceClient]:
        if not ctx.guild or not isinstance(ctx.author, discord.Member):
            await ctx.reply("This command only works in a server.", mention_author=False, delete_after=10)
            return None

        if not ctx.author.voice or not ctx.author.voice.channel:
            await ctx.reply("You need to be in a voice channel first.", mention_author=False, delete_after=10)
            return None

        state = self.state_for(ctx.guild.id)
        target_channel = ctx.author.voice.channel
        vc = ctx.guild.voice_client

        try:
            if vc and vc.is_connected():
                if vc.channel != target_channel:
                    await vc.move_to(target_channel)
            else:
                vc = await target_channel.connect(self_deaf=True)
        except discord.ClientException:
            await ctx.reply("I couldn't join that voice channel.", mention_author=False, delete_after=10)
            return None
        except discord.Forbidden:
            await ctx.reply(
                "I don't have permission to join or speak in that voice channel.",
                mention_author=False,
                delete_after=10,
            )
            return None
        except Exception:
            LOG.exception("Music: failed to connect to voice")
            await ctx.reply("Something went wrong while joining voice.", mention_author=False, delete_after=10)
            return None

        state.voice_client = vc
        state.text_channel_id = MUSIC_TEXT_CHANNEL_ID
        return vc

    async def ytdl_extract(self, query: str, *, search: bool = False) -> dict:
        opts = dict(YTDL_BASE_OPTS)

        if self.cookiefile_path:
            opts["cookiefile"] = self.cookiefile_path

        if search:
            target = f"ytsearch1:{query}"
            opts["noplaylist"] = True
        else:
            target = query

        def _run() -> dict:
            with yt_dlp.YoutubeDL(opts) as ydl:
                return ydl.extract_info(target, download=False)

        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, _run)

    def _host(self, url: str) -> str:
        try:
            return urllib.parse.urlparse(url).netloc.lower()
        except Exception:
            return ""

    def is_youtube_url(self, url: str) -> bool:
        host = self._host(url)
        return any(host == h or host.endswith(f".{h}") for h in YOUTUBE_HOSTS)

    def is_spotify_url(self, url: str) -> bool:
        host = self._host(url)
        return any(host == h or host.endswith(f".{h}") for h in SPOTIFY_HOSTS)

    async def resolve_spotify_url(self, url: str) -> str:
        if not self.session:
            return url

        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0 Safari/537.36"
            )
        }

        try:
            async with self.session.get(
                url,
                headers=headers,
                allow_redirects=True,
                timeout=aiohttp.ClientTimeout(total=15),
            ) as resp:
                return str(resp.url)
        except Exception:
            LOG.exception("Music: failed to resolve Spotify URL")
            return url

    async def spotify_track_to_search(self, url: str) -> Optional[str]:
        if not self.session:
            return None

        url = await self.resolve_spotify_url(url)

        if not SPOTIFY_TRACK_RE.search(url):
            return None

        lookup_url = url.split("?", 1)[0]

        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0 Safari/537.36"
            )
        }

        try:
            async with self.session.get(
                lookup_url,
                headers=headers,
                allow_redirects=True,
                timeout=aiohttp.ClientTimeout(total=15),
            ) as resp:
                if resp.status != 200:
                    return None
                html = await resp.text()
        except Exception:
            LOG.exception("Music: failed to fetch Spotify page")
            return None

        title_match = re.search(r'<meta\s+property="og:title"\s+content="([^"]+)"', html, re.IGNORECASE)
        desc_match = re.search(r'<meta\s+property="og:description"\s+content="([^"]+)"', html, re.IGNORECASE)

        title = html_unescape(title_match.group(1).strip()) if title_match else ""
        desc = html_unescape(desc_match.group(1).strip()) if desc_match else ""

        if not title:
            return None

        query_parts = [title]
        if desc and desc.lower() not in title.lower():
            query_parts.append(desc)

        return " ".join(query_parts) + " audio"

    def queue_item_from_info(self, info: dict, requested_by: str) -> Optional[QueueItem]:
        if not info:
            return None

        if "entries" in info and info["entries"]:
            for entry in info["entries"]:
                built = self.queue_item_from_info(entry, requested_by)
                if built:
                    return built
            return None

        stream_url = info.get("url")
        webpage_url = info.get("webpage_url") or info.get("original_url") or ""
        title = info.get("title") or "Unknown title"
        duration = info.get("duration")
        thumbnail = info.get("thumbnail")

        if not stream_url:
            return None

        return QueueItem(
            title=title,
            stream_url=stream_url,
            webpage_url=webpage_url,
            requested_by=requested_by,
            duration=duration,
            thumbnail=thumbnail,
        )

    async def build_items_from_input(self, raw_input: str, requested_by: str) -> list[QueueItem]:
        if not URL_RE.match(raw_input):
            raise commands.BadArgument("Only links are allowed.")

        if self.is_youtube_url(raw_input):
            try:
                info = await self.ytdl_extract(raw_input, search=False)
            except Exception as e:
                msg = str(e)
                if "Sign in to confirm you’re not a bot" in msg or "Sign in to confirm you're not a bot" in msg:
                    raise RuntimeError("YouTube blocked that request. Cookies are still missing or invalid.")
                raise RuntimeError("I couldn't read that YouTube link.")

            if info.get("entries"):
                items: list[QueueItem] = []
                for entry in info["entries"]:
                    built = self.queue_item_from_info(entry, requested_by)
                    if built:
                        items.append(built)
                if items:
                    return items

            item = self.queue_item_from_info(info, requested_by)
            if not item:
                raise RuntimeError("I couldn't read that YouTube link.")
            return [item]

        if self.is_spotify_url(raw_input):
            query = await self.spotify_track_to_search(raw_input)
            if not query:
                raise RuntimeError(
                    "That Spotify link couldn't be used. v1 supports Spotify track links, not playlists/albums."
                )

            try:
                info = await self.ytdl_extract(query, search=True)
            except Exception:
                raise RuntimeError("I couldn't find a playable YouTube match for that Spotify track.")

            item = self.queue_item_from_info(info, requested_by)
            if not item:
                raise RuntimeError("I found no playable match for that Spotify track.")
            return [item]

        raise commands.BadArgument("Unsupported link. Use a YouTube link or a Spotify track link.")

    async def start_next(self, guild: discord.Guild) -> None:
        state = self.state_for(guild.id)
        vc = state.voice_client or guild.voice_client
        state.voice_client = vc

        if not vc or not vc.is_connected():
            state.reset()
            return

        if vc.is_playing() or vc.is_paused():
            return

        if not state.queue:
            state.current = None
            return

        next_item = state.queue.pop(0)
        state.current = next_item

        def _after_play(error: Optional[Exception]) -> None:
            if error:
                LOG.exception("Music: player error", exc_info=error)
            fut = asyncio.run_coroutine_threadsafe(self._after_track(guild.id), self.bot.loop)
            try:
                fut.result()
            except Exception:
                LOG.exception("Music: failed after-track handler")

        source = discord.FFmpegPCMAudio(
            next_item.stream_url,
            before_options=FFMPEG_BEFORE_OPTIONS,
            options=FFMPEG_OPTIONS,
        )
        vc.play(source, after=_after_play)

        channel = guild.get_channel(state.text_channel_id or MUSIC_TEXT_CHANNEL_ID)
        if isinstance(channel, discord.TextChannel):
            desc = f"**{discord.utils.escape_markdown(next_item.title)}**"
            duration_text = format_duration(next_item.duration)
            if duration_text:
                desc += f"\nDuration: {duration_text}"
            desc += f"\nRequested by {next_item.requested_by}"
            if next_item.webpage_url:
                desc += f"\n{next_item.webpage_url}"
            await self.send_embed(channel, "Now playing", desc)

    async def _after_track(self, guild_id: int) -> None:
        guild = self.bot.get_guild(guild_id)
        if not guild:
            return
        await self.start_next(guild)

    @commands.command(name="play")
    async def play_cmd(self, ctx: commands.Context, *, link: str) -> None:
        if not self.is_music_channel(ctx):
            await self.send_music_only_notice(ctx)
            return
        if not ctx.guild:
            return

        vc = await self.ensure_voice(ctx)
        if not vc:
            return

        state = self.state_for(ctx.guild.id)
        async with state.lock:
            try:
                items = await self.build_items_from_input(link.strip(), str(ctx.author.display_name))
            except commands.BadArgument as e:
                await ctx.reply(str(e), mention_author=False, delete_after=10)
                return
            except Exception as e:
                await ctx.reply(str(e), mention_author=False, delete_after=12)
                return

            state.voice_client = vc
            state.text_channel_id = MUSIC_TEXT_CHANNEL_ID
            state.queue.extend(items)

            if len(items) == 1:
                duration_text = format_duration(items[0].duration)
                desc = f"**{discord.utils.escape_markdown(items[0].title)}**\nRequested by {ctx.author.mention}"
                if duration_text:
                    desc += f"\nDuration: {duration_text}"
                await self.send_embed(ctx.channel, "Queued", desc)
            else:
                await self.send_embed(
                    ctx.channel,
                    "Queued playlist",
                    f"Added **{len(items)}** tracks to the queue for {ctx.author.mention}.",
                )

            if not vc.is_playing() and not vc.is_paused():
                await self.start_next(ctx.guild)

    @commands.command(name="skip")
    async def skip_cmd(self, ctx: commands.Context) -> None:
        if not self.is_music_channel(ctx):
            await self.send_music_only_notice(ctx)
            return
        if not ctx.guild or not ctx.guild.voice_client:
            await ctx.reply("I'm not playing anything right now.", mention_author=False, delete_after=10)
            return

        vc = ctx.guild.voice_client
        if vc.is_playing() or vc.is_paused():
            vc.stop()
            await self.send_embed(ctx.channel, "Skipped", "Skipped the current track.")
        else:
            await ctx.reply("There's nothing to skip.", mention_author=False, delete_after=10)

    @commands.command(name="pause")
    async def pause_cmd(self, ctx: commands.Context) -> None:
        if not self.is_music_channel(ctx):
            await self.send_music_only_notice(ctx)
            return
        if not ctx.guild or not ctx.guild.voice_client:
            await ctx.reply("I'm not in voice right now.", mention_author=False, delete_after=10)
            return

        vc = ctx.guild.voice_client
        if vc.is_playing():
            vc.pause()
            await self.send_embed(ctx.channel, "Paused", "Playback paused.")
        else:
            await ctx.reply("There's nothing playing to pause.", mention_author=False, delete_after=10)

    @commands.command(name="resume")
    async def resume_cmd(self, ctx: commands.Context) -> None:
        if not self.is_music_channel(ctx):
            await self.send_music_only_notice(ctx)
            return
        if not ctx.guild or not ctx.guild.voice_client:
            await ctx.reply("I'm not in voice right now.", mention_author=False, delete_after=10)
            return

        vc = ctx.guild.voice_client
        if vc.is_paused():
            vc.resume()
            await self.send_embed(ctx.channel, "Resumed", "Playback resumed.")
        else:
            await ctx.reply("There's nothing paused right now.", mention_author=False, delete_after=10)

    @commands.command(name="queue")
    async def queue_cmd(self, ctx: commands.Context) -> None:
        if not self.is_music_channel(ctx):
            await self.send_music_only_notice(ctx)
            return
        if not ctx.guild:
            return

        state = self.state_for(ctx.guild.id)
        lines: list[str] = []

        if state.current:
            now_line = f"**Now:** {discord.utils.escape_markdown(state.current.title)}"
            now_duration = format_duration(state.current.duration)
            if now_duration:
                now_line += f" ({now_duration})"
            lines.append(now_line)

        if state.queue:
            preview = state.queue[:10]
            if lines:
                lines.append("")
            lines.append("**Up next:**")
            for idx, item in enumerate(preview, start=1):
                line = f"{idx}. {discord.utils.escape_markdown(item.title)}"
                duration_text = format_duration(item.duration)
                if duration_text:
                    line += f" ({duration_text})"
                lines.append(line)
            if len(state.queue) > 10:
                lines.append(f"…and **{len(state.queue) - 10}** more.")

        if not lines:
            await ctx.reply("The queue is empty.", mention_author=False, delete_after=10)
            return

        await self.send_embed(ctx.channel, "Music queue", "\n".join(lines))

    @commands.command(name="stop")
    async def stop_cmd(self, ctx: commands.Context) -> None:
        if not self.is_music_channel(ctx):
            await self.send_music_only_notice(ctx)
            return
        if not ctx.guild:
            return

        state = self.state_for(ctx.guild.id)
        state.queue.clear()
        state.current = None

        vc = ctx.guild.voice_client
        if vc and (vc.is_playing() or vc.is_paused()):
            vc.stop()

        await self.send_embed(ctx.channel, "Stopped", "Playback stopped and the queue was cleared.")

    @commands.command(name="leave")
    async def leave_cmd(self, ctx: commands.Context) -> None:
        if not self.is_music_channel(ctx):
            await self.send_music_only_notice(ctx)
            return
        if not ctx.guild:
            return

        state = self.state_for(ctx.guild.id)
        state.queue.clear()
        state.current = None

        vc = ctx.guild.voice_client
        if vc and vc.is_connected():
            try:
                await vc.disconnect(force=True)
            except Exception:
                LOG.exception("Music: failed to disconnect")
                await ctx.reply("I couldn't leave the voice channel cleanly.", mention_author=False, delete_after=10)
                return

        state.voice_client = None
        await self.send_embed(ctx.channel, "Disconnected", "Left voice and cleared the queue.")


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Music(bot))


def html_unescape(text: str) -> str:
    return (
        text.replace("&amp;", "&")
        .replace("&quot;", '"')
        .replace("&#x27;", "'")
        .replace("&#39;", "'")
        .replace("&lt;", "<")
        .replace("&gt;", ">")
    )


def format_duration(seconds: Optional[int]) -> str:
    if not seconds or seconds < 0:
        return ""
    minutes, sec = divmod(int(seconds), 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours}:{minutes:02}:{sec:02}"
    return f"{minutes}:{sec:02}"