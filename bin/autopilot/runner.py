"""`ap4a run --job <id> --gen <n>`: the service a job's transient timer starts.

The sequence is claim, spawn, classify, act (contract 3.8, v2 section 7.1). The jobs lock is held
only while the store is read and written; the per-session lock is held for the whole run. The
runner exits 0 for every job outcome, so a failed unit always means a defect in the plugin itself,
and it writes only fixed diagnostic codes to the journal.
"""

import copy
import fcntl
import hashlib
import os
import re
import signal
import stat
import threading

from . import (classify, consts, edition, fsio, harness, identity, jobs, liveness, notify, reconcile, sessions,
               supervise, systemd, timeutil, trigger)
from .errors import ApError

CONTINUE_TEXT = b"Continue with the previous request."
_GEN_RE = re.compile(r"^[0-9]{1,6}$")
_VERSION_RE = re.compile(r"^[0-9A-Za-z.+_-]{1,40}$")
_HISTORY_FOR = {"done": "run_done", "failed": "run_failed", "limit": "limit", "busy": "busy",
                "interrupted": "interrupted", "skipped": "skipped", "gave_up": "gave_up"}
_WAIT_HISTORY = {"limit": "limit", "transient": "transient", "busy": "busy"}
# Gate refusal code -> run reason; paid.REASON_FOR_CODE is authoritative when present.
_REASON_FOR_CODE = {"paid_blocked": "paid_blocked", "paid_zen": "paid_blocked", "paid_opencode_claude": "paid_blocked",
                    "paid_pi_claude": "paid_blocked", "paid_pi_key": "paid_blocked",
                    "cursor_autorun_config": "cursor_autorun_config",
                    "cursor_network_config": "cursor_network_config",
                    "cursor_project_rules": "cursor_project_rules", "cursor_untrusted": "untrusted",
                    "harness_gated": "harness_gated", "not_logged_in": "not_logged_in", "pi_auth_invalid": "failed"}
_OBSERVED_HARNESSES = ("cursor", "opencode", "pi", "codex")
_OBSERVED_SOURCES = ("stderr", "retry_after", "event")
_SHORT_LABEL = {"session": "5-hour", "weekly": "Weekly", "monthly": "Monthly", "daily": "Daily",
                "model_weekly": "Model weekly", "model_session": "Model session", "model_monthly": "Model monthly"}


class _Stop(Exception):
    """Raised inside the claim to end the run early; the notification is sent after unlocking."""

    def __init__(self, code, event=None, fire_at=None, reason=None):
        super().__init__(code)
        self.code = code
        self.event = event
        self.fire_at = fire_at
        self.reason = reason


def _diag(code, job_id, gen):
    line = "ap4a: %s job=%s gen=%s\n" % (code, job_id if consts.JOB_ID_RE.match(job_id or "") else "-",
                                         gen if isinstance(gen, int) else "-")
    try:
        os.write(2, line.encode("ascii"))
    except OSError:
        pass


def _parse_args(argv):
    args = list(argv)
    if args and args[0] == "run":
        args = args[1:]
    if len(args) != 4 or args[0] != "--job" or args[2] != "--gen":
        raise ApError("bad_args")
    if not consts.JOB_ID_RE.match(args[1]) or not _GEN_RE.match(args[3]) or int(args[3]) < 1:
        raise ApError("bad_args")
    return args[1], int(args[3])


def _find(store, job_id):
    for job in store.get("jobs", []):
        if job.get("id") == job_id:
            return job
    return None


def _touch(job, now):
    job["updatedAt"] = now


def _set_final(sd, job, status, now, reason, event):
    state = job["state"]
    jobs.set_status(job, status, now, reason=reason, wait=None)
    state["unit"] = None
    jobs.add_history(job, event, now, reason)
    if status in consts.CLOSED_STATUSES:
        jobs.delete_prompt(sd, job, now)
    _touch(job, now)


def _set_armed(job, fire_at, now, *, wait, reason, counter, event):
    state = job["state"]
    if counter:
        state[counter] = int(state.get(counter) or 0) + 1
    new_gen = int(state["gen"]) + 1
    jobs.set_status(job, "armed", now, reason=reason, wait=wait)
    state["gen"] = new_gen
    state["fireAt"] = int(fire_at)
    state["unit"] = systemd.unit_name(job["id"], new_gen)
    jobs.add_history(job, event, now, reason)
    _touch(job, now)
    return new_gen


def _set_basis(state, source, reset_epoch):
    """The reset a re-armed job now waits for; the next pre-fire check compares against it."""
    state["basis"] = {"source": source, "resetEpoch": reset_epoch if isinstance(reset_epoch, int) else None,
                      "fetchedAtMs": None, "percent": None}


def _arm_after_save(sd, store, job, now):
    """Create the next timer. On failure the job stays armed without a unit; reconcile re-arms it."""
    state = job["state"]
    try:
        systemd.arm_unit(job["id"], state["gen"], state["fireAt"], int(job["limits"]["runtimeSec"]))
        return True
    except ApError:
        state["unit"] = None
        jobs.add_history(job, "arm_failed", now, None)
        try:
            jobs.save_store(sd, store)
        except ApError:
            pass
        return False


def _session_lock(job):
    """(fd, held_elsewhere). fd is None when no lock applies or it could not be taken."""
    sid = harness.write_session(job)
    if not sid:
        return None, False
    try:
        runtime = fsio.open_runtime()
    except ApError:
        return None, False
    digest = hashlib.sha256((job["harness"] + ":" + sid).encode("utf-8")).hexdigest()
    try:
        fd = os.open("session-" + digest + ".lock", os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600,
                     dir_fd=runtime.fd)
    except OSError:
        return None, False
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        os.close(fd)
        return None, True
    except OSError:
        os.close(fd)
        return None, False
    return fd, False


def _read_usage():
    try:
        from . import usage
        return usage.read_usage(timeutil.now_ms())
    except Exception:
        return None


def _empty_gate(**over):
    gate = {"ok": True, "code": None, "detail": None, "notes": [], "billing": None, "provider": None,
            "resetAtMs": None, "pending": False, "defer": None}
    gate.update(over)
    return gate


def _prefire_gate(sd, job, cli, now, usage):
    """paid.check_job at pre-fire. A gate that cannot be evaluated refuses the run."""
    if job["harness"] in consts.GATED_HARNESSES:
        return _empty_gate(ok=False, code="harness_gated")
    try:
        from . import paid
        gate = paid.check_job(job, phase="prefire", exec_prefix=cli["exec"], now=now, usage=usage, sd=sd,
                              deadline_s=30)
    except Exception:
        gate = None
    if not isinstance(gate, dict):
        return _empty_gate(ok=False, code="gate_failed")
    return gate


def _reason_for_code(code):
    reason = None
    try:
        from . import paid
        mapping = getattr(paid, "REASON_FOR_CODE", None)
        reason = mapping.get(code) if isinstance(mapping, dict) else None
    except Exception:
        reason = None
    if reason not in consts.REASONS:
        reason = _REASON_FOR_CODE.get(code, "failed")
    return reason


def _defer_busy(sd, store, job, now, reason, stop_code):
    """Session in use before spawning: defer through postrun's busy rule (or finish as busy)."""
    action = trigger.postrun(job, {"outcome": "busy", "detail": reason}, now, None)
    if action["status"] == "armed":
        if reason == "session_locked":
            action = dict(action, fireAt=now + consts.SESSION_LOCK_DEFER_S)
        _set_armed(job, action["fireAt"], now, wait="busy", reason=None, counter=action["counter"], event="busy")
        jobs.save_store(sd, store)
        _arm_after_save(sd, store, job, now)
        raise _Stop(stop_code, "busy_deferred", fire_at=action["fireAt"])
    final_reason = "session_locked" if reason == "session_locked" else "session_busy"
    _set_final(sd, job, "busy", now, final_reason, "busy")
    jobs.save_store(sd, store)
    raise _Stop(stop_code, "busy_final", reason=final_reason)


def _apply_verdict(sd, store, job, verdict, now):
    """End the claim for a prefire or paid-defer verdict other than fire."""
    state = job["state"]
    action = verdict["action"]
    if action == "missed":
        jobs.set_status(job, "missed", now, reason="late", wait=None)
        state["unit"] = None
        jobs.add_history(job, "missed", now, "late")
        _touch(job, now)
        jobs.save_store(sd, store)
        raise _Stop("MISSED", "missed", fire_at=state.get("fireAt"), reason="late")
    if action == "skip":
        _set_final(sd, job, "skipped", now, verdict["reason"], "skipped")
        jobs.save_store(sd, store)
        raise _Stop("SKIPPED", "skipped", reason=verdict["reason"])
    if action == "gave_up":
        _set_final(sd, job, "gave_up", now, verdict["reason"], "gave_up")
        jobs.save_store(sd, store)
        raise _Stop("GAVE_UP", "gave_up", reason=verdict["reason"])
    if action == "rearm":
        _set_armed(job, verdict["fireAt"], now, wait="deferred", reason=verdict["reason"], counter="defers",
                   event="deferred")
        if isinstance(verdict.get("resetEpoch"), int):
            _set_basis(state, "record", verdict["resetEpoch"])
        jobs.save_store(sd, store)
        _arm_after_save(sd, store, job, now)
        raise _Stop("DEFERRED", "deferred", fire_at=verdict["fireAt"], reason=verdict["reason"])


def _slash_prompt(prompt):
    try:
        return prompt.decode("utf-8").lstrip().startswith("/")
    except UnicodeDecodeError:
        return True


def _apply_cli_change(job, cli, now):
    """Record a moved CLI path on the job once (the claim may apply it to two loads of the store)."""
    if cli["changed"] and job["cli"].get("real") != cli["real"]:
        job["cli"] = dict(job["cli"], real=cli["real"])
        jobs.add_history(job, "cli_changed", now, None)


def _claim(sd, job_id, gen, ctx):
    """Everything before the agent starts, in two jobs-lock sections around the paid gate's probes.
    Raises _Stop to end early."""
    with sd.lock(consts.RUNNER_LOCK_WAIT_S):
        store = jobs.load_store(sd)
        job = _find(store, job_id)
        state = (job or {}).get("state") or {}
        if job is None or state.get("gen") != gen or state.get("status") != "armed":
            raise _Stop("STALE")
        ctx["job"] = job
        now = timeutil.now()

        reason = identity.check_plugin_identity(job)
        if reason:
            jobs.set_status(job, "paused", now, reason=reason, wait=None)
            state["unit"] = None
            jobs.add_history(job, "paused", now, reason)
            _touch(job, now)
            jobs.save_store(sd, store)
            raise _Stop("PAUSED", "paused", reason=reason)

        if jobs.full_digest(job) != job.get("digest"):
            jobs.set_status(job, "needs_confirm", now, reason="digest_mismatch", wait=None)
            state["unit"] = None
            jobs.add_history(job, "needs_confirm", now, "digest_mismatch")
            _touch(job, now)
            jobs.save_store(sd, store)
            raise _Stop("NEEDS_CONFIRM", "needs_confirm", reason="digest_mismatch")

        claude_reset = job["trigger"]["kind"] == "claude_5h_reset" and job["harness"] == "claude"
        usage = _read_usage() if claude_reset else None
        verdict = trigger.prefire(job, now, usage)
        if verdict["action"] != "fire":
            _apply_verdict(sd, store, job, verdict, now)

        try:
            cli = identity.resolve_for_fire(job)
        except ApError as err:
            reason = "cli_untrusted" if err.code == "cli_untrusted" else "cli_missing"
            _set_final(sd, job, "failed", now, reason, "run_failed")
            jobs.save_store(sd, store)
            raise _Stop("CLI_REFUSED", "failed", reason=reason) from None
        ctx["cli"] = cli
        _apply_cli_change(job, cli, now)

        try:
            cwd_ok = jobs.check_cwd(job["target"]["cwd"]) == job["target"]["cwd"]
        except ApError:
            cwd_ok = False
        if not cwd_ok:
            _set_final(sd, job, "failed", now, "cwd_refused", "run_failed")
            jobs.save_store(sd, store)
            raise _Stop("CWD_REFUSED", "failed", reason="cwd_refused")
        snapshot = copy.deepcopy(job)
        digest = job.get("digest")

    # Paid usage, sign-in and Cursor preflights, then the included-usage defers (v2 6.1). The probes
    # (up to 30 s) run outside the jobs lock, as the arm verb's do, so panel writes and other runners
    # never wait on them; the checks below prove the job did not change meanwhile.
    if usage is None and snapshot["harness"] != "gemini":
        usage = _read_usage()
    gate = _prefire_gate(sd, snapshot, cli, now, usage)

    with sd.lock(consts.RUNNER_LOCK_WAIT_S):
        store = jobs.load_store(sd)
        job = _find(store, job_id)
        state = (job or {}).get("state") or {}
        if (job is None or state.get("gen") != gen or state.get("status") != "armed" or job.get("digest") != digest
                or jobs.full_digest(job) != digest):
            raise _Stop("STALE")
        ctx["job"] = job
        now = timeutil.now()
        _apply_cli_change(job, cli, now)
        ctx["gate"] = gate
        if gate.get("code"):
            reason = _reason_for_code(gate["code"])
            _set_final(sd, job, "failed", now, reason, "run_failed")
            jobs.save_store(sd, store)
            raise _Stop("FINAL", "failed", reason=reason)
        if isinstance(gate.get("defer"), dict):
            _apply_verdict(sd, store, job, trigger.defer_verdict(job, gate["defer"], now), now)

        if liveness.session_busy(job, cli["exec"], now) is True:
            _defer_busy(sd, store, job, now, "session_busy", "BUSY")

        lock_fd, held = _session_lock(job)
        if held:
            _defer_busy(sd, store, job, now, "session_locked", "SESSION_LOCKED")
        ctx["session_lock"] = lock_fd

        try:
            prompt = jobs.read_prompt(sd, job_id)
        except ApError:
            prompt = None
        if prompt is None:
            _set_final(sd, job, "failed", now, "prompt_missing", "run_failed")
            jobs.save_store(sd, store)
            raise _Stop("FINAL", "failed", reason="prompt_missing")
        if hashlib.sha256(prompt).hexdigest() != job.get("promptSha256"):
            jobs.set_status(job, "needs_confirm", now, reason="digest_mismatch", wait=None)
            state["unit"] = None
            jobs.add_history(job, "needs_confirm", now, "digest_mismatch")
            _touch(job, now)
            jobs.save_store(sd, store)
            raise _Stop("NEEDS_CONFIRM", "needs_confirm", reason="digest_mismatch")
        if job["harness"] == "pi" and _slash_prompt(prompt):
            # Pi runs a prompt that starts with / as a command; job-create refuses it already.
            _set_final(sd, job, "failed", now, "failed", "run_failed")
            jobs.save_store(sd, store)
            raise _Stop("FINAL", "failed", reason="failed")

        jobs.set_status(job, "running", now, reason=None, wait=None)
        state["lastRun"] = {"runId": "%s-g%d" % (job_id, gen), "startedAt": now, "endedAt": None, "outcome": None,
                            "exit": None, "signal": None, "bytesDropped": 0}
        jobs.add_history(job, "fired", now, None)
        _touch(job, now)
        jobs.save_store(sd, store)
        ctx["claimed"] = True
        ctx["startedAt"] = now
        return job, prompt


def _prompt_for_run(job, prompt):
    """R0 U5: a retry into a session that already holds this prompt sends a short continuation."""
    state = job["state"]
    retries = sum(int(state.get(k) or 0) for k in ("limitRetries", "transientRetries", "unknownRetries"))
    mode, sid = harness.effective_session(job)
    if job["harness"] != "claude" or not retries or mode != "resume" or not sid:
        return prompt
    try:
        last = sessions.claude_last_user_sha256(sid)
    except Exception:
        return prompt
    if last is None:
        return prompt
    # The transcript stores the text as Claude received it, which may lack a trailing newline.
    candidates = {hashlib.sha256(prompt).hexdigest(), hashlib.sha256(prompt.strip()).hexdigest()}
    return CONTINUE_TEXT if last in candidates else prompt


def first_line_deadline(job, billing=None):
    """First-event watchdog: OpenCode sleeps silently on a provider retry-after (R7 4.4).

    billing is the gate's class: an Agent-default job (no model) that resolved to a Zen or Go
    model gets the same short watchdog as that model named outright.
    """
    if job["harness"] != "opencode":
        return None
    model = job.get("model") if isinstance(job.get("model"), str) else ""
    if model.startswith(("opencode/", "opencode-go/")) or (not model and billing in classify.ZEN_BILLING):
        return consts.FIRST_EVENT_ZEN_S
    return consts.FIRST_EVENT_OTHER_S


def _spawn(sd, job, gen, prompt, cli, stop_event, gate=None):
    level = edition.level(job["level"])
    expected = level["harness"][job["harness"]]["initPermissionMode"]
    state = classify.new_stream_state(job["harness"], expected, allow_paid=job.get("allowPaid") is True,
                                      provider=job.get("provider"), level_id=job["level"],
                                      billing=(gate or {}).get("billing"))
    state["targetSession"] = harness.effective_session(job)[1]
    state["model"] = job.get("model")
    runs = sd.subdir("runs", create=True)
    try:
        log_fd = runs.open_append("%s-g%d.log" % (job["id"], gen), consts.RUN_LOG_MAX)
    except ApError:
        runs.close()
        raise
    try:
        if stop_event.is_set():
            run = {"rc": None, "signal": None, "timedOut": False, "killedBy": "sigterm", "bytesDropped": 0,
                   "stdoutBytes": 0, "stderrBytes": 0, "stderrTail": b"", "stdoutTail": b"", "logBytes": 0,
                   "error": None}
        else:
            cmd = harness.build_command(job, exec_prefix=cli["exec"], run_dir=runs.path, gen=gen)
            deadline = int(job["limits"]["runtimeSec"]) - consts.RUNNER_DEADLINE_MARGIN_S
            run = supervise.run_agent(cmd, _prompt_for_run(job, prompt), deadline_s=deadline, log_fd=log_fd,
                                      on_stdout_line=lambda line: classify.feed_line(state, line),
                                      stop_event=stop_event,
                                      first_line_deadline_s=first_line_deadline(job, (gate or {}).get("billing")))
    finally:
        try:
            os.close(log_fd)
        except OSError:
            pass
        runs.close()
    return state, run


def _classify(job, state, run, now):
    result = classify.classify(state, run, level_id=job["level"], now=now)
    limit = result.get("limit")
    if job["harness"] == "claude" and result["outcome"] == "limit" and (limit is None or limit["source"] != "event"):
        sid = state.get("sessionId") or harness.effective_session(job)[1]
        quota = None
        if sid:
            try:
                quota = sessions.claude_last_quota(sid)
            except Exception:
                quota = None
        if quota and not quota.get("status") and quota.get("resetsAt") is None and limit is not None \
                and limit["source"] == "backoff":
            # A rate_limit entry without quota data is the server throttling, not the usage window (R5 1.7).
            return {"outcome": "transient", "detail": "server_throttle", "sessionId": result.get("sessionId"),
                    "limit": None}
        if quota and isinstance(quota.get("resetsAt"), int) and quota["resetsAt"] > now - 60:
            kind = classify.RATE_KIND.get(quota.get("rateLimitType"), "other")
            result["limit"] = {"kind": kind, "resetEpoch": quota["resetsAt"], "source": "transcript",
                               "isUsingOverage": bool(limit and limit.get("isUsingOverage")), "rearm": True}
    if job["harness"] == "pi" and result["outcome"] == "limit" and result.get("detail") == "codex_limit" \
            and job.get("provider") == "openai-codex" and isinstance(limit, dict):
        # Pi's ChatGPT limit message names no window: take it from the Codex record.
        window = trigger.reset_window(_read_usage(), "codex", now)
        if window is not None:
            if window.get("kind") in consts.LIMIT_KINDS:
                limit["kind"] = window["kind"]
            if limit.get("resetEpoch") is None:
                limit["resetEpoch"] = window["resetsAt"]
                limit["source"] = "record"
    return result


def _run_session_id(job, result):
    sid = result.get("sessionId")
    grammar = consts.OPENCODE_ID_RE if job["harness"] == "opencode" else consts.UUID_RE
    if isinstance(sid, str) and grammar.match(sid):
        return sid
    target = job["target"]
    if job["harness"] == "gemini" and target["mode"] == "new" and result["outcome"] not in (
            "auth", "not_found", "untrusted"):
        return target.get("newSessionId")
    return None


def verified_pi_session_path(path):
    """The session file of a Pi run once lstat shows a regular file inside the real sessions folder."""
    if not isinstance(path, str) or not harness.pi_session_path_ok(path):
        return None
    try:
        st = os.lstat(path)
        root = os.path.realpath(os.path.join(fsio.home(), consts.PI_SESSIONS_REL))
        real_dir = os.path.realpath(os.path.dirname(path))
    except (OSError, ApError):
        return None
    if not stat.S_ISREG(st.st_mode) or not real_dir.startswith(root + "/"):
        return None
    real = os.path.join(real_dir, os.path.basename(path))
    return real if harness.pi_session_path_ok(real) else None


def _limit_source(job, billing, usage):
    try:
        from . import windows
        value = windows.limit_source_for(job, billing, usage)
    except Exception:
        return None
    return value if isinstance(value, str) and consts.SOURCE_ID_RE.fullmatch(value) else None


def _cli_version(harness_id):
    try:
        from . import agents
        fn = getattr(agents, "cached_version", None)
        value = fn(harness_id) if callable(fn) else None
    except Exception:
        return None
    return value if isinstance(value, str) and _VERSION_RE.fullmatch(value) else None


def _observed(job, result, billing, usage):
    """(source id, kind, short label, reset) of a limit the run itself reported, or None (v2 7.6)."""
    limit = result.get("limit") if result.get("outcome") == "limit" else None
    if not isinstance(limit, dict) or job["harness"] not in _OBSERVED_HARNESSES:
        return None
    epoch = limit.get("resetEpoch")
    if not jobs.is_epoch(epoch) or limit.get("source") not in _OBSERVED_SOURCES:
        return None
    if job["harness"] == "cursor":
        return ("cursor", "billing_total", "Cursor cycle", epoch)
    source_id = "zen-free" if result.get("detail") == "zen_free_limit" else _limit_source(job, billing, usage)
    if source_id is None:
        return None
    kind = limit.get("kind") if limit.get("kind") in consts.LIMIT_KINDS else "other"
    return (source_id, kind, _SHORT_LABEL.get(kind, "Limit"), epoch)


def _append_observed(sd, observed, now):
    if not observed:
        return
    try:
        from . import limits_history
        limits_history.append_observed(sd, observed[0], observed[1], observed[2], observed[3], now)
    except Exception:
        pass


def _record(job, gen, run, result, cli, started, ended, action, extra):
    state = job["state"]
    scheduled = state.get("fireAt") if isinstance(state.get("fireAt"), int) else started
    mode, _sid = harness.effective_session(job)
    return {
        "schemaVersion": 1, "runId": "%s-g%d" % (job["id"], gen), "jobId": job["id"], "gen": gen,
        "harness": job["harness"], "level": job["level"], "mode": mode,
        "scheduledFor": scheduled, "startedAt": started, "endedAt": ended, "lateSec": max(0, started - scheduled),
        "outcome": result["outcome"], "detail": result["detail"],
        "exit": run.get("rc"), "signal": run.get("signal"), "killedBy": run.get("killedBy"),
        "bytesDropped": run.get("bytesDropped", 0), "stdoutBytes": run.get("stdoutBytes", 0),
        "stderrBytes": run.get("stderrBytes", 0), "logBytes": run.get("logBytes", 0),
        "sessionId": result.get("sessionId"),
        "limit": result.get("limit"),
        "cli": {"exec": cli["real"], "changed": cli["changed"], "from": cli["from"], "to": cli["to"]},
        "cwd": job["target"]["cwd"],
        "action": action,
        "allowPaid": job.get("allowPaid") is True,
        "provider": job.get("provider"),
        "model": job.get("model"),
        "billing": extra["billing"],
        "limitSource": extra["limitSource"],
        "cliVersion": extra["cliVersion"],
        "sessionPath": extra["sessionPath"],
    }


def _extra(job, result, gate, usage):
    billing = (gate or {}).get("billing") if job["harness"] == "opencode" else None
    billing = billing if billing in consts.BILLING_CLASSES else None
    session_path = verified_pi_session_path(result.get("sessionPath")) if job["harness"] == "pi" else None
    return {"billing": billing, "limitSource": _limit_source(job, billing, usage),
            "cliVersion": _cli_version(job["harness"]), "sessionPath": session_path}


def _act(sd, job_id, gen, run, result, cli, started, claimed_job, gate=None):
    """Apply the outcome under the jobs lock. Returns the diagnostic code and the notification to send."""
    # Read for every outcome, so a run's limitSource does not depend on how it ended (a Cursor run
    # without a record belongs to "No limit data" whether it finished or hit a limit).
    usage = _read_usage()
    post_usage = usage if result["outcome"] == "limit" else None
    extra = _extra(claimed_job, result, gate, usage)
    observed = _observed(claimed_job, result, extra["billing"], usage)
    with sd.lock(consts.RUNNER_LOCK_WAIT_S):
        store = jobs.load_store(sd)
        job = _find(store, job_id)
        now = timeutil.now()
        state = (job or {}).get("state") or {}
        if job is None or state.get("gen") != gen or state.get("status") != "running":
            # Disarmed, updated or deleted while running: keep the record, change nothing else.
            record = _record(claimed_job, gen, run, result, cli, started, now,
                             {"status": None, "fireAt": None, "reason": None}, extra)
            jobs.write_run_record(sd, record)
            if job is not None:
                jobs.add_history(job, "late_result", now, None)
                _touch(job, now)
                jobs.save_store(sd, store)
            return {"code": "LATE_RESULT", "notify": None, "observed": observed, "now": now}

        action = trigger.postrun(job, dict(result, endedAt=now), now, post_usage)
        # wait and counter let reconcile finish this transition if the runner dies before the save below.
        record = _record(job, gen, run, result, cli, started, now,
                         {"status": action["status"], "fireAt": action["fireAt"], "reason": action["reason"],
                          "wait": action.get("wait"), "counter": action.get("counter")}, extra)
        if job["target"]["mode"] in ("new", "fork") and not state.get("runSessionId"):
            if job["harness"] == "pi":
                sid = result.get("sessionId")
                if extra["sessionPath"] and isinstance(sid, str) and consts.UUID_RE.fullmatch(sid):
                    state["runSessionId"] = sid
                    state["runSessionPath"] = extra["sessionPath"]
            else:
                sid = _run_session_id(job, result)
                if sid:
                    state["runSessionId"] = sid
        state["lastRun"] = {"runId": "%s-g%d" % (job_id, gen), "startedAt": started, "endedAt": now,
                            "outcome": result["outcome"], "exit": run.get("rc"), "signal": run.get("signal"),
                            "bytesDropped": run.get("bytesDropped", 0)}
        jobs.write_run_record(sd, record)
        if action["status"] == "armed":
            event = _WAIT_HISTORY.get(action["wait"], "transient")
            _set_armed(job, action["fireAt"], now, wait=action["wait"], reason=action["reason"],
                       counter=action["counter"], event=event)
            if action["wait"] == "limit":
                limit = result.get("limit") or {}
                source = limit.get("source") if limit.get("source") in consts.LIMIT_SOURCES else "backoff"
                _set_basis(state, source, limit.get("resetEpoch"))
            jobs.save_store(sd, store)
            _arm_after_save(sd, store, job, now)
            code = "REARMED"
        else:
            _set_final(sd, job, action["status"], now, action["reason"], _HISTORY_FOR.get(action["status"], "run_failed"))
            jobs.save_store(sd, store)
            code = "FINAL"
        return {"code": code, "notify": action["notify"], "job": job, "fireAt": action["fireAt"],
                "reason": action["reason"], "duration": max(0, now - started), "now": now, "observed": observed}


def _mark_failed_best_effort(sd, job_id, gen):
    try:
        with sd.lock(consts.RUNNER_LOCK_WAIT_S):
            store = jobs.load_store(sd)
            job = _find(store, job_id)
            if job is None or job["state"].get("gen") != gen or job["state"].get("status") != "running":
                return
            now = timeutil.now()
            _set_final(sd, job, "failed", now, "failed", "run_failed")
            jobs.save_store(sd, store)
    except Exception:
        pass


def _finish(sd, *, reconcile_after):
    notify.ipc_ping()
    if not reconcile_after:
        return
    try:
        reconcile.reconcile(sd)
    except Exception:
        pass


def cmd_run(argv, payload):
    job_id, gen = _parse_args(argv)
    if not systemd.under_systemd():
        _diag("E_NOT_SYSTEMD", job_id, gen)
        return {}
    stop_event = threading.Event()
    signal.signal(signal.SIGTERM, lambda _signum, _frame: stop_event.set())

    try:
        sd = fsio.open_state(create=True)
    except ApError:
        _diag("E_STATE", job_id, gen)
        return {}

    ctx = {"claimed": False, "job": None, "session_lock": None, "cli": None, "startedAt": None, "gate": None}
    try:
        try:
            job, prompt = _claim(sd, job_id, gen, ctx)
        except _Stop as stop:
            _diag(stop.code, job_id, gen)
            if stop.event and ctx["job"] is not None:
                notify.send(stop.event, ctx["job"], now=timeutil.now(), fire_at=stop.fire_at, reason=stop.reason)
                _finish(sd, reconcile_after=False)
            return {}
        except ApError:
            _diag("E_STATE", job_id, gen)
            return {}
        _diag("FIRED", job_id, gen)

        cli = ctx["cli"]
        state, run = _spawn(sd, job, gen, prompt, cli, stop_event, ctx["gate"])
        prompt = None
        started = ctx["startedAt"]
        result = _classify(job, state, run, timeutil.now())
        _diag("OUTCOME_" + result["outcome"].upper(), job_id, gen)

        try:
            done = _act(sd, job_id, gen, run, result, cli, started, job, ctx["gate"])
        except ApError:
            _diag("E_STATE", job_id, gen)
            return {}
        _diag(done["code"], job_id, gen)
        # Outside the jobs lock: the limits history has its own lock and never fails a run.
        _append_observed(sd, done.get("observed"), done.get("now") or timeutil.now())
        if done.get("notify"):
            notify.send(done["notify"], done["job"], now=done["now"], fire_at=done["fireAt"],
                        duration_s=done["duration"], reason=done["reason"], cli_changed=bool(cli["changed"]))
        # After SIGTERM the manager is stopping this unit; reconcile runs at the next start instead.
        _finish(sd, reconcile_after=not stop_event.is_set())
        return {}
    except Exception:
        _diag("E_INTERNAL", job_id, gen)
        if ctx["claimed"]:
            _mark_failed_best_effort(sd, job_id, gen)
        raise SystemExit(70) from None
    finally:
        if ctx["session_lock"] is not None:
            try:
                os.close(ctx["session_lock"])
            except OSError:
                pass
