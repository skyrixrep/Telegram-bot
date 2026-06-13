"""
Spotify Cookie Health Checker — ported from terminal spotifycookie.py.
Uses requests to hit Spotify account APIs and extract plan/account details.
"""

import logging
import json
import re
import random
import time
import urllib.parse
from dataclasses import dataclass, field
from typing import Dict, Any
from datetime import datetime

import requests

from .enums import PrivatizationStatus

logger = logging.getLogger(__name__)

OVERVIEW_URLS = [
    "https://www.spotify.com/us/account/overview/?utm_source=spotify&utm_medium=menu&utm_campaign=your_account",
    "https://www.spotify.com/account/overview/?utm_source=spotify&utm_medium=menu&utm_campaign=your_account",
]
PROFILE_URL = "https://www.spotify.com/api/account-settings/v1/profile"
FAMILY_HOME_URL = "https://www.spotify.com/api/family/v1/family/home"

OVERVIEW_HEADERS = {
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.5",
    "Cache-Control": "no-cache",
    "Connection": "keep-alive",
    "Pragma": "no-cache",
    "Upgrade-Insecure-Requests": "1",
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:132.0) Gecko/20100101 Firefox/132.0",
    "X-Requested-With": "XMLHttpRequest",
}

API_HEADERS = {
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.5",
    "Cache-Control": "no-cache",
    "Connection": "keep-alive",
    "Pragma": "no-cache",
    "Referer": "https://www.spotify.com/account/profile/",
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:132.0) Gecko/20100101 Firefox/132.0",
}

PLAN_NAME_MAP = {
    "duo_premium": "Duo Premium",
    "family_premium_v2": "Family Premium",
    "family_basic": "Family Basic",
    "premium": "Premium",
    "premium_mini": "Premium Mini",
    "basic_premium": "Premium Basic",
    "student_premium": "Student Premium",
    "student_premium_hulu": "Student Premium-Hulu",
    "free": "Free",
}


@dataclass
class SpotifyValidationResult:
    """Result of a Spotify cookie validation check."""
    status: PrivatizationStatus
    message: str
    details: Dict[str, Any] = field(default_factory=dict)


# ── Cookie parsing ──────────────────────────────────────────────

def parse_spotify_cookie_content_to_dict(cookie_content: str) -> dict:
    """Parse Spotify cookie content from Netscape or JSON format."""
    cookies = {}
    if not cookie_content or not cookie_content.strip():
        return cookies

    # Try JSON format
    lines = cookie_content.strip().split('\n')
    json_lines = []
    in_json = False
    for line in lines:
        stripped = line.strip()
        if stripped.startswith('['):
            in_json = True
            json_lines.append(line)
        elif in_json:
            json_lines.append(line)

    if json_lines:
        json_text = '\n'.join(json_lines).strip()
        if json_text.startswith('[') and json_text.endswith(']'):
            try:
                for cookie in json.loads(json_text):
                    name = cookie.get('name')
                    value = cookie.get('value')
                    if name and value:
                        cookies[name] = urllib.parse.unquote(value)
                if cookies:
                    return cookies
            except (json.JSONDecodeError, KeyError):
                pass

    # Netscape format
    for line in lines:
        line = line.strip()
        if line.startswith('# ') or not line:
            continue
        if line.startswith('#HttpOnly_'):
            line = line[10:]
        parts = line.split('\t')
        if len(parts) == 7:
            cookies[parts[5]] = urllib.parse.unquote(parts[6])

    return cookies


def validate_spotify_cookies(cookie_dict: dict) -> bool:
    """Check that essential Spotify cookies are present."""
    if not cookie_dict:
        return False
    names_lower = [n.lower() for n in cookie_dict.keys()]
    has_sp = any('sp_' in n or 'sp_dc' in n or 'sp_key' in n for n in names_lower)
    has_values = any(bool(str(v).strip()) for v in cookie_dict.values() if v is not None)
    return has_sp and has_values


# ── HTML / API parsing helpers (from terminal version) ──────────

def _extract_first(text, patterns, flags=0):
    for pattern in patterns:
        match = re.search(pattern, text, flags)
        if match:
            return match.group(1)
    return None


def infer_plan_key(plan_name):
    if not plan_name:
        return "unknown"
    name = plan_name.strip().lower()
    if "free" in name:
        return "free"
    if "family" in name and "basic" in name:
        return "family_basic"
    if "family" in name:
        return "family_premium_v2"
    if "duo" in name:
        return "duo_premium"
    if "student" in name and "hulu" in name:
        return "student_premium_hulu"
    if "student" in name:
        return "student_premium"
    if "mini" in name:
        return "premium_mini"
    if "basic" in name and "premium" in name:
        return "basic_premium"
    if "premium" in name:
        return "premium"
    return "unknown"


def parse_overview_data(source):
    """Parse the overview HTML for account info."""
    normalized = source.replace('\\"', '"').replace("&quot;", '"')
    combined = f"{source}\n{normalized}"

    logged_in = (
        ('loggedIn\\":true' in source)
        or ('"loggedIn":true' in normalized)
        or ('"isLoggedInUser":true' in normalized)
    )

    plan_name = _extract_first(
        combined,
        [
            r'planName\\":\\"([^"]+)',
            r'"planName":"([^"]+)"',
            r'data-encore-id="text">([^<]+)<',
        ],
        flags=re.IGNORECASE,
    )
    plan_key = infer_plan_key(plan_name or "")

    country = _extract_first(
        combined,
        [
            r'country\\":\\"([A-Za-z]{2})',
            r'"country":"([A-Za-z]{2})"',
            r'countryCode\\":\\"([A-Za-z]{2})',
            r'"countryCode":"([A-Za-z]{2})"',
        ],
    )
    if country:
        country = country.upper()

    is_master_match = _extract_first(
        combined, [r'isMaster\\":(true|false)', r'"isMaster":(true|false)'], flags=re.IGNORECASE
    )
    is_sub_account_match = _extract_first(
        combined, [r'isSubAccount\\":(true|false)', r'"isSubAccount":(true|false)'], flags=re.IGNORECASE
    )
    recurring_match = _extract_first(
        combined, [r'isRecurring\\":(true|false)', r'"isRecurring":(true|false)'], flags=re.IGNORECASE
    )
    trial_match = _extract_first(
        combined, [r'isTrialUser\\":(true|false)', r'"isTrialUser":(true|false)'], flags=re.IGNORECASE
    )
    email = _extract_first(
        combined, [r'email\\":\\"([^"]+)', r'"email":"([^"]+)"'], flags=re.IGNORECASE
    )
    invite_link = _extract_first(
        combined,
        [
            r'inviteLink\\":\\"([^"]+)',
            r'"inviteLink":"([^"]+)"',
            r'(https://www\.spotify\.com/[^"\s]*family[^"\s]*)',
        ],
        flags=re.IGNORECASE,
    )
    address = _extract_first(
        combined,
        [
            r'address\\":\\"([^"]+)', r'"address":"([^"]+)"',
            r'streetAddress\\":\\"([^"]+)', r'"streetAddress":"([^"]+)"',
        ],
        flags=re.IGNORECASE,
    )
    free_slots_direct = _extract_first(
        combined,
        [r'freeSlots\\":(\d+)', r'"freeSlots":(\d+)', r'availableSlots\\":(\d+)', r'"availableSlots":(\d+)'],
        flags=re.IGNORECASE,
    )
    members_count = _extract_first(
        combined,
        [r'membersCount\\":(\d+)', r'"membersCount":(\d+)', r'memberCount\\":(\d+)', r'"memberCount":(\d+)'],
        flags=re.IGNORECASE,
    )
    max_members = _extract_first(
        combined,
        [r'maxMembers\\":(\d+)', r'"maxMembers":(\d+)', r'memberLimit\\":(\d+)', r'"memberLimit":(\d+)'],
        flags=re.IGNORECASE,
    )

    is_sub_account = None
    if is_master_match is not None:
        is_sub_account = (is_master_match.lower() != "true")
    elif is_sub_account_match is not None:
        is_sub_account = (is_sub_account_match.lower() == "true")

    free_slots = None
    try:
        free_slots = int(free_slots_direct) if free_slots_direct else None
    except (ValueError, TypeError):
        pass
    if free_slots is None:
        try:
            mc = int(members_count) if members_count else None
            mm = int(max_members) if max_members else None
            if mc is not None and mm is not None:
                free_slots = max(mm - mc, 0)
        except (ValueError, TypeError):
            pass

    if invite_link:
        invite_link = invite_link.replace("\\/", "/")

    return {
        "loggedIn": logged_in,
        "currentPlan": plan_key,
        "country": country or "Unknown",
        "isRecurring": recurring_match is not None and recurring_match.lower() == "true",
        "isTrialUser": trial_match is not None and trial_match.lower() == "true",
        "isSubAccount": is_sub_account,
        "email": email or "Unknown",
        "inviteLink": invite_link or "",
        "address": address or "",
        "freeSlots": free_slots,
    }


def enrich_family_data(data, family_json):
    """Enrich data with family API response."""
    if not isinstance(family_json, dict):
        return data

    members = family_json.get("members", [])
    if not isinstance(members, list):
        members = []

    logged_member = None
    for member in members:
        if isinstance(member, dict) and member.get("isLoggedInUser") is True:
            logged_member = member
            break

    if logged_member is not None:
        is_master = logged_member.get("isMaster")
        if isinstance(is_master, bool):
            data["isSubAccount"] = not is_master
        is_child = logged_member.get("isChildAccount")
        if isinstance(is_child, bool):
            data["isChildAccount"] = is_child
        member_country = logged_member.get("country")
        if (not data.get("country") or data.get("country") == "Unknown") and member_country:
            data["country"] = str(member_country).upper()

    max_capacity = family_json.get("maxCapacity")
    if max_capacity is not None:
        try:
            data["freeSlots"] = max(int(max_capacity) - len(members), 0)
        except (ValueError, TypeError):
            pass

    family_address = family_json.get("address")
    if family_address:
        data["address"] = str(family_address)

    invite_token = family_json.get("inviteToken")
    if invite_token:
        data["inviteLink"] = f"https://www.spotify.com/family/join/invite/{invite_token}/"

    features = family_json.get("features", [])
    if data.get("currentPlan") in ("unknown", "free"):
        if isinstance(features, list) and ("kids" in features or "genAlpha" in features):
            data["currentPlan"] = "family_premium_v2"
        elif isinstance(members, list) and len(members) > 0:
            data["currentPlan"] = "family_basic"

    return data


def parse_next_payment_date(source):
    """Extract next payment date from subscription management page."""
    normalized = source.replace('\\"', '"').replace("&quot;", '"')
    combined = f"{source}\n{normalized}"
    candidate = _extract_first(
        combined,
        [
            r'next bill[^<]{0,220}?\bon\b\s*([0-9]{1,2}/[0-9]{1,2}/[0-9]{4})',
            r'next payment[^<]{0,220}?\bon\b\s*([0-9]{1,2}/[0-9]{1,2}/[0-9]{4})',
            r'next bill[^<]{0,220}?\bon\b\s*([A-Za-z]{3,9}\s+\d{1,2},\s+\d{4})',
            r'next payment[^<]{0,220}?\bon\b\s*([A-Za-z]{3,9}\s+\d{1,2},\s+\d{4})',
        ],
        flags=re.IGNORECASE,
    )
    if not candidate:
        return None
    candidate = candidate.strip()
    for fmt in ("%m/%d/%Y", "%B %d, %Y", "%b %d, %Y"):
        try:
            return datetime.strptime(candidate, fmt).date()
        except ValueError:
            pass
    return None


# ── Main checker ────────────────────────────────────────────────

def check_spotify_cookie_health(cookie_content: str, session_arg=None) -> SpotifyValidationResult:
    """
    Performs a synchronous health check on Spotify cookies.
    Hits Spotify account overview, profile API, family API to extract full details.
    """
    if not cookie_content or not cookie_content.strip():
        return SpotifyValidationResult(status=PrivatizationStatus.INVALID_FORMAT, message="Cookie content is empty")

    try:
        cookies_dict = parse_spotify_cookie_content_to_dict(cookie_content)
    except Exception as e:
        return SpotifyValidationResult(status=PrivatizationStatus.INVALID_FORMAT, message=f"Failed to parse: {str(e)}")

    if not validate_spotify_cookies(cookies_dict):
        return SpotifyValidationResult(
            status=PrivatizationStatus.FAILURE_INVALID_COOKIE,
            message="Invalid Spotify cookies - missing sp_dc or sp_key",
        )

    logger.info(f"Spotify Health Check: Testing cookies: {list(cookies_dict.keys())}")

    session = requests.Session()
    session.cookies.update(cookies_dict)
    session.headers.update({"Accept-Encoding": "identity"})

    try:
        # Smart delay
        time.sleep(random.uniform(0.1, 0.3))

        # Hit overview page
        overview_resp = None
        last_status = None
        for url in OVERVIEW_URLS:
            try:
                resp = session.get(url, headers=OVERVIEW_HEADERS, timeout=20)
                last_status = resp.status_code
                if resp.status_code in (403, 429):
                    msg = "Rate limited" if resp.status_code == 429 else "Forbidden (403)"
                    return SpotifyValidationResult(status=PrivatizationStatus.FAILURE_NETWORK, message=msg)
                if resp.status_code == 200:
                    overview_resp = resp
                    break
            except requests.exceptions.Timeout:
                return SpotifyValidationResult(status=PrivatizationStatus.FAILURE_NETWORK, message="Connection timeout")
            except Exception as e:
                return SpotifyValidationResult(status=PrivatizationStatus.FAILURE_NETWORK, message=f"Connection error: {str(e)[:80]}")

        if overview_resp is None:
            return SpotifyValidationResult(
                status=PrivatizationStatus.FAILURE_NETWORK,
                message=f"Overview page failed (Status: {last_status})",
            )

        data = parse_overview_data(overview_resp.text)
        if not data.get("loggedIn"):
            return SpotifyValidationResult(
                status=PrivatizationStatus.FAILURE_INVALID_COOKIE,
                message="Invalid cookies - not logged in",
            )

        # Profile API for email/country
        time.sleep(random.uniform(0.1, 0.3))
        try:
            profile_resp = session.get(PROFILE_URL, headers=API_HEADERS, timeout=20, allow_redirects=False)
            if profile_resp.status_code == 200:
                try:
                    pj = profile_resp.json()
                    ps = pj.get("profile", {}) if isinstance(pj.get("profile"), dict) else {}
                    pc = ps.get("country") or pj.get("country")
                    pe = ps.get("email") or pj.get("email")
                    if pc:
                        data["country"] = str(pc).upper()
                    if pe:
                        data["email"] = str(pe)
                except Exception:
                    pass
        except Exception:
            pass

        # Family API
        time.sleep(random.uniform(0.1, 0.3))
        try:
            family_resp = session.get(FAMILY_HOME_URL, headers=API_HEADERS, timeout=20, allow_redirects=False)
            if family_resp.status_code == 200:
                try:
                    data = enrich_family_data(data, family_resp.json())
                except Exception:
                    pass
        except Exception:
            pass

        # Subscription management page for next payment
        next_payment = None
        manage_urls = [
            "https://www.spotify.com/us/account/subscription/manage/",
            "https://www.spotify.com/account/subscription/manage/",
        ]
        for manage_url in manage_urls:
            try:
                time.sleep(random.uniform(0.1, 0.2))
                manage_resp = session.get(manage_url, headers=OVERVIEW_HEADERS, timeout=20, allow_redirects=True)
                if manage_resp.status_code == 200 and manage_resp.text:
                    npd = parse_next_payment_date(manage_resp.text)
                    if npd:
                        next_payment = npd.isoformat()
                        data["isRecurring"] = True
                        break
            except Exception:
                pass

        # Build result
        plan_key = data.get("currentPlan", "unknown")
        plan_display = PLAN_NAME_MAP.get(plan_key, "Unknown")
        email = data.get("email", "Unknown")
        country = data.get("country", "Unknown")
        is_sub = data.get("isSubAccount")
        owner = "Yes" if is_sub is False else ("No" if is_sub is True else "N/A")
        free_slots = data.get("freeSlots")
        invite_link = data.get("inviteLink", "")
        address = data.get("address", "")
        is_recurring = data.get("isRecurring", False)
        is_trial = data.get("isTrialUser", False)

        return SpotifyValidationResult(
            status=PrivatizationStatus.SUCCESS,
            message=f"Cookie is healthy - Plan: {plan_display}, Email: {email}",
            details={
                "email": email,
                "plan": plan_display,
                "plan_key": plan_key,
                "country": country,
                "owner": owner,
                "free_slots": free_slots,
                "invite_link": invite_link,
                "address": address,
                "is_recurring": is_recurring,
                "is_trial": is_trial,
                "next_payment": next_payment,
            },
        )

    except Exception as e:
        return SpotifyValidationResult(status=PrivatizationStatus.FAILURE_NETWORK, message=f"Unexpected error: {str(e)}")
    finally:
        try:
            session.close()
        except Exception:
            pass
