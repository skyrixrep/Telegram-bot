"""
Netflix Token Generator module.
Takes Netflix cookies and generates login token links via the Netflix iOS API.
Ported from nf.py.
"""

import logging
from datetime import datetime

import requests
from urllib3.exceptions import InsecureRequestWarning

from .netflix_cookie_extractor import extract_cookie_dict
from .health_checker import ValidationResult
from .enums import PrivatizationStatus

requests.packages.urllib3.disable_warnings(category=InsecureRequestWarning)

logger = logging.getLogger(__name__)

API_URL = "https://ios.prod.ftl.netflix.com/iosui/user/15.48"

QUERY_PARAMS = {
    "appVersion": "15.48.1",
    "config": '{"gamesInTrailersEnabled":"false","isTrailersEvidenceEnabled":"false","cdsMyListSortEnabled":"true","kidsBillboardEnabled":"true","addHorizontalBoxArtToVideoSummariesEnabled":"false","skOverlayTestEnabled":"false","homeFeedTestTVMovieListsEnabled":"false","baselineOnIpadEnabled":"true","trailersVideoIdLoggingFixEnabled":"true","postPlayPreviewsEnabled":"false","bypassContextualAssetsEnabled":"false","roarEnabled":"false","useSeason1AltLabelEnabled":"false","disableCDSSearchPaginationSectionKinds":["searchVideoCarousel"],"cdsSearchHorizontalPaginationEnabled":"true","searchPreQueryGamesEnabled":"true","kidsMyListEnabled":"true","billboardEnabled":"true","useCDSGalleryEnabled":"true","contentWarningEnabled":"true","videosInPopularGamesEnabled":"true","avifFormatEnabled":"false","sharksEnabled":"true"}',
    "device_type": "NFAPPL-02-",
    "esn": "NFAPPL-02-IPHONE8%3D1-PXA-02026U9VV5O8AUKEAEO8PUJETCGDD4PQRI9DEB3MDLEMD0EACM4CS78LMD334MN3MQ3NMJ8SU9O9MVGS6BJCURM1PH1MUTGDPF4S4200",
    "idiom": "phone",
    "iosVersion": "15.8.5",
    "isTablet": "false",
    "languages": "en-US",
    "locale": "en-US",
    "maxDeviceWidth": "375",
    "model": "saget",
    "modelType": "IPHONE8-1",
    "odpAware": "true",
    "path": '["account","token","default"]',
    "pathFormat": "graph",
    "pixelDensity": "2.0",
    "progressive": "false",
    "responseFormat": "json",
}

BASE_HEADERS = {
    "User-Agent": "Argo/15.48.1 (iPhone; iOS 15.8.5; Scale/2.00)",
    "x-netflix.request.attempt": "1",
    "x-netflix.request.client.user.guid": "A4CS633D7VCBPE2GPK2HL4EKOE",
    "x-netflix.context.profile-guid": "A4CS633D7VCBPE2GPK2HL4EKOE",
    "x-netflix.request.routing": '{"path":"/nq/mobile/nqios/~15.48.0/user","control_tag":"iosui_argo"}',
    "x-netflix.context.app-version": "15.48.1",
    "x-netflix.argo.translated": "true",
    "x-netflix.context.form-factor": "phone",
    "x-netflix.context.sdk-version": "2012.4",
    "x-netflix.client.appversion": "15.48.1",
    "x-netflix.context.max-device-width": "375",
    "x-netflix.context.ab-tests": "",
    "x-netflix.tracing.cl.useractionid": "4DC655F2-9C3C-4343-8229-CA1B003C3053",
    "x-netflix.client.type": "argo",
    "x-netflix.client.ftl.esn": "NFAPPL-02-IPHONE8=1-PXA-02026U9VV5O8AUKEAEO8PUJETCGDD4PQRI9DEB3MDLEMD0EACM4CS78LMD334MN3MQ3NMJ8SU9O9MVGS6BJCURM1PH1MUTGDPF4S4200",
    "x-netflix.context.locales": "en-US",
    "x-netflix.context.top-level-uuid": "90AFE39F-ADF1-4D8A-B33E-528730990FE3",
    "x-netflix.client.iosversion": "15.8.5",
    "accept-language": "en-US;q=1",
    "x-netflix.argo.abtests": "",
    "x-netflix.context.os-version": "15.8.5",
    "x-netflix.request.client.context": '{"appState":"foreground"}',
    "x-netflix.context.ui-flavor": "argo",
    "x-netflix.argo.nfnsm": "9",
    "x-netflix.context.pixel-density": "2.0",
    "x-netflix.request.toplevel.uuid": "90AFE39F-ADF1-4D8A-B33E-528730990FE3",
    "x-netflix.request.client.timezoneid": "Asia/Dhaka",
}


def generate_nf_token(cookie_content: str) -> ValidationResult:
    """
    Generate Netflix login token from cookie content.
    Returns ValidationResult with token details on success.
    """
    cookies = extract_cookie_dict(cookie_content)
    if not cookies:
        return ValidationResult(
            status=PrivatizationStatus.INVALID_FORMAT,
            message="No NetflixId found in cookie content"
        )

    netflix_id = cookies.get("NetflixId")
    if not netflix_id:
        return ValidationResult(
            status=PrivatizationStatus.FAILURE_INVALID_COOKIE,
            message="No NetflixId cookie found"
        )

    headers = dict(BASE_HEADERS)
    headers["Cookie"] = f"NetflixId={netflix_id}"

    try:
        response = requests.get(
            API_URL, params=QUERY_PARAMS, headers=headers,
            timeout=20, verify=False
        )
        response.raise_for_status()
        data = response.json()

        token_data = (
            (((data.get("value") or {}).get("account") or {})
             .get("token") or {}).get("default") or {}
        )
        token = token_data.get("token")
        expires = token_data.get("expires")

        if not token:
            return ValidationResult(
                status=PrivatizationStatus.FAILURE_INVALID_COOKIE,
                message="Dead cookie (no token returned)"
            )

        if isinstance(expires, int) and len(str(expires)) == 13:
            expires //= 1000
        expiry_date = datetime.fromtimestamp(expires).strftime("%Y-%m-%d") if expires else "N/A"

        phone_link = f"https://www.netflix.com/unsupported?nftoken={token}"
        desktop_link = f"https://www.netflix.com/browse?nftoken={token}"
        tv_link = f"https://www.netflix.com/tv8?nftoken={token}"

        return ValidationResult(
            status=PrivatizationStatus.SUCCESS,
            message="Token generated successfully",
            details={
                "token": token,
                "expiry_date": expiry_date,
                "phone_link": phone_link,
                "desktop_link": desktop_link,
                "tv_link": tv_link,
            }
        )

    except Exception as e:
        logger.error(f"NF Token generation error: {e}")
        return ValidationResult(
            status=PrivatizationStatus.FAILURE_NETWORK,
            message=f"Error: {str(e)}"
        )
