"""The systemd user manager: transient job units, their lookup and their stopping.

Every call goes through bounded.run_bounded with an absolute tool path, the plugin-side tool
environment, byte caps and a deadline. Unit names are built only from a validated job id and
generation under the edition prefix, and are checked against the unit grammar again right
before use, because systemd-run silently escapes names it does not like. Listing filters by
the same grammar, so a unit that belongs to anything else is never stopped.
"""
import os
import re
import time

from . import bounded, consts, edition, fsio, proto, timeutil
from .errors import ApError

_CALL_DEADLINE_S = 10.0
_CALL_CAP = 16384
_LIST_CAP = 65536
_POLL_EVERY_S = 0.25
_CLOCK_DEADLINE_S = 3.0
_CLOCK_CAP = 4096
_NAMES_PER_CALL = 64
_INVOCATION_RE = re.compile(r"^[0-9a-f]{32}$")
# The runner path lands in ExecStart, where the manager expands $VAR and %-specifiers. A plugin
# folder with anything outside this set is refused instead of being escaped.
_PLUGIN_PATH_RE = re.compile(r"^/[A-Za-z0-9._+@/-]{1,1024}$")
LIVE_STATES = ("active", "activating", "deactivating", "reloading", "refreshing")


def _is_int(value):
    return isinstance(value, int) and not isinstance(value, bool)


def unit_name(job_id, gen):
    """<prefix>-<16hex>-g<gen>, checked against UNIT_RE."""
    if not isinstance(job_id, str) or not consts.JOB_ID_RE.fullmatch(job_id):
        raise ApError("internal")
    if not _is_int(gen) or not 0 <= gen <= 999999:
        raise ApError("internal")
    name = "%s-%s-g%d" % (edition.UNIT_PREFIX, job_id, gen)
    if not consts.UNIT_RE.fullmatch(name):
        raise ApError("internal")
    return name


def _run(argv, *, cap=_CALL_CAP, deadline_s=_CALL_DEADLINE_S):
    return bounded.run_bounded(argv, env=bounded.tool_env(), stdout_cap=cap, stderr_cap=_CALL_CAP,
                               deadline_s=deadline_s)


def _failed(res):
    return bool(res["timedOut"] or res["error"] or res["overflow"] or res["rc"] != 0)


def _raise_for(res):
    if res["timedOut"]:
        raise ApError("systemd_timeout")
    if _failed(res):
        raise ApError("systemd_failed")


def _systemctl(args, **kw):
    return _run([consts.TOOLS["systemctl"], "--user"] + list(args), **kw)


def arm_argv(job_id, gen, fire_at, runtime_sec):
    """The exact systemd-run argv of contract 3.4.1 (no checks beyond the unit grammar)."""
    unit = unit_name(job_id, gen)
    argv = [consts.TOOLS["systemd_run"], "--user", "--quiet", "--no-ask-password", "--collect",
            "--unit=" + unit, "--description=" + edition.UNIT_DESCRIPTION]
    if fire_at is not None:
        argv += ["--on-calendar=@" + str(fire_at), "--timer-property=AccuracySec=1s"]
    argv += ["-p", "Type=exec", "-p", "RuntimeMaxSec=" + str(runtime_sec), "-p", "TimeoutStopSec=30s",
             "-p", "KillMode=control-group", "-p", "SendSIGKILL=yes",
             "-p", "MemoryHigh=3G", "-p", "MemoryMax=4G", "-p", "TasksMax=512", "-p", "CPUWeight=50",
             "-p", "OOMPolicy=kill",
             "-p", "Nice=10", "-p", "IOSchedulingClass=best-effort", "-p", "IOSchedulingPriority=7",
             "-p", "NoNewPrivileges=yes", "-p", "UMask=0077", "-p", "LimitCORE=0",
             "-p", "StandardInput=null", "-p", "StandardOutput=null", "-p", "StandardError=journal",
             "-p", "SyslogIdentifier=" + edition.SYSLOG_IDENTIFIER,
             "-p", "LogRateLimitIntervalSec=30s", "-p", "LogRateLimitBurst=200",
             "-E", "PATH=/usr/bin", "-E", "LANG=C.UTF-8",
             "-p", "UnsetEnvironment=DISPLAY WAYLAND_DISPLAY HYPRLAND_INSTANCE_SIGNATURE OMARCHY_PATH",
             "--", consts.TOOLS["python3"], "-I", "-S", "-B", fsio.plugin_dir() + "/bin/ap4a", "run",
             "--job", job_id, "--gen", str(gen)]
    return argv


def arm_unit(job_id, gen, fire_at, runtime_sec):
    """Create <unit>.timer (fire_at given) or start <unit>.service right away (fire_at None)."""
    unit_name(job_id, gen)
    if gen < 1:
        raise ApError("internal")
    now = timeutil.now()
    if fire_at is not None:
        if not _is_int(fire_at):
            raise ApError("invalid_trigger")
        if fire_at < now - 60:
            raise ApError("time_past")
        if fire_at > now + consts.HORIZON_S:
            raise ApError("time_too_far")
    if not _is_int(runtime_sec) or not consts.RUNTIME_MIN <= runtime_sec <= consts.RUNTIME_MAX:
        raise ApError("invalid_limits")
    if fsio.manifest_id() != edition.PLUGIN_ID or not _PLUGIN_PATH_RE.fullmatch(fsio.plugin_dir()) \
            or not fsio.plugin_code_trusted():
        raise ApError("plugin_identity")
    if fire_at is not None:
        # Callers plan with a `now` read before slow work (listing units, login probes). A calendar
        # time at or before the present never elapses: the timer would unload without starting the
        # runner. So the time is re-read here, right before the argv is built.
        fire_at = max(fire_at, timeutil.now() + consts.ARM_MIN_LEAD_S)
    _raise_for(_run(arm_argv(job_id, gen, fire_at, runtime_sec)))


def _json_list(res):
    try:
        value = proto.parse_json_bytes(res["stdout"])
    except (UnicodeDecodeError, ValueError):
        raise ApError("systemd_failed") from None
    if not isinstance(value, list):
        raise ApError("systemd_failed")
    return value


def _unit_entry(entry):
    """(base, kind) for a listed unit of this edition, else None."""
    if not isinstance(entry, dict):
        return None
    name = entry.get("unit")
    match = consts.UNIT_FILE_RE.fullmatch(name) if isinstance(name, str) else None
    if not match:
        return None
    return name.rsplit(".", 1)[0], match.group(3)


def list_units():
    """Loaded timers and services of this edition (orphans included)."""
    pattern = edition.UNIT_PREFIX + "-*"
    timers_res = _systemctl(["list-timers", "--all", "--output=json", pattern], cap=_LIST_CAP)
    _raise_for(timers_res)
    units_res = _systemctl(["list-units", "--all", "--output=json", pattern], cap=_LIST_CAP)
    _raise_for(units_res)
    timers = {}
    services = {}
    for entry in _json_list(timers_res):
        parsed = _unit_entry(entry)
        if not parsed or parsed[1] != "timer":
            continue
        nxt = entry.get("next")
        timers[parsed[0]] = {"nextUsec": nxt if _is_int(nxt) and nxt > 0 else None, "active": "active"}
    for entry in _json_list(units_res):
        parsed = _unit_entry(entry)
        if not parsed:
            continue
        load, active, sub = entry.get("load"), entry.get("active"), entry.get("sub")
        if not all(isinstance(v, str) and len(v) <= 64 for v in (load, active, sub)):
            continue
        if load == "not-found" and active == "inactive":
            continue
        base, kind = parsed
        if kind == "timer":
            timers.setdefault(base, {"nextUsec": None, "active": active})["active"] = active
        else:
            services[base] = {"active": active, "sub": sub}
    return {"timers": timers, "services": services}


def _show_states(names):
    """{name: (LoadState, ActiveState)} for every name, or None when systemctl did not answer."""
    states = {}
    for start in range(0, len(names), _NAMES_PER_CALL):
        chunk = names[start:start + _NAMES_PER_CALL]
        res = _systemctl(["show", "-p", "LoadState,ActiveState"] + chunk)
        if _failed(res):
            return None
        try:
            text = res["stdout"].decode("utf-8", errors="strict")
        except UnicodeDecodeError:
            return None
        blocks = [b for b in text.strip("\n").split("\n\n")] if text.strip() else []
        if len(blocks) != len(chunk):
            return None
        for name, block in zip(chunk, blocks):
            props = {}
            for line in block.split("\n"):
                key, sep, value = line.partition("=")
                if sep:
                    props[key] = value
            states[name] = (props.get("LoadState"), props.get("ActiveState"))
    return states


def _gone(state):
    load, active = state
    return load == "not-found" or active in ("inactive", "failed")


def _batched(verb_args, names):
    ok = True
    for start in range(0, len(names), _NAMES_PER_CALL):
        if _failed(_systemctl(verb_args + names[start:start + _NAMES_PER_CALL])):
            ok = False
    return ok


def stop_names(names):
    """Stop unit files of this edition and wait until they are inactive.

    Returns {"units": [names that were loaded], "verified": bool, "wasRunning": bool,
    "stuck": [names still active after the poll]}. Never raises for systemd failures.
    """
    names = sorted({n for n in names if isinstance(n, str) and consts.UNIT_FILE_RE.fullmatch(n)})
    result = {"units": [], "verified": True, "wasRunning": False, "stuck": []}
    if not names:
        return result
    before = _show_states(names)
    if before is None:
        loaded = names
    else:
        loaded = [n for n in names if before[n][0] != "not-found" or before[n][1] not in ("inactive", None)]
        result["wasRunning"] = any(n.endswith(".service") and before[n][1] in LIVE_STATES for n in loaded)
    result["units"] = loaded
    if not loaded:
        return result
    _batched(["stop", "--no-block"], loaded)
    limit = consts.STOP_POLL_RUNNING_S if result["wasRunning"] or before is None else consts.STOP_POLL_S
    end = time.monotonic() + limit
    while True:
        states = _show_states(loaded)
        if states is not None and all(_gone(states[n]) for n in loaded):
            break
        left = bounded.remaining_budget()
        if time.monotonic() + _POLL_EVERY_S >= end or (left is not None and left < 1.0):
            break
        time.sleep(_POLL_EVERY_S)
    if states is None:
        result["stuck"] = list(loaded)
        result["verified"] = False
        return result
    result["stuck"] = [n for n in loaded if not _gone(states[n])]
    failed = [n for n in loaded if states[n][1] == "failed"]
    ok = _batched(["reset-failed"], failed) if failed else True
    result["verified"] = ok and not result["stuck"]
    return result


def stop_units(job_id, gen):
    """Stop <unit>.timer and <unit>.service of one job (gen None: every loaded generation).

    Raises stop_unverified when a unit is still active after the poll. verified False with a
    normal return means the units are gone but reset-failed did not succeed.
    """
    if gen is None:
        listed = list_units()
        bases = sorted(b for b in set(listed["timers"]) | set(listed["services"])
                       if consts.UNIT_RE.fullmatch(b).group(1) == job_id)
    else:
        bases = [unit_name(job_id, gen)]
    res = stop_names([b + ".timer" for b in bases] + [b + ".service" for b in bases])
    if res["stuck"]:
        raise ApError("stop_unverified")
    return {"units": res["units"], "verified": res["verified"], "wasRunning": res["wasRunning"]}


def stop_all_prefix_units():
    """cancel-all: every loaded unit matching UNIT_FILE_RE, orphans included."""
    listed = list_units()
    names = [b + ".timer" for b in listed["timers"]] + [b + ".service" for b in listed["services"]]
    res = stop_names(names)
    return {"units": res["units"], "verified": res["verified"]}


def clock_synced():
    """timedatectl NTPSynchronized: True, False, or None when unknown."""
    try:
        res = bounded.run_bounded([consts.TOOLS["timedatectl"], "show", "-p", "NTPSynchronized", "--value"],
                                  env=bounded.tool_env(), stdout_cap=_CLOCK_CAP, stderr_cap=_CLOCK_CAP,
                                  deadline_s=_CLOCK_DEADLINE_S)
    except ApError:
        return None
    if _failed(res):
        return None
    value = res["stdout"].strip()
    if value == b"yes":
        return True
    if value == b"no":
        return False
    return None


def under_systemd():
    return bool(_INVOCATION_RE.fullmatch(os.environ.get("INVOCATION_ID", "")))
