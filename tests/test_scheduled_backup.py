"""Backup/restore regression checks using only synthetic databases/workbooks."""
import argparse
from datetime import datetime, timedelta, timezone
import importlib.util
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
spec = importlib.util.spec_from_file_location("scheduled_backup", ROOT / "scripts/scheduled_backup.py")
B = importlib.util.module_from_spec(spec)
spec.loader.exec_module(B)


class BackupTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.project = self.root / "project"
        self.project.mkdir()
        self.destination = self.root / "backups"
        self.destination.mkdir()
        for folder in ("data", "upload_archive"):
            (self.project / folder).mkdir()
            with zipfile.ZipFile(self.project / folder / "sample.xlsx", "w") as z:
                z.writestr("synthetic.txt", "sample only")
        (self.project / "data/~$sample.xlsx").write_bytes(b"lock")
        with sqlite3.connect(self.project / "directory.db") as con:
            con.execute("CREATE TABLE example(id INTEGER PRIMARY KEY, value TEXT)")
            con.execute("INSERT INTO example VALUES(1,'synthetic')")
        con.close()
        self.args = argparse.Namespace(project=self.project, destination=self.destination,
                                       daily=7, weekly=3, monthly=2, cleanup=False)

    def make(self, when=None):
        return B.create_backup(self.project, self.destination,
                               when or datetime.now(timezone.utc))

    def test_archive_restore_and_originals_unchanged(self):
        original = B.digest(self.project / "directory.db")
        archive = self.make()
        manifest = B.verify(archive)
        self.assertEqual(set(manifest["files"]),
                         {"directory.db", "data/sample.xlsx", "upload_archive/sample.xlsx"})
        target = self.root / "restored"
        B.restore(archive, target)
        with sqlite3.connect(target / "directory.db") as con:
            self.assertEqual(con.execute("SELECT value FROM example").fetchone(), ("synthetic",))
        con.close()
        for name, meta in manifest["files"].items():
            self.assertEqual(B.digest(target / name), meta["sha256"])
        self.assertEqual(B.digest(self.project / "directory.db"), original)
        with self.assertRaises(FileExistsError):
            B.restore(archive, target)

    def test_retention_union_completed_periods_and_daily_reruns(self):
        now = datetime(2026, 10, 14, 18, tzinfo=timezone.utc)
        records = [(Path(f"p{i}"), now - timedelta(days=i)) for i in range(100)]
        keep = B.retained(records, now)
        self.assertEqual(len(keep), 11)  # One weekly point overlaps daily retention.
        self.assertTrue({Path(f"p{i}") for i in range(7)} <= keep)
        rerun = (Path("rerun"), now + timedelta(seconds=1))
        keep = B.retained(records + [rerun], now + timedelta(seconds=1))
        self.assertIn(Path("rerun"), keep)
        self.assertNotIn(Path("p0"), keep)

    def test_preview_never_deletes_and_cleanup_ignores_unrelated_files(self):
        now = datetime.now(timezone.utc)
        archives = [self.make(now - timedelta(days=i)) for i in range(10)]
        unrelated = self.destination / "personal.zip"
        unrelated.write_bytes(b"untouched")
        self.args.daily = self.args.weekly = self.args.monthly = 1
        B.backup(self.args)
        self.assertTrue(all(p.exists() for p in archives))
        self.args.cleanup = True
        B.backup(self.args)
        self.assertTrue(unrelated.exists())
        self.assertLess(len(list(self.destination.glob("recovery-*.zip"))), 12)
        self.assertTrue((self.project / "data/sample.xlsx").exists())

    def test_failed_snapshot_preserves_old_archives(self):
        old = self.make()
        self.args.cleanup = True
        with patch.object(B, "check_database", side_effect=RuntimeError("injected failure")):
            with self.assertRaises(RuntimeError):
                B.backup(self.args)
        self.assertTrue(old.exists())
        self.assertEqual(list(self.destination.glob("recovery-*.zip")), [old])

    def test_corrupt_recognized_archive_blocks_housekeeping(self):
        old = self.make()
        old.write_bytes(b"corrupt synthetic archive")
        self.args.cleanup = True
        with self.assertRaises(zipfile.BadZipFile):
            B.backup(self.args)
        self.assertTrue(old.exists())

    def test_overlap_and_import_lock(self):
        with B.file_lock(self.destination / ".scheduled-backup.lock", timeout=0):
            with self.assertRaises(TimeoutError):
                B.backup(self.args)
        code = ("import sys; from utils.backup_lock import file_lock; "
                "\nwith file_lock(sys.argv[1], timeout=0): print('unexpected')")
        with B.file_lock(self.project / ".backup-import.lock", timeout=0):
            result = subprocess.run([sys.executable, "-B", "-c", code,
                                     str(self.project / ".backup-import.lock")],
                                    cwd=ROOT, capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("TimeoutError", result.stderr)


if __name__ == "__main__":
    unittest.main()
