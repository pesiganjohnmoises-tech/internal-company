"""Backup/restore regression checks using only synthetic databases/workbooks."""
import argparse
from datetime import datetime, timedelta, timezone
import importlib.util
import json
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
        (self.project / "app.py").write_text("# synthetic source\n", encoding="utf-8")
        (self.project / "requirements.txt").write_text("Flask\n", encoding="utf-8")
        (self.project / "templates").mkdir()
        (self.project / "templates/example.html").write_text("synthetic", encoding="utf-8")
        subprocess.run(["git", "init", "-q", str(self.project)], check=True, capture_output=True)
        subprocess.run(["git", "add", "app.py", "requirements.txt", "templates"],
                       cwd=self.project, check=True, capture_output=True)
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

    def test_first_run_source_and_same_month_daily_only(self):
        B.backup(self.args)
        first = next(self.destination.glob("recovery-*.zip"))
        self.assertTrue(B.verify(first)["includes_source"])
        B.backup(self.args)
        archives = list(self.destination.glob("recovery-*.zip"))
        self.assertEqual(sum(B.verify(p)["includes_source"] for p in archives), 1)
        self.args.cleanup = True
        B.backup(self.args)
        self.assertTrue(first.exists())  # Newer data-only daily cannot evict monthly source.

    def test_source_restore_exclusions_and_forced_snapshot(self):
        (self.project / "untracked.py").write_text("not archived")
        archive = B.create_backup(self.project, self.destination,
                                  datetime.now(timezone.utc), include_source=True)
        manifest = B.verify(archive)
        self.assertIn("source/app.py", manifest["files"])
        self.assertNotIn("source/untracked.py", manifest["files"])
        target = self.root / "source-restore"
        B.restore(archive, target)
        self.assertEqual((target / "source/templates/example.html").read_text(), "synthetic")
        for name in ("../app.py", "utils/../app.py", "utils/secrets.py", "wsgi.py",
                     "utils/__pycache__/cache.py", "OLD_FOLDER/app.py", "backups/app.py"):
            self.assertFalse(B.source_member(name), name)
        self.args.include_source_now = True
        B.backup(self.args)
        self.assertEqual(sum(B.verify(p)["includes_source"]
                             for p in self.destination.glob("recovery-*.zip")), 2)

    def test_month_boundary_and_failed_source_retry(self):
        prior = datetime(2026, 9, 30, 15, 59, tzinfo=timezone.utc)
        old = B.create_backup(self.project, self.destination, prior, include_source=True)
        with patch.object(B, "copy_source", side_effect=RuntimeError("source failure")):
            with self.assertRaises(RuntimeError):
                B.backup(self.args)
        self.assertEqual(list(self.destination.glob("recovery-*.zip")), [old])
        B.backup(self.args)
        self.assertEqual(sum(B.verify(p)["includes_source"]
                             for p in self.destination.glob("recovery-*.zip")), 2)

    def test_legacy_archive_still_restores(self):
        archive = self.make()
        with zipfile.ZipFile(archive) as z:
            members = {name: z.read(name) for name in z.namelist()}
        manifest = json.loads(members["manifest.json"])
        manifest["format"] = B.FORMAT
        for name in ("includes_source", "source_dirty", "python_version"):
            manifest.pop(name)
        members["manifest.json"] = json.dumps(manifest).encode()
        with zipfile.ZipFile(archive, "w") as z:
            for name, content in members.items():
                z.writestr(name, content)
        B.restore(archive, self.root / "legacy-restore")


if __name__ == "__main__":
    unittest.main()
