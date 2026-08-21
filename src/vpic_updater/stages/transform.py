"""
Stage 2: Transform.

Extracts the downloaded .custom.zip archive and locates/sanity-checks the
.backup dump file inside it, before Load attempts a pg_restore. Catching a
corrupt or unexpectedly-shaped archive here is cheaper and clearer than
letting pg_restore fail deep into Stage 3.
"""

import logging
import zipfile
from pathlib import Path

from vpic_updater.core.config import MIN_EXPECTED_BACKUP_BYTES
from vpic_updater.model.extract import ExtractedDump, TransformError


logger = logging.getLogger("vpic_updater.transform")

def unzip_dump(zip_path: Path, extract_dir: Path) -> ExtractedDump:
    """Extract `zip_path` into `extract_dir` and locate the .backup file.

    extract_dir is expected to be a fresh/per-run directory (e.g. named
    after the version or a temp dir) -- this function does not clean up
    pre-existing unrelated files there.
    """
    if not zip_path.exists():
        raise TransformError(f"Zip file does not exist: {zip_path}")

    extract_dir.mkdir(parents=True, exist_ok=True)

    try:
        with zipfile.ZipFile(zip_path) as zf:
            names = zf.namelist()
            if not names:
                raise TransformError(f"Zip file is empty: {zip_path}")

            bad_member = zf.testzip()
            if bad_member is not None:
                raise TransformError(
                    f"Zip file failed CRC check on member '{bad_member}': {zip_path}"
                )

            zf.extractall(extract_dir)
    except zipfile.BadZipFile as exc:
        raise TransformError(f"Corrupt zip file: {zip_path}") from exc

    dump_path = _locate_backup_file(extract_dir)
    size_bytes = dump_path.stat().st_size

    if size_bytes < MIN_EXPECTED_BACKUP_BYTES:
        raise TransformError(
            f"Extracted dump smaller than expected floor "
            f"({size_bytes} < {MIN_EXPECTED_BACKUP_BYTES} bytes): {dump_path}"
        )

    logger.info("Extracted and validated dump: %s (%d bytes)", dump_path, size_bytes)
    return ExtractedDump(dump_path=dump_path, size_bytes=size_bytes)


def _locate_backup_file(extract_dir: Path) -> Path:
    """Find the .backup file produced by extraction.

    Matches the ops guide's naming convention (vPICList_lite_2026_07.backup).
    Searches recursively in case the zip nests files in a subdirectory.
    """
    candidates = list(extract_dir.rglob("*.backup"))

    if not candidates:
        found = [str(p.relative_to(extract_dir)) for p in extract_dir.rglob("*") if p.is_file()]
        raise TransformError(
            f"No .backup file found after extracting archive. "
            f"Files present: {found or '(none)'}"
        )

    if len(candidates) > 1:
        raise TransformError(
            f"Expected exactly one .backup file, found {len(candidates)}: "
            f"{[str(p) for p in candidates]}"
        )

    return candidates[0]