"""
Stage 0: Check.

Scrapes the vPIC downloads page for the current published version,
release date, and PostgreSQL custom-format download URL. Used to decide
whether a new update cycle should run at all.
"""

import logging
import re
from datetime import date
import requests
from bs4 import BeautifulSoup

from vpic_updater.core.config import VPIC_BASE_URL, VPIC_DOWNLOADS_URL
from vpic_updater.model.version import VpicVersion, VersionCheckError

logger = logging.getLogger("vpic_updater.check")

# Regex to match "Version: 4.08 last updated on 8/15/2026"
VERSION_RE = re.compile(
    r"Version:\s*([\d.]+)\s*last updated on\s*(\d{1,2}/\d{1,2}/\d{4})"
)


def fetch_current_version(
    url: str = VPIC_DOWNLOADS_URL, timeout: int = 30
) -> VpicVersion:
    """Fetch and parse the vPIC downloads page.

    Raises VersionCheckError on any network failure or if expected content
    (version string, Postgres download links) cannot be located -- this is
    treated as distinct from "no new version" and should alert, since it
    usually means the page structure changed upstream.
    """
    try:
        response = requests.get(url, timeout=timeout)
        response.raise_for_status()
    except requests.RequestException as exc:
        logger.error("Failed to fetch vPIC downloads page (%s): %s", url, exc)
        raise VersionCheckError(f"Could not reach {url}: {exc}") from exc

    soup = BeautifulSoup(response.text, "html.parser")
    page_text = soup.get_text(" ", strip=True)

    version_match = VERSION_RE.search(page_text)
    if not version_match:
        logger.error("Version string not found on downloads page")
        raise VersionCheckError(
            "Could not locate version string on downloads page -- "
            "page structure may have changed"
        )

    version_str, date_str = version_match.group(1), version_match.group(2)
    try:
        month, day, year = (int(part) for part in date_str.split("/"))
        released_on = date(year, month, day)
    except ValueError as exc:
        raise VersionCheckError(f"Could not parse release date '{date_str}'") from exc

    custom_link = soup.find("a", href=re.compile(r"\.custom\.zip$"))
    plain_link = soup.find("a", href=re.compile(r"\.plain\.zip$"))
    if not custom_link or not plain_link:
        logger.error("PostgreSQL download links not found on downloads page")
        raise VersionCheckError(
            "Could not locate PostgreSQL (.custom.zip / .plain.zip) download "
            "links -- page structure may have changed"
        )

    custom_url = _absolute_url(custom_link["href"])
    plain_url = _absolute_url(plain_link["href"])

    result = VpicVersion(
        version=version_str,
        released_on=released_on,
        postgres_custom_url=custom_url,
        postgres_plain_url=plain_url,
    )
    logger.info(
        "Fetched current vPIC version: %s (released %s)",
        result.version, result.released_on,
    )
    return result


def _absolute_url(href: str) -> str:
    if href.startswith("http"):
        return href
    return VPIC_BASE_URL + href


def is_new_version(remote: VpicVersion, last_deployed_version: str | None) -> bool:
    """Compare the scraped version against the last *successfully deployed*
    version (not merely the last attempted one). None means nothing has
    ever been deployed -- always treated as new."""
    if last_deployed_version is None:
        return True
    return remote.version != last_deployed_version