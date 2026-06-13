"""
Hotstar Cookie Health Checker — ported from terminal main_fixed.py.
Uses curl_cffi with TLS impersonation for session validation.
"""

import logging
import json
import base64
import random
import time
from dataclasses import dataclass, field
from typing import Dict, Any, List
from datetime import datetime, timezone

from curl_cffi import requests as curl_requests

from .enums import PrivatizationStatus

logger = logging.getLogger(__name__)

PAYMENT_DETAILS_API = "https://www.hotstar.com/api/internal/bff/v2/slugs/in/payment/details"
SESSION_CHECK_API = "https://www.hotstar.com/api/internal/bff/v2/start"

BASE_HEADERS = {
    'accept': 'application/json, text/plain, */*',
    'accept-language': 'eng',
    'content-type': 'application/json',
    'sec-ch-ua-mobile': '?0',
    'sec-ch-ua-platform': '"Windows"',
    'sec-fetch-dest': 'empty',
    'sec-fetch-mode': 'cors',
    'sec-fetch-site': 'same-site',
}

USER_AGENTS = [
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36',
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/127.0.0.0 Safari/537.36',
    'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/127.0.0.0 Safari/537.36',
]

PLAN_MAPPING = {
    'HotstarPremiumSmp': 'Premium Annual Plan',
    'HotstarPremium': 'Premium',
    'HotstarMobile': 'Mobile Plan',
    'SingleDevice': 'Single Device Plan',
    'HotstarSuperVip': 'Super VIP',
    'HotstarVip': 'VIP',
    'HotstarSuper': 'HotstarSuper',
    'HotstarBundle': 'HotstarBundle',
}


@dataclass
class HotstarValidationResult:
    """Result of a Hotstar cookie validation check."""
    status: PrivatizationStatus
    message: str
    details: Dict[str, Any] = field(default_factory=dict)


def parse_hotstar_cookie_content_to_dict(cookie_content: str) -> dict:
    """Parse Hotstar cookie content from Netscape format."""
    cookies = {}
    if not cookie_content or not cookie_content.strip():
        return cookies

    for line in cookie_content.strip().split('\n'):
        line = line.strip()
        if not line:
            continue
        if line.startswith('#HttpOnly_'):
            line = line[10:]
        elif line.startswith('#'):
            continue
        parts = line.split('\t')
        if len(parts) >= 7:
            cookies[parts[5]] = parts[6]

    return cookies


def validate_hotstar_cookies(cookie_dict: dict) -> bool:
    """Check that essential Hotstar cookies are present."""
    if not cookie_dict:
        return False
    return bool(cookie_dict.get('userUP') or cookie_dict.get('sessionUserUP'))


def _decode_jwt(token: str) -> dict:
    """Decode JWT token payload."""
    try:
        parts = token.split('.')
        if len(parts) != 3:
            return None
        payload = parts[1]
        padding = 4 - len(payload) % 4
        if padding != 4:
            payload += '=' * padding
        return json.loads(base64.urlsafe_b64decode(payload))
    except Exception:
        return None


def _extract_user_from_jwt(token: str) -> dict:
    """Extract user info from JWT sub field."""
    decoded = _decode_jwt(token)
    if not decoded or 'sub' not in decoded:
        return None
    try:
        return json.loads(decoded['sub'])
    except Exception:
        return None


def _calculate_days_remaining(date_str: str):
    """Calculate days remaining from ISO date or 'dd Mon, YYYY' format."""
    try:
        if 'T' in date_str:
            expiry = datetime.fromisoformat(date_str.replace('Z', '+00:00'))
        else:
            expiry = datetime.strptime(date_str, "%d %b, %Y").replace(tzinfo=timezone.utc)
        return max(0, (expiry - datetime.now(timezone.utc)).days)
    except Exception:
        return None


def _format_duration_tag(days_remaining):
    """Format days remaining into a short tag like P3months, P1year."""
    if days_remaining is None:
        return ""
    if days_remaining >= 335:
        years = round(days_remaining / 365)
        return f"P{years}year" if years == 1 else f"P{years}years"
    elif days_remaining >= 25:
        months = round(days_remaining / 30)
        return f"P{months}month" if months == 1 else f"P{months}months"
    else:
        return f"P{days_remaining}days"


def _parse_date_range(date_str):
    """Parse 'dd Mon, YYYY to dd Mon, YYYY' format."""
    try:
        if ' to ' in date_str:
            start_str, end_str = date_str.split(' to ')
            start = datetime.strptime(start_str.strip(), "%d %b, %Y")
            end = datetime.strptime(end_str.strip(), "%d %b, %Y")
            return start, end
    except Exception:
        pass
    return None, None


def check_hotstar_cookie_health(cookie_content: str, session_arg=None) -> HotstarValidationResult:
    """
    Performs a synchronous health check on Hotstar cookies.
    Validates session, extracts subscription info from JWT + payment API.
    """
    if not cookie_content or not cookie_content.strip():
        return HotstarValidationResult(status=PrivatizationStatus.INVALID_FORMAT, message="Cookie content is empty")

    try:
        cookies_dict = parse_hotstar_cookie_content_to_dict(cookie_content)
    except Exception as e:
        return HotstarValidationResult(status=PrivatizationStatus.INVALID_FORMAT, message=f"Failed to parse: {str(e)}")

    if not validate_hotstar_cookies(cookies_dict):
        return HotstarValidationResult(
            status=PrivatizationStatus.FAILURE_INVALID_COOKIE,
            message="Invalid Hotstar cookies - missing userUP token",
        )

    # Extract user from JWT
    user_token = cookies_dict.get('userUP') or cookies_dict.get('sessionUserUP', '')
    user_data = _extract_user_from_jwt(user_token)
    if not user_data:
        return HotstarValidationResult(
            status=PrivatizationStatus.FAILURE_INVALID_COOKIE,
            message="Invalid JWT token",
        )

    pid = user_data.get('pId', user_data.get('dpid', 'Unknown'))
    name = user_data.get('name', 'N/A')
    phone = user_data.get('phone', 'N/A')
    user_type = user_data.get('type', 'N/A')
    country = user_data.get('countryCode', 'N/A')

    logger.info(f"Hotstar Health Check: PID={pid}, Name={name}")

    try:
        # Session validation
        time.sleep(random.uniform(0.1, 0.3))
        device_id = cookies_dict.get('deviceId', '')
        session_headers = BASE_HEADERS.copy()
        session_headers.update({
            'user-agent': random.choice(USER_AGENTS),
            'x-hs-usertoken': user_token,
            'x-hs-device-id': device_id,
            'x-country-code': 'in',
            'x-hs-platform': 'web',
        })

        session_payload = '/in/home?client_capabilities=%7B%22ads%22%3A%5B%22non_ssai%22%5D%2C%22audio_channel%22%3A%5B%22stereo%22%5D%2C%22container%22%3A%5B%22fmp4%22%2C%22fmp4br%22%2C%22ts%22%5D%2C%22dvr%22%3A%5B%22short%22%5D%2C%22dynamic_range%22%3A%5B%22sdr%22%5D%2C%22encryption%22%3A%5B%22widevine%22%2C%22plain%22%5D%2C%22ladder%22%3A%5B%22web%22%2C%22tv%22%2C%22phone%22%5D%2C%22package%22%3A%5B%22dash%22%2C%22hls%22%5D%2C%22resolution%22%3A%5B%22sd%22%2C%22hd%22%2C%22fhd%22%5D%2C%22video_codec%22%3A%5B%22h264%22%5D%2C%22video_codec_non_secure%22%3A%5B%22h264%22%5D%7D&drm_parameters=%7B%22hdcp_version%22%3A%5B%22HDCP_V2_2%22%5D%2C%22widevine_security_level%22%3A%5B%22SW_SECURE_DECODE%22%5D%2C%22playready_security_level%22%3A%5B%5D%7D'

        try:
            session_check = curl_requests.post(
                SESSION_CHECK_API,
                headers=session_headers,
                cookies=cookies_dict,
                data=session_payload,
                timeout=12,
                impersonate="chrome110"
            )
        except curl_requests.exceptions.Timeout:
            return HotstarValidationResult(status=PrivatizationStatus.FAILURE_NETWORK, message="Connection timeout")
        except Exception as e:
            return HotstarValidationResult(status=PrivatizationStatus.FAILURE_NETWORK, message=f"Connection error: {str(e)[:80]}")

        # Check for HTML (redirected to login = logged out)
        ct = session_check.headers.get('content-type', '')
        if 'text/html' in ct:
            return HotstarValidationResult(status=PrivatizationStatus.FAILURE_INVALID_COOKIE, message="Session logged out")

        if session_check.status_code in (200, 401, 403):
            try:
                sd = session_check.json()
                error_code = sd.get('error', {}).get('error_code', '')
                if error_code == 'ERR_UM_USER_LOGGED_OUT':
                    return HotstarValidationResult(status=PrivatizationStatus.FAILURE_INVALID_COOKIE, message="Session logged out")

                # Check for GUEST login
                logout_data = sd.get('error', {}).get('widget_wrapper', {}).get('widget', {}).get('data', {})
                if logout_data:
                    button = logout_data.get('button', {})
                    if button:
                        for action in button.get('actions', {}).get('on_click', []):
                            if action.get('logout', {}).get('login_status', '') == 'GUEST':
                                return HotstarValidationResult(status=PrivatizationStatus.FAILURE_INVALID_COOKIE, message="Guest session - not logged in")
            except json.JSONDecodeError:
                return HotstarValidationResult(status=PrivatizationStatus.FAILURE_INVALID_COOKIE, message="Session logged out - invalid response")

        # Extract subscriptions from JWT
        subscriptions = user_data.get('subscriptions', {}).get('in', {})
        active_plans = []
        transactions = []

        for sub_name, sub_data in subscriptions.items():
            status = sub_data.get('status', '')
            expiry_str = sub_data.get('expiry', '')

            if status == 'S' and expiry_str:
                days_remaining = _calculate_days_remaining(expiry_str)
                if days_remaining and days_remaining > 0:
                    plan_name = PLAN_MAPPING.get(sub_name, sub_name)
                    active_plans.append(plan_name)

                    try:
                        expiry_date = datetime.fromisoformat(expiry_str.replace('Z', '+00:00'))
                        end_date_fmt = expiry_date.strftime("%d-%m-%Y")
                    except Exception:
                        end_date_fmt = 'N/A'

                    transactions.append({
                        'plan_name': plan_name,
                        'end_date': end_date_fmt,
                        'days_remaining': days_remaining,
                        'amount': 'N/A',
                        'payment_method': 'N/A',
                    })

        # Try payment API for enhanced details
        time.sleep(random.uniform(0.1, 0.3))
        pay_headers = BASE_HEADERS.copy()
        pay_headers.update({
            'user-agent': random.choice(USER_AGENTS),
            'origin': 'https://www.hotstar.com',
            'referer': 'https://www.hotstar.com/in/settings',
            'x-country-code': 'in',
            'x-hs-accept-language': 'eng',
            'x-hs-device-id': device_id,
            'x-hs-platform': 'web',
            'x-hs-usertoken': user_token,
        })

        try:
            pay_resp = curl_requests.get(
                PAYMENT_DETAILS_API,
                headers=pay_headers,
                cookies=cookies_dict,
                timeout=8,
                impersonate="chrome110"
            )

            if pay_resp.status_code == 200 and 'application/json' in pay_resp.headers.get('content-type', ''):
                try:
                    pay_data = pay_resp.json()
                    rows = (pay_data.get('success', {})
                            .get('page', {})
                            .get('spaces', {})
                            .get('content', {})
                            .get('widget_wrappers', [{}])[0]
                            .get('widget', {})
                            .get('data', {})
                            .get('rows', []))

                    enhanced = []
                    i = 0
                    while i < len(rows):
                        plan_details = rows[i].get('title', [])
                        if not plan_details:
                            i += 1
                            continue

                        amount_info = rows[i+1].get('title', ['N/A'])[0] if i+1 < len(rows) else 'N/A'
                        pm_desc = rows[i+1].get('desc', ['N/A'])[0] if i+1 < len(rows) else 'N/A'
                        payment_method = pm_desc.replace('Via ', '').strip() if 'Via ' in pm_desc else pm_desc

                        desc_lines = rows[i].get('desc', [])
                        date_range = desc_lines[0] if desc_lines else 'N/A'

                        start_date = 'N/A'
                        end_date = 'N/A'
                        days_remaining = None

                        if ' to ' in date_range:
                            s, e = _parse_date_range(date_range)
                            if s and e:
                                start_date = s.strftime("%d-%m-%Y")
                                end_date = e.strftime("%d-%m-%Y")
                                end_str = date_range.split(' to ')[1].strip()
                                days_remaining = _calculate_days_remaining(end_str)

                        if days_remaining and days_remaining > 0:
                            enhanced.append({
                                'plan_name': plan_details[0],
                                'start_date': start_date,
                                'end_date': end_date,
                                'days_remaining': days_remaining,
                                'amount': amount_info,
                                'payment_method': payment_method,
                            })

                        i += 4

                    if enhanced:
                        transactions = enhanced
                except Exception:
                    pass
        except Exception:
            pass

        if not active_plans:
            active_plans = ['Free']

        # Build the primary plan display and duration tag
        primary_plan = active_plans[0]
        primary_days = transactions[0]['days_remaining'] if transactions else None
        duration_tag = _format_duration_tag(primary_days)

        return HotstarValidationResult(
            status=PrivatizationStatus.SUCCESS,
            message=f"Cookie is healthy - Plan: {primary_plan}",
            details={
                'pid': pid,
                'name': name,
                'phone': phone,
                'user_type': user_type,
                'country': country.upper() if country else 'N/A',
                'plans': active_plans,
                'plan_display': ', '.join(active_plans),
                'duration_tag': duration_tag,
                'transactions': transactions,
            },
        )

    except Exception as e:
        return HotstarValidationResult(status=PrivatizationStatus.FAILURE_NETWORK, message=f"Unexpected error: {str(e)}")
