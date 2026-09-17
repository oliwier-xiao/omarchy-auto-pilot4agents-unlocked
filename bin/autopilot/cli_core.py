"""Verbs that read and change jobs: edition, list, job-get, job-create, job-update, job-delete,
preview, arm, run-now, disarm, cancel-all, reschedule, swap, shift, reconcile, settings-get,
settings-set and copy-resume.

Every mutation persists the new state under the jobs lock, with the generation bumped whenever
units change, before systemd is asked to create or stop anything. A timer that survives a
failed stop can therefore only fire as a stale generation, which the runner ignores. Stopping
units, which may poll for up to 15 s, happens after the lock is released, because a runner
that is being stopped needs that lock to record its result.
"""
import copy
import hashlib
import os
import uuid

from . import (agents, bounded, consts, edition, fsio, harness, identity, jobs, models, reconcile, sessions,
               settings, systemd, timeutil, trigger, usage)
from .errors import ApError

_PLACEHOLDER_ID = "0" * 16
_PLACEHOLDER_SHA = "0" * 64
_PLACEHOLDER_SESSION = "00000000-0000-4000-8000-000000000000"
_DISARMABLE = ("armed", "running", "paused", "needs_confirm")
_FIRE_AT_ERRORS = ("time_past", "time_too_far", "no_reset_data", "reset_not_open", "weekly_exhausted",
                   "trigger_unsupported")
# Gate codes arm, run-now and the preview may report (v2 3.5), and the field each one points at.
GATE_CODES = ("harness_gated", "cursor_autorun_config", "cursor_network_config", "cursor_project_rules",
              "cursor_untrusted", "not_logged_in", "pi_auth_invalid", "paid_blocked", "paid_zen",
              "paid_opencode_claude", "paid_pi_claude", "paid_pi_key")
_GATE_FIELD = {"harness_gated": "harness", "cursor_autorun_config": "target.cwd",
               "cursor_network_config": "target.cwd", "cursor_project_rules": "target.cwd",
               "cursor_untrusted": "target.cwd", "pi_auth_invalid": "provider", "paid_blocked": "allowPaid",
               "paid_zen": "allowPaid", "paid_opencode_claude": "allowPaid", "paid_pi_claude": "allowPaid",
               "paid_pi_key": "allowPaid"}
_GATE_DEADLINE_MAX_S = 30.0


# ---------------------------------------------------------------------------- shared helpers

def _open_existing():
    """(StateDir or None, store). A missing state folder reads as an empty store."""
    sd = fsio.open_state(create=False)
    if sd is None:
        return None, jobs.empty_store()
    return sd, jobs.load_store(sd)


def _read_usage():
    try:
        return usage.read_usage(timeutil.now_ms())
    except ApError:
        return None


def _discover(harness_id):
    found = identity.discover_cli(harness_id)
    if not found.get("ok"):
        raise ApError("cli_missing" if found.get("reason") == "not_found" else "cli_untrusted", "harness")
    return found


def _resolve_target(draft):
    """(cwd, title) of the target: the session's recorded folder for resume/fork, else the draft's.

    Pi sessions are looked up by their in-store file (the header id must match); Cursor chats by
    the Auto Pilot run record that created them.
    """
    target = draft["target"]
    title = ""
    if target["mode"] in ("resume", "fork"):
        if draft["harness"] == "pi":
            found = sessions.lookup_session("pi", target["sessionId"], session_path=target.get("sessionPath"))
        else:
            found = sessions.lookup_session(draft["harness"], target["sessionId"])
        if not isinstance(found, dict) or not isinstance(found.get("cwd"), str):
            raise ApError("session_not_found", "target.sessionId")
        cwd = jobs.check_cwd(found["cwd"])
        title = jobs.clean_text(found.get("title") or "", consts.TITLE_MAX)
    else:
        cwd = jobs.check_cwd(target["cwd"])
    return cwd, title or jobs.clean_text(os.path.basename(cwd) or cwd, consts.TITLE_MAX)


def _check_login(harness_id, found):
    if harness_id == "codex" and agents.codex_logged_in(found["exec"]) is False:
        raise ApError("not_logged_in", "harness")


def _resolve(draft, *, check_login=True):
    found = _discover(draft["harness"])
    cwd, title = _resolve_target(draft)
    if check_login:
        _check_login(draft["harness"], found)
    return found, cwd, title


def _fresh_state(gen, last_run, history):
    return {"status": "draft", "wait": None, "reason": None, "gen": gen, "fireAt": None, "unit": None,
            "armedAt": None, "pluginDir": None, "basis": None, "runSessionId": None, "runSessionPath": None,
            "defers": 0, "limitRetries": 0, "transientRetries": 0, "busyDefers": 0, "unknownRetries": 0,
            "lastRun": last_run, "history": history}


def _gate(job, phase, found, now, usage_obj, sd):
    """paid.check_job for preview or arm. Raises internal when the gate could not be used."""
    budget = bounded.remaining_budget()
    deadline = _GATE_DEADLINE_MAX_S if budget is None else max(0.5, min(_GATE_DEADLINE_MAX_S, budget - 2.0))
    try:
        from . import paid
        gate = paid.check_job(job, phase=phase, now=now, usage=usage_obj, sd=sd, exec_prefix=found["exec"],
                              deadline_s=deadline)
    except ApError:
        raise
    except Exception:
        raise ApError("internal")
    if not isinstance(gate, dict):
        raise ApError("internal")
    return gate


def _public_gate(gate, job):
    """The preview's gate object (v2 3.5): fixed keys, every value checked."""
    if gate is None:
        gated = job["harness"] in consts.GATED_HARNESSES
        return {"ok": False, "code": "harness_gated" if gated else None, "detail": None, "notes": [],
                "billing": None, "provider": None, "resetAtMs": None, "pending": not gated}
    code = gate.get("code") if gate.get("code") in GATE_CODES else None
    detail = gate.get("detail") if isinstance(gate.get("detail"), dict) else {}
    provider_detail = detail.get("provider")
    notes = gate.get("notes") if isinstance(gate.get("notes"), list) else []
    reset_ms = gate.get("resetAtMs")
    provider = gate.get("provider")
    return {
        "ok": gate.get("ok") is True and code is None,
        "code": code,
        "detail": ({"provider": provider_detail} if isinstance(provider_detail, str)
                   and consts.PI_PROVIDER_RE.fullmatch(provider_detail) else None),
        "notes": [n for n in notes if isinstance(n, str) and len(n) <= 40][:8],
        "billing": gate.get("billing") if gate.get("billing") in consts.BILLING_CLASSES else None,
        "provider": provider if isinstance(provider, str) and consts.PI_PROVIDER_RE.fullmatch(provider) else None,
        "resetAtMs": reset_ms if jobs.is_int(reset_ms) and reset_ms >= 0 else None,
        "pending": gate.get("pending") is True,
    }


def _build_job(draft, *, job_id, found, cwd, title, now, prefs, prompt_sha, prompt_bytes, existing=None):
    """A complete Job (status draft) from a validated draft. existing: the job being updated."""
    harness_id = draft["harness"]
    target = draft["target"]
    trig = draft["trigger"]
    new_sid = None
    if target["mode"] == "new" and harness_id in harness.NEW_SESSION_HARNESSES:
        old = (existing or {}).get("target") or {}
        reusable = (existing is not None and existing["state"]["lastRun"] is None
                    and existing["harness"] == harness_id and old.get("mode") == "new" and old.get("newSessionId"))
        new_sid = old["newSessionId"] if reusable else str(uuid.uuid4())
    # A label the user typed is kept; a generated one follows the agent and folder. The prompt
    # never supplies a label (it would reach the notification and busctl's argv).
    if draft["label"]:
        label = draft["label"]
    elif existing is not None and existing["label"] != jobs.default_label(existing["harness"],
                                                                         existing["target"]["cwd"]):
        label = existing["label"]
    else:
        label = jobs.default_label(harness_id, cwd)
    version = None
    if existing is not None and existing["cli"]["link"] == found["link"]:
        version = existing["cli"]["version"]
    history = copy.deepcopy(existing["state"]["history"]) if existing is not None else []
    job = {
        "id": job_id,
        "label": label,
        "createdAt": existing["createdAt"] if existing is not None else now,
        "updatedAt": now,
        "harness": harness_id,
        "cli": {"link": found["link"], "real": found["real"], "version": version},
        "target": {"mode": target["mode"], "sessionId": target["sessionId"], "newSessionId": new_sid, "cwd": cwd,
                   "title": title, "allowNonGit": bool(target["allowNonGit"]),
                   "sessionPath": target.get("sessionPath")},
        "level": draft["level"],
        "allowPaid": draft.get("allowPaid") is True,
        "provider": draft.get("provider"),
        "limits": dict(draft["limits"]),
        "model": draft["model"],
        "trigger": {"kind": trig["kind"], "fireAt": trig["fireAt"], "delaySec": trig["delaySec"],
                    "marginSec": trig["marginSec"] if trig["marginSec"] is not None else prefs["resetMarginSec"],
                    "weeklyPolicy": trig["weeklyPolicy"],
                    "graceSec": consts.GRACE_RESET_S if trig["kind"] in consts.RESET_KINDS else consts.GRACE_TIME_S},
        "promptSha256": prompt_sha,
        "promptBytes": prompt_bytes,
        "promptAvailable": True,
        "commandDigest": "",
        "digest": "",
        "state": _fresh_state(existing["state"]["gen"] if existing is not None else 0,
                              copy.deepcopy(existing["state"]["lastRun"]) if existing is not None else None,
                              history),
    }
    jobs.refresh_digests(job)
    return job


def _disarm_in_place(job, now):
    """status disarmed, gen+1, no unit. Returns the generation whose units must be stopped."""
    state = job["state"]
    old_gen = state["gen"]
    jobs.set_status(job, "disarmed", now)
    state["gen"] = old_gen + 1
    state["unit"] = None
    jobs.add_history(job, "disarmed", now)
    return old_gen


def _set_at(job, fire_at):
    """Turn the trigger into a fixed time. Returns True when it was bound to a reset."""
    trig = job["trigger"]
    unbound = trig["kind"] in consts.RESET_KINDS
    trig.update(kind="at", fireAt=fire_at, delaySec=None, graceSec=consts.GRACE_TIME_S)
    jobs.refresh_digests(job)
    return unbound


def _prepare_rearm(job, fire_at, now):
    """armed at fire_at under a new generation (persist before arming). Returns the old generation."""
    state = job["state"]
    old_gen = state["gen"]
    jobs.set_status(job, "armed", now, wait=None)
    state.update(gen=old_gen + 1, fireAt=fire_at, unit=systemd.unit_name(job["id"], old_gen + 1), armedAt=now,
                 basis=None, pluginDir=fsio.plugin_dir())
    job["updatedAt"] = now
    return old_gen


def _arm_prepared(job, now):
    """Create the timer of an already persisted armed job. Returns the ApError or None."""
    state = job["state"]
    try:
        systemd.arm_unit(job["id"], state["gen"], state["fireAt"], job["limits"]["runtimeSec"])
        return None
    except ApError as err:
        state["unit"] = None
        jobs.add_history(job, "arm_failed", now)
        return err


def _stop_quietly(job_id, gen):
    """Stop units of a generation that is already stale; failures change nothing."""
    try:
        systemd.stop_units(job_id, gen)
    except ApError:
        pass


def _reconcile_quietly(sd):
    try:
        reconcile.reconcile(sd)
    except ApError:
        pass


# ---------------------------------------------------------------------------- read verbs

def cmd_edition(argv, payload):
    return {
        "ok": True,
        "edition": {
            "pluginId": edition.PLUGIN_ID,
            "displayName": edition.DISPLAY_NAME,
            "unitPrefix": edition.UNIT_PREFIX,
            "stateDirName": edition.STATE_DIR_NAME,
            "runtimeDirName": edition.RUNTIME_DIR_NAME,
            "killSwitchPath": fsio.kill_switch_path(),
            "widgetIpcTarget": edition.WIDGET_IPC_TARGET,
            "serviceIpcTarget": edition.SERVICE_IPC_TARGET,
            "notifyAppName": edition.NOTIFY_APP_NAME,
            "schemaVersion": edition.SCHEMA_VERSION,
            "pluginDir": fsio.plugin_dir(),
            "version": fsio.manifest_version(),
        },
        "levels": copy.deepcopy(list(edition.LEVELS)),
        "harnesses": [{"id": h, "name": consts.HARNESS_NAMES[h], "cliName": consts.CLI_NAMES[h],
                       "canFork": consts.CAN_FORK[h], "gated": h in consts.GATED_HARNESSES,
                       "resetTrigger": consts.RESET_TRIGGER[h], "resetTriggers": list(consts.RESET_KINDS_FOR[h])}
                      for h in consts.HARNESSES],
        "caps": {
            "promptBytes": consts.PROMPT_MAX_BYTES,
            "labelChars": consts.LABEL_MAX,
            "titleChars": consts.TITLE_MAX,
            "maxTurns": [consts.MAX_TURNS_MIN, consts.MAX_TURNS_MAX],
            "budgetUsd": [consts.BUDGET_MIN, consts.BUDGET_MAX],
            "runtimeSec": [consts.RUNTIME_MIN, consts.RUNTIME_MAX],
            "marginSec": [consts.MARGIN_MIN, consts.MARGIN_MAX],
            "horizonSec": consts.HORIZON_S,
            "uiMinLeadSec": consts.UI_MIN_LEAD_S,
            "shiftMaxIds": consts.SHIFT_MAX_IDS,
            "shiftRangeSec": consts.SHIFT_RANGE_S,
        },
    }


def cmd_list(argv, payload):
    _sd, store = _open_existing()
    now = timeutil.now()
    ordered = sorted(store["jobs"], key=lambda j: (j["state"]["fireAt"] is None, j["state"]["fireAt"] or 0,
                                                   j["createdAt"]))
    models.begin_list_cache()
    try:
        public = [jobs.public_job(j, now) for j in ordered]
    finally:
        models.end_list_cache()
    return {"ok": True, "nowMs": timeutil.now_ms(), "jobs": public,
            "killSwitch": fsio.kill_switch_present(), "enabledInShell": fsio.plugin_enabled_in_shell(),
            "linger": fsio.linger_enabled(), "reconciledAt": store["reconciledAt"]}


def cmd_job_get(argv, payload):
    job_id = argv[0]
    sd, store = _open_existing()
    job = jobs.find_job(store, job_id)
    prompt = None
    if job["promptAvailable"]:
        data = jobs.read_prompt(sd, job_id)
        prompt = data.decode("utf-8") if data is not None else None
    try:
        preview = harness.preview_command(job)
    except ApError:
        preview = None
    runs = jobs.read_run_records(sd, job_id, consts.RUNS_PER_JOB)
    last_lines = []
    newest_gen = runs[0].get("gen") if runs else None
    if not jobs.is_int(newest_gen) and job["state"]["lastRun"]:
        newest_gen = int(job["state"]["lastRun"]["runId"].rsplit("-g", 1)[1])
    if jobs.is_int(newest_gen):
        last_lines = jobs.read_log_tail_lines(sd, job_id, newest_gen, 20)
    return {"ok": True, "job": job, "prompt": prompt, "promptAvailable": job["promptAvailable"], "preview": preview,
            "runs": runs, "lastLines": last_lines}


def cmd_copy_resume(argv, payload):
    _sd, store = _open_existing()
    job = jobs.find_job(store, argv[0])
    return {"ok": True, "command": harness.resume_display(job)}


def cmd_settings_get(argv, payload):
    sd = fsio.open_state(create=False)
    return {"ok": True, "settings": settings.load(sd)}


def cmd_preview(argv, payload):
    if not isinstance(payload, dict):
        raise ApError("bad_input")
    body = {k: v for k, v in payload.items() if k not in ("prompt", "expectCommandDigest")}
    has_trigger = body.get("trigger") is not None
    if not has_trigger:
        body["trigger"] = {"kind": "now"}
    draft = jobs.validate_draft(body, require_prompt=False)
    # The sign-in probes belong to the gate below, which reports instead of refusing.
    found, cwd, title = _resolve(draft, check_login=False)
    sd = fsio.open_state(create=False)
    existing = None
    if "id" in body and sd is not None:
        try:
            existing = jobs.find_job(jobs.load_store(sd), body["id"])
        except ApError:
            existing = None
    now = timeutil.now()
    job = _build_job(draft, job_id=existing["id"] if existing else _PLACEHOLDER_ID, found=found, cwd=cwd,
                     title=title, now=now, prefs=settings.load(sd), prompt_sha=_PLACEHOLDER_SHA, prompt_bytes=0,
                     existing=existing)
    if existing is None and job["target"]["newSessionId"]:
        job["target"]["newSessionId"] = _PLACEHOLDER_SESSION
    preview = harness.preview_command(job)
    usage_obj = _read_usage()
    gate = _gate(job, "preview", found, now, usage_obj, sd)
    preview["gate"] = _public_gate(gate, job)
    if has_trigger:
        try:
            plan = trigger.compute_fire_at(job, now, usage_obj, billing=(gate or {}).get("billing"))
            preview["fireAt"] = plan["fireAt"]
            preview["immediate"] = bool(plan["immediate"])
            preview["fireAtHint"] = plan["hint"]
        except ApError as err:
            if err.code not in _FIRE_AT_ERRORS:
                raise
            preview["fireAtError"] = err.code
    return {"ok": True, "preview": preview}


# ---------------------------------------------------------------------------- job store verbs

def cmd_job_create(argv, payload):
    draft = jobs.validate_draft(payload, require_prompt=True)
    found, cwd, title = _resolve(draft)
    data = draft["prompt"].encode("utf-8")
    sd = fsio.open_state(create=True)
    with sd.lock(consts.LOCK_WAIT_S):
        store = jobs.load_store(sd)
        now = timeutil.now()
        job_id = jobs.new_job_id(store)
        job = _build_job(draft, job_id=job_id, found=found, cwd=cwd, title=title, now=now, prefs=settings.load(sd),
                         prompt_sha=hashlib.sha256(data).hexdigest(), prompt_bytes=len(data))
        jobs.add_history(job, "created", now)
        if draft["expectCommandDigest"] is not None and draft["expectCommandDigest"] != job["commandDigest"]:
            raise ApError("preview_stale")
        if len(store["jobs"]) >= consts.JOBS_MAX or not jobs.has_room_for(store, job):
            jobs.prune(sd, store, now)
            if len(store["jobs"]) >= consts.JOBS_MAX or not jobs.has_room_for(store, job):
                jobs.save_store(sd, store)
                raise ApError("store_full")
        jobs.store_prompt(sd, job_id, draft["prompt"])
        store["jobs"].append(job)
        try:
            jobs.save_store(sd, store)
        except ApError:
            jobs.delete_job_files(sd, job_id)
            raise
    return {"ok": True, "id": job_id, "job": jobs.public_job(job, now), "digest": job["digest"],
            "commandDigest": job["commandDigest"]}


def _updated_job(sd, job, draft, found, cwd, title, now):
    """The replacement job for job-update (nothing written yet)."""
    if draft["prompt"] is None:
        data = jobs.read_prompt(sd, job["id"]) if job["promptAvailable"] else None
        if data is None:
            raise ApError("prompt_missing")
    else:
        data = draft["prompt"].encode("utf-8")
    new_job = _build_job(draft, job_id=job["id"], found=found, cwd=cwd, title=title, now=now,
                         prefs=settings.load(sd), prompt_sha=hashlib.sha256(data).hexdigest(),
                         prompt_bytes=len(data), existing=job)
    if draft["expectCommandDigest"] is not None and draft["expectCommandDigest"] != new_job["commandDigest"]:
        raise ApError("preview_stale")
    return new_job


def _commit_update(sd, store, job, new_job, draft, now):
    jobs.add_history(new_job, "updated", now)
    if draft["prompt"] is not None:
        jobs.store_prompt(sd, job["id"], draft["prompt"])
    store["jobs"][store["jobs"].index(job)] = new_job
    jobs.save_store(sd, store)


def cmd_job_update(argv, payload):
    job_id = argv[0]
    draft = jobs.validate_draft(payload, require_prompt=False)
    found, cwd, title = _resolve(draft)
    sd = fsio.open_state(create=True)
    with sd.lock(consts.LOCK_WAIT_S):
        store = jobs.load_store(sd)
        job = jobs.find_job(store, job_id)
        status = job["state"]["status"]
        if status == "running":
            raise ApError("bad_status", detail={"status": status})
        now = timeutil.now()
        new_job = _updated_job(sd, job, draft, found, cwd, title, now)
        if status != "armed":
            _commit_update(sd, store, job, new_job, draft, now)
            return {"ok": True, "id": job_id, "job": jobs.public_job(new_job, now), "digest": new_job["digest"],
                    "commandDigest": new_job["commandDigest"], "wasArmed": False}
        old_gen = _disarm_in_place(job, now)
        job["updatedAt"] = now
        jobs.save_store(sd, store)
    # Stop outside the lock; stop_unverified leaves the job disarmed with its generation bumped.
    systemd.stop_units(job_id, old_gen)
    with sd.lock(consts.LOCK_WAIT_S):
        store = jobs.load_store(sd)
        job = jobs.find_job(store, job_id)
        if job["state"]["status"] != "disarmed" or job["state"]["gen"] != old_gen + 1:
            raise ApError("bad_status", detail={"status": job["state"]["status"]})
        now = timeutil.now()
        new_job = _updated_job(sd, job, draft, found, cwd, title, now)
        _commit_update(sd, store, job, new_job, draft, now)
    return {"ok": True, "id": job_id, "job": jobs.public_job(new_job, now), "digest": new_job["digest"],
            "commandDigest": new_job["commandDigest"], "wasArmed": True}


def cmd_job_delete(argv, payload):
    job_id = argv[0]
    sd = fsio.open_state(create=True)
    with sd.lock(consts.LOCK_WAIT_S):
        store = jobs.load_store(sd)
        job = jobs.find_job(store, job_id)
        if job["state"]["status"] in ("armed", "running"):
            raise ApError("bad_status", detail={"status": job["state"]["status"]})
        store["jobs"].remove(job)
        jobs.save_store(sd, store)
        jobs.delete_job_files(sd, job_id)
    return {"ok": True, "id": job_id}


def cmd_settings_set(argv, payload):
    partial = settings.validate_partial(payload)
    sd = fsio.open_state(create=True)
    with sd.lock(consts.LOCK_WAIT_S):
        merged = settings.load(sd)
        merged.update(partial)
        settings.save(sd, merged)
    return {"ok": True, "settings": merged}


# ---------------------------------------------------------------------------- scheduling verbs

def _arm_checks(sd, store, job, digest, run_now):
    """Status, prompt, plugin identity, digest and CLI checks under the jobs lock. Returns the CLI."""
    job_id = job["id"]
    status = job["state"]["status"]
    allowed = consts.ARMABLE_STATUSES + (("armed",) if run_now else ())
    if status not in allowed:
        raise ApError("bad_status", detail={"status": status})
    if not job["promptAvailable"] or jobs.read_prompt(sd, job_id) is None:
        raise ApError("prompt_missing")
    if fsio.kill_switch_present():
        raise ApError("kill_switch")
    if fsio.plugin_enabled_in_shell() is not True:
        raise ApError("plugin_disabled")
    if fsio.manifest_id() != edition.PLUGIN_ID:
        raise ApError("plugin_identity")
    now = timeutil.now()
    stored = (job["commandDigest"], job["digest"])
    jobs.refresh_digests(job)
    if (job["commandDigest"], job["digest"]) != stored:
        job["updatedAt"] = now
        jobs.save_store(sd, store)
        raise ApError("digest_mismatch")
    if digest != job["digest"]:
        raise ApError("digest_mismatch")
    found = _discover(job["harness"])
    if found["link"] != job["cli"]["link"]:
        job["cli"] = {"link": found["link"], "real": found["real"], "version": None}
        jobs.refresh_digests(job)
        jobs.add_history(job, "cli_changed", now)
        job["updatedAt"] = now
        jobs.save_store(sd, store)
        raise ApError("digest_mismatch")
    return found


def _gate_field(code, harness_id):
    if code == "not_logged_in":
        return "provider" if harness_id == "pi" else "harness"
    return _GATE_FIELD.get(code, "harness")


def _arm_verb(argv, run_now):
    job_id, digest = argv[0], argv[2]
    sd = fsio.open_state(create=True)
    stop_gen = None
    with sd.lock(consts.LOCK_WAIT_S):
        store = jobs.load_store(sd)
        job = jobs.find_job(store, job_id)
        found = _arm_checks(sd, store, job, digest, run_now)
        if job["harness"] in consts.GATED_HARNESSES:
            raise ApError("harness_gated", "harness")
        snapshot = copy.deepcopy(job)
    # Sign-in probes and the billing check run outside the jobs lock, so a runner that fires
    # meanwhile never waits on them; the digest check below proves the job did not change.
    usage_obj = _read_usage()
    gate = _gate(snapshot, "arm", found, timeutil.now(), usage_obj, sd)
    if gate is None:
        raise ApError("internal")
    code = gate.get("code")
    if code:
        raise ApError(code if code in GATE_CODES else "internal", _gate_field(code, snapshot["harness"]),
                      detail=gate.get("detail") if isinstance(gate.get("detail"), dict) else None)
    if gate.get("ok") is False:
        # A gate that refuses without a known code is treated as a refusal, never as a pass.
        raise ApError("internal")
    with sd.lock(consts.LOCK_WAIT_S):
        store = jobs.load_store(sd)
        job = jobs.find_job(store, job_id)
        status = job["state"]["status"]
        if status not in consts.ARMABLE_STATUSES + (("armed",) if run_now else ()):
            raise ApError("bad_status", detail={"status": status})
        stored = (job["commandDigest"], job["digest"])
        jobs.refresh_digests(job)
        if (job["commandDigest"], job["digest"]) != stored or digest != job["digest"] \
                or job["cli"]["link"] != found["link"]:
            raise ApError("digest_mismatch")
        now = timeutil.now()
        if run_now:
            plan = {"fireAt": now, "immediate": True, "basis": None, "hint": None, "wait": None}
        else:
            plan = trigger.compute_fire_at(job, now, usage_obj, billing=gate.get("billing"))
        fire_at = None if plan["immediate"] else max(int(plan["fireAt"]), now + consts.ARM_MIN_LEAD_S)
        before = copy.deepcopy(job["state"])
        old_gen = before["gen"]
        if run_now and status == "armed":
            stop_gen = old_gen
        state = job["state"]
        job["cli"]["real"] = found["real"]
        jobs.set_status(job, "armed", now, wait=None if run_now else plan.get("wait"))
        state.update(gen=old_gen + 1, fireAt=now if fire_at is None else fire_at,
                     unit=systemd.unit_name(job_id, old_gen + 1), armedAt=now, pluginDir=fsio.plugin_dir(),
                     basis=plan.get("basis"))
        jobs.reset_counters(job)
        jobs.add_history(job, "armed", now)
        jobs.save_store(sd, store)
        failure = None
        try:
            systemd.arm_unit(job_id, state["gen"], fire_at, job["limits"]["runtimeSec"])
        except ApError as err:
            failure = err
            before.update(gen=old_gen + 1, unit=None)
            job["state"] = before
            jobs.add_history(job, "arm_failed", now)
            jobs.save_store(sd, store)
        public = jobs.public_job(job, now)
    if stop_gen is not None:
        _stop_quietly(job_id, stop_gen)
    if failure is not None:
        raise failure
    return {"ok": True, "id": job_id, "status": "armed", "fireAt": public["state"]["fireAt"],
            "unit": public["state"]["unit"], "immediate": fire_at is None, "hint": plan.get("hint"), "job": public}


def cmd_arm(argv, payload):
    return _arm_verb(argv, run_now=False)


def cmd_run_now(argv, payload):
    return _arm_verb(argv, run_now=True)


def cmd_disarm(argv, payload):
    job_id = argv[0]
    sd = fsio.open_state(create=True)
    with sd.lock(consts.LOCK_WAIT_S):
        store = jobs.load_store(sd)
        job = jobs.find_job(store, job_id)
        if job["state"]["status"] not in _DISARMABLE:
            raise ApError("bad_status", detail={"status": job["state"]["status"]})
        now = timeutil.now()
        old_gen = _disarm_in_place(job, now)
        job["updatedAt"] = now
        jobs.save_store(sd, store)
        public = jobs.public_job(job, now)
    stopped = systemd.stop_units(job_id, old_gen)
    return {"ok": True, "id": job_id, "verified": stopped["verified"], "job": public}


def cmd_cancel_all(argv, payload):
    disarmed = []
    fallback = []
    state_error = None
    try:
        sd = fsio.open_state(create=True)
        with sd.lock(consts.LOCK_WAIT_S):
            store = jobs.load_store(sd)
            now = timeutil.now()
            for job in store["jobs"]:
                if job["state"]["status"] in _DISARMABLE:
                    old_gen = _disarm_in_place(job, now)
                    job["updatedAt"] = now
                    disarmed.append(job["id"])
                    base = systemd.unit_name(job["id"], old_gen)
                    fallback += [base + ".timer", base + ".service"]
            if disarmed:
                jobs.save_store(sd, store)
    except ApError as err:
        state_error = err
    # Units are stopped even when the job store could not be read: this is the removal path.
    try:
        stopped = systemd.stop_all_prefix_units()
    except ApError:
        partial = systemd.stop_names(fallback)
        stopped = {"units": partial["units"], "verified": False}
    if state_error is not None:
        raise state_error
    return {"ok": True, "disarmed": disarmed, "stoppedUnits": stopped["units"], "verified": stopped["verified"]}


def _check_window(fire_at, now):
    if fire_at < now + consts.UI_MIN_LEAD_S:
        raise ApError("time_past")
    if fire_at > now + consts.HORIZON_S:
        raise ApError("time_too_far")


def cmd_reschedule(argv, payload):
    job_id, epoch = argv[0], int(argv[1])
    sd = fsio.open_state(create=True)
    old_gen = None
    failure = None
    with sd.lock(consts.LOCK_WAIT_S):
        store = jobs.load_store(sd)
        job = jobs.find_job(store, job_id)
        status = job["state"]["status"]
        if status not in ("draft", "armed"):
            raise ApError("bad_status", detail={"status": status})
        now = timeutil.now()
        _check_window(epoch, now)
        unbound = _set_at(job, epoch)
        jobs.add_history(job, "rescheduled", now)
        job["updatedAt"] = now
        if status == "armed":
            old_gen = _prepare_rearm(job, epoch, now)
            jobs.save_store(sd, store)
            failure = _arm_prepared(job, now)
            if failure is not None:
                jobs.save_store(sd, store)
        else:
            jobs.save_store(sd, store)
        public = jobs.public_job(job, now)
    if old_gen is not None:
        _stop_quietly(job_id, old_gen)
    if failure is not None:
        _reconcile_quietly(sd)
        raise failure
    return {"ok": True, "id": job_id, "fireAt": epoch, "unbound": unbound, "job": public}


def _rearm_many(sd, store, moves, event, now):
    """moves: [(job, fire_at)]. Persist all, arm all, then stop old units and reconcile on failure."""
    results = []
    olds = []
    for job, fire_at in moves:
        unbound = _set_at(job, fire_at)
        jobs.add_history(job, event, now)
        olds.append((job["id"], _prepare_rearm(job, fire_at, now)))
        results.append({"id": job["id"], "fireAt": fire_at, "unbound": unbound})
    jobs.save_store(sd, store)
    partial = [job["id"] for job, _t in moves if _arm_prepared(job, now) is not None]
    if partial:
        jobs.save_store(sd, store)
    return results, olds, partial


def _finish_many(sd, olds, partial, results):
    for job_id, gen in olds:
        _stop_quietly(job_id, gen)
    if partial:
        _reconcile_quietly(sd)
        raise ApError("systemd_failed", detail={"partial": partial})
    return {"ok": True, "jobs": results}


def cmd_swap(argv, payload):
    sd = fsio.open_state(create=True)
    with sd.lock(consts.LOCK_WAIT_S):
        store = jobs.load_store(sd)
        first, second = jobs.find_job(store, argv[0]), jobs.find_job(store, argv[1])
        for job in (first, second):
            if job["state"]["status"] != "armed" or job["state"]["fireAt"] is None:
                raise ApError("bad_status", detail={"status": job["state"]["status"]})
        now = timeutil.now()
        lead = now + consts.ARM_MIN_LEAD_S
        moves = [(first, max(second["state"]["fireAt"], lead)), (second, max(first["state"]["fireAt"], lead))]
        results, olds, partial = _rearm_many(sd, store, moves, "swapped", now)
    return _finish_many(sd, olds, partial, results)


def cmd_shift(argv, payload):
    delta = int(argv[1])
    ids = argv[2:]
    if delta == 0 or abs(delta) > consts.SHIFT_RANGE_S:
        raise ApError("bad_args")
    sd = fsio.open_state(create=True)
    with sd.lock(consts.LOCK_WAIT_S):
        store = jobs.load_store(sd)
        targets = [jobs.find_job(store, job_id) for job_id in ids]
        if any(j["state"]["status"] != "armed" or j["state"]["fireAt"] is None for j in targets):
            raise ApError("bad_status")
        now = timeutil.now()
        for job in targets:
            _check_window(job["state"]["fireAt"] + delta, now)
        moves = [(job, job["state"]["fireAt"] + delta) for job in targets]
        results, olds, partial = _rearm_many(sd, store, moves, "shifted", now)
    return _finish_many(sd, olds, partial, results)


def cmd_reconcile(argv, payload):
    sd = fsio.open_state(create=True)
    return dict({"ok": True}, **reconcile.reconcile(sd))
