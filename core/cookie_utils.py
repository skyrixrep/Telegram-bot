import logging
import requests
import urllib.parse
import re
from http.cookies import SimpleCookie

logger = logging.getLogger(__name__)

# --- Constants ---
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
PROFILE_GATE_URL = "https://www.netflix.com/browse"

def parse_netscape_to_dict(cookie_content: str) -> dict:
    """
    Parses a Netscape format cookie string into a key-value dictionary.
    Skips comments and malformed lines.
    """
    cookie_dict = {}
    if not cookie_content:
        return cookie_dict
        
    for line in cookie_content.strip().split('\n'):
        line = line.strip()
        if line:
            # Handle Cookie-Editor format with #HttpOnly_ prefix
            line_to_parse = line
            if line.startswith('#HttpOnly_'):
                line_to_parse = line[10:]  # Remove #HttpOnly_ prefix
            elif line.startswith('# '):
                continue  # Skip comment lines that start with "# "
            elif line.startswith('#') and not line.startswith('#HttpOnly_'):
                continue  # Skip other comment lines that don't have the HttpOnly prefix
                
            fields = line_to_parse.split('\t')
            if len(fields) >= 7:
                name = fields[5]
                value = fields[6]
                # Handle URL-encoded values by decoding them
                import urllib.parse
                try:
                    value = urllib.parse.unquote(value)
                except Exception:
                    pass  # Keep original value if decoding fails
                cookie_dict[name] = value
    return cookie_dict

def parse_header_string_to_dict(cookie_content: str) -> dict:
    """
    Parse header string format keeping URL-encoded values intact.
    Netflix requires the raw encoded values!
    """
    cookie_dict = {}
    if not cookie_content:
        return cookie_dict
    
    # Clean the input
    cookie_content = cookie_content.strip().replace('\n', '').replace('\r', '')
    
    # Method 1: Try traditional cookie header parsing (semicolon-separated)
    if ';' in cookie_content:
        try:
            # Manual parsing to avoid automatic URL decoding
            for cookie_pair in cookie_content.split(';'):
                cookie_pair = cookie_pair.strip()
                if '=' in cookie_pair:
                    name, value = cookie_pair.split('=', 1)
                    # Keep the raw value - don't URL decode!
                    cookie_dict[name.strip()] = value.strip()
            if cookie_dict:
                return cookie_dict
        except Exception as e:
            logger.debug(f"Manual cookie parsing failed: {e}")
    
    # Method 2: Handle single cookie format like "NetflixId=value" 
    # Keep the raw URL-encoded value!
    if '=' in cookie_content and ';' not in cookie_content:
        parts = cookie_content.split('=', 1)
        if len(parts) == 2:
            name, value = parts
            # KEEP THE RAW VALUE - Don't URL decode!
            cookie_dict[name.strip()] = value.strip()
            return cookie_dict
    
    # Method 3: Manual regex parsing for complex cases
    cookie_patterns = [
        r'([^=\s;,&]+)=([^;,&]*)',  # Standard cookie pattern
        r'(\w+)=([^&\s]*)',         # Simple key=value
    ]
    
    for pattern in cookie_patterns:
        matches = re.findall(pattern, cookie_content)
        if matches:
            for name, value in matches:
                # Keep raw value - don't URL decode
                cookie_dict[name.strip()] = value.strip()
            break
    
    return cookie_dict

def detect_cookie_format(cookie_content: str) -> str:
    """
    Detect the format of cookie content.
    Returns: 'netscape', 'header', 'json', or 'unknown'
    """
    if not cookie_content.strip():
        return 'unknown'
    
    content = cookie_content.strip()
    
    # JSON format
    if content.startswith('[') or content.startswith('{'):
        return 'json'
    
    # Netscape format
    if ("# Netscape HTTP Cookie File" in content or 
        (".netflix.com" in content and "\t" in content and content.count('\t') >= 6)):
        return 'netscape'
    
    # Header string format (including single cookies and URL-encoded)
    if ('=' in content and 
        ('\t' not in content or content.count('\t') < 3) and  # Not netscape
        not content.startswith('#')):  # Not comment
        return 'header'
    
    return 'unknown'

def parse_cookie_content_to_dict(cookie_content: str) -> dict:
    """
    Universal cookie parser that detects format and parses accordingly.
    Returns a dictionary of cookie name-value pairs.
    """
    format_type = detect_cookie_format(cookie_content)
    
    if format_type == 'netscape':
        return parse_netscape_to_dict(cookie_content)
    elif format_type == 'header':
        return parse_header_string_to_dict(cookie_content)
    elif format_type == 'json':
        # Handle JSON format
        try:
            import json
            data = json.loads(cookie_content)
            cookie_dict = {}
            if isinstance(data, list):
                for cookie in data:
                    if isinstance(cookie, dict) and 'name' in cookie and 'value' in cookie:
                        cookie_dict[cookie['name']] = cookie['value']
            elif isinstance(data, dict):
                # Handle single cookie JSON object
                if 'name' in data and 'value' in data:
                    cookie_dict[data['name']] = data['value']
                # Handle key-value JSON object
                else:
                    cookie_dict = data
            return cookie_dict
        except (json.JSONDecodeError, KeyError) as e:
            logger.error(f"Failed to parse JSON cookie format: {e}")
            return {}
    
    logger.warning(f"Unknown cookie format detected: {format_type}")
    return {}

def serialize_cookie_jar_to_netscape(cookie_jar: requests.cookies.RequestsCookieJar) -> str:
    """
    Converts a requests.cookies.RequestsCookieJar to a Netscape format cookie string.
    This format is required for saving the privatized cookie.
    """
    netscape_lines = ["# Netscape HTTP Cookie File", "# Generated by AetherX ⚡🌟 | Created by SkyriX"]
    
    for cookie in cookie_jar:
        # We only care about essential Netflix cookies for re-use.
        if cookie.name not in ['NetflixId', 'SecureNetflixId']:
            continue

        domain = cookie.domain if cookie.domain.startswith('.') else '.' + cookie.domain
        
        # Create a standardized timestamp for expiry
        expires_ts = str(cookie.expires) if cookie.expires else "1735689600"  # ~2025

        line = "\t".join([
            domain,
            "TRUE",  # include subdomains
            cookie.path,
            "TRUE" if cookie.secure else "FALSE",
            expires_ts,
            cookie.name,
            cookie.value
        ])
        netscape_lines.append(line)
    
    return "\n".join(netscape_lines) + "\n"

def validate_netflix_cookies(cookie_dict: dict) -> bool:
    """
    Validate that the cookie dictionary contains essential Netflix cookies.
    """
    if not cookie_dict:
        return False
    
    # Check for essential Netflix cookies (case-insensitive)
    cookie_names_lower = [name.lower() for name in cookie_dict.keys()]
    
    # Must have at least NetflixId
    has_netflix_id = any('netflixid' in name for name in cookie_names_lower)
    
    # Check that values are not empty
    has_valid_values = any(bool(str(value).strip()) for value in cookie_dict.values() if value is not None)
    
    return has_netflix_id and has_valid_values

def extract_cookie_info(cookie_content: str) -> dict:
    """
    Extract useful information from cookie content for debugging/logging.
    """
    format_type = detect_cookie_format(cookie_content)
    cookie_dict = parse_cookie_content_to_dict(cookie_content)
    
    info = {
        'format': format_type,
        'cookie_count': len(cookie_dict),
        'cookie_names': list(cookie_dict.keys()),
        'has_netflix_id': any('netflixid' in name.lower() for name in cookie_dict.keys()),
        'has_secure_netflix_id': any('securenetflixid' in name.lower() for name in cookie_dict.keys()),
        'is_valid': validate_netflix_cookies(cookie_dict)
    }
    
    return info
