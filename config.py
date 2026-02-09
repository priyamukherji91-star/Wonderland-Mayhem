# config.py
from __future__ import annotations

import os
from typing import Final, Optional, Set, List

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

GATE_CHANNEL_ID: Final[int] = 1251693839962607675          # #choose-your-door
HERE_THEN_GONE_CHANNEL_ID: Final[int] = 1251693839962607675  # #here-then-gone
ROLES_CHANNEL_ID: Final[int] = 1251693839962607675         # #pick-your-roles

GIVEAWAYS_CHANNEL_ID: Final[int] = 1251693839962607675     # #giveaways
FFXIV_WIKI_CHANNEL_ID: Final[int] = 1251693839962607675    # #ffxiv-wiki
FOOLS_CHANNEL_ID: Final[int] = 1251693839962607675         # #where-fools-get-sent

BIRTHDAY_SET_CHANNEL_ID: Final[int] = 1251693839962607675
BIRTHDAY_ANNOUNCE_CHANNEL_ID: Final[int] = 1251693839962607675
BIRTHDAY_STAFF_CHANNEL_ID: Final[int] = 1251693839962607675

MODLOG_CHANNEL_ID: Optional[int] = None

# Channels where the link-fix feature is active
# (keep empty if unrestricted or unused)
LINKFIX_CHANNEL_IDS: Final[List[int]] = []

# ─────────────────────────────────────────────
# ROLE IDS / NAMES
# ─────────────────────────────────────────────

GATE_ROLE_ID: Final[int] = 1251693839962607675

ADMIN_ROLE_NAMES: Final[Set[str]] = {
    "Admin",
    "Moderator",
}

DOOMED_RABBIT_ROLE_NAME: Final[str] = "Doomed Rabbit"
MADNESS_WARDEN_ROLE_NAME: Final[str] = "Madness Warden"

# Used by permissions helpers
MOD_ROLE_NAMES: Final[tuple[str, ...]] = (
    *tuple(ADMIN_ROLE_NAMES),
    DOOMED_RABBIT_ROLE_NAME,
    MADNESS_WARDEN_ROLE_NAME,
)

# FC / Friend roles
FC_ROLE_NAME: Final[str] = "Hatter’s Strays"
FRIEND_ROLE_NAME: Final[str] = "Tea Party VIP"

# Roles allowed to use the fools gallery
FOOLS_ALLOWED_ROLE_NAMES: Final[tuple[str, ...]] = (
    FC_ROLE_NAME,
    FRIEND_ROLE_NAME,
)

# Gender / pronoun roles
GENDER_ROLE_IDS: Final[dict[str, int]] = {
    "She/Her": 1441330743589736549,
    "They/Them": 1441330775378100234,
    "He/Him": 1441330673360048199,
}

# ─────────────────────────────────────────────
# XIVAPI (character cards / !iam)
# ─────────────────────────────────────────────

XIVAPI_BASE_URL: Final[str] = "https://xivapi.com"

# Optional: set as environment variable for higher rate limits
XIVAPI_PRIVATE_KEY: Optional[str] = os.getenv("XIVAPI_PRIVATE_KEY")

XIVAPI_TIMEOUT_SECONDS: Final[int] = 15
