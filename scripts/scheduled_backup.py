"""Private SQLite/workbook backups. No app import, secrets, or default deletion."""
import argparse
from contextlib import closing, nullcontext
from datetime import datetime, timedelta, timezone
import hashlib
import json
import logging
import os
from pathlib import Path
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
import uuid
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from utils.backup_lock import file_lock
from utils.workbook_submissions import check_snapshot

FORMAT = "directory-scheduled-backup-v1"
SOURCE_FORMAT = "directory-scheduled-backup-v2"
PATTERN = re.compile(r"recovery-\d{8}T\d{12}Z-[0-9a-f]{32}\.zip")
# Contemporary Manila time is UTC+8, without daylight saving. Avoid an
# additional tzdata dependency on Windows for these present-day snapshots.
MANILA = timezone(timedelta(hours=8), "Asia/Manila")


def safe_path(path):
    path = Path(path).absolute()
    for part in (path, *path.parents):
        if part.is_symlink():
            raise ValueError(f"Symlink is not allowed: {part}")
    return path


def digest(path):
    with path.open("rb") as stream:
        return stream_digest(stream)


def stream_digest(stream):
    value = hashlib.sha256()
    for chunk in iter(lambda: stream.read(1024 * 1024), b""):
        value.update(chunk)
    return value.hexdigest()


def check_database(path):
    with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as con:
        if con.execute("PRAGMA integrity_check").fetchall() != [("ok",)]:
            raise RuntimeError("Database integrity check failed")
        if con.execute("PRAGMA foreign_key_check").fetchone():
            raise RuntimeError("Database foreign-key check failed")


def valid_member(name):
    return (name == "directory.db" or (name.startswith("source/") and source_member(name[7:])) or
            bool(re.fullmatch(r"(?:data|upload_archive)/[^/\\]+\.xlsx", name, re.I)) or
            bool(re.fullmatch(r"workbook_submissions/[0-9a-f]{32}\.xlsx", name)))


def source_member(name):
    parts = name.split("/")
    if any(not part or part in (".", "..") or part.startswith(".") for part in parts):
        return False
    if "\\" in name or any(part.lower() in ("old_folder", "__pycache__", "backups", "venv", "secrets") for part in parts):
        return False
    if name in ("app.py", "requirements.txt"):
        return True
    suffix = Path(name).suffix.lower()
    return ((parts[0] in ("utils", "scripts") and suffix == ".py"
             and Path(name).stem.lower() not in ("wsgi", "config", "settings", "secrets", "credentials"))
            or (parts[0] == "templates" and suffix == ".html")
            or (parts[0] == "static" and suffix in (".css", ".js", ".svg", ".png", ".jpg", ".jpeg", ".ico", ".woff", ".woff2"))
            or (name.startswith("utils/import_profiles/") and suffix == ".json"))


def copy_source(project, stage):
    result = subprocess.run(["git", "ls-files", "-z"], cwd=project,
                            capture_output=True, timeout=10, check=True)
    names = [name for name in result.stdout.decode("utf-8").split("\0") if source_member(name)]
    if not {"app.py", "requirements.txt"} <= set(names):
        raise RuntimeError("Required tracked source files are missing")
    for name in names:
        original = safe_path(project / name)
        target = stage / "source" / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(original, target)
        if digest(original) != digest(target):
            raise RuntimeError("Source changed during snapshot")
    status = subprocess.run(["git", "status", "--porcelain", "--", *names], cwd=project,
                            capture_output=True, timeout=10, check=True)
    return bool(status.stdout)


def verify(archive):
    archive = safe_path(archive)
    with zipfile.ZipFile(archive) as z:
        names = z.namelist()
        if len(names) != len(set(names)):
            raise ValueError("Duplicate archive members")
        manifest = json.loads(z.read("manifest.json"))
        if manifest.get("format") not in (FORMAT, SOURCE_FORMAT):
            raise ValueError("Unrecognized backup format")
        files = manifest["files"]
        has_source = any(name.startswith("source/") for name in files)
        if has_source and (manifest.get("format") != SOURCE_FORMAT or
                           not {"source/app.py", "source/requirements.txt"} <= set(files)):
            raise ValueError("Incomplete source snapshot")
        if manifest.get("format") == SOURCE_FORMAT and manifest.get("includes_source") != has_source:
            raise ValueError("Source snapshot metadata mismatch")
        if "directory.db" not in files or set(names) != set(files) | {"manifest.json"}:
            raise ValueError("Unexpected archive members")
        for name, expected in files.items():
            if not valid_member(name) or z.getinfo(name).file_size != expected["size"]:
                raise ValueError("Invalid archive member or size")
            with z.open(name) as stream:
                if stream_digest(stream) != expected["sha256"]:
                    raise ValueError("Archive checksum mismatch")
        with tempfile.TemporaryDirectory(prefix="backup-verify-") as temporary:
            db = Path(temporary) / "directory.db"
            with z.open("directory.db") as src, db.open("xb") as out:
                shutil.copyfileobj(src, out)
            check_database(db)
            with closing(sqlite3.connect(db)) as con: check_snapshot(con,files)
    created = datetime.fromisoformat(manifest["created_utc"])
    if created.tzinfo is None:
        raise ValueError("Missing timestamp timezone")
    return manifest


def retained(records, now, daily=7, weekly=3, monthly=2, source_records=None):
    local_now = now.astimezone(MANILA)
    current_week = local_now.date().isocalendar()[:2]
    current_month = (local_now.year, local_now.month)
    days, weeks, months = {}, {}, {}
    for path, created in sorted(records, key=lambda item: (item[1], item[0].name)):
        local = created.astimezone(MANILA)
        day = local.date()
        week = day.isocalendar()[:2]
        month = (local.year, local.month)
        days[day] = path
        if week < current_week:
            weeks[week] = path
        if month < current_month:
            months[month] = path
    keep = set()
    for groups, count in ((days, daily), (weeks, weekly)):
        keep.update(groups[key] for key in sorted(groups)[-count:])
    if source_records is None:
        keep.update(months[key] for key in sorted(months)[-monthly:])
    else:
        source_months = {}
        for path, created in sorted(source_records, key=lambda item: (item[1], item[0].name)):
            local = created.astimezone(MANILA)
            source_months[(local.year, local.month)] = path
        selected = sorted(source_months)[-monthly:]
        keep.update(source_months[key] for key in selected)
        # Preserve legacy monthly history while source snapshots accumulate.
        remaining = monthly - len(selected)
        eligible = sorted(key for key in months if key not in selected)
        if remaining:
            keep.update(months[key] for key in eligible[-remaining:])
    return keep


def create_backup(project, destination, now, include_source=False, *, lock_held=False):
    source = safe_path(project / "directory.db")
    if not source.is_file():
        raise FileNotFoundError("Source database is missing")
    with tempfile.TemporaryDirectory(dir=destination, prefix=".staging-") as tmp:
        stage = Path(tmp)
        # Web approval already holds this same process lock. CLI callers always acquire it.
        with (nullcontext() if lock_held else file_lock(project / ".backup-import.lock", timeout=60)):
            source_dirty = copy_source(project, stage) if include_source else None
            deadline = time.monotonic() + 600
            def progress(status, remaining, total):
                if time.monotonic() > deadline:
                    raise TimeoutError("Database snapshot timed out")
            with closing(sqlite3.connect(source.as_uri() + "?mode=ro", uri=True)) as src:
                with closing(sqlite3.connect(stage / "directory.db")) as out:
                    src.backup(out, pages=256, progress=progress, sleep=0.1)
            for folder in ("data", "upload_archive", "workbook_submissions"):
                directory = safe_path(project / folder)
                if not directory.is_dir():
                    if folder=="workbook_submissions": continue  # Absent before the first submission.
                    raise FileNotFoundError(f"Required workbook directory missing: {directory}")
                (stage / folder).mkdir()
                for original in directory.iterdir():
                    if folder=="workbook_submissions" and not re.fullmatch(r"[0-9a-f]{32}\.xlsx",original.name): continue
                    if original.name.startswith("~$") or original.suffix.lower() != ".xlsx":
                        continue
                    original = safe_path(original)
                    if not original.is_file():
                        raise ValueError("Workbook must be a regular file")
                    target = stage / folder / original.name
                    shutil.copyfile(original, target)
                    if digest(original) != digest(target):
                        raise RuntimeError("Workbook changed during snapshot")
            # External edits/deployments must be excluded during this phase.
            revision = subprocess.run(["git", "rev-parse", "HEAD"], cwd=project,
                                      capture_output=True, text=True, timeout=10)
        check_database(stage / "directory.db")
        files = {p.relative_to(stage).as_posix(): {"size": p.stat().st_size, "sha256": digest(p)}
                 for p in stage.rglob("*") if p.is_file()}
        manifest = {"format": SOURCE_FORMAT, "created_utc": now.isoformat(),
                    "includes_source": include_source, "source_dirty": source_dirty,
                    "python_version": sys.version.split()[0],
                    "git_commit": revision.stdout.strip() if revision.returncode == 0 else None,
                    "files": files}
        (stage / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        temporary_archive = stage / "complete.zip"
        with zipfile.ZipFile(temporary_archive, "x", compression=zipfile.ZIP_DEFLATED) as z:
            for name in (*files, "manifest.json"):
                z.write(stage / name, name)
        verify(temporary_archive)
        with temporary_archive.open("r+b") as stream:
            os.fsync(stream.fileno())
        name = f"recovery-{now.strftime('%Y%m%dT%H%M%S%fZ')}-{uuid.uuid4().hex}.zip"
        final = destination / name
        os.link(temporary_archive, final)
        logging.info("Created and verified %s (%s bytes)", final, final.stat().st_size)
        return final


def backup(args):
    project, destination = safe_path(args.project), safe_path(args.destination)
    if not project.is_dir() or project == destination or project.is_relative_to(destination) or destination.is_relative_to(project):
        raise ValueError("Project and backup directories must be separate")
    destination.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(destination, 0o700)
    with file_lock(destination / ".scheduled-backup.lock", timeout=0):
        now = datetime.now(timezone.utc)
        records, source_records = [], []
        # Fail closed: a damaged recognized archive prevents all housekeeping.
        for path in destination.iterdir():
            if not PATTERN.fullmatch(path.name):
                continue
            manifest = verify(path)
            created = datetime.fromisoformat(manifest["created_utc"])
            if created > now:
                raise ValueError("Future backup timestamp; cleanup cancelled")
            records.append((path, created))
            if manifest.get("includes_source"):
                source_records.append((path, created))
        local_now = now.astimezone(MANILA)
        month = (local_now.year, local_now.month)
        has_month_source = any((created.astimezone(MANILA).year, created.astimezone(MANILA).month) == month
                               for _, created in source_records)
        include_source = getattr(args, "include_source_now", False) or not has_month_source
        new = create_backup(project, destination, now, include_source=include_source)
        records.append((new, now))
        if include_source:
            source_records.append((new, now))
        logging.info("Source snapshot included: %s", include_source)
        keep = retained(records, now, args.daily, args.weekly, args.monthly, source_records)
        expired = sorted(path for path, _ in records if path not in keep)
        for path in expired:
            logging.info("%s %s", "DELETE" if args.cleanup else "WOULD DELETE", path)
            if args.cleanup:
                path.unlink()
        logging.info("Retained recovery points: %s; expired: %s", len(keep), len(expired))


def restore(archive, destination):
    manifest = verify(archive)
    destination = safe_path(destination)
    destination.mkdir(mode=0o700, parents=False, exist_ok=False)
    with zipfile.ZipFile(archive) as z:
        for name in (*manifest["files"], "manifest.json"):
            target = destination / name
            target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            with z.open(name) as src, target.open("xb") as out:
                shutil.copyfileobj(src, out)
    check_database(destination / "directory.db")
    logging.info("Restored into NEW isolated directory: %s", destination)


def main():
    os.umask(0o077)
    logging.basicConfig(level=logging.INFO, format="%(asctime)sZ %(levelname)s %(message)s")
    logging.Formatter.converter = time.gmtime
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", type=Path)
    parser.add_argument("--destination", type=Path)
    for label, default in (("daily", 7), ("weekly", 3), ("monthly", 2)):
        parser.add_argument("--" + label, type=int, default=default)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--cleanup", action="store_true")
    mode.add_argument("--cleanup-preview", action="store_true")
    parser.add_argument("--verify", type=Path)
    parser.add_argument("--restore-to", type=Path)
    parser.add_argument("--include-source-now", action="store_true",
                        help="Include a source snapshot even if this month already has one")
    args = parser.parse_args()
    if min(args.daily, args.weekly, args.monthly) < 1:
        parser.error("Retention counts must be positive")
    try:
        if args.verify:
            if args.restore_to:
                restore(args.verify, args.restore_to)
            else:
                verify(args.verify)
                logging.info("Archive verified")
        elif args.project and args.destination and not args.restore_to:
            backup(args)
        else:
            parser.error("Provide --project and --destination, or --verify")
    except Exception:
        logging.exception("Backup operation failed; review logs before retrying")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
