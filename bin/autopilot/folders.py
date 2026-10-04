"""The session picker's folder tree and the No project folder.

The tree browses the home folder only, one folder per request:

  - the folder is resolved with realpath and must be the home folder or inside it;
  - it is listed with one scandir, at most DIRS_SCAN_ENTRIES entries and DIRS_DEADLINE_S;
  - only subfolders are answered, at most DIRS_LIST_MAX, by name, hidden ones last, and never a
    .git folder itself;
  - an entry is never followed: a link is answered as a link, with its target only when that
    target is a folder inside the home folder, so the tree can open it there;
  - a name that is not printable UTF-8 is left out and counted;
  - per subfolder one lstat of its .git entry says whether it is a git checkout, and nothing in it
    is opened or read.

The No project folder is ~/AutoPilot. It is made only when someone picks it, mode 0700, and only
when nothing is there yet; an existing entry is used only when it passes the same working folder
check as any other folder (jobs.check_cwd).
"""

import os
import stat
import time

from . import consts, fsio, jobs
from .errors import ApError

_NAME_MAX_BYTES = 255


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


def _is_git(path):
    try:
        info = os.lstat(os.path.join(path, ".git"))
    except OSError:
        return False
    return stat.S_ISDIR(info.st_mode) or stat.S_ISREG(info.st_mode)


def _entry(entry, home, uid):
    """One answer row for a subfolder or a link to one, or None for anything else."""
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
           "link": False, "target": None}
    if stat.S_ISDIR(info.st_mode):
        row["git"] = _is_git(entry.path)
        return row, False
    if not stat.S_ISLNK(info.st_mode):
        return None, False
    target = os.path.realpath(entry.path)
    try:
        target_info = os.stat(target)
    except OSError:
        return None, False
    if not stat.S_ISDIR(target_info.st_mode):
        return None, False
    row["link"] = True
    row["own"] = target_info.st_uid == uid
    if _under(target, home) and len(target.encode("utf-8", "surrogatepass")) <= consts.CWD_MAX_BYTES:
        row["target"] = target
        row["git"] = _is_git(target)
    return row, False


def list_dirs(path):
    """Dirs answer (without "ok") for one folder inside the home folder.

    state: "ok", "missing" (gone or not a folder) or "denied" (cannot be listed). A path outside
    the home folder is invalid_cwd. Paths in the answer are real paths.
    """
    home = _home_real()
    real = os.path.realpath(path)
    if not _under(real, home) or len(real.encode("utf-8", "surrogatepass")) > consts.CWD_MAX_BYTES:
        raise ApError("invalid_cwd", "path")
    answer = {"path": real, "home": home, "parent": None if real == home else os.path.dirname(real),
              "git": False, "own": False, "state": "ok", "entries": [], "truncated": False,
              "skipped": 0, "workspace": workspace_path_real()}
    try:
        info = os.stat(real)
    except FileNotFoundError:
        answer["state"] = "missing"
        return answer
    except OSError:
        answer["state"] = "denied"
        return answer
    if not stat.S_ISDIR(info.st_mode):
        answer["state"] = "missing"
        return answer
    uid = os.getuid()
    answer["own"] = info.st_uid == uid
    answer["git"] = real != home and _is_git(real)
    deadline = time.monotonic() + consts.DIRS_DEADLINE_S
    rows, seen, skipped, truncated = [], 0, 0, False
    try:
        with os.scandir(real) as iterator:
            for entry in iterator:
                if seen >= consts.DIRS_SCAN_ENTRIES or time.monotonic() > deadline:
                    truncated = True
                    break
                seen += 1
                row, bad_name = _entry(entry, home, uid)
                if bad_name:
                    skipped += 1
                if row is not None:
                    rows.append(row)
    except PermissionError:
        answer["state"] = "denied"
        return answer
    except FileNotFoundError:
        answer["state"] = "missing"
        return answer
    except OSError:
        answer["state"] = "denied"
        return answer
    rows.sort(key=lambda row: (row["hidden"], row["name"].casefold(), row["name"]))
    if len(rows) > consts.DIRS_LIST_MAX:
        rows, truncated = rows[:consts.DIRS_LIST_MAX], True
    answer.update({"entries": rows, "truncated": truncated, "skipped": skipped})
    return answer


def workspace_path():
    return os.path.join(fsio.home(), consts.WORKSPACE_NAME)


def workspace_path_real():
    return os.path.join(_home_real(), consts.WORKSPACE_NAME)


def workspace(create):
    """Workspace answer (without "ok"): the No project folder, made first when create is set.

    exists says whether it is there now. A made or existing folder must pass jobs.check_cwd;
    anything else there (a file, a link out of the home folder, another owner) is invalid_cwd
    and is left as it is.
    """
    path = workspace_path()
    created = False
    try:
        os.lstat(path)
        present = True
    except FileNotFoundError:
        present = False
    except OSError:
        raise ApError("invalid_cwd", "workspace") from None
    if not present and create:
        try:
            os.mkdir(path, 0o700)
            created = True
        except FileExistsError:
            pass
        except OSError:
            raise ApError("invalid_cwd", "workspace") from None
        present = True
    real = None
    if present:
        try:
            real = jobs.check_cwd(path)
        except ApError:
            if create:
                raise
    return {"path": real or workspace_path_real(), "exists": real is not None, "created": created}
