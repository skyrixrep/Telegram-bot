import os
import shutil
import json
import pathlib
import re
import urllib.parse
from http.cookies import SimpleCookie

# --- Constants ---
FORMAT_JSON = "json"
FORMAT_NETSCAPE = "netscape"
FORMAT_HEADER = "header_string"
FORMAT_UNKNOWN = "unknown"
FORMAT_EMPTY = "empty"
FORMAT_FILE_ERROR = "file_error"
DETECTION_ERROR = "detection_error"

# --- Enhanced Format Detection ---
def detect_file_format(file_path: str) -> str:
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            content = f.read().strip()
            if not content: 
                return FORMAT_EMPTY
            
            return detect_file_format_from_content(content)
    except IOError: 
        return FORMAT_FILE_ERROR
    except Exception: 
        return DETECTION_ERROR

def _is_header_string_format(content: str) -> bool:
    """Enhanced detection for header string format including URL-encoded cookies."""
    # Remove whitespace and newlines
    content = content.strip().replace('\n', '').replace('\r', '')
    
    # Check for basic cookie patterns
    has_equals = '=' in content
    has_netflix_keywords = any(keyword in content.lower() for keyword in ['netflixid', 'securenetflixid', 'netflix'])
    
    # Check if it looks like a single-line cookie string (not tab-separated like Netscape)
    is_single_line = '\t' not in content or content.count('\t') < 3
    
    # Check for URL encoding patterns (% followed by hex digits)
    has_url_encoding = bool(re.search(r'%[0-9A-Fa-f]{2}', content))
    
    # Check for cookie separator patterns
    has_cookie_separators = ';' in content or '&' in content
    
    # If it contains Netflix keywords and looks like a header string
    if has_netflix_keywords and has_equals and is_single_line:
        return True
    
    # If it has URL encoding and basic cookie structure
    if has_url_encoding and has_equals and is_single_line:
        return True
        
    # Traditional header string format
    if has_equals and has_cookie_separators and is_single_line and not content.startswith('#'):
        return True
    
    return False

# --- Enhanced Parsers ---
def parse_json_content(json_string: str) -> list[dict]:
    """
    Universal JSON cookie parser that handles multiple formats from any domain.
    Supports:
    - Browser export format (array of objects)
    - EditThisCookie format
    - Cookie-Editor format  
    - Custom JSON formats
    """
    try:
        data = json.loads(json_string)
        cookies = []
        
        # Handle array of cookie objects (most common format)
        if isinstance(data, list):
            for cookie in data:
                if isinstance(cookie, dict):
                    parsed_cookie = _parse_single_json_cookie(cookie)
                    if parsed_cookie:
                        cookies.append(parsed_cookie)
        
        # Handle single cookie object
        elif isinstance(data, dict):
            # Check if it's a single cookie
            if 'name' in data and 'value' in data:
                parsed_cookie = _parse_single_json_cookie(data)
                if parsed_cookie:
                    cookies.append(parsed_cookie)
            # Check if it's an object containing cookies array
            elif 'cookies' in data:
                if isinstance(data['cookies'], list):
                    for cookie in data['cookies']:
                        if isinstance(cookie, dict):
                            parsed_cookie = _parse_single_json_cookie(cookie)
                            if parsed_cookie:
                                cookies.append(parsed_cookie)
            # Check if it's domain-grouped cookies
            else:
                for key, value in data.items():
                    if isinstance(value, list):
                        for cookie in value:
                            if isinstance(cookie, dict):
                                parsed_cookie = _parse_single_json_cookie(cookie, default_domain=key)
                                if parsed_cookie:
                                    cookies.append(parsed_cookie)
        
        return cookies
    except (json.JSONDecodeError, AttributeError, TypeError):
        return []

def _parse_single_json_cookie(cookie_data: dict, default_domain: str = None) -> dict:
    """
    Parse a single cookie from various JSON formats.
    """
    # Extract name and value (required fields)
    name = cookie_data.get('name') or cookie_data.get('Name') or cookie_data.get('key')
    value = cookie_data.get('value') or cookie_data.get('Value') or cookie_data.get('val')
    
    if not name or value is None:
        return None
    
    # Extract domain with fallback logic
    domain = (cookie_data.get('domain') or 
              cookie_data.get('Domain') or 
              cookie_data.get('host') or 
              cookie_data.get('Host') or 
              default_domain)
    
    # Auto-detect domain from cookie name if not provided
    if not domain:
        name_lower = name.lower()
        if 'netflix' in name_lower:
            domain = '.netflix.com'
        elif 'claude' in name_lower or 'anthropic' in name_lower:
            domain = '.claude.ai'
        elif 'chatgpt' in name_lower or 'openai' in name_lower:
            domain = '.chatgpt.com'
        elif 'hotstar' in name_lower:
            domain = '.hotstar.com'
        elif 'disney' in name_lower:
            domain = '.disneyplus.com'
        else:
            domain = '.example.com'  # Fallback
    
    # Ensure domain starts with dot for proper Netscape format
    if domain and not domain.startswith('.'):
        domain = '.' + domain
    
    # Extract other attributes with various naming conventions
    path = (cookie_data.get('path') or 
            cookie_data.get('Path') or 
            '/')
    
    # Handle secure flag
    secure = cookie_data.get('secure', cookie_data.get('Secure'))
    if secure is None:
        secure = cookie_data.get('isSecure', True)  # Default to True for modern cookies
    
    # Handle httpOnly flag
    http_only = cookie_data.get('httpOnly', cookie_data.get('HttpOnly'))
    if http_only is None:
        http_only = cookie_data.get('isHttpOnly', True)  # Default to True
    
    # Handle expiry (convert various formats to timestamp)
    expires = (cookie_data.get('expires') or 
               cookie_data.get('Expires') or 
               cookie_data.get('expiry') or 
               cookie_data.get('expirationDate'))  # Chrome extension format
    
    if expires is None or expires == -1:
        expires = 1735689600  # Far future timestamp (2025)
    elif isinstance(expires, str):
        try:
            from datetime import datetime
            # Try to parse ISO format or other common formats
            if 'T' in expires:  # ISO format
                dt = datetime.fromisoformat(expires.replace('Z', '+00:00'))
                expires = int(dt.timestamp())
            else:
                expires = 1735689600  # Fallback
        except:
            expires = 1735689600
    elif isinstance(expires, (int, float)):
        # Handle both Unix timestamps and millisecond timestamps
        if expires > 2000000000:  # Likely milliseconds (after year 2033)
            expires = int(expires / 1000)
        else:
            expires = int(expires)
    
    return {
        "name": str(name),
        "value": str(value),
        "domain": domain,
        "path": path,
        "secure": bool(secure),
        "httpOnly": bool(http_only),
        "expires": expires
    }

def parse_netscape_content(netscape_string: str) -> list[dict]:
    """Parse Netscape format cookies."""
    cookies = []
    for line in netscape_string.splitlines():
        line = line.strip()
        if not line or line.startswith('#'): 
            continue
        parts = line.split('\t')
        if len(parts) >= 7:
            cookies.append({
                "name": parts[5],
                "value": parts[6],
                "domain": parts[0],
                "path": parts[2],
                "secure": parts[3].upper() == 'TRUE',
                "httpOnly": True  # Default for Netflix cookies
            })
    return cookies

def parse_header_string_content(header_string: str, domain: str = ".netflix.com") -> list[dict]:
    """Enhanced parser for header string format including URL-encoded cookies."""
    cookies = []
    
    # Clean the input
    header_string = header_string.strip().replace('\n', '').replace('\r', '')
    
    # Method 1: Try to parse as traditional cookie header (semicolon-separated)
    if ';' in header_string:
        try:
            jar = SimpleCookie()
            jar.load(header_string)
            for key, morsel in jar.items():
                cookies.append({
                    "name": key,
                    "value": morsel.value,
                    "domain": domain,
                    "path": "/",
                    "secure": True,
                    "httpOnly": True
                })
            if cookies:  # If we successfully parsed cookies this way, return them
                return cookies
        except Exception:
            pass  # Fall through to other methods
    
    # Method 2: Handle single cookie format like "NetflixId=value"
    if '=' in header_string and ';' not in header_string:
        # This handles the case where we have a single cookie like your example
        parts = header_string.split('=', 1)
        if len(parts) == 2:
            name, value = parts
            # URL decode the value if it appears to be URL encoded
            if '%' in value:
                try:
                    value = urllib.parse.unquote(value)
                except Exception:
                    pass  # Keep original value if decoding fails
            
            cookies.append({
                "name": name.strip(),
                "value": value.strip(),
                "domain": domain,
                "path": "/",
                "secure": True,
                "httpOnly": True
            })
            return cookies
    
    # Method 3: Try to parse as URL parameters (ampersand-separated)
    if '&' in header_string:
        try:
            parsed = urllib.parse.parse_qs(header_string, keep_blank_values=True)
            for name, values in parsed.items():
                if values:  # Take the first value if multiple
                    cookies.append({
                        "name": name,
                        "value": values[0],
                        "domain": domain,
                        "path": "/",
                        "secure": True,
                        "httpOnly": True
                    })
            if cookies:
                return cookies
        except Exception:
            pass
    
    # Method 4: Manual parsing for complex cases
    # Look for key=value patterns separated by various delimiters
    cookie_patterns = [
        r'([^=\s;,&]+)=([^;,&]*)',  # Standard cookie pattern
        r'(\w+)=([^&\s]*)',         # Simple key=value
    ]
    
    for pattern in cookie_patterns:
        matches = re.findall(pattern, header_string)
        if matches:
            for name, value in matches:
                # URL decode if necessary
                if '%' in value:
                    try:
                        value = urllib.parse.unquote(value)
                    except Exception:
                        pass
                
                cookies.append({
                    "name": name.strip(),
                    "value": value.strip(),
                    "domain": domain,
                    "path": "/",
                    "secure": True,
                    "httpOnly": True
                })
            break  # Use the first pattern that finds matches
    
    return cookies

# --- Enhanced Serializers ---
def serialize_to_json_string(cookie_list: list[dict]) -> str:
    """Serialize cookies to JSON format."""
    json_cookies = []
    for cookie in cookie_list:
        json_cookies.append({
            "name": cookie.get("name", ""),
            "value": cookie.get("value", ""),
            "domain": cookie.get("domain", ".netflix.com"),
            "path": cookie.get("path", "/"),
            "secure": cookie.get("secure", True),
            "httpOnly": cookie.get("httpOnly", True),
            "sameSite": "None"  # Required for Netflix
        })
    return json.dumps(json_cookies, indent=2)

def serialize_to_netscape_string(cookie_list: list[dict]) -> str:
    """Serialize cookies to Netscape format."""
    lines = ["# Netscape HTTP Cookie File", "# Generated by AetherX ⚡🌟 | Created by SkyriX"]
    
    for cookie in cookie_list:
        domain = cookie.get('domain', '.example.com')
        if not domain.startswith('.'):
            domain = '.' + domain
        
        # Get expires timestamp
        expires = cookie.get('expires', 1735689600)  # Default to far future
        if not isinstance(expires, (int, float)):
            expires = 1735689600
        
        # Format: domain, include_subdomains, path, secure, expires, name, value
        line_parts = [
            domain,
            "TRUE",  # include subdomains (always TRUE for compatibility)
            cookie.get('path', '/'),
            "TRUE" if cookie.get('secure', True) else "FALSE",
            str(int(expires)),  # Expires timestamp
            cookie.get('name', ''),
            cookie.get('value', '')
        ]
        lines.append('\t'.join(line_parts))
    
    return '\n'.join(lines) + '\n'

def serialize_to_header_string(cookie_list: list[dict]) -> str:
    """Serialize cookies to header string format."""
    cookie_parts = []
    for cookie in cookie_list:
        name = cookie.get('name', '')
        value = cookie.get('value', '')
        if name and value:
            cookie_parts.append(f"{name}={value}")
    return '; '.join(cookie_parts)

# --- Main Processing Function ---
def process_single_file(file_path_obj: pathlib.Path, output_folder_obj: pathlib.Path, target_format: str, **kwargs) -> str:
    """Process a single cookie file and convert it to the target format."""
    try:
        detected_format = detect_file_format(str(file_path_obj))
        if detected_format in [FORMAT_EMPTY, FORMAT_UNKNOWN, FORMAT_FILE_ERROR, DETECTION_ERROR]:
            return f"failed: {detected_format}"

        with open(file_path_obj, 'r', encoding='utf-8') as f:
            content = f.read()

        # Parse based on detected format
        parsed_cookies = []
        if detected_format == FORMAT_JSON:
            parsed_cookies = parse_json_content(content)
        elif detected_format == FORMAT_NETSCAPE:
            parsed_cookies = parse_netscape_content(content)
        elif detected_format == FORMAT_HEADER:
            parsed_cookies = parse_header_string_content(content, ".netflix.com")

        if not parsed_cookies:
            return "failed: no cookies parsed"

        # Basic validation - ensure we have valid cookies with names and values
        valid_cookies = []
        for cookie in parsed_cookies:
            if cookie.get('name') and cookie.get('value'):
                valid_cookies.append(cookie)
        
        if not valid_cookies:
            return "failed: no valid cookies found"
        
        parsed_cookies = valid_cookies

        # Serialize to target format
        output_content = ""
        if target_format == FORMAT_JSON:
            output_content = serialize_to_json_string(parsed_cookies)
        elif target_format == FORMAT_NETSCAPE:
            output_content = serialize_to_netscape_string(parsed_cookies)
        elif target_format == FORMAT_HEADER:
            output_content = serialize_to_header_string(parsed_cookies)

        if not output_content:
            return "failed: serialization error"

        # Determine output filename
        extension_map = {
            FORMAT_JSON: ".json",
            FORMAT_NETSCAPE: ".txt",
            FORMAT_HEADER: ".txt"
        }
        new_filename = file_path_obj.stem + extension_map.get(target_format, ".txt")
        final_target_file_path = output_folder_obj / new_filename
        
        # Write output file
        with open(final_target_file_path, 'w', encoding='utf-8') as f_out:
            f_out.write(output_content)
        
        return "success"
        
    except Exception as e:
        return f"failed: {str(e)}"

# --- Utility Functions ---
def validate_cookie_content(content: str) -> bool:
    """Validate if the content contains valid cookies from any domain."""
    if not content:
        return False
    
    # Detect format and try to parse
    format_type = detect_file_format_from_content(content)
    if format_type in [FORMAT_UNKNOWN, FORMAT_EMPTY]:
        return False
    
    parsed_cookies = []
    if format_type == FORMAT_JSON:
        parsed_cookies = parse_json_content(content)
    elif format_type == FORMAT_NETSCAPE:
        parsed_cookies = parse_netscape_content(content)
    elif format_type == FORMAT_HEADER:
        parsed_cookies = parse_header_string_content(content)
    
    # Check if we have any valid cookies (name and value present)
    for cookie in parsed_cookies:
        if cookie.get('name') and cookie.get('value'):
            return True
    
    return False

def detect_file_format_from_content(content: str) -> str:
    """Detect format from content string directly."""
    if not content.strip():
        return FORMAT_EMPTY
    
    # JSON format detection (enhanced)
    content_stripped = content.strip()
    
    # Check for array format [...]
    if (content_stripped.startswith('[') and content_stripped.endswith(']')) or \
       (content_stripped.startswith('{') and content_stripped.endswith('}')):
        try:
            data = json.loads(content)
            # Validate it's cookie-like data
            if _is_valid_json_cookie_data(data):
                return FORMAT_JSON
        except json.JSONDecodeError:
            pass
    
    # Netscape format detection (more flexible)
    if ("# Netscape HTTP Cookie File" in content or 
        (("\t" in content and content.count('\t') >= 6) and 
         (any(domain in content for domain in ['.com', '.net', '.org', '.ai']) or
          any(keyword in content.lower() for keyword in ['secure', 'httponly'])))):
        return FORMAT_NETSCAPE
    
    # Header string detection
    if _is_header_string_format(content):
        return FORMAT_HEADER
    
    return FORMAT_UNKNOWN

def _is_valid_json_cookie_data(data) -> bool:
    """Check if JSON data contains cookie-like information."""
    if isinstance(data, list):
        # Check if it's an array of cookie objects
        if data and isinstance(data[0], dict):
            first_item = data[0]
            # Look for cookie-like fields (including Chrome extension format)
            cookie_fields = ['name', 'value', 'domain', 'Name', 'Value', 'Domain', 'key', 'val', 'expirationDate', 'hostOnly']
            if any(field in first_item for field in cookie_fields):
                return True
    
    elif isinstance(data, dict):
        # Single cookie object
        if 'name' in data and 'value' in data:
            return True
        # Object with cookies array
        if 'cookies' in data and isinstance(data['cookies'], list):
            return True
        # Domain-grouped cookies
        for value in data.values():
            if isinstance(value, list) and value and isinstance(value[0], dict):
                cookie_fields = ['name', 'value', 'domain']
                if any(field in value[0] for field in cookie_fields):
                    return True
    
    return False