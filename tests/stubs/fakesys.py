"""Fake systemd-run, systemctl, busctl, qs and timedatectl for the helper tests.

Never imported by runtime code. Each wrapper script next to this file calls main(tool, argv).

State lives in <dir>/state.json ({"units": {name: {...}}}) and every call is appended to
<dir>/calls.jsonl as {"tool", "argv", "env", "at"}. <dir> is $FAKESYS_DIR, or $HOME/.fakesys
because the helper starts tools with an allowlisted environment that drops FAKESYS_*.
Options come from FAKESYS_* variables or from <dir>/config.json:

  fire        true: systemd-run runs the job at once through tests/support/launch.py
              (FAKESYS_FIRE=1), then removes its units
  tools       JSON file handed to launch.py --tools when firing (FAKESYS_TOOLS)
  runtimeRe   regex handed to launch.py --runtime-re when firing (FAKESYS_RUNTIME_RE)
  launchArgs  extra launch.py options when firing (list)
  failRun     substrings: systemd-run fails for unit names containing one
  failCtl     systemctl verbs that fail, for example "list-units" or "show"
  stuck       substrings: stop leaves matching units deactivating
  ntp         what timedatectl prints (FAKESYS_NTP, default "yes")
"""
import fcntl
import fnmatch
import json
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.realpath(__file__))
LAUNCH = os.path.join(os.path.dirname(HERE), "support", "launch.py")


def state_dir():
    path = os.environ.get("FAKESYS_DIR") or os.path.join(os.environ.get("HOME", "/nonexistent"), ".fakesys")
    os.makedirs(path, mode=0o700, exist_ok=True)
    return path


def config(path):
    try:
        with open(os.path.join(path, "config.json")) as handle:
            cfg = json.load(handle)
    except (OSError, ValueError):
        cfg = {}
    if not isinstance(cfg, dict):
        cfg = {}
    if os.environ.get("FAKESYS_FIRE"):
        cfg["fire"] = os.environ["FAKESYS_FIRE"] == "1"
    for env_key, key in (("FAKESYS_TOOLS", "tools"), ("FAKESYS_NTP", "ntp"), ("FAKESYS_RUNTIME_RE", "runtimeRe")):
        if os.environ.get(env_key):
            cfg[key] = os.environ[env_key]
    return cfg


class Locked:
    def __init__(self, path):
        self.path = os.path.join(path, "lock")
        self.fd = None

    def __enter__(self):
        self.fd = os.open(self.path, os.O_RDWR | os.O_CREAT, 0o600)
        fcntl.flock(self.fd, fcntl.LOCK_EX)
        return self

    def __exit__(self, *_exc):
        fcntl.flock(self.fd, fcntl.LOCK_UN)
        os.close(self.fd)
        return False


def load_state(path):
    try:
        with open(os.path.join(path, "state.json")) as handle:
            state = json.load(handle)
    except (OSError, ValueError):
        state = {}
    state.setdefault("units", {})
    return state


def save_state(path, state):
    tmp = os.path.join(path, "state.json.tmp")
    with open(tmp, "w") as handle:
        json.dump(state, handle, sort_keys=True)
    os.replace(tmp, os.path.join(path, "state.json"))


def record(path, tool, argv):
    entry = {"tool": tool, "argv": list(argv), "env": dict(os.environ), "at": time.time()}
    with Locked(path):
        with open(os.path.join(path, "calls.jsonl"), "a") as handle:
            handle.write(json.dumps(entry) + "\n")


_PASS_ENV = ("HOME", "USER", "XDG_RUNTIME_DIR", "DBUS_SESSION_BUS_ADDRESS", "LANG", "PATH")


def _fire(path, cfg, cmd, unit):
    """Start the job like a service would: detached, so systemd-run returns at once.

    The helper holds the jobs lock while it calls systemd-run and the runner needs that lock to
    claim the job, so running it inline would block until the helper's deadline.
    """
    job = cmd[cmd.index("--job") + 1]
    gen = cmd[cmd.index("--gen") + 1]
    with open(os.path.join(path, "firing"), "w"):
        pass
    with Locked(path):
        state = load_state(path)
        state["units"].pop(unit + ".timer", None)
        state["units"][unit + ".service"] = {"load": "loaded", "active": "active", "sub": "running"}
        save_state(path, state)
    env = {k: os.environ[k] for k in _PASS_ENV if k in os.environ}
    env.update(FAKESYS_DIR=path, PYTHONDONTWRITEBYTECODE="1")
    child = ["/usr/bin/python3", "-I", "-S", "-B", os.path.join(HERE, "fakesys.py"), "--fire-child", unit, job, gen]
    with open(os.path.join(path, "runner-stderr.log"), "ab") as err:
        subprocess.Popen(child, env=env, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=err,
                         start_new_session=True, close_fds=True)


def fire_child(unit, job, gen):
    """Detached part of FAKESYS_FIRE: run the job through launch.py, then unload the service."""
    path = state_dir()
    cfg = config(path)
    argv = ["/usr/bin/python3", "-I", "-S", "-B", LAUNCH]
    if cfg.get("tools"):
        argv += ["--tools", cfg["tools"]]
    if cfg.get("runtimeRe"):
        argv += ["--runtime-re", cfg["runtimeRe"]]
    argv += list(cfg.get("launchArgs") or [])
    argv += ["--", "run", "--job", job, "--gen", gen]
    env = {k: os.environ[k] for k in _PASS_ENV if k in os.environ}
    env.update(INVOCATION_ID=os.urandom(16).hex(), PYTHONDONTWRITEBYTECODE="1")
    try:
        subprocess.run(argv, env=env, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, timeout=300, check=False)
    finally:
        try:
            os.unlink(os.path.join(path, "firing"))
        except OSError:
            pass
        with Locked(path):
            state = load_state(path)
            state["units"].pop(unit + ".service", None)
            save_state(path, state)
        with open(os.path.join(path, "fired.jsonl"), "a") as handle:
            handle.write(json.dumps({"unit": unit, "job": job, "gen": gen, "at": time.time()}) + "\n")
    return 0


if __name__ == "__main__" and len(sys.argv) == 5 and sys.argv[1] == "--fire-child":
    sys.dont_write_bytecode = True
    raise SystemExit(fire_child(*sys.argv[2:]))


def systemd_run(path, cfg, argv):
    args = argv[1:]
    opts, cmd = (args[:args.index("--")], args[args.index("--") + 1:]) if "--" in args else (args, [])
    unit = None
    calendar = None
    for opt in opts:
        if opt.startswith("--unit="):
            unit = opt[len("--unit="):]
        elif opt.startswith("--on-calendar="):
            calendar = opt[len("--on-calendar="):]
    if not unit or not cmd:
        sys.stderr.write("fake systemd-run: missing unit or command\n")
        return 1
    if any(part in unit for part in cfg.get("failRun") or []):
        sys.stderr.write("Failed to start transient unit\n")
        return 1
    with Locked(path):
        state = load_state(path)
        if unit + ".timer" in state["units"] or unit + ".service" in state["units"]:
            sys.stderr.write("Unit %s was already loaded\n" % unit)
            return 1
        if calendar is not None:
            next_usec = int(calendar.lstrip("@")) * 1000000
            state["units"][unit + ".timer"] = {"load": "loaded", "active": "active", "sub": "waiting",
                                               "next": next_usec}
        else:
            state["units"][unit + ".service"] = {"load": "loaded", "active": "active", "sub": "running"}
        save_state(path, state)
    if cfg.get("fire") and "run" in cmd and not os.path.exists(os.path.join(path, "firing")):
        _fire(path, cfg, cmd, unit)
    return 0


def _names(args):
    """Positional unit names; the value after -p is a property list, not a name."""
    names = []
    skip = False
    for arg in args:
        if skip:
            skip = False
        elif arg == "-p":
            skip = True
        elif not arg.startswith("-"):
            names.append(arg)
    return names


def systemctl(path, cfg, argv):
    args = argv[1:]
    if args[:1] != ["--user"] or len(args) < 2:
        return 1
    verb = args[1]
    rest = args[2:]
    if verb in (cfg.get("failCtl") or []):
        sys.stderr.write("Failed to connect to bus\n")
        return 1
    with Locked(path):
        state = load_state(path)
        units = state["units"]
        if verb in ("list-timers", "list-units"):
            patterns = _names(rest) or ["*"]
            matched = sorted(n for n in units if any(fnmatch.fnmatchcase(n, p) for p in patterns))
            if verb == "list-timers":
                out = [{"next": units[n].get("next", 0), "left": units[n].get("next", 0), "last": 0, "passed": 0,
                        "unit": n, "activates": n[:-len(".timer")] + ".service"}
                       for n in matched if n.endswith(".timer")]
            else:
                out = [{"unit": n, "load": units[n]["load"], "active": units[n]["active"], "sub": units[n]["sub"],
                        "description": "fake"} for n in matched]
            sys.stdout.write(json.dumps(out) + "\n")
            return 0
        if verb == "stop":
            for name in _names(rest):
                unit = units.get(name)
                if unit is None or unit["active"] == "failed":
                    continue
                if any(part in name for part in cfg.get("stuck") or []):
                    unit["active"], unit["sub"] = "deactivating", "stop-sigterm"
                else:
                    del units[name]
            save_state(path, state)
            return 0
        if verb == "show":
            blocks = []
            for name in _names(rest):
                unit = units.get(name)
                if unit is None:
                    blocks.append("LoadState=not-found\nActiveState=inactive\n")
                else:
                    blocks.append("LoadState=%s\nActiveState=%s\n" % (unit["load"], unit["active"]))
            sys.stdout.write("\n".join(blocks))
            return 0
        if verb == "reset-failed":
            for name in _names(rest):
                if name in units and units[name]["active"] == "failed":
                    del units[name]
            save_state(path, state)
            return 0
    sys.stderr.write("fake systemctl: unsupported verb\n")
    return 1


def main(tool, argv):
    path = state_dir()
    cfg = config(path)
    record(path, tool, argv)
    if tool == "systemd-run":
        return systemd_run(path, cfg, argv)
    if tool == "systemctl":
        return systemctl(path, cfg, argv)
    if tool == "timedatectl":
        sys.stdout.write(str(cfg.get("ntp") or "yes") + "\n")
        return 0
    return 0
