"""
Stage 1: Extract.

Downloads the PostgreSQL custom-format dump from the vPIC downloads page
via a streaming request, and validates the result is a well-formed,
non-truncated zip file before handing off to Transform.
"""

import logging
from pathlib import Path
import requests

from vpic_updater.core.config import DEFAULT_CHUNK_SIZE, DEFAULT_TIMEOUT_SECONDS, MIN_EXPECTED_ZIP_BYTES
from vpic_updater.model.download import DownloadResult, ExtractError


logger = logging.getLogger("vpic_updater.extract")

def download_file(
    url: str,
    dest_dir: Path,
    chunk_size: int = DEFAULT_CHUNK_SIZE,
    timeout: int = DEFAULT_TIMEOUT_SECONDS,
) -> DownloadResult:
    """Stream-download `url` into `dest_dir`.

    Writes to a `.part` temp file first and only renames to the final
    filename on success, so a crash mid-download never leaves a
    plausible-looking-but-truncated file at the expected path.
    """
    dest_dir.mkdir(parents=True, exist_ok=True)

    filename = url.rsplit("/", 1)[-1]
    if not filename:
        raise ExtractError(f"Could not determine filename from URL: {url}")

    final_path = dest_dir / filename
    tmp_path = final_path.with_suffix(final_path.suffix + ".part")

    logger.info("Starting download: %s -> %s", url, final_path)

    try:
        with requests.get(url, stream=True, timeout=timeout) as response:
            response.raise_for_status()
            expected_size = _content_length(response)

            bytes_written = 0
            with open(tmp_path, "wb") as f:
                for chunk in response.iter_content(chunk_size=chunk_size):
                    if chunk:
                        f.write(chunk)
                        bytes_written += len(chunk)
                        
    except requests.RequestException as exc:
        _cleanup(tmp_path)
        logger.error("Download failed for %s: %s", url, exc)
        raise ExtractError(f"Download failed for {url}: {exc}") from exc

    if bytes_written == 0:
        _cleanup(tmp_path)
        raise ExtractError(f"Downloaded file is empty: {url}")

    if expected_size is not None and bytes_written != expected_size:
        _cleanup(tmp_path)
        raise ExtractError(
            f"Download size mismatch for {url}: expected {expected_size} bytes, "
            f"got {bytes_written} bytes -- likely truncated"
        )

    if bytes_written < MIN_EXPECTED_ZIP_BYTES:
        _cleanup(tmp_path)
        raise ExtractError(
            f"Downloaded file smaller than expected floor "
            f"({bytes_written} < {MIN_EXPECTED_ZIP_BYTES} bytes): {url}"
        )

    if not _is_zip(tmp_path):
        _cleanup(tmp_path)
        raise ExtractError(f"Downloaded file is not a valid zip: {url}")

    tmp_path.rename(final_path)
    logger.info("Download complete: %s (%d bytes)", final_path, bytes_written)

    return DownloadResult(file_path=final_path, url=url, size_bytes=bytes_written)


def _content_length(response: requests.Response) -> int | None:
    header = response.headers.get("Content-Length")
    return int(header) if header is not None and header.isdigit() else None


def _is_zip(path: Path) -> bool:
    import zipfile
    return zipfile.is_zipfile(path)


def _cleanup(path: Path) -> None:
    try:
        path.unlink(missing_ok=True)
    except OSError as exc:
        logger.warning("Could not remove partial download %s: %s", path, exc)