"""
Shared Netflix cookie extraction module.
Provides robust cookie parsing from multiple formats: Netscape, JSON, raw key=value.
Ported from TVBot's extraction logic.
"""

import re
import json
import urllib.parse

REQUIRED_COOKIES = ("NetflixId",)
OPTIONAL_COOKIES = ("SecureNetflixId", "nfvdid", "OptanonConsent")
ALL_COOKIE_NAMES = set(REQUIRED_COOKIES + OPTIONAL_COOKIES)
CANONICAL_NAMES = {name.lower(): name for name in ALL_COOKIE_NAMES}


def canonicalize_name(name):
    return CANONICAL_NAMES.get(str(name or "").strip().lower(), str(name or "").strip())


def is_netflix_cookie(domain, name):
    return canonicalize_name(name) in ALL_COOKIE_NAMES or "netflix." in str(domain or "").lower()


def extract_netscape_entries(raw_text):
    entries = []
    for line in raw_text.splitlines():
        if line.startswith("#HttpOnly_"):
            line = line[len("#HttpOnly_"):]
        parts = line.split("\t")
        if len(parts) < 7:
            parts = re.split(r"\s+", line, maxsplit=6)
        if len(parts) < 7:
            continue
        if parts[1].upper() not in ("TRUE", "FALSE"):
            continue
        if parts[3].upper() not in ("TRUE", "FALSE"):
            continue
        if not re.match(r"^-?\d+(?:\.\d+)?$", parts[4].strip()):
            continue
        name = canonicalize_name(parts[5])
        if not is_netflix_cookie(parts[0], name):
            continue
        entries.append({"name": name, "value": parts[6]})
    return entries


def extract_json_entries(content):
    try:
        data = json.loads(content)
    except Exception:
        return []
    if isinstance(data, dict):
        data = data.get("cookies") or data.get("items") or [data]
    if not isinstance(data, list):
        return []
    entries = []
    for cookie in data:
        if not isinstance(cookie, dict):
            continue
        name = canonicalize_name(cookie.get("name", ""))
        if not is_netflix_cookie(cookie.get("domain", ""), name):
            continue
        entries.append({"name": name, "value": cookie.get("value", "")})
    return entries


def extract_raw_entries(raw_text):
    pattern = re.compile(
        r"(?:['\"])?(?P<name>" + "|".join(sorted(ALL_COOKIE_NAMES, key=len, reverse=True)) +
        r")(?:['\"])?\s*(?:=|:)\s*(?P<value>\"[^\"]*\"|'[^']*'|[^;\s]+)", re.IGNORECASE)
    entries = []
    for m in pattern.finditer(raw_text):
        name = canonicalize_name(m.group("name"))
        value = m.group("value")
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        else:
            value = value.rstrip(",")
        entries.append({"name": name, "value": value})
    return entries


def extract_cookie_dict(content):
    """
    Extract Netflix cookies from content in any supported format.
    Tries JSON, Netscape, then raw regex extraction.
    Returns dict of {cookie_name: value} or None if NetflixId not found.
    """
    for extractor in (extract_json_entries, extract_netscape_entries, extract_raw_entries):
        entries = extractor(content)
        if entries:
            break
    else:
        return None
    cookies = {}
    for e in entries:
        if e["name"] not in cookies:
            cookies[e["name"]] = e["value"]
    return cookies if "NetflixId" in cookies else None
