"""The session picker's folder tree and the No project folder.

The tree browses the home folder only, one folder per request:

  - the folder is resolved with realpath, must be the home folder or inside it, and every part of
    the resolved path must be printable UTF-8;
  - it is opened O_DIRECTORY | O_NOFOLLOW and listed through that descriptor, with one scandir of
    at most DIRS_SCAN_ENTRIES entries within DIRS_DEADLINE_S;
  - only subfolders are answered, by name, hidden ones last, never a .git folder itself; hidden
    names are left out unless asked for, and only counted;
  - at most DIRS_LIST_MAX rows, and fewer when the answer would pass its output cap;
  - nothing is opened or read in any entry: a subfolder gets one lstat of its .git entry, which
    says whether it is a git checkout;
  - a link is never opened: it is resolved only to tell whether it leads to a folder inside the
    home folder, which is then stat'ed and answered as the link's target; a link that leaves the
    home folder is answered without a target and its target is never looked at;
  - a name or a target that is not printable UTF-8 is left out (a name) or answered without a
    target (a link), and counted.

The No project folder is ~/AutoPilot. It is made only when someone picks it, mode 0700, and only
when nothing is there yet. Whatever is there already is used only when it is a real folder (not a
link) owned by this user that nobody else can write, and it passes the working folder check of
any other folder (jobs.check_cwd); anything else is left as it is and refused.
"""

import os
import stat
import time

from . import consts, fsio, jobs, proto
from .errors import ApError

_NAME_MAX_BYTES = 255
# Room kept under OUTPUT_CAP["dirs"] for the answer's own fields and the ok wrapper.
_CAP_SLACK = 4096


def _under(path, root):
    return path == root or path.startswith(root.rstrip("/") + "/")


def _home_real():
    return os.path.realpath(fsio.home())


def _clean_name(name):
    """The name when it is printable UTF-8 within a file name's size, else None."""
    if not isinstance(name, str) or not name or name in (".", ".."):
        return None
    try:
        size = len(name.encode("utf-8"))
    except UnicodeEncodeError:
        return None
    if size > _NAME_MAX_BYTES or not name.isprintable():
        return None
    return name


def _clean_path(path):
    """path when it is absolute UTF-8 within CWD_MAX_BYTES and every part of it is a clean name."""
    if not isinstance(path, str) or not path.startswith("/"):
        return None
    try:
        size = len(path.encode("utf-8"))
    except UnicodeEncodeError:
        return None
    if size > consts.CWD_MAX_BYTES:
        return None
    if path == "/":
        return path
    return path if all(_clean_name(part) is not None for part in path[1:].split("/")) else None


def _git_at(name, dir_fd):
    """Whether name (relative to dir_fd) holds a .git folder or file, looked at with lstat."""
    try:
        info = os.stat(os.path.join(name, ".git"), dir_fd=dir_fd, follow_symlinks=False)
    except (OSError, ValueError):
        return False
    return stat.S_ISDIR(info.st_mode) or stat.S_ISREG(info.st_mode)


def _entry(entry, real, home, uid, dir_fd):
    """(row or None, unclean) for one directory entry; only subfolders and links to one are rows."""
    if entry.name == ".git":
        return None, False
    name = _clean_name(entry.name)
    if name is None:
        return None, True
    try:
        info = entry.stat(follow_symlinks=False)
    except OSError:
        return None, False
    row = {"name": name, "hidden": name.startswith("."), "git": False, "own": info.st_uid == uid,
           "link": False, "target": None, "outside": False}
    if stat.S_ISDIR(info.st_mode):
        row["git"] = _git_at(name, dir_fd)
        return row, False
    if not stat.S_ISLNK(info.st_mode):
        return None, False
    row["link"] = True
    target = os.path.realpath(os.path.join(real, name))
    if not _under(target, home) or target == home:
        row["outside"] = True
        return row, False
    try:
        target_info = os.stat(target)
    except OSError:
        return None, False
    if not stat.S_ISDIR(target_info.st_mode):
        return None, False
    row["own"] = target_info.st_uid == uid
    if _clean_path(target) is None:
        return row, True
    row["target"] = target
    row["git"] = _git_at(target, None)
    return row, False


def list_dirs(path, hidden=False):
    """Dirs answer (without "ok") for one folder inside the home folder.

    state: "ok", "missing" (gone or not a folder) or "denied" (cannot be listed). A path outside
    the home folder, or one whose resolved form is not clean, is invalid_cwd. Paths in the answer
    are real paths. Hidden subfolders are answered only with hidden; hiddenCount counts them.
    """
    home = _home_real()
    real = os.path.realpath(path)
    if not _under(real, home) or _clean_path(real) is None:
        raise ApError("invalid_cwd", "path")
    answer = {"path": real, "home": home, "parent": None if real == home else os.path.dirname(real),
              "git": False, "own": False, "state": "ok", "entries": [], "truncated": False,
              "hidden": bool(hidden), "hiddenCount": 0, "skipped": 0, "workspace": workspace_path_real()}
    try:
        fd = os.open(real, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
    except (FileNotFoundError, NotADirectoryError):
        answer["state"] = "missing"
        return answer
    except OSError:
        answer["state"] = "denied"
        return answer
    uid = os.getuid()
    rows, skipped, hidden_count, truncated = [], 0, 0, False
    try:
        info = os.fstat(fd)
        answer["own"] = info.st_uid == uid
        answer["git"] = real != home and _git_at(".", fd)
        deadline = time.monotonic() + consts.DIRS_DEADLINE_S
        seen = 0
        with os.scandir(fd) as iterator:
            for entry in iterator:
                if seen >= consts.DIRS_SCAN_ENTRIES or time.monotonic() > deadline:
                    truncated = True
                    break
                seen += 1
                row, unclean = _entry(entry, real, home, uid, fd)
                if unclean:
                    skipped += 1
                if row is None:
                    continue
                if row["hidden"]:
                    hidden_count += 1
                    if not hidden:
                        continue
                rows.append(row)
    except OSError:
        answer["state"] = "denied"
        return answer
    finally:
        os.close(fd)
    rows.sort(key=lambda row: (row["hidden"], row["name"].casefold(), row["name"]))
    if len(rows) > consts.DIRS_LIST_MAX:
        rows, truncated = rows[:consts.DIRS_LIST_MAX], True
    # Rows that would carry the answer past its output cap are cut, never the whole answer.
    room = consts.OUTPUT_CAP["dirs"] - _CAP_SLACK - len(proto.serialize(answer))
    kept = 0
    for row in rows:
        room -= len(proto.serialize(row)) + 1
        if room < 0:
            truncated = True
            break
        kept += 1
    answer.update({"entries": rows[:kept], "truncated": truncated, "skipped": skipped,
                   "hiddenCount": hidden_count})
    return answer


def workspace_path():
    return os.path.join(fsio.home(), consts.WORKSPACE_NAME)


def workspace_path_real():
    return os.path.join(_home_real(), consts.WORKSPACE_NAME)


def _usable(info):
    """A real folder, not a link, owned by this user, that nobody else can write."""
    return (stat.S_ISDIR(info.st_mode) and info.st_uid == os.getuid()
            and stat.S_IMODE(info.st_mode) & 0o022 == 0)


def workspace(create):
    """Workspace answer (without "ok"): the No project folder, made first when create is set.

    exists says whether it is there and usable now; refused says something else is at the path
    (a file, a link, another owner's folder, a folder others can write), which is never changed.
    With create, a refused path is invalid_cwd.
    """
    path = workspace_path()
    created = False
    try:
        info = os.lstat(path)
    except FileNotFoundError:
        info = None
    except OSError:
        raise ApError("invalid_cwd", "workspace") from None
    if info is None and create:
        try:
            os.mkdir(path, 0o700)
            created = True
        except FileExistsError:
            pass
        except OSError:
            raise ApError("invalid_cwd", "workspace") from None
        try:
            info = os.lstat(path)
        except OSError:
            raise ApError("invalid_cwd", "workspace") from None
    real = None
    if info is not None and _usable(info):
        try:
            real = jobs.check_cwd(path)
        except ApError:
            real = None
    refused = info is not None and real is None
    if refused and create:
        raise ApError("invalid_cwd", "workspace")
    return {"path": real or workspace_path_real(), "exists": real is not None, "created": created,
            "refused": refused}
