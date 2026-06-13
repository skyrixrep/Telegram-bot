import logging
from dataclasses import dataclass, field
from typing import Dict, Any

import requests

from .cookie_utils import parse_cookie_content_to_dict, validate_netflix_cookies, PROFILE_GATE_URL
from .enums import PrivatizationStatus
from .session_pool import get_new_session_sync as get_new_session

logger = logging.getLogger(__name__)

@dataclass
class ValidationResult:
    """Dataclass to hold the detailed result of a validation check."""
    status: PrivatizationStatus
    message: str
    details: Dict[str, Any] = field(default_factory=dict)

def check_cookie_health(cookie_content: str, session: requests.Session = None) -> ValidationResult:
    """
    Performs a synchronous, read-only health check on any supported cookie format.
    This function does NOT modify the session. It uses cookies for a one-time request.
    """
    active_session = session if session is not None else get_new_session()

    if not cookie_content or not cookie_content.strip():
        return ValidationResult(status=PrivatizationStatus.INVALID_FORMAT, message="Cookie content is empty")

    try:
        cookie_dict = parse_cookie_content_to_dict(cookie_content)
    except Exception as e:
        return ValidationResult(status=PrivatizationStatus.INVALID_FORMAT, message=f"Failed to parse cookie content: {str(e)}")

    if not validate_netflix_cookies(cookie_dict):
        return ValidationResult(status=PrivatizationStatus.FAILURE_INVALID_COOKIE, message="Invalid Netflix cookies")

    logger.info(f"Health Check: Testing cookies: {list(cookie_dict.keys())}")

    try:
        # Use the cookies for this one request only. DO NOT load them into the session jar.
        # Create a session with specific headers to avoid zstd issues
        temp_session = get_new_session()
        response = temp_session.get(
            PROFILE_GATE_URL,
            cookies=cookie_dict,
            timeout=10,
            allow_redirects=False
        )

        if response.status_code == 200:
            logger.info("Health Check: Passed with status 200.")
            return ValidationResult(status=PrivatizationStatus.SUCCESS, message="Cookie is healthy")

        if response.status_code in [301, 302, 307, 308]:
            if 'login' in response.headers.get('Location', '').lower():
                return ValidationResult(status=PrivatizationStatus.FAILURE_LOGIN_REDIRECT, message="Cookie expired (redirected to login)")

        return ValidationResult(status=PrivatizationStatus.FAILURE_INVALID_COOKIE, message=f"Unexpected status code {response.status_code}")

    except Exception as e:
        error_str = str(e).lower()
        # Handle specific zstd compression errors
        if 'zstd' in error_str or 'decompressobj' in error_str:
            logger.warning("Health Check: zstd compression issue detected, retrying with alternative method...")
            try:
                # Retry with a fresh session and no compression
                fallback_session = requests.Session()
                fallback_session.headers.update({
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:109.0) Gecko/20100101 Firefox/115.0",
                    "Accept-Encoding": "identity"  # No compression
                })
                response = fallback_session.get(
                    PROFILE_GATE_URL,
                    cookies=cookie_dict,
                    timeout=15,
                    allow_redirects=False
                )
                
                if response.status_code == 200:
                    return ValidationResult(status=PrivatizationStatus.SUCCESS, message="Cookie is healthy")
                elif response.status_code in [301, 302, 307, 308]:
                    if 'login' in response.headers.get('Location', '').lower():
                        return ValidationResult(status=PrivatizationStatus.FAILURE_LOGIN_REDIRECT, message="Cookie expired (redirected to login)")
                else:
                    return ValidationResult(status=PrivatizationStatus.FAILURE_INVALID_COOKIE, message=f"Status code {response.status_code}")
                    
            except Exception as fallback_error:
                logger.error(f"Fallback health check also failed: {fallback_error}")
                return ValidationResult(status=PrivatizationStatus.FAILURE_NETWORK, message="Compression error - unable to check")
        
        logger.error(f"Health check failed due to a network error: {e}")
        return ValidationResult(status=PrivatizationStatus.FAILURE_NETWORK, message=f"Network error: {str(e)}")