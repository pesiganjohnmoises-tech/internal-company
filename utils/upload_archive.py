"""Private, verified preservation of completed temporary Excel uploads."""
import hashlib
import os
from pathlib import Path
import shutil
import tempfile


def _digest(path):
    result = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            result.update(chunk)
    return result.digest()


def preserve_upload(source, archive_dir):
    """Publish a verified copy without overwriting or deleting the source."""
    source, archive_dir = Path(source), Path(archive_dir)
    if source.is_symlink() or not source.is_file() or source.suffix.lower() != ".xlsx":
        raise ValueError("Expected a regular Excel upload")
    for component in (archive_dir, *archive_dir.parents):
        if component.is_symlink():
            raise ValueError("Archive path must not contain symlinks")
    archive_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    if source.parent.resolve() == archive_dir.resolve():
        raise ValueError("Archive must be separate from temporary uploads")
    os.chmod(archive_dir, 0o700)
    destination = archive_dir / source.name
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=archive_dir, prefix=".partial-", delete=False) as out:
            temporary = Path(out.name)
            with source.open("rb") as incoming:
                shutil.copyfileobj(incoming, out)
            out.flush()
            os.fsync(out.fileno())
        if _digest(source) != _digest(temporary):
            raise OSError("Archived upload verification failed")
        # Both paths share a filesystem; hard-link publication fails if the
        # destination exists and never exposes a partially copied workbook.
        os.link(temporary, destination)
        return destination
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
