import logging
import json
import random
import time
import urllib.parse
from dataclasses import dataclass, field
from typing import Dict, Any
from datetime import datetime, timezone

from curl_cffi import requests as curl_requests

from .enums import PrivatizationStatus

logger = logging.getLogger(__name__)

# ChatGPT API endpoints
SESSION_URL = "https://chatgpt.com/api/auth/session"
VERIFY_URL = "https://chatgpt.com/backend-api/settings/user"
SUBSCRIPTION_URL = "https://chatgpt.com/backend-api/subscriptions"

@dataclass
class ChatGPTValidationResult:
    """Dataclass to hold the detailed result of a ChatGPT cookie validation check."""
    status: PrivatizationStatus
    message: str
    details: Dict[str, Any] = field(default_factory=dict)

BASE_HEADERS = {
    'Accept': 'application/json, text/plain, */*',
    'Accept-Language': 'en-US,en;q=0.9',
    'Accept-Encoding': 'gzip, deflate, br',
    'Referer': 'https://chatgpt.com/',
    'Sec-Ch-Ua': '"Not/A)Brand";v="8", "Chromium";v="126", "Google Chrome";v="126"',
    'Sec-Ch-Ua-Mobile': '?0',
    'Sec-Ch-Ua-Platform': '"Windows"',
    'Sec-Fetch-Dest': 'empty',
    'Sec-Fetch-Mode': 'cors',
    'Sec-Fetch-Site': 'same-origin',
    'Cache-Control': 'no-cache',
    'DNT': '1',
    'Connection': 'keep-alive',
    'OAI-Language': 'en-US',
    'Pragma': 'no-cache',
}

USER_AGENTS = [
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/127.0.0.0 Safari/537.36',
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36',
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:126.0) Gecko/20100101 Firefox/126.0',
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/127.0.0.0 Safari/537.36 Edg/127.0.0.0',
    'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/127.0.0.0 Safari/537.36',
]

PLAN_MAPPING = {
    'free': 'Free',
    'plus': 'ChatGPT Plus',
    'team': 'ChatGPT Team',
    'pro': 'ChatGPT Pro',
    'go': 'ChatGPT Go',
    'chatgptgo': 'ChatGPT Go',
    'halfpro': 'ChatGPT HalfPro',
    'enterprise': 'ChatGPT Enterprise',
}


def parse_chatgpt_cookie_content_to_dict(cookie_content: str) -> dict:
    """Parse ChatGPT cookie content from Netscape or JSON format."""
    cookies = {}
    if not cookie_content or not cookie_content.strip():
        return cookies

    # Try JSON format first
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
        json_content = '\n'.join(json_lines).strip()
        if json_content.startswith('[') and json_content.endswith(']'):
            try:
                json_cookies = json.loads(json_content)
                for cookie in json_cookies:
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


def validate_chatgpt_cookies(cookie_dict: dict) -> bool:
    """Validate that the cookie dictionary contains essential ChatGPT cookies."""
    if not cookie_dict:
        return False
    cookie_names_lower = [name.lower() for name in cookie_dict.keys()]
    essential_cookies = ['__secure-next-auth.session-token', '_cfuvid', 'cf_clearance']
    has_session = any(
        any(ess in name for ess in essential_cookies)
        for name in cookie_names_lower
    )
    has_values = any(bool(str(v).strip()) for v in cookie_dict.values() if v is not None)
    return has_session and has_values


def _check_subscription_endpoint(headers, cookies_dict, access_token, account_id):
    """Check subscription endpoint for detailed plan info."""
    try:
        sub_headers = headers.copy()
        sub_headers['Authorization'] = f'Bearer {access_token}'
        subscription_url = f"{SUBSCRIPTION_URL}?account_id={account_id}"

        time.sleep(random.uniform(0.1, 0.3))
        response = curl_requests.get(
            subscription_url,
            headers=sub_headers,
            cookies=cookies_dict,
            timeout=15,
            impersonate="chrome110"
        )

        if response.status_code == 200:
            data = response.json()
            plan_type = data.get('plan_type', '').lower()

            plan_mapping = {
                'plus': 'ChatGPT Plus',
                'team': 'ChatGPT Team',
                'pro': 'ChatGPT Pro',
                'go': 'ChatGPT Go',
                'chatgptgo': 'ChatGPT Go',
                'halfpro': 'ChatGPT HalfPro',
                'enterprise': 'ChatGPT Enterprise',
            }

            subscription_info = {
                'plan': plan_mapping.get(plan_type, 'Free'),
                'billing_period': data.get('billing_period'),
                'will_renew': data.get('will_renew'),
                'billing_currency': data.get('billing_currency'),
                'active_until': data.get('active_until'),
                'expires': None,
            }

            if data.get('active_until'):
                try:
                    expires_date = datetime.fromisoformat(data['active_until'].replace('Z', '+00:00'))
                    subscription_info['expires'] = expires_date.strftime('%d-%m-%Y')
                except Exception:
                    subscription_info['expires'] = data.get('active_until')

            return subscription_info

    except Exception as e:
        logger.debug(f"Subscription endpoint failed: {e}")
    return None


def _check_app_store_subscription(headers, cookies_dict, access_token):
    """Check app store subscription status."""
    try:
        sub_headers = headers.copy()
        sub_headers['Authorization'] = f'Bearer {access_token}'
        url = f"{SUBSCRIPTION_URL}/has_app_store_subscription_in_billing_retry"

        time.sleep(random.uniform(0.1, 0.3))
        response = curl_requests.get(
            url,
            headers=sub_headers,
            cookies=cookies_dict,
            timeout=15,
            impersonate="chrome110"
        )

        if response.status_code == 200:
            data = response.json()
            if not data.get('value', False):
                return 'Free'

    except Exception as e:
        logger.debug(f"App store endpoint failed: {e}")
    return None


def _check_user_settings(headers, cookies_dict, access_token):
    """Check user settings for plan indicators."""
    try:
        sub_headers = headers.copy()
        sub_headers['Authorization'] = f'Bearer {access_token}'

        time.sleep(random.uniform(0.1, 0.3))
        response = curl_requests.get(
            VERIFY_URL,
            headers=sub_headers,
            cookies=cookies_dict,
            timeout=15,
            impersonate="chrome110"
        )

        if response.status_code == 200:
            data = response.json()
            features = data.get('features', [])

            team_features = ['team', 'workspace', 'organization']
            if any(f for f in features if any(tw in str(f).lower() for tw in team_features)):
                return 'ChatGPT Team'

            plus_features = ['plus', 'premium', 'paid']
            if any(f for f in features if any(pw in str(f).lower() for pw in plus_features)):
                return 'ChatGPT Plus'

            if 'account_plan' in data and data['account_plan']:
                plan_title = data['account_plan'].get('title', 'Free')
                if plan_title and plan_title.lower() != 'free':
                    return plan_title

    except Exception as e:
        logger.debug(f"Settings check failed: {e}")
    return None


def get_subscription_plan(headers, access_token, cookies_dict, user_info):
    """Enhanced subscription plan detection — matches terminal checker logic."""
    default_info = {
        'plan': 'Free',
        'billing_period': None,
        'will_renew': None,
        'billing_currency': None,
        'expires': None,
        'active_until': None,
    }

    try:
        account_data = user_info.get('account', {})
        account_id = account_data.get('id') if account_data else None

        # Method 1: Subscription endpoint
        if account_id:
            sub_info = _check_subscription_endpoint(headers, cookies_dict, access_token, account_id)
            if sub_info and isinstance(sub_info, dict):
                return sub_info
            elif sub_info and sub_info != 'Free':
                default_info['plan'] = sub_info
                return default_info

        # Method 2: planType in session response
        if account_data and 'planType' in account_data:
            plan_type = account_data.get('planType', '').lower()
            if 'go' in plan_type or plan_type == 'chatgptgo':
                default_info['plan'] = 'ChatGPT Go'
                return default_info
            elif plan_type in PLAN_MAPPING:
                default_info['plan'] = PLAN_MAPPING[plan_type]
                return default_info

        # Method 3: App store subscription
        plan = _check_app_store_subscription(headers, cookies_dict, access_token)
        if plan:
            default_info['plan'] = plan
            return default_info

        # Method 4: User settings
        plan = _check_user_settings(headers, cookies_dict, access_token)
        if plan and plan != 'Free':
            default_info['plan'] = plan
            return default_info

        return default_info

    except Exception as e:
        logger.error(f"Plan detection failed: {e}")
        return default_info


def check_chatgpt_cookie_health(cookie_content: str, session=None) -> ChatGPTValidationResult:
    """
    Performs a synchronous health check on ChatGPT cookies.
    Uses curl_cffi with TLS fingerprint impersonation to bypass Cloudflare.
    """
    if not cookie_content or not cookie_content.strip():
        return ChatGPTValidationResult(status=PrivatizationStatus.INVALID_FORMAT, message="Cookie content is empty")

    try:
        cookies_dict = parse_chatgpt_cookie_content_to_dict(cookie_content)
    except Exception as e:
        return ChatGPTValidationResult(status=PrivatizationStatus.INVALID_FORMAT, message=f"Failed to parse: {str(e)}")

    if not validate_chatgpt_cookies(cookies_dict):
        return ChatGPTValidationResult(status=PrivatizationStatus.FAILURE_INVALID_COOKIE, message="Invalid ChatGPT cookies - missing session tokens")

    logger.info(f"ChatGPT Health Check: Testing cookies: {list(cookies_dict.keys())}")

    try:
        headers = BASE_HEADERS.copy()
        headers['User-Agent'] = random.choice(USER_AGENTS)

        # Smart delay
        time.sleep(random.uniform(0.1, 0.3))

        # Session request
        try:
            session_response = curl_requests.get(
                SESSION_URL,
                headers=headers,
                cookies=cookies_dict,
                timeout=15,
                impersonate="chrome110"
            )
        except curl_requests.exceptions.Timeout:
            return ChatGPTValidationResult(status=PrivatizationStatus.FAILURE_NETWORK, message="Connection timeout")
        except Exception as e:
            return ChatGPTValidationResult(status=PrivatizationStatus.FAILURE_NETWORK, message=f"Connection error: {str(e)[:80]}")

        if session_response.status_code == 403:
            return ChatGPTValidationResult(status=PrivatizationStatus.FAILURE_INVALID_COOKIE, message="Invalid Session (403 Forbidden)")
        if session_response.status_code == 429:
            return ChatGPTValidationResult(status=PrivatizationStatus.FAILURE_NETWORK, message="Rate limited")
        if session_response.status_code != 200:
            return ChatGPTValidationResult(status=PrivatizationStatus.FAILURE_NETWORK, message=f"Session failed (Status: {session_response.status_code})")

        try:
            user_info = session_response.json()
            access_token = user_info.get("accessToken")

            if not access_token or user_info.get("error"):
                return ChatGPTValidationResult(status=PrivatizationStatus.FAILURE_INVALID_COOKIE, message=user_info.get('error', 'No access token'))

            # Verify the token works
            time.sleep(random.uniform(0.1, 0.3))
            verify_headers = headers.copy()
            verify_headers['Authorization'] = f'Bearer {access_token}'

            try:
                verify_response = curl_requests.get(
                    VERIFY_URL,
                    headers=verify_headers,
                    cookies=cookies_dict,
                    timeout=15,
                    impersonate="chrome110"
                )
            except Exception as e:
                return ChatGPTValidationResult(status=PrivatizationStatus.FAILURE_NETWORK, message=f"Verification failed: {str(e)[:80]}")

            if verify_response.status_code != 200:
                msg = f"Token verification failed (Status: {verify_response.status_code})"
                if verify_response.status_code == 429:
                    msg = "Rate limited during verification"
                return ChatGPTValidationResult(status=PrivatizationStatus.FAILURE_INVALID_COOKIE, message=msg)

            # Extract user data
            user = user_info.get('user', {})
            account = user_info.get('account', {})
            email = user.get('email', 'Unknown')
            name = user.get('name', 'N/A')
            user_id = user.get('id', 'Unknown')
            plan_type = account.get('planType', 'free')
            structure = account.get('structure', 'personal')

            # Get subscription plan
            subscription_info = get_subscription_plan(headers, access_token, cookies_dict, user_info)

            plan = subscription_info.get('plan', 'Free') if isinstance(subscription_info, dict) else 'Free'

            # Determine actual plan type for identifier
            actual_plan_type = plan_type
            plan_lower = plan.lower()
            if 'plus' in plan_lower:
                actual_plan_type = 'plus'
            elif 'pro' in plan_lower and 'half' not in plan_lower:
                actual_plan_type = 'pro'
            elif 'halfpro' in plan_lower:
                actual_plan_type = 'halfpro'
            elif 'team' in plan_lower:
                actual_plan_type = 'team'
            elif 'go' in plan_lower:
                actual_plan_type = 'go'
            elif plan_lower == 'free':
                actual_plan_type = 'free'

            # Plan identifier
            plan_id_map = {
                'plus': 'chatgptplusplan', 'team': 'chatgptteamplan',
                'pro': 'chatgptproplan', 'halfpro': 'chatgpthalfproplan',
                'go': 'chatgptgoplan', 'free': 'chatgptfreeplan',
            }
            plan_identifier = plan_id_map.get(actual_plan_type, f'chatgpt{actual_plan_type}plan')

            # Days remaining
            days_remaining = None
            if isinstance(subscription_info, dict) and subscription_info.get('active_until'):
                try:
                    expiry = datetime.fromisoformat(subscription_info['active_until'].replace('Z', '+00:00'))
                    days_remaining = max(0, (expiry - datetime.now(timezone.utc)).days)
                except Exception:
                    pass

            # Billing details
            billing_period = 'N/A'
            will_renew = 'N/A'
            billing_currency = 'N/A'
            expires = 'N/A'
            if isinstance(subscription_info, dict):
                billing_period = subscription_info.get('billing_period') or 'N/A'
                wr = subscription_info.get('will_renew')
                will_renew = str(wr) if wr is not None else 'N/A'
                billing_currency = subscription_info.get('billing_currency') or 'N/A'
                expires = subscription_info.get('expires') or 'N/A'

            return ChatGPTValidationResult(
                status=PrivatizationStatus.SUCCESS,
                message=f"Cookie is healthy - Plan: {plan}, Email: {email}",
                details={
                    'email': email,
                    'name': name,
                    'user_id': user_id,
                    'plan': plan,
                    'plan_type': actual_plan_type,
                    'structure': structure,
                    'plan_identifier': plan_identifier,
                    'billing_period': billing_period,
                    'will_renew': will_renew,
                    'billing_currency': billing_currency,
                    'expires': expires,
                    'days_remaining': days_remaining,
                    'subscription_info': subscription_info,
                }
            )

        except json.JSONDecodeError:
            return ChatGPTValidationResult(status=PrivatizationStatus.FAILURE_INVALID_COOKIE, message="Invalid JSON response")

    except Exception as e:
        return ChatGPTValidationResult(status=PrivatizationStatus.FAILURE_NETWORK, message=f"Unexpected error: {str(e)}")
