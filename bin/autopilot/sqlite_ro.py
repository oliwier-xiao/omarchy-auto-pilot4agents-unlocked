"""Read-only, bounded SQLite opener for other programs' stores.

OpenCode keeps its sessions in a database that is routinely larger than a gigabyte,
and Codex keeps a thread index next to its rollouts. Both belong to the user's agents,
so the helper only ever looks at them and never waits on them:

  open(O_RDONLY | O_NOFOLLOW | O_NONBLOCK | O_CLOEXEC)   refuse a symlink or a FIFO
  fstat that descriptor                                  regular file owned by this user
  lstat the -wal/-shm/-journal companions                regular files owned by this user
  main + wal size <= max_bytes                           else TooLarge (state_refused)
  connect "file:<quoted>?mode=ro"                        then re-check the inode by name
  query_only, busy_timeout, mmap_size 0, small cache     no writes, no mapping, bounded memory
  progress handler                                       interrupts any statement past the deadline

The descriptor is held while SQLite opens the same name, so a file swapped in between
is caught by comparing device and inode afterwards.
"""

import os
import sqlite3
import stat
import time
import urllib.parse

from .errors import ApError

_OPEN_FLAGS = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC
_COMPANIONS = ("-wal", "-shm", "-journal")
_BUSY_TIMEOUT_MS = 2000
_CACHE_KIB = 2048
_VALUE_MAX_BYTES = 4 * 1024 * 1024
_SQL_MAX_BYTES = 64 * 1024
_PROGRESS_OPS = 200


class TooLarge(ApError):
    """The store plus its write-ahead log is over the ceiling. Still code state_refused."""

    def __init__(self):
        super().__init__("state_refused")


def _owned_regular(info):
    return stat.S_ISREG(info.st_mode) and info.st_uid == os.getuid()


def open_ro(path, *, max_bytes, deadline_s):
    """Open path read-only with every bound applied, or raise.

    Raises FileNotFoundError when the store does not exist and ApError("state_refused") for
    every refusal. A size refusal is the TooLarge subclass, so a scanner can report it as
    "too large" rather than "unreadable". SQLite errors while configuring the connection
    propagate as sqlite3.Error; callers catch them.
    """
    if not isinstance(path, str) or not path.startswith("/") or "\x00" in path:
        raise ApError("state_refused")
    try:
        fd = os.open(path, _OPEN_FLAGS)
    except FileNotFoundError:
        raise
    except OSError:
        raise ApError("state_refused") from None
    try:
        info = os.fstat(fd)
        if not _owned_regular(info):
            raise ApError("state_refused")
        total = info.st_size
        for suffix in _COMPANIONS:
            try:
                companion = os.lstat(path + suffix)
            except FileNotFoundError:
                continue
            except OSError:
                raise ApError("state_refused") from None
            if not _owned_regular(companion):
                raise ApError("state_refused")
            if suffix == "-wal":
                total += companion.st_size
        if total > max_bytes:
            raise TooLarge()

        uri = "file:" + urllib.parse.quote(path, safe="/") + "?mode=ro"
        connection = sqlite3.connect(uri, uri=True, timeout=_BUSY_TIMEOUT_MS / 1000.0,
                                     isolation_level=None)
        try:
            try:
                latest = os.lstat(path)
            except OSError:
                raise ApError("state_refused") from None
            if not stat.S_ISREG(latest.st_mode) or latest.st_dev != info.st_dev \
                    or latest.st_ino != info.st_ino:
                raise ApError("state_refused")
            connection.setlimit(sqlite3.SQLITE_LIMIT_LENGTH, _VALUE_MAX_BYTES)
            connection.setlimit(sqlite3.SQLITE_LIMIT_SQL_LENGTH, _SQL_MAX_BYTES)
            connection.execute("PRAGMA query_only = 1")
            connection.execute("PRAGMA busy_timeout = %d" % _BUSY_TIMEOUT_MS)
            connection.execute("PRAGMA mmap_size = 0")
            connection.execute("PRAGMA cache_size = -%d" % _CACHE_KIB)
            connection.execute("PRAGMA temp_store = MEMORY")
            # Installed last so the configuration statements above cannot be interrupted.
            deadline = time.monotonic() + max(0.0, float(deadline_s))
            connection.set_progress_handler(lambda: 1 if time.monotonic() >= deadline else 0,
                                            _PROGRESS_OPS)
            return connection
        except BaseException:
            connection.close()
            raise
    finally:
        os.close(fd)


def interrupted(error):
    """True when a sqlite3 error came from the progress-handler deadline."""
    code = getattr(error, "sqlite_errorcode", None)
    if code is not None and code == getattr(sqlite3, "SQLITE_INTERRUPT", 9):
        return True
    return "interrupt" in str(error).lower()
