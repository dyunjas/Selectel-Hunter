import json
import os
import shutil
import time
from pathlib import Path


class InstanceLock:
    """OS-owned lock: released automatically on process death."""
    def __init__(self, path):
        self.path, self.handle = Path(path), None

    def __enter__(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.handle = self.path.open("a+b")
        self.handle.seek(0)
        if self.path.stat().st_size == 0:
            self.handle.write(b"0")
            self.handle.flush()
        self.handle.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(self.handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            self.handle.close()
            raise RuntimeError("Another bot instance already owns this database") from None
        return self

    def __exit__(self, *args):
        self.handle.close()


def write_health(path, db_path, scheduler_ok):
    free = shutil.disk_usage(Path(db_path).resolve().parent).free
    health = {"at": time.time(), "scheduler_ok": scheduler_ok, "free_bytes": free,
              "ok": scheduler_ok and free >= int(os.getenv("MIN_FREE_DISK_MB", "100")) * 1024 * 1024}
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(".tmp")
    temporary.write_text(json.dumps(health), encoding="utf-8")
    temporary.replace(target)
    return health
