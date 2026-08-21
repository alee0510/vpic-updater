"""Unit tests for the transform (unzip + sanity-check) stage. Fully offline."""

import zipfile
from pathlib import Path
import pytest

from vpic_updater.core.config import MIN_EXPECTED_BACKUP_BYTES
from vpic_updater.model.extract import TransformError
from vpic_updater.stages.transform import unzip_dump


def _make_zip_with_backup(
    zip_path: Path,
    backup_filename: str = "vPICList_lite_2026_08.backup",
    padding_bytes: int = MIN_EXPECTED_BACKUP_BYTES,
) -> None:
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_STORED) as zf:
        zf.writestr(backup_filename, b"\x00" * padding_bytes)


class TestUnzipDump:
    def test_successful_extraction(self, tmp_path: Path):
        zip_path = tmp_path / "vPICList_lite_2026_08.custom.zip"
        extract_dir = tmp_path / "extracted"
        _make_zip_with_backup(zip_path)

        result = unzip_dump(zip_path, extract_dir)

        assert result.dump_path.exists()
        assert result.dump_path.name == "vPICList_lite_2026_08.backup"
        assert result.size_bytes == MIN_EXPECTED_BACKUP_BYTES

    def test_raises_on_missing_zip_file(self, tmp_path: Path):
        missing_zip = tmp_path / "does_not_exist.zip"
        extract_dir = tmp_path / "extracted"

        with pytest.raises(TransformError, match="does not exist"):
            unzip_dump(missing_zip, extract_dir)

    def test_raises_on_corrupt_zip(self, tmp_path: Path):
        zip_path = tmp_path / "corrupt.zip"
        zip_path.write_bytes(b"not actually a zip file")
        extract_dir = tmp_path / "extracted"

        with pytest.raises(TransformError, match="Corrupt zip file"):
            unzip_dump(zip_path, extract_dir)

    def test_raises_on_empty_zip(self, tmp_path: Path):
        zip_path = tmp_path / "empty.zip"
        with zipfile.ZipFile(zip_path, "w"):
            pass  # zero members
        extract_dir = tmp_path / "extracted"

        with pytest.raises(TransformError, match="is empty"):
            unzip_dump(zip_path, extract_dir)

    def test_raises_when_no_backup_file_present(self, tmp_path: Path):
        zip_path = tmp_path / "wrong_contents.zip"
        with zipfile.ZipFile(zip_path, "w") as zf:
            zf.writestr("readme.txt", b"this is not a backup file")
        extract_dir = tmp_path / "extracted"

        with pytest.raises(TransformError, match="No .backup file found"):
            unzip_dump(zip_path, extract_dir)

    def test_raises_when_multiple_backup_files_present(self, tmp_path: Path):
        zip_path = tmp_path / "ambiguous.zip"
        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_STORED) as zf:
            zf.writestr("a.backup", b"\x00" * MIN_EXPECTED_BACKUP_BYTES)
            zf.writestr("b.backup", b"\x00" * MIN_EXPECTED_BACKUP_BYTES)
        extract_dir = tmp_path / "extracted"

        with pytest.raises(TransformError, match="exactly one .backup file"):
            unzip_dump(zip_path, extract_dir)

    def test_raises_on_undersized_backup_file(self, tmp_path: Path):
        zip_path = tmp_path / "small.zip"
        _make_zip_with_backup(zip_path, padding_bytes=100)
        extract_dir = tmp_path / "extracted"

        with pytest.raises(TransformError, match="smaller than expected floor"):
            unzip_dump(zip_path, extract_dir)