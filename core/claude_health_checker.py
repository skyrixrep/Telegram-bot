"""
Claude AI Cookie Health Checker — ported from terminal main_classy.py.
Uses curl_cffi with TLS fingerprint impersonation to bypass Cloudflare.
"""

import logging
import json
import random
import time
from dataclasses import dataclass, field
from typing import Dict, Any
from datetime import datetime

from curl_cffi import requests as curl_requests

from .enums import PrivatizationStatus

logger = logging.getLogger(__name__)

@dataclass
class ClaudeValidationResult:
    """Dataclass to hold the detailed result of a Claude AI cookie validation check."""
    status: PrivatizationStatus
    message: str
    details: Dict[str, Any] = field(default_factory=dict)

BASE_HEADERS = {
    'accept': 'application/json',
    'accept-language': 'en-US,en;q=0.9',
    'cache-control': 'no-cache',
    'pragma': 'no-cache',
    'referer': 'https://claude.ai/',
    'sec-ch-ua': '"Chromium";v="140", "Not A(Brand";v="24", "Microsoft Edge";v="140"',
    'sec-ch-ua-mobile': '?0',
    'sec-ch-ua-platform': '"Windows"',
    'sec-fetch-dest': 'empty',
    'sec-fetch-mode': 'cors',
    'sec-fetch-site': 'same-origin',
}

USER_AGENTS = [
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36 Edg/140.0.0.0',
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/127.0.0.0 Safari/537.36',
    'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/127.0.0.0 Safari/537.36',
]


def parse_claude_cookie_content_to_dict(cookie_content: str) -> dict:
    """Parse Claude AI cookie content from Netscape format."""
    cookie_dict = {}
    if not cookie_content or not cookie_content.strip():
        return cookie_dict

    for line in cookie_content.strip().split('\n'):
        line = line.strip()
        if not line or line.startswith('# '):
            continue
        if line.startswith('#HttpOnly_'):
            line = line[10:]
        parts = line.split('\t')
        if len(parts) >= 7:
            name = parts[5].strip()
            value = parts[6].strip()
            if name and value:
                cookie_dict[name] = value

    return cookie_dict


def validate_claude_cookies(cookie_dict: dict) -> bool:
    """Validate that the cookie dictionary contains essential Claude AI cookies."""
    if not cookie_dict:
        return False
    names_lower = [n.lower() for n in cookie_dict.keys()]
    has_session = any('sessionkey' in n for n in names_lower)
    has_values = any(bool(str(v).strip()) for v in cookie_dict.values() if v is not None)
    return has_session and has_values


def detect_plan_type(org_data):
    """Detect Claude plan type from organization data — matches terminal checker logic."""
    try:
        if not org_data or not isinstance(org_data, dict):
            return 'Unknown'

        rate_limit_tier = org_data.get('rate_limit_tier', '').lower()
        if 'max_20x' in rate_limit_tier:
            return 'Max (20x)'
        if 'max_5x' in rate_limit_tier or 'claude_max' in rate_limit_tier:
            return 'Max (5x)'

        raven_type = org_data.get('raven_type', '')
        if raven_type:
            raven_lower = raven_type.lower()
            if raven_lower == 'enterprise':
                return 'Enterprise'
            if raven_lower == 'team':
                return 'Team'

        capabilities = org_data.get('capabilities', [])
        if isinstance(capabilities, list):
            if 'claude_max' in capabilities:
                return 'Max (5x)'
            if 'raven' in capabilities:
                return 'Team'

        billing_type = org_data.get('billing_type', '').lower()
        if 'stripe' in billing_type or 'subscription' in billing_type:
            return 'Pro'

        return 'Free'

    except Exception:
        return 'Unknown'


def _calculate_days_until(date_str):
    """Calculate days until a given date string (YYYY-MM-DD)."""
    try:
        if not date_str or date_str == 'N/A':
            return None
        target = datetime.strptime(date_str, '%Y-%m-%d')
        delta = target - datetime.now()
        return max(0, delta.days)
    except Exception:
        return None


def check_claude_cookie_health(cookie_content: str, session=None) -> ClaudeValidationResult:
    """
    Performs a synchronous health check on Claude AI cookies.
    Uses curl_cffi with TLS fingerprint impersonation to bypass Cloudflare.
    """
    if not cookie_content or not cookie_content.strip():
        return ClaudeValidationResult(status=PrivatizationStatus.INVALID_FORMAT, message="Cookie content is empty")

    try:
        cookie_dict = parse_claude_cookie_content_to_dict(cookie_content)
    except Exception as e:
        return ClaudeValidationResult(status=PrivatizationStatus.INVALID_FORMAT, message=f"Failed to parse: {str(e)}")

    if not validate_claude_cookies(cookie_dict):
        return ClaudeValidationResult(status=PrivatizationStatus.FAILURE_INVALID_COOKIE, message="Invalid Claude cookies - missing sessionKey")

    logger.info(f"Claude Health Check: Testing cookies: {list(cookie_dict.keys())}")

    try:
        headers = BASE_HEADERS.copy()
        headers['user-agent'] = random.choice(USER_AGENTS)

        # Smart delay
        time.sleep(random.uniform(0.1, 0.3))

        # Organizations API
        try:
            response = curl_requests.get(
                'https://claude.ai/api/organizations',
                headers=headers,
                cookies=cookie_dict,
                timeout=15,
                impersonate="chrome110"
            )
        except curl_requests.exceptions.Timeout:
            return ClaudeValidationResult(status=PrivatizationStatus.FAILURE_NETWORK, message="Connection timeout")
        except Exception as e:
            return ClaudeValidationResult(status=PrivatizationStatus.FAILURE_NETWORK, message=f"Connection error: {str(e)[:80]}")

        if response.status_code in (401, 403):
            return ClaudeValidationResult(status=PrivatizationStatus.FAILURE_INVALID_COOKIE, message="Invalid Session Key")
        if response.status_code == 429:
            return ClaudeValidationResult(status=PrivatizationStatus.FAILURE_NETWORK, message="Rate limited")
        if response.status_code != 200:
            return ClaudeValidationResult(status=PrivatizationStatus.FAILURE_NETWORK, message=f"Status: {response.status_code}")

        # Parse organizations
        try:
            json_data = response.json()
            if not json_data or not isinstance(json_data, list) or len(json_data) == 0:
                return ClaudeValidationResult(status=PrivatizationStatus.FAILURE_INVALID_COOKIE, message="Empty or invalid response")
            org_data = json_data[0]
        except (json.JSONDecodeError, IndexError, TypeError) as e:
            return ClaudeValidationResult(status=PrivatizationStatus.FAILURE_INVALID_COOKIE, message=f"Failed to parse JSON: {str(e)}")

        org_uuid = org_data.get('uuid')
        org_name = org_data.get('name', 'Unknown')
        plan = detect_plan_type(org_data)
        billing_type = org_data.get('billing_type', 'N/A')

        # Bootstrap API for email/name
        time.sleep(random.uniform(0.1, 0.3))
        email = 'Unknown'
        display_name = 'N/A'

        try:
            bootstrap_resp = curl_requests.get(
                'https://claude.ai/api/bootstrap',
                headers=headers,
                cookies=cookie_dict,
                timeout=15,
                impersonate="chrome110"
            )
            if bootstrap_resp.status_code == 200:
                bdata = bootstrap_resp.json()
                account = bdata.get('account', {})
                email = account.get('email_address', 'Unknown')
                display_name = account.get('display_name', '').strip()
                if not display_name:
                    display_name = account.get('full_name', '').strip()
                if not display_name and email != 'Unknown' and '@' in email:
                    display_name = email.split('@')[0]
                if not display_name:
                    display_name = 'N/A'
        except Exception as e:
            logger.debug(f"Bootstrap API failed: {str(e)[:50]}")

        # Subscription details for paid plans
        subscription_details = {}
        next_charge_date = 'N/A'
        days_until_charge = None
        status_val = 'N/A'
        billing_interval = 'N/A'
        payment_info = 'N/A'

        if plan not in ['Free', 'Unknown'] and org_uuid:
            time.sleep(random.uniform(0.1, 0.3))
            try:
                sub_resp = curl_requests.get(
                    f'https://claude.ai/api/organizations/{org_uuid}/subscription_details',
                    headers=headers,
                    cookies=cookie_dict,
                    timeout=15,
                    impersonate="chrome110"
                )
                if sub_resp.status_code == 200:
                    subscription_details = sub_resp.json()
                    next_charge_date = subscription_details.get('next_charge_date', 'N/A')
                    days_until_charge = _calculate_days_until(next_charge_date)
                    status_val = subscription_details.get('status', 'N/A')
                    billing_interval = subscription_details.get('billing_interval', 'N/A')

                    payment_method = subscription_details.get('payment_method', {})
                    if payment_method and isinstance(payment_method, dict):
                        pm_brand = payment_method.get('brand', 'N/A')
                        pm_type = payment_method.get('type', 'N/A')
                        pm_last4 = payment_method.get('last4', '')
                        payment_info = f"{pm_brand} | {pm_type}"
                        if pm_last4:
                            payment_info += f" | ******{pm_last4}"
            except Exception as e:
                logger.debug(f"Subscription API failed: {str(e)[:50]}")

        return ClaudeValidationResult(
            status=PrivatizationStatus.SUCCESS,
            message=f"Cookie is healthy - Plan: {plan}, Email: {email}",
            details={
                'email': email,
                'name': display_name,
                'plan': plan,
                'billing_type': billing_type,
                'org_name': org_name,
                'next_charge_date': next_charge_date,
                'days_until_charge': days_until_charge,
                'status': status_val,
                'billing_interval': billing_interval,
                'payment': payment_info,
                'subscription_details': subscription_details,
            }
        )

    except Exception as e:
        return ClaudeValidationResult(status=PrivatizationStatus.FAILURE_NETWORK, message=str(e))
