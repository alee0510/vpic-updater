"""Unit tests for the extract (download) stage. Fully offline."""

import zipfile
from pathlib import Path

import pytest
import requests
import requests_mock

from vpic_updater.core.config import MIN_EXPECTED_ZIP_BYTES
from vpic_updater.model.download import ExtractError
from vpic_updater.stages.extract import download_file

TEST_URL = "https://vpic.nhtsa.dot.gov/downloads/vPICList_lite_2026_08.custom.zip"


def _make_valid_zip_bytes(padding_bytes: int = MIN_EXPECTED_ZIP_BYTES) -> bytes:
    """Build an in-memory zip large enough to pass the size floor."""
    import io

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_STORED) as zf:
        zf.writestr("vPICList_lite_2026_08.custom", b"\x00" * padding_bytes)
    return buf.getvalue()


class TestDownloadFile:
    def test_successful_download(self, tmp_path: Path):
        content = _make_valid_zip_bytes()
        with requests_mock.Mocker() as m:
            m.get(
                TEST_URL,
                content=content,
                headers={"Content-Length": str(len(content))},
            )
            result = download_file(TEST_URL, tmp_path)

        assert result.file_path.exists()
        assert result.file_path.name == "vPICList_lite_2026_08.custom.zip"
        assert result.size_bytes == len(content)
        assert zipfile.is_zipfile(result.file_path)
        # temp file should not be left behind
        assert not result.file_path.with_suffix(".zip.part").exists()

    def test_raises_on_truncated_download(self, tmp_path: Path):
        full_content = _make_valid_zip_bytes()
        truncated = full_content[: len(full_content) // 2]
        with requests_mock.Mocker() as m:
            # Server claims full size, but we simulate only partial bytes
            # actually arriving by mocking a mismatched Content-Length.
            m.get(
                TEST_URL,
                content=truncated,
                headers={"Content-Length": str(len(full_content))},
            )
            with pytest.raises(ExtractError, match="size mismatch"):
                download_file(TEST_URL, tmp_path)

        # no partial file left behind
        assert not (tmp_path / "vPICList_lite_2026_08.custom.zip").exists()
        assert not (tmp_path / "vPICList_lite_2026_08.custom.zip.part").exists()

    def test_raises_on_empty_response(self, tmp_path: Path):
        with requests_mock.Mocker() as m:
            m.get(TEST_URL, content=b"")
            with pytest.raises(ExtractError, match="empty"):
                download_file(TEST_URL, tmp_path)

    def test_raises_on_below_size_floor(self, tmp_path: Path):
        small_content = _make_valid_zip_bytes(padding_bytes=100)
        with requests_mock.Mocker() as m:
            m.get(
                TEST_URL,
                content=small_content,
                headers={"Content-Length": str(len(small_content))},
            )
            with pytest.raises(ExtractError, match="smaller than expected floor"):
                download_file(TEST_URL, tmp_path)

    def test_raises_on_invalid_zip_content(self, tmp_path: Path):
        # Large enough to pass the size floor, but not actually a zip --
        # simulates an HTML error page served with a 200 status.
        fake_html = b"<html><body>Service Unavailable</body></html>"
        padded = fake_html + b"\x00" * MIN_EXPECTED_ZIP_BYTES
        with requests_mock.Mocker() as m:
            m.get(
                TEST_URL,
                content=padded,
                headers={"Content-Length": str(len(padded))},
            )
            with pytest.raises(ExtractError, match="not a valid zip"):
                download_file(TEST_URL, tmp_path)

    def test_raises_on_network_failure(self, tmp_path: Path):
        with requests_mock.Mocker() as m:
            m.get(TEST_URL, exc=requests.exceptions.ConnectionError)
            with pytest.raises(ExtractError, match="Download failed"):
                download_file(TEST_URL, tmp_path)

        assert not (tmp_path / "vPICList_lite_2026_08.custom.zip.part").exists()

    def test_raises_on_http_error_status(self, tmp_path: Path):
        with requests_mock.Mocker() as m:
            m.get(TEST_URL, status_code=503)
            with pytest.raises(ExtractError, match="Download failed"):
                download_file(TEST_URL, tmp_path)