"""Unit tests for the cleanup stage. Fully offline."""

from pathlib import Path

from vpic_updater.stages.cleanup import cleanup_temp_files


class TestCleanupTempFiles:
    def test_removes_a_file(self, tmp_path: Path):
        f = tmp_path / "some_download.zip"
        f.write_bytes(b"data")
        assert f.exists()

        cleanup_temp_files(f)

        assert not f.exists()

    def test_removes_a_directory(self, tmp_path: Path):
        d = tmp_path / "extracted"
        d.mkdir()
        (d / "nested_file.backup").write_bytes(b"data")
        assert d.exists()

        cleanup_temp_files(d)

        assert not d.exists()

    def test_removes_multiple_paths_in_one_call(self, tmp_path: Path):
        f1 = tmp_path / "a.zip"
        f2 = tmp_path / "b.backup"
        d1 = tmp_path / "extracted_dir"
        f1.write_bytes(b"1")
        f2.write_bytes(b"2")
        d1.mkdir()

        cleanup_temp_files(f1, f2, d1)

        assert not f1.exists()
        assert not f2.exists()
        assert not d1.exists()

    def test_missing_path_does_not_raise(self, tmp_path: Path):
        missing = tmp_path / "does_not_exist.zip"
        assert not missing.exists()

        # Should not raise -- cleanup of a nonexistent path is a no-op.
        cleanup_temp_files(missing)

    def test_one_bad_path_does_not_prevent_cleanup_of_others(self, tmp_path: Path, monkeypatch):
        """Simulate a permission error on one path -- the other valid
        path must still be cleaned up, and no exception should propagate."""
        good_file = tmp_path / "removable.zip"
        good_file.write_bytes(b"data")

        bad_file = tmp_path / "unremovable.zip"
        bad_file.write_bytes(b"data")

        original_unlink = Path.unlink

        def flaky_unlink(self, *args, **kwargs):
            if self == bad_file:
                raise OSError("Permission denied (simulated)")
            return original_unlink(self, *args, **kwargs)

        monkeypatch.setattr(Path, "unlink", flaky_unlink)

        # Should not raise despite bad_file failing internally.
        cleanup_temp_files(bad_file, good_file)

        assert not good_file.exists()
        # bad_file's removal failed, so it's still present -- expected.
        assert bad_file.exists()

    def test_empty_call_is_a_noop(self):
        # No paths given -- should simply do nothing, no error.
        cleanup_temp_files()