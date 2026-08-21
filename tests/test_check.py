"""Unit tests for the version-check stage. Fully offline -- uses saved
HTML fixtures and requests-mock, never hits the real NHTSA site."""

from datetime import date
from pathlib import Path
import pytest
import requests
import requests_mock

from vpic_updater.core.config import VPIC_DOWNLOADS_URL
from vpic_updater.model.version import VersionCheckError, VpicVersion
from vpic_updater.stages.check import fetch_current_version, is_new_version

FIXTURES_DIR = Path(__file__).parent / "fixtures"


def _load_fixture(name: str) -> str:
    return (FIXTURES_DIR / name).read_text(encoding="utf-8")


class TestFetchCurrentVersion:
    def test_parses_valid_page(self):
        html = _load_fixture("vpic.nhtsa.dot.gov.html")
        with requests_mock.Mocker() as m:
            m.get(VPIC_DOWNLOADS_URL, text=html, status_code=200)
            result = fetch_current_version()

        assert result.version == "4.08"
        assert result.released_on == date(2026, 8, 15)
        assert result.postgres_custom_url.endswith(
            "vPICList_lite_2026_08.custom.zip"
        )
        assert result.postgres_plain_url.endswith(
            "vPICList_lite_2026_08.plain.zip"
        )
        assert result.postgres_custom_url.startswith("https://vpic.nhtsa.dot.gov")

    def test_derives_year_month_from_custom_url(self):
        html = _load_fixture("vpic.nhtsa.dot.gov.html")
        with requests_mock.Mocker() as m:
            m.get(VPIC_DOWNLOADS_URL, text=html, status_code=200)
            result = fetch_current_version()

        assert result.year_month == "2026_08"

    def test_raises_on_missing_version_string(self):
        html = _load_fixture("downloads_page_malformed.html")
        with requests_mock.Mocker() as m:
            m.get(VPIC_DOWNLOADS_URL, text=html, status_code=200)
            with pytest.raises(VersionCheckError, match="version string"):
                fetch_current_version()

    def test_raises_on_network_failure(self):
        with requests_mock.Mocker() as m:
            m.get(VPIC_DOWNLOADS_URL, exc=requests.exceptions.ConnectTimeout)
            with pytest.raises(VersionCheckError, match="Could not reach"):
                fetch_current_version()

    def test_raises_on_http_error_status(self):
        with requests_mock.Mocker() as m:
            m.get(VPIC_DOWNLOADS_URL, status_code=503)
            with pytest.raises(VersionCheckError):
                fetch_current_version()

    def test_raises_on_missing_postgres_links(self):
        # Version string present, but no .custom.zip / .plain.zip links
        html = """
        <html><body>
        <p>Version: 4.08 last updated on 8/15/2026</p>
        </body></html>
        """
        with requests_mock.Mocker() as m:
            m.get(VPIC_DOWNLOADS_URL, text=html, status_code=200)
            with pytest.raises(VersionCheckError, match="PostgreSQL"):
                fetch_current_version()


class TestIsNewVersion:
    def _make_version(self, version: str) -> VpicVersion:
        return VpicVersion(
            version=version,
            released_on=date(2026, 8, 15),
            postgres_custom_url="https://vpic.nhtsa.dot.gov/downloads/vPICList_lite_2026_08.custom.zip",
            postgres_plain_url="https://vpic.nhtsa.dot.gov/downloads/vPICList_lite_2026_08.plain.zip",
        )

    def test_new_when_no_prior_deployment(self):
        remote = self._make_version("4.08")
        assert is_new_version(remote, last_deployed_version=None) is True

    def test_new_when_version_differs(self):
        remote = self._make_version("4.08")
        assert is_new_version(remote, last_deployed_version="4.07") is True

    def test_not_new_when_version_matches(self):
        remote = self._make_version("4.08")
        assert is_new_version(remote, last_deployed_version="4.08") is False