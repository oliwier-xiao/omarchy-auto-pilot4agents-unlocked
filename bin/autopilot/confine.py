"""OS confinement for the Auto level of the Unlocked edition (Linux Landlock + seccomp).

Auto turns the shell on, so a job may run commands with nobody watching. This module confines the
agent process to writing only a named set of folders and reading everything except what is never
granted, and it stops the one escape Landlock alone leaves open on current kernels: a sandboxed
process can still connect to a pathname or abstract AF_UNIX socket, which reaches the session D-Bus
and `systemd --user` (so `systemd-run --user` would launch a process outside the sandbox). A seccomp
filter that refuses `socket(AF_UNIX)` closes it. Network over AF_INET/AF_INET6 stays open.

Standard library only, no privilege required: Landlock and a seccomp filter both work for an
ordinary user once PR_SET_NO_NEW_PRIVS is set (the job's transient unit already sets it; apply() sets
it again). apply() is written to run as a subprocess preexec_fn: it raises on any failure, so a job
whose confinement cannot be installed never starts (fail closed). available() reports whether this
kernel can confine at all; the helper refuses an Auto job for a sandboxed agent when it cannot.
"""

import ctypes
import os
import platform
import stat
import struct

# Landlock and seccomp are reached through syscalls; the numbers are the same on every architecture
# Landlock supports. prctl is a libc call.
_SYS_landlock_create_ruleset = 444
_SYS_landlock_add_rule = 445
_SYS_landlock_restrict_self = 446
_PR_SET_NO_NEW_PRIVS = 38
_PR_SET_SECCOMP = 22
_SECCOMP_MODE_FILTER = 2
_RULE_PATH_BENEATH = 1

# Landlock filesystem access rights (uapi/linux/landlock.h), by ABI that introduced each.
_FS = {"EXECUTE": 1 << 0, "WRITE_FILE": 1 << 1, "READ_FILE": 1 << 2, "READ_DIR": 1 << 3,
       "REMOVE_DIR": 1 << 4, "REMOVE_FILE": 1 << 5, "MAKE_CHAR": 1 << 6, "MAKE_DIR": 1 << 7,
       "MAKE_REG": 1 << 8, "MAKE_SOCK": 1 << 9, "MAKE_FIFO": 1 << 10, "MAKE_BLOCK": 1 << 11,
       "MAKE_SYM": 1 << 12, "REFER": 1 << 13, "TRUNCATE": 1 << 14, "IOCTL_DEV": 1 << 15}
_READ = _FS["EXECUTE"] | _FS["READ_FILE"] | _FS["READ_DIR"]
# Everything that creates, changes or removes a filesystem object. REFER (ABI 2) and TRUNCATE
# (ABI 3) and IOCTL_DEV (ABI 5) are added only when the kernel knows them, or add_rule rejects them.
_WRITE_BASE = (_FS["WRITE_FILE"] | _FS["REMOVE_DIR"] | _FS["REMOVE_FILE"] | _FS["MAKE_CHAR"]
               | _FS["MAKE_DIR"] | _FS["MAKE_REG"] | _FS["MAKE_SOCK"] | _FS["MAKE_FIFO"]
               | _FS["MAKE_BLOCK"] | _FS["MAKE_SYM"])
# A rule on a file (not a directory) accepts only these rights; the rest give EINVAL.
_FILE_RIGHTS = _FS["EXECUTE"] | _FS["WRITE_FILE"] | _FS["READ_FILE"] | _FS["TRUNCATE"] | _FS["IOCTL_DEV"]
_SCOPE_ABSTRACT_UNIX_SOCKET = 1 << 0   # LANDLOCK_SCOPE_ABSTRACT_UNIX_SOCKET, ABI 6


def _libc():
    return ctypes.CDLL(None, use_errno=True)


def abi():
    """The kernel's Landlock ABI version, or 0 when Landlock is not available."""
    try:
        version = _libc().syscall(_SYS_landlock_create_ruleset, None, 0, 1)  # LANDLOCK_CREATE_RULESET_VERSION
    except Exception:
        return 0
    return version if isinstance(version, int) and version >= 1 else 0


def seccomp_arch():
    """(AUDIT_ARCH value, __NR_socket, has_x32) for this machine, or None when unsupported."""
    machine = platform.machine()
    if machine == "x86_64":
        return 0xC000003E, 41, True
    if machine == "aarch64":
        return 0xC00000B7, 198, False
    return None


def available():
    """True when this kernel can both confine the filesystem and refuse AF_UNIX sockets."""
    return abi() >= 1 and seccomp_arch() is not None


def _write_rights(version):
    rights = _WRITE_BASE
    if version >= 2:
        rights |= _FS["REFER"]
    if version >= 3:
        rights |= _FS["TRUNCATE"]
    if version >= 5:
        rights |= _FS["IOCTL_DEV"]
    return rights


def _deny_af_unix(libc):
    """Install a seccomp filter: socket(AF_UNIX, ...) returns EACCES; every other call is allowed.

    socketpair() is a different syscall and stays allowed (node and bun use it for child stdio).
    A foreign ABI (i386 compat, x32) is refused so a call made through it cannot dodge the filter.
    """
    arch = seccomp_arch()
    if arch is None:
        raise OSError("seccomp: unsupported architecture %r" % platform.machine())
    audit_arch, nr_socket, has_x32 = arch
    af_unix = 1

    def ins(code, jt, jf, k):
        return struct.pack("<HBBI", code, jt, jf, k)

    ld_abs, jeq, jge, ret = 0x20, 0x15, 0x35, 0x06
    deny = 0x00050000 | 13          # SECCOMP_RET_ERRNO | EACCES
    allow = 0x7FFF0000              # SECCOMP_RET_ALLOW
    prog = [ins(ld_abs, 0, 0, 4),            # 0: A = arch (seccomp_data.arch, offset 4)
            ins(jeq, 0, 6, audit_arch),      # 1: arch != ours -> jump to deny (offset 8)
            ins(ld_abs, 0, 0, 0)]            # 2: A = nr (offset 0)
    if has_x32:
        prog.append(ins(jge, 4, 0, 0x40000000))  # 3: x32 bit set -> deny
    else:
        prog.append(ins(jeq, 0, 0, 0xFFFFFFFF))   # 3: no-op, keeps the offsets identical
    prog += [ins(jeq, 0, 2, nr_socket),      # 4: nr != socket -> allow (offset 7)
             ins(ld_abs, 0, 0, 16),          # 5: A = args[0] low word (domain, offset 16)
             ins(jeq, 1, 0, af_unix),        # 6: domain == AF_UNIX -> deny (offset 8), else allow
             ins(ret, 0, 0, allow),          # 7
             ins(ret, 0, 0, deny)]           # 8
    blob = ctypes.create_string_buffer(b"".join(prog), len(prog) * 8)
    fprog = ctypes.create_string_buffer(struct.pack("<HxxxxxxQ", len(prog), ctypes.addressof(blob)), 16)
    if libc.prctl(ctypes.c_ulong(_PR_SET_SECCOMP), ctypes.c_ulong(_SECCOMP_MODE_FILTER),
                  ctypes.c_void_p(ctypes.addressof(fprog)), ctypes.c_ulong(0), ctypes.c_ulong(0)) != 0:
        raise OSError(ctypes.get_errno(), "seccomp PR_SET_SECCOMP failed")


def apply(rw_roots, ro_roots, mkdir_roots=(), rw_files=()):
    """Confine this process and raise on any failure, so a preexec_fn using it fails the spawn closed.

    - rw_roots: folders the process may read and write freely (its working folder, a private tmp).
    - ro_roots: folders and files it may read and execute but not change (the system, its own config).
    - mkdir_roots: folders where it may only create and remove sub-folders, not files (lock folders a
      config dir needs, without letting it plant a file a later run would load).
    - rw_files: single files it may read and rewrite although their folder is read-only (a token the
      agent refreshes in place).
    A path that is not present is skipped (the set is a ceiling, not a requirement). Callers pass real
    paths. A rule on a file carries only file rights; a directory rule carries the directory rights.
    """
    version = abi()
    if version < 1:
        raise OSError("Landlock is not available on this kernel")
    libc = _libc()
    read = _READ
    write = read | _write_rights(version)
    mkdir = read | _FS["MAKE_DIR"] | _FS["REMOVE_DIR"] | (_FS["REFER"] if version >= 2 else 0)
    file_rw = read | _FS["WRITE_FILE"] | (_FS["TRUNCATE"] if version >= 3 else 0)
    handled = write
    if version >= 6:
        attr = struct.pack("QQQ", handled, 0, _SCOPE_ABSTRACT_UNIX_SOCKET)
    elif version >= 4:
        attr = struct.pack("QQ", handled, 0)
    else:
        attr = struct.pack("Q", handled)
    buf = ctypes.create_string_buffer(attr, len(attr))
    fd = libc.syscall(_SYS_landlock_create_ruleset, buf, len(attr), 0)
    if fd < 0:
        raise OSError(ctypes.get_errno(), "landlock_create_ruleset failed")
    try:
        grants = ([(p, read) for p in ro_roots] + [(p, mkdir) for p in mkdir_roots]
                  + [(p, write) for p in rw_roots] + [(p, file_rw) for p in rw_files])
        for path, rights in grants:
            _grant(libc, fd, path, rights, handled)
        if libc.prctl(_PR_SET_NO_NEW_PRIVS, 1, 0, 0, 0) != 0:
            raise OSError(ctypes.get_errno(), "prctl(NO_NEW_PRIVS) failed")
        _deny_af_unix(libc)
        if libc.syscall(_SYS_landlock_restrict_self, fd, 0) != 0:
            raise OSError(ctypes.get_errno(), "landlock_restrict_self failed")
    finally:
        os.close(fd)


def _grant(libc, fd, path, rights, handled):
    # Callers pass real paths (the spec builder resolves every root), so a granted root cannot be a
    # symlink that leads into a denied tree; O_PATH opens the target to add the rule.
    try:
        pfd = os.open(path, os.O_PATH | os.O_CLOEXEC)
    except OSError:
        return  # a path that is not present is simply not granted
    try:
        info = os.fstat(pfd)
        if not stat.S_ISDIR(info.st_mode):
            rights &= _FILE_RIGHTS
        raw = struct.pack("<Qi", rights & handled, pfd)
        rule = ctypes.create_string_buffer(raw, len(raw))
        if libc.syscall(_SYS_landlock_add_rule, fd, _RULE_PATH_BENEATH, rule, 0) != 0:
            raise OSError(ctypes.get_errno(), "landlock_add_rule failed for %s" % path)
    finally:
        os.close(pfd)


def preexec(spec):
    """A preexec_fn that confines the child from a spec dict before exec.

    spec keys: "rw", "ro", "mkdir", "rwFiles" (each a list of real paths; missing keys are empty).
    """
    rw = list(spec.get("rw", ()))
    ro = list(spec.get("ro", ()))
    mkdir = list(spec.get("mkdir", ()))
    rw_files = list(spec.get("rwFiles", ()))

    def run():
        apply(rw, ro, mkdir, rw_files)

    return run
