# config.py
from __future__ import annotations

from typing import Final, Optional, Set

# ─────────────────────────────────────────────
# GUILD / TIMEZONE
# ─────────────────────────────────────────────

# Your main server
GUILD_ID: Final[int] = 1251693839249313863

# Server time (used by birthdays, duties, etc.)
ST_TIMEZONE: Final[str] = "Europe/Luxembourg"

# ─────────────────────────────────────────────
# CHANNEL IDS
# ─────────────────────────────────────────────

# Roles / reaction menus
ROLES_CHANNEL_ID: Final[int] = 1421819010055671868  # #roles / #pick-your-roles

# Giveaways
GIVEAWAYS_CHANNEL_ID: Final[int] = 1255205422005227602  # #giveaways

# Duty events
DUTY_EVENTS_CHANNEL_ID: Final[int] = 1261600281293099019  # #duty-events

# FFXIV resources + wiki
FFXIV_RESOURCES_CHANNEL_ID: Final[int] = 1251693839962607669  # #ffxiv-resources
FFXIV_WIKI_CHANNEL_ID: Final[int] = 1428352638998544475      # #ffxiv-wiki

# Fools gallery
FOOLS_CHANNEL_ID: Final[int] = 1251693840365125698           # #where-fools-get-sent

# Join/leave logs + gate
HERE_THEN_GONE_CHANNEL_ID: Final[int] = 1441321146820591728  # #here-then-gone
GATE_CHANNEL_ID: Final[int] = 1441321713395826788            # #choose-your-door

# Shitposting / chaos
SHITPOSTING_CHANNEL_ID: Final[int] = 1251693839962607675     # 💩-shitposting

# Link-fix / media reupload channels
MUSIC_MEDIA_CHANNEL_ID: Final[int] = 1255281446697042061     # 🎧-music-n-media
FORBIDDEN_DOOR_CHANNEL_ID: Final[int] = 1254679880482820157  # 🔞-forbidden-door
SUSSY_HUMOUR_CHANNEL_ID: Final[int] = 1254688995221176365    # 👀🤡-sussy-humour

# Birthdays
BIRTHDAY_SET_CHANNEL_ID: Final[int] = 1251693839962607672
BIRTHDAY_ANNOUNCE_CHANNEL_ID: Final[int] = 1251693840604332077
BIRTHDAY_STAFF_CHANNEL_ID: Final[int] = 1323505715599376424

# Optional: dedicated mod-log channel (None = disabled)
MODLOG_CHANNEL_ID: Optional[int] = None

# Private / internal log channel that should never be auto-cleaned
PRIVATE_LOG_CHANNEL_ID: Final[int] = 1444159488054792273

# ─────────────────────────────────────────────
# CHANNEL SETS
# ─────────────────────────────────────────────

# Where link-fixing / media reupload is active
LINKFIX_CHANNEL_IDS: Final[Set[int]] = {
    MUSIC_MEDIA_CHANNEL_ID,
    FORBIDDEN_DOOR_CHANNEL_ID,
    SUSSY_HUMOUR_CHANNEL_ID,
    SHITPOSTING_CHANNEL_ID,
}

# Channels you want AUTOCLEAN to completely ignore
AUTO_CLEAN_EXEMPT_CHANNEL_IDS: Final[Set[int]] = {
    FFXIV_RESOURCES_CHANNEL_ID,
    FOOLS_CHANNEL_ID,
    ROLES_CHANNEL_ID,
    DUTY_EVENTS_CHANNEL_ID,
    GIVEAWAYS_CHANNEL_ID,
    FFXIV_WIKI_CHANNEL_ID,
    HERE_THEN_GONE_CHANNEL_ID,
    PRIVATE_LOG_CHANNEL_ID,  # log channel (messages should not be deleted)
}

# ─────────────────────────────────────────────
# ROLES
# ─────────────────────────────────────────────

# Gate role given on join, removed once they choose FC/Friend
GATE_ROLE_ID: Final[int] = 1441319854974959616  # "Not Yet Mad Enough"

# Core admin/staff roles
DOOMED_RABBIT_ROLE_NAME: Final[str] = "Doomed Rabbit"
MADNESS_WARDEN_ROLE_NAME: Final[str] = "Madness Warden"

# Tuple so we can reuse easily in permission checks
ADMIN_ROLE_NAMES: Final[tuple[str, ...]] = (
    DOOMED_RABBIT_ROLE_NAME,
    MADNESS_WARDEN_ROLE_NAME,
)

# FC / Friend roles (names used in multiple places)
FC_ROLE_NAME: Final[str] = "Hatter’s Strays"
FRIEND_ROLE_NAME: Final[str] = "Tea Party VIP"

# Roles allowed to use the fools gallery, etc.
FOOLS_ALLOWED_ROLE_NAMES: Final[tuple[str, ...]] = (
    FC_ROLE_NAME,
    FRIEND_ROLE_NAME,
)

# Gender/pronoun roles (used by the pronoun dropdown)
GENDER_ROLE_IDS: Final[dict[str, int]] = {
    "She/Her": 1441330743589736549,
    "They/Them": 1441330775378100234,
    "He/Him": 1441330673360048199,
}
