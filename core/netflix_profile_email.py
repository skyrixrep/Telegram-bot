"""
Netflix Profile Email Module
Implements:
  - RSA key pre-generation pool (background thread)
  - fetch_profiles()        — scrape profiles from /profiles/manage
  - ale_provision()         — AleProvision GraphQL (Netflix key exchange)
  - encrypt_email()         — JWE / AES-128-GCM email encryption
  - update_profile_email()  — UpdateProfileEmail GraphQL mutation

Integrated into AetherX from Nettrix (telegram_bot.py).
"""

import json
import re
import uuid
import base64
import queue
import threading
import requests
from requests.adapters import HTTPAdapter

from jwcrypto import jwk, jwe
from jwcrypto.common import json_encode
from cryptography.hazmat.primitives.asymmetric import rsa as _rsa, padding as _padding
from cryptography.hazmat.primitives import serialization as _ser, hashes as _hashes
from cryptography.hazmat.backends import default_backend as _backend

import config

# ─── Shared HTTP session with keep-alive ────────────────────────────────────────
_HTTP = requests.Session()
_adapter = HTTPAdapter(pool_connections=10, pool_maxsize=20, max_retries=0)
_HTTP.mount("https://", _adapter)
_HTTP.mount("http://", _adapter)

# ─── RSA-2048 key pre-generation pool ───────────────────────────────────────────
# RSA keygen takes ~0.3–1 s. A background thread keeps a small pool ready so
# ale_provision() can grab a key instantly.
_RSA_POOL: queue.Queue = queue.Queue(maxsize=4)


def _rsa_keygen_worker() -> None:
    """Background daemon that keeps the RSA key pool topped up."""
    while True:
        try:
            if _RSA_POOL.full():
                threading.Event().wait(0.5)
                continue
            key = _rsa.generate_private_key(
                public_exponent=65537, key_size=2048, backend=_backend()
            )
            _RSA_POOL.put(key)
        except Exception:
            threading.Event().wait(1)


def start_rsa_pool() -> None:
    """Start the background RSA key pre-generator. Call once at bot startup."""
    threading.Thread(target=_rsa_keygen_worker, daemon=True).start()


def get_rsa_key():
    """Return a pre-generated RSA-2048 key, or generate one on-demand if pool is empty."""
    try:
        return _RSA_POOL.get_nowait()
    except queue.Empty:
        return _rsa.generate_private_key(
            public_exponent=65537, key_size=2048, backend=_backend()
        )


# ─── Profile fetcher ────────────────────────────────────────────────────────────

def fetch_profiles(cookies: dict) -> list:
    """
    Scrape the Netflix /profiles/manage page and return a list of profile dicts:
      [{"name": str, "guid": str, "owner": bool}, ...]
    Returns an empty list on failure.
    """
    try:
        r = _HTTP.get(
            "https://www.netflix.com/profiles/manage",
            cookies=cookies,
            headers={
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Encoding": "identity",
                "User-Agent": config.USER_AGENT,
                "Referer": "https://www.netflix.com/browse",
                "Upgrade-Insecure-Requests": "1",
            },
            timeout=config.REQUEST_TIMEOUT,
        )
    except Exception:
        return []

    if r.status_code != 200:
        return []

    html = r.text
    profiles: list = []
    seen: set = set()

    # Extract profile names preserving order
    names = list(dict.fromkeys(re.findall(r'"profileName"\s*:\s*"([^"]+)"', html)))

    for name in names:
        # Try finding guid after the name, then before it (Netflix HTML varies)
        m = re.search(
            rf'"profileName"\s*:\s*"{re.escape(name)}"[^{{}}]{{0,300}}"guid"\s*:\s*"([A-Z0-9]{{26}})"',
            html,
        )
        if not m:
            m = re.search(
                rf'"guid"\s*:\s*"([A-Z0-9]{{26}})"[^{{}}]{{0,300}}"profileName"\s*:\s*"{re.escape(name)}"',
                html,
            )
        if m:
            guid = m.group(1)
            if guid not in seen:
                seen.add(guid)
                om = re.search(
                    rf'"guid"\s*:\s*"{guid}"[^{{}}]{{0,400}}"isAccountOwner"\s*:\s*(true|false)',
                    html,
                )
                is_owner = bool(om and om.group(1) == "true")
                profiles.append({"name": name, "guid": guid, "owner": is_owner})

    return profiles


# ─── AleProvision ───────────────────────────────────────────────────────────────

def ale_provision(cookies: dict, profile_guid: str) -> dict:
    """
    Call the Netflix AleProvision GraphQL endpoint to receive a Netflix-wrapped
    AES-128 key.  Returns a dict with keys:
      ale_token, wrapped_key, kid, private_key
    Raises Exception on API error.
    """
    priv = get_rsa_key()
    pub_der = priv.public_key().public_bytes(
        _ser.Encoding.DER, _ser.PublicFormat.SubjectPublicKeyInfo
    )
    # Netflix expects URL-safe base64 WITHOUT padding
    pub_b64 = base64.urlsafe_b64encode(pub_der).rstrip(b"=").decode()

    headers = {
        "accept": "*/*",
        "cache-control": "no-cache",
        "content-type": "application/json",
        "origin": "https://www.netflix.com",
        "pragma": "no-cache",
        "referer": "https://www.netflix.com/",
        "user-agent": config.USER_AGENT,
        "x-netflix.context.app-version": config.NF_EMAIL_APP_VERSION,
        "x-netflix.context.locales": "en-de",
        "x-netflix.context.operation-name": "AleProvision",
        "x-netflix.context.ui-flavor": "akira",
        "x-netflix.request.attempt": "1",
        "x-netflix.request.client.context": json.dumps({"appstate": "foreground"}),
        "x-netflix.request.id": uuid.uuid4().hex,
        "x-netflix.request.toplevel.uuid": str(uuid.uuid4()),
        "x-netflix.request.originating.url": (
            f"https://www.netflix.com/account/profile/newProfileEmail/{profile_guid}"
        ),
        "dnt": "1",
        "sec-fetch-dest": "empty",
        "sec-fetch-mode": "cors",
        "sec-fetch-site": "same-site",
    }
    payload = {
        "operationName": "AleProvision",
        "variables": {
            "keyProvisionReq": {
                "ver":    1,
                "scheme": "A128GCM",
                "type":   "CLCS",
                "keyx": {
                    "scheme": "RSA-OAEP-256",
                    "data":   {"pubkey": pub_b64},
                },
            }
        },
        "extensions": {
            "persistedQuery": {"id": config.NF_EMAIL_ALE_PROVISION_ID, "version": 102}
        },
    }

    r = _HTTP.post(
        config.NF_EMAIL_GRAPHQL_URL,
        headers=headers,
        cookies=cookies,
        json=payload,
        timeout=config.REQUEST_TIMEOUT,
    )
    data = r.json()

    if "errors" in data and data["errors"]:
        raise Exception(f"AleProvision: {data['errors'][0].get('message', 'Unknown')}")

    kp = data["data"]["keyProvision"]
    return {
        "ale_token":   kp["token"],
        "wrapped_key": kp["keyx"]["data"]["wrappedkey"],
        "kid":         kp["keyx"]["kid"],
        "private_key": priv,
    }


# ─── Email encryption ────────────────────────────────────────────────────────────

def encrypt_email(
    email: str,
    kid: str,
    wrapped_key_b64: str,
    rsa_private_key,
) -> str:
    """
    1. RSA-OAEP-SHA256-decrypt the wrapped AES key Netflix gave us.
    2. JWE-encrypt the email with that AES-128-GCM key.
    Returns a compact JWE token string.
    """

    def _b64url_decode(s: str) -> bytes:
        s = s.replace("-", "+").replace("_", "/")
        pad = 4 - len(s) % 4
        if pad != 4:
            s += "=" * pad
        return base64.b64decode(s)

    def _b64url_encode(b: bytes) -> str:
        return base64.urlsafe_b64encode(b).rstrip(b"=").decode()

    aes_key = rsa_private_key.decrypt(
        _b64url_decode(wrapped_key_b64),
        _padding.OAEP(
            mgf=_padding.MGF1(algorithm=_hashes.SHA256()),
            algorithm=_hashes.SHA256(),
            label=None,
        ),
    )
    k = jwk.JWK(kty="oct", k=_b64url_encode(aes_key))
    protected = json_encode({"alg": "dir", "enc": "A128GCM", "kid": kid})
    token = jwe.JWE(
        plaintext=email.encode("utf-8"), protected=protected, recipient=k
    )
    return token.serialize(compact=True)


# ─── UpdateProfileEmail mutation ─────────────────────────────────────────────────

def update_profile_email(
    cookies: dict,
    profile_guid: str,
    encrypted_email: str,
    ale_token: str,
) -> dict:
    """
    Call the Netflix UpdateProfileEmail GraphQL mutation.
    Returns the raw JSON response dict.
    """
    headers = {
        "accept": "*/*",
        "cache-control": "no-cache",
        "content-type": "application/json",
        "origin": "https://www.netflix.com",
        "pragma": "no-cache",
        "referer": "https://www.netflix.com/",
        "user-agent": config.USER_AGENT,
        "x-netflix.context.ale.token": ale_token,
        "x-netflix.context.app-version": config.NF_EMAIL_APP_VERSION,
        "x-netflix.context.locales": "en-de",
        "x-netflix.context.operation-name": "UpdateProfileEmail",
        "x-netflix.context.ui-flavor": "akira",
        "x-netflix.request.attempt": "1",
        "x-netflix.request.client.context": json.dumps({"appstate": "foreground"}),
        "x-netflix.request.id": uuid.uuid4().hex,
        "x-netflix.request.toplevel.uuid": str(uuid.uuid4()),
        "x-netflix.request.originating.url": (
            f"https://www.netflix.com/account/profile/newProfileEmail/{profile_guid}"
        ),
        "dnt": "1",
        "sec-fetch-dest": "empty",
        "sec-fetch-mode": "cors",
        "sec-fetch-site": "same-site",
    }
    payload = {
        "operationName": "UpdateProfileEmail",
        "variables": {
            "profileGuid": profile_guid,
            "email": {"isEncrypted": True, "value": encrypted_email},
            "emailConsentPreferences": {"emailConsent": False},
        },
        "extensions": {
            "persistedQuery": {"id": config.NF_EMAIL_UPDATE_ID, "version": 102}
        },
    }
    r = _HTTP.post(
        config.NF_EMAIL_GRAPHQL_URL,
        headers=headers,
        cookies=cookies,
        json=payload,
        timeout=config.REQUEST_TIMEOUT,
    )
    return r.json()
