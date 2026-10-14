"""Process locks shared by web imports and scheduled backups."""
from contextlib import contextmanager
import os
from pathlib import Path
import time


@contextmanager
def file_lock(path, timeout=30):
    path = Path(path)
    if path.is_symlink():
        raise ValueError("Lock file cannot be a symlink")
    fd = os.open(path, os.O_CREAT | os.O_RDWR, 0o600)
    locked = False
    try:
        if os.name == "nt":
            import msvcrt
            if os.fstat(fd).st_size == 0:
                os.write(fd, b"0")
            def acquire():
                os.lseek(fd, 0, os.SEEK_SET)
                msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
            def release():
                os.lseek(fd, 0, os.SEEK_SET)
                msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
        else:
            import fcntl
            def acquire():
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            def release():
                fcntl.flock(fd, fcntl.LOCK_UN)
        deadline = time.monotonic() + timeout
        while True:
            try:
                acquire()
                locked = True
                break
            except (BlockingIOError, PermissionError):
                if time.monotonic() >= deadline:
                    raise TimeoutError("Backup/import lock is busy")
                time.sleep(0.1)
        yield
    finally:
        if locked:
            release()
        os.close(fd)
