"""One runner per computer at a time.

A runner started by hand while the one started at login is still running would poll with the same key and could claim a job the person expected the other to run, and two windows would confuse them.
So each runner holds a lock on a small file in its own folder for as long as it lives. The operating system lets go of it when the process ends, however it ends (a crash, a kill, a shutdown), so a
stale lock cannot lock anyone out, which a file with a process id in it can.
"""
import os
from pathlib import Path
from typing import Any, Optional

from .config import home
from .service import service_id


class InstanceLock:
    def __init__(self) -> None:
        self.path = home() / f"{service_id()}.lock"
        self._handle: Optional[Any] = None

    def acquire(self) -> bool:
        """True when this process now holds the lock; False when another runner for this project already does."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        handle = open(self.path, "a+b")
        try:
            handle.seek(0)
            if not handle.read(1):
                handle.write(b"x")
                handle.flush()
            handle.seek(0)
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            handle.close()
            return False
        self._handle = handle
        return True

    def release(self) -> None:
        handle, self._handle = self._handle, None
        if handle is None:
            return
        try:
            if os.name == "nt":
                import msvcrt

                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        except OSError:
            pass
        handle.close()
