"""Filesystem access for the helper: state by directory descriptor, bounded reads, atomic writes.

The state folder is walked one component at a time with O_DIRECTORY|O_NOFOLLOW, and every
later open is relative to the descriptor that walk produced, so a component swapped for a
symlink after the check cannot redirect a read or a write. Reads use O_NOFOLLOW|O_NONBLOCK
(a FIFO planted at the path returns instead of blocking inside open), then decide on fstat
of that same descriptor: a regular file, owned by this user, one link, private mode, within
the cap. Writes go to an O_EXCL temporary with a random name in the same folder, are synced,
and replace the target with a rename between the same two descriptors.

All paths derive from HOME. XDG_STATE_HOME and XDG_CONFIG_HOME are ignored on purpose, so the
panel, the systemd unit and a terminal always agree on where the state is.
"""
import contextlib
import fcntl
import os
import pwd
import secrets
import stat
import time

from . import consts, edition, proto
from .errors import ApError

_DIR_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
_READ_FLAGS = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC
_LIST_CAP = 4096
_NAME_MAX = 200


def _uid():
    return os.getuid()


def _read_capped(fd, cap, too_large="state_too_large"):
    """Read at most cap bytes from fd; one byte more means the file grew past the cap."""
    chunks = []
    total = 0
    while True:
        try:
            chunk = os.read(fd, min(1 << 20, cap + 1 - total))
        except BlockingIOError:
            break
        except InterruptedError:
            continue
        if not chunk:
            break
        chunks.append(chunk)
        total += len(chunk)
        if total > cap:
            raise ApError(too_large)
    return b"".join(chunks)


def _open_dir_at(parent_fd, name, create, code, private):
    """Open (and optionally create) one directory component relative to parent_fd.

    private: the final state folder, which must be 0700 and ours. Otherwise an ancestor such as
    ~/.local, which must be ours, not a symlink and not world-writable.
    """
    created = False
    try:
        fd = os.open(name, _DIR_FLAGS, dir_fd=parent_fd)
    except FileNotFoundError:
        if not create:
            return None
        try:
            os.mkdir(name, 0o700, dir_fd=parent_fd)
            created = True
        except FileExistsError:
            pass
        except OSError:
            raise ApError(code) from None
        try:
            fd = os.open(name, _DIR_FLAGS, dir_fd=parent_fd)
        except OSError:
            raise ApError(code) from None
    except OSError:
        raise ApError(code) from None
    try:
        st = os.fstat(fd)
        if not stat.S_ISDIR(st.st_mode) or st.st_uid != _uid():
            raise ApError(code)
        if private:
            if created:
                os.fchmod(fd, 0o700)
                st = os.fstat(fd)
            if stat.S_IMODE(st.st_mode) & 0o077:
                raise ApError(code)
        elif st.st_mode & 0o002:
            raise ApError(code)
    except ApError:
        os.close(fd)
        raise
    except OSError:
        os.close(fd)
        raise ApError(code) from None
    return fd


class StateDir:
    """A private folder held open by descriptor. Every name is a single path component."""

    def __init__(self, path, fd, code="state_refused"):
        self.path = path
        self.fd = fd
        self._code = code

    def close(self):
        if self.fd is not None and self.fd >= 0:
            try:
                os.close(self.fd)
            except OSError:
                pass
            self.fd = -1

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        self.close()
        return False

    def __del__(self):
        self.close()

    def _check_name(self, name):
        if (not isinstance(name, str) or not name or len(name) > _NAME_MAX or "/" in name
                or "\0" in name or name in (".", "..")):
            raise ApError(self._code)

    def read_bytes(self, name, cap):
        self._check_name(name)
        try:
            fd = os.open(name, _READ_FLAGS, dir_fd=self.fd)
        except FileNotFoundError:
            return None
        except OSError:
            raise ApError(self._code) from None
        try:
            st = os.fstat(fd)
            if (not stat.S_ISREG(st.st_mode) or st.st_uid != _uid() or st.st_nlink != 1
                    or stat.S_IMODE(st.st_mode) & 0o077):
                raise ApError(self._code)
            if st.st_size > cap:
                raise ApError("state_too_large")
            return _read_capped(fd, cap)
        except OSError:
            raise ApError(self._code) from None
        finally:
            os.close(fd)

    def read_json(self, name, cap):
        data = self.read_bytes(name, cap)
        if data is None:
            return None
        try:
            return proto.parse_json_bytes(data)
        except (UnicodeDecodeError, ValueError):
            raise ApError("state_corrupt") from None

    def write_atomic(self, name, data):
        self._check_name(name)
        if not isinstance(data, (bytes, bytearray)):
            raise ApError("internal")
        tmp = ".%s.%s.tmp" % (name, secrets.token_hex(8))
        try:
            fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
                         0o600, dir_fd=self.fd)
        except OSError:
            raise ApError(self._code) from None
        done = False
        try:
            os.fchmod(fd, 0o600)
            view = memoryview(bytes(data))
            while view:
                written = os.write(fd, view)
                view = view[written:]
            os.fsync(fd)
            done = True
        except OSError:
            raise ApError(self._code) from None
        finally:
            os.close(fd)
            if not done:
                self._discard(tmp)
        try:
            os.replace(tmp, name, src_dir_fd=self.fd, dst_dir_fd=self.fd)
        except OSError:
            self._discard(tmp)
            raise ApError(self._code) from None
        try:
            os.fsync(self.fd)
        except OSError:
            pass

    def _discard(self, name):
        try:
            os.unlink(name, dir_fd=self.fd)
        except OSError:
            pass

    def unlink(self, name):
        self._check_name(name)
        try:
            os.unlink(name, dir_fd=self.fd)
            return True
        except FileNotFoundError:
            return False
        except OSError:
            raise ApError(self._code) from None

    def stat_name(self, name):
        """lstat of one entry, or None when it is absent."""
        self._check_name(name)
        try:
            return os.stat(name, dir_fd=self.fd, follow_symlinks=False)
        except FileNotFoundError:
            return None
        except OSError:
            raise ApError(self._code) from None

    def list_names(self):
        names = []
        try:
            os.lseek(self.fd, 0, os.SEEK_SET)
        except OSError:
            pass
        try:
            with os.scandir(self.fd) as entries:
                for entry in entries:
                    names.append(entry.name)
                    if len(names) >= _LIST_CAP:
                        break
        except OSError:
            raise ApError(self._code) from None
        names.sort()
        return names

    def subdir(self, name, create=True):
        self._check_name(name)
        fd = _open_dir_at(self.fd, name, create, self._code, private=True)
        if fd is None:
            return None
        return StateDir(self.path + "/" + name, fd, self._code)

    @contextlib.contextmanager
    def lock(self, wait_s, name="lock"):
        """Exclusive flock on a lock file in this folder; lock_busy after wait_s seconds."""
        self._check_name(name)
        try:
            fd = os.open(name, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC,
                         0o600, dir_fd=self.fd)
        except OSError:
            raise ApError(self._code) from None
        try:
            st = os.fstat(fd)
            if (not stat.S_ISREG(st.st_mode) or st.st_uid != _uid() or st.st_nlink != 1
                    or stat.S_IMODE(st.st_mode) & 0o077):
                raise ApError(self._code)
            deadline = time.monotonic() + max(0.0, float(wait_s))
            while True:
                try:
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except BlockingIOError:
                    if time.monotonic() >= deadline:
                        raise ApError("lock_busy") from None
                    time.sleep(0.05)
                except InterruptedError:
                    continue
                except OSError:
                    raise ApError(self._code) from None
            try:
                yield
            finally:
                try:
                    fcntl.flock(fd, fcntl.LOCK_UN)
                except OSError:
                    pass
        finally:
            os.close(fd)

    def open_append(self, name, cap):
        """Descriptor for a private append-only log; a log already at cap starts over."""
        self._check_name(name)
        try:
            fd = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_APPEND | os.O_NOFOLLOW | os.O_NONBLOCK
                         | os.O_CLOEXEC, 0o600, dir_fd=self.fd)
        except OSError:
            raise ApError(self._code) from None
        try:
            st = os.fstat(fd)
            if (not stat.S_ISREG(st.st_mode) or st.st_uid != _uid() or st.st_nlink != 1
                    or stat.S_IMODE(st.st_mode) & 0o077):
                raise ApError(self._code)
            if st.st_size >= cap:
                os.ftruncate(fd, 0)
        except ApError:
            os.close(fd)
            raise
        except OSError:
            os.close(fd)
            raise ApError(self._code) from None
        return fd


def home():
    """$HOME, which must be an absolute directory owned by this user."""
    value = os.environ.get("HOME", "")
    if not value or "\0" in value or "\n" in value or not os.path.isabs(value):
        raise ApError("bad_env")
    path = os.path.normpath(value)
    try:
        st = os.stat(path)
    except OSError:
        raise ApError("bad_env") from None
    if not stat.S_ISDIR(st.st_mode) or st.st_uid != _uid():
        raise ApError("bad_env")
    return path


def _open_demo_state(path):
    if not os.path.isabs(path) or "\0" in path or "\n" in path:
        raise ApError("state_refused")
    try:
        fd = os.open(os.path.normpath(path), _DIR_FLAGS)
    except OSError:
        raise ApError("state_refused") from None
    st = os.fstat(fd)
    if not stat.S_ISDIR(st.st_mode) or st.st_uid != _uid() or stat.S_IMODE(st.st_mode) & 0o077:
        os.close(fd)
        raise ApError("state_refused")
    return StateDir(os.path.normpath(path), fd)


def open_state(create=True):
    """The state folder, walked from HOME by descriptor. None when absent and create is False.

    A seeded demo folder named by AP4A_STATE_DIR is honoured only while the kill switch file
    exists, so demo jobs can never fire (R0 C14).
    """
    home_path = home()
    demo = os.environ.get(edition.DEMO_STATE_ENV, "")
    if demo and kill_switch_present():
        return _open_demo_state(demo)
    try:
        parent = os.open(home_path, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    except OSError:
        raise ApError("bad_env") from None
    path = home_path
    try:
        for component in (".local", "state", "omarchy"):
            fd = _open_dir_at(parent, component, create, "state_refused", private=False)
            if fd is None:
                return None
            os.close(parent)
            parent = fd
            path += "/" + component
        final = _open_dir_at(parent, edition.STATE_DIR_NAME, create, "state_refused", private=True)
    finally:
        os.close(parent)
    if final is None:
        return None
    return StateDir(path + "/" + edition.STATE_DIR_NAME, final)


def runtime_dir_valid(value):
    """True when value is a per-user runtime folder: grammar, ours, 0700, not a symlink."""
    if not isinstance(value, str) or not consts.XDG_RUNTIME_RE.fullmatch(value):
        return False
    try:
        st = os.lstat(value)
    except OSError:
        return False
    return stat.S_ISDIR(st.st_mode) and st.st_uid == _uid() and not stat.S_IMODE(st.st_mode) & 0o077


def open_runtime():
    """$XDG_RUNTIME_DIR/<edition runtime name>, created 0700. No /tmp fallback."""
    value = os.environ.get("XDG_RUNTIME_DIR", "")
    if not runtime_dir_valid(value):
        raise ApError("runtime_dir")
    try:
        parent = os.open(value, _DIR_FLAGS)
    except OSError:
        raise ApError("runtime_dir") from None
    try:
        fd = _open_dir_at(parent, edition.RUNTIME_DIR_NAME, True, "runtime_dir", private=True)
    finally:
        os.close(parent)
    return StateDir(value + "/" + edition.RUNTIME_DIR_NAME, fd, "runtime_dir")


def read_file_nofollow(path, cap, *, owner_uid_or_root=False):
    """Bounded read of a file this plugin does not own. None when absent.

    Raises state_refused (symlink, not a regular file, foreign owner, unreadable) or
    state_too_large.
    """
    if not isinstance(path, str) or not os.path.isabs(path) or "\0" in path:
        raise ApError("state_refused")
    try:
        fd = os.open(path, _READ_FLAGS)
    except (FileNotFoundError, NotADirectoryError):
        return None
    except OSError:
        raise ApError("state_refused") from None
    try:
        st = os.fstat(fd)
        owners = (_uid(), 0) if owner_uid_or_root else (_uid(),)
        if not stat.S_ISREG(st.st_mode) or st.st_uid not in owners:
            raise ApError("state_refused")
        if st.st_size > cap:
            raise ApError("state_too_large")
        return _read_capped(fd, cap)
    except OSError:
        raise ApError("state_refused") from None
    finally:
        os.close(fd)


def check_trusted_file(path, *, executable, check_ancestors=True):
    """Regular file owned by this user or root, not group/world-writable, ancestors likewise."""
    if not isinstance(path, str) or not os.path.isabs(path) or "\0" in path:
        raise ApError("cli_untrusted")
    owners = (_uid(), 0)
    try:
        st = os.lstat(path)
    except OSError:
        raise ApError("cli_untrusted") from None
    if not stat.S_ISREG(st.st_mode) or st.st_uid not in owners or st.st_mode & 0o022:
        raise ApError("cli_untrusted")
    if executable and not st.st_mode & 0o111:
        raise ApError("cli_untrusted")
    if not check_ancestors:
        return
    _check_ancestors(os.path.dirname(os.path.normpath(path)), owners)


def _check_ancestors(parent, owners):
    """Every folder from parent up to / is a real folder owned by owners and not group/world-writable."""
    while True:
        try:
            pst = os.lstat(parent)
        except OSError:
            raise ApError("cli_untrusted") from None
        if not stat.S_ISDIR(pst.st_mode) or pst.st_uid not in owners or pst.st_mode & 0o022:
            raise ApError("cli_untrusted")
        if parent == "/":
            return
        parent = os.path.dirname(parent)


def check_tool(path):
    """A system tool of consts.TOOLS: owned by root (TOOL_OWNER_UIDS), not group/world-writable, and
    so are the folders above it. A symlink (for example /usr/bin/qs) must itself be owned likewise and
    is checked through to its target. Raises cli_untrusted."""
    if not isinstance(path, str) or not os.path.isabs(path) or "\0" in path:
        raise ApError("cli_untrusted")
    try:
        st = os.lstat(path)
        real = os.path.realpath(path)
        rst = os.lstat(real)
    except OSError:
        raise ApError("cli_untrusted") from None
    if st.st_uid not in consts.TOOL_OWNER_UIDS or rst.st_uid not in consts.TOOL_OWNER_UIDS:
        raise ApError("cli_untrusted")
    if stat.S_ISLNK(st.st_mode):
        _check_ancestors(os.path.dirname(os.path.normpath(path)), (_uid(), 0))
    check_trusted_file(real, executable=True)


def plugin_code_trusted():
    """True when the code a timer runs, possibly days after it was armed, is safe to run.

    bin/ap4a and every module of bin/autopilot are regular files (no symlinks) owned by this user
    or root and not group- or world-writable, and so is every folder above them.
    """
    base = plugin_dir()
    package = os.path.join(base, "bin", "autopilot")
    try:
        check_trusted_file(os.path.join(base, "bin", "ap4a"), executable=True)
        pst = os.lstat(package)
        if not stat.S_ISDIR(pst.st_mode) or pst.st_uid not in (_uid(), 0) or pst.st_mode & 0o022:
            return False
        for name in os.listdir(package):
            if name.endswith(".py"):
                check_trusted_file(os.path.join(package, name), executable=False, check_ancestors=False)
    except (ApError, OSError):
        return False
    return True


def kill_switch_path():
    return "%s/.config/omarchy/%s/%s" % (home(), edition.CONFIG_DIR_NAME, edition.KILL_SWITCH_NAME)


def kill_switch_present():
    """lstat of the kill switch file. An unreadable parent counts as present (fail closed)."""
    try:
        os.lstat(kill_switch_path())
        return True
    except (FileNotFoundError, NotADirectoryError):
        return False
    except OSError:
        return True


def plugin_dir():
    return os.path.realpath(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))


def _manifest():
    try:
        data = read_file_nofollow(os.path.join(plugin_dir(), "manifest.json"), consts.MANIFEST_MAX,
                                  owner_uid_or_root=True)
    except ApError:
        return None
    if data is None:
        return None
    try:
        obj = proto.parse_json_bytes(data)
    except (UnicodeDecodeError, ValueError):
        return None
    return obj if isinstance(obj, dict) else None


def manifest_id():
    value = (_manifest() or {}).get("id")
    return value if isinstance(value, str) else None


def manifest_version():
    value = (_manifest() or {}).get("version")
    return value if isinstance(value, str) and len(value) <= 40 else None


def _lists_plugin(entries, plugin_id):
    if not isinstance(entries, list):
        return False
    for entry in entries:
        if entry == plugin_id:
            return True
        if isinstance(entry, dict) and entry.get("id") == plugin_id:
            return True
    return False


def plugin_enabled_in_shell():
    """True iff shell.json lists the plugin in bar.layout, bar.id or plugins[]; None if unreadable."""
    try:
        data = read_file_nofollow(home() + "/.config/omarchy/shell.json", consts.SHELL_JSON_MAX)
    except ApError:
        return None
    if data is None:
        return False
    try:
        obj = proto.parse_json_bytes(data)
    except (UnicodeDecodeError, ValueError):
        return None
    if not isinstance(obj, dict):
        return None
    plugin_id = edition.PLUGIN_ID
    bar = obj.get("bar")
    if isinstance(bar, dict):
        layout = bar.get("layout")
        if isinstance(layout, dict):
            for side in ("left", "center", "right"):
                if _lists_plugin(layout.get(side), plugin_id):
                    return True
        if bar.get("id") == plugin_id:
            return True
    return _lists_plugin(obj.get("plugins"), plugin_id)


def linger_enabled():
    """Whether systemd keeps this user's manager after logout (DC16: a file, no child process)."""
    try:
        name = pwd.getpwuid(_uid()).pw_name
    except KeyError:
        return None
    if not name or "/" in name:
        return None
    try:
        os.stat("/var/lib/systemd/linger/" + name)
        return True
    except FileNotFoundError:
        return False
    except OSError:
        return None
