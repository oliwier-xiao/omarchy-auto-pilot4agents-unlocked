"""Reconciler (R0 2.5, contract 3.4.2): bring the systemd units back in line with jobs.json.

Transient units vanish on success, on logout without linger and on reboot, so jobs.json is the
only source of truth and this pass restores what the units should be. It is idempotent and
runs under the jobs lock. It only touches units whose names match this edition's unit
grammar, and it never reloads the manager.

Stopping stale units and sending notifications happen after the lock is released: a stopping
runner needs that lock to record its result, and a toast should never hold up other changes.
"""
from . import consts, fsio, identity, jobs, notify, systemd, timeutil, trigger
from .errors import ApError

RESULT_LISTS = ("rearmed", "fired", "missed", "interrupted", "resumed", "stopped")
_HISTORY_FOR_STATUS = {"done": "run_done", "failed": "run_failed", "limit": "limit", "busy": "busy",
                       "skipped": "skipped", "gave_up": "gave_up", "missed": "missed",
                       "interrupted": "interrupted", "paused": "paused", "needs_confirm": "needs_confirm",
                       "disarmed": "disarmed"}
_COUNTERS = ("defers", "limitRetries", "transientRetries", "busyDefers", "unknownRetries")
_DERIVE = object()


def reconcile(sd, now=None):
    """Take the lock, reconcile, save, then stop stale units and send notifications."""
    with sd.lock(consts.LOCK_WAIT_S):
        store = jobs.load_store(sd)
        now = timeutil.now() if now is None else int(now)
        result = reconcile_locked(sd, store, now)
        jobs.save_store(sd, store)
    return finish(result)


def finish(result):
    """Work deferred until the lock is released. Returns the public result."""
    stale = result.pop("_stop", [])
    notices = result.pop("_notices", [])
    if stale:
        result["stopped"] = systemd.stop_names(stale)["units"]
    for event, job, fire_at in notices:
        try:
            notify.send(event, job, now=timeutil.now(), fire_at=fire_at)
        except Exception:
            pass
    return result


class _Pass:
    def __init__(self, sd, store, now, units):
        self.sd = sd
        self.store = store
        self.now = now
        self.timers = units["timers"]
        self.services = units["services"]
        self._blocked = False
        self._blocked_reason = None
        self.result = {key: [] for key in RESULT_LISTS}
        self.result.update(pruned=0, linger=None, _stop=[], _notices=[])

    def blocked(self):
        """kill_switch or plugin_disabled when nothing may be armed now, else None (cached per pass)."""
        if not self._blocked:
            self._blocked = True
            if fsio.kill_switch_present():
                self._blocked_reason = "kill_switch"
            elif fsio.plugin_enabled_in_shell() is not True:
                self._blocked_reason = "plugin_disabled"
        return self._blocked_reason

    def timer_live(self, base):
        return base in self.timers and self.timers[base].get("active") in systemd.LIVE_STATES

    def service_live(self, base):
        return base in self.services and self.services[base].get("active") in systemd.LIVE_STATES

    # -- transitions ----------------------------------------------------------------------

    def arm(self, job, fire_at, event, bucket, wait=_DERIVE):
        """gen+1, persist, create the unit. On failure the job stays armed without a unit."""
        state = job["state"]
        kind = job["trigger"]["kind"]
        if wait is _DERIVE:
            wait = state["wait"] if state["status"] == "armed" else ("reset" if kind in consts.RESET_KINDS else None)
        new_gen = state["gen"] + 1
        jobs.set_status(job, "armed", self.now, wait=wait)
        state["gen"] = new_gen
        state["unit"] = systemd.unit_name(job["id"], new_gen)
        state["armedAt"] = self.now
        state["pluginDir"] = fsio.plugin_dir()
        if fire_at is not None:
            state["fireAt"] = fire_at
        elif state["fireAt"] is None:
            state["fireAt"] = self.now
        jobs.add_history(job, event, self.now)
        jobs.save_store(self.sd, self.store)
        try:
            systemd.arm_unit(job["id"], new_gen, fire_at, job["limits"]["runtimeSec"])
        except ApError:
            state["unit"] = None
            jobs.add_history(job, "arm_failed", self.now)
            return False
        self.result[bucket].append(job["id"])
        return True

    def pause(self, job, reason):
        jobs.set_status(job, "paused", self.now, reason=reason)
        job["state"]["unit"] = None
        jobs.add_history(job, "paused", self.now, reason)

    def missed(self, job):
        state = job["state"]
        jobs.set_status(job, "missed", self.now, reason="late")
        state["unit"] = None
        jobs.add_history(job, "missed", self.now, "late")
        self.result["missed"].append(job["id"])
        self.result["_notices"].append(("missed", jobs.public_job(job, self.now), state["fireAt"]))

    def interrupted(self, job):
        jobs.set_status(job, "interrupted", self.now, reason="interrupted")
        job["state"]["unit"] = None
        jobs.add_history(job, "interrupted", self.now, "interrupted")
        self.result["interrupted"].append(job["id"])
        self.result["_notices"].append(("interrupted", jobs.public_job(job, self.now), None))

    def due_or_missed(self, job, fire_at, event, bucket_immediate, wait=_DERIVE):
        """Lateness rule of step 2: fire now within the grace, else missed."""
        if self.now - fire_at <= trigger.catchup_grace_s(job):
            reason = self.blocked()
            if reason:
                self.pause(job, reason)
            else:
                self.arm(job, None, event, bucket_immediate, wait=wait)
        else:
            self.missed(job)

    def apply_record(self, job, record):
        """A run record exists for the current generation: finish what the runner started."""
        action = record.get("action") if isinstance(record, dict) else None
        action = action if isinstance(action, dict) else {}
        status = action.get("status")
        if status == "armed" and jobs.is_epoch(action.get("fireAt")):
            reason = self.blocked()
            if reason:
                self.pause(job, reason)
                return
            state = job["state"]
            fire_at = action["fireAt"]
            wait = _DERIVE
            if state["status"] == "running":
                # The runner wrote this record and died before its save: apply what that save held (the
                # retry counter, the wait and the retry time), so the retry cap and the grace still hold.
                wait = action.get("wait") if action.get("wait") in consts.WAITS else None
                if action.get("counter") in _COUNTERS:
                    state[action["counter"]] = int(state.get(action["counter"]) or 0) + 1
                state["wait"] = wait
                state["fireAt"] = fire_at
            if fire_at > self.now:
                self.arm(job, max(fire_at, self.now + consts.ARM_MIN_LEAD_S), "rearmed", "rearmed", wait=wait)
            else:
                self.due_or_missed(job, fire_at, "rearmed", "fired", wait=wait)
            return
        if status not in consts.STATUSES or status in ("armed", "running", "draft"):
            self.interrupted(job)
            return
        reason = action.get("reason") if action.get("reason") in consts.REASONS else None
        jobs.set_status(job, status, self.now, reason=reason)
        job["state"]["unit"] = None
        jobs.add_history(job, _HISTORY_FOR_STATUS.get(status, "run_failed"), self.now, reason)
        if status in consts.CLOSED_STATUSES:
            jobs.delete_prompt(self.sd, job, self.now)

    # -- steps ----------------------------------------------------------------------------

    def job_step(self, job):
        state = job["state"]
        status = state["status"]
        if status not in ("armed", "running", "paused"):
            return
        base = systemd.unit_name(job["id"], state["gen"])
        if status == "armed":
            if self.timer_live(base) or self.service_live(base):
                return
            record = jobs.read_run_record(self.sd, job["id"], state["gen"])
            if record is not None:
                self.apply_record(job, record)
                return
            fire_at = state["fireAt"] if state["fireAt"] is not None else self.now
            if fire_at > self.now:                                           # step 1
                reason = self.blocked()
                if reason:
                    self.pause(job, reason)
                else:
                    self.arm(job, max(fire_at, self.now + consts.ARM_MIN_LEAD_S), "rearmed", "rearmed")
            else:                                                            # step 2
                self.due_or_missed(job, fire_at, "rearmed", "fired")
        elif status == "running":                                            # step 3
            if self.service_live(base):
                return
            record = jobs.read_run_record(self.sd, job["id"], state["gen"])
            if record is not None:
                self.apply_record(job, record)
            else:
                self.interrupted(job)
        else:                                                                # step 4
            if self.blocked() or identity.check_plugin_identity(job) is not None:
                return
            if not job["promptAvailable"]:
                return
            fire_at = state["fireAt"] if state["fireAt"] is not None else self.now
            if self.now - fire_at <= trigger.catchup_grace_s(job):
                self.arm(job, max(fire_at, self.now + consts.ARM_MIN_LEAD_S), "resumed", "resumed")
            else:
                self.missed(job)

    def finishing_runner(self, base):
        """A live service whose runner has already saved its outcome and is on its way out.

        The runner reconciles right after its final save, while its own service is still active,
        and a panel may reconcile in that same moment. Stopping that service would only send TERM
        to a process that is about to leave (cutting its notification) and make the stop poll wait.

        A run leaves a record. A runner that stopped before starting the agent (deferred, paused,
        missed, refused) leaves none: it changed the job, released the lock and is sending its
        notification. Its service is left alone while that change is recent; a later pass stops it
        if it is still there.
        """
        if not self.service_live(base):
            return False
        match = consts.UNIT_RE.fullmatch(base)
        if not match:
            return False
        job_id, gen = match.group(1), int(match.group(2))
        if jobs.read_run_record(self.sd, job_id, gen) is not None:
            return True
        for job in self.store["jobs"]:
            if job["id"] == job_id:
                return gen <= job["state"]["gen"] and 0 <= self.now - job["updatedAt"] <= consts.RUNNER_EXIT_WINDOW_S
        return False

    def stale_units(self):                                                   # step 5
        keep = set()
        for job in self.store["jobs"]:
            if job["state"]["status"] in ("armed", "running"):
                keep.add(systemd.unit_name(job["id"], job["state"]["gen"]))
        names = [base + ".timer" for base in self.timers if base not in keep]
        names += [base + ".service" for base in self.services
                  if base not in keep and not self.finishing_runner(base)]
        return sorted(n for n in names if consts.UNIT_FILE_RE.fullmatch(n))


def reconcile_locked(sd, store, now):
    """Steps 1-6 of contract 3.4.2. The caller holds the lock, saves the store and calls finish()."""
    try:
        units = systemd.list_units()
    except ApError as err:
        result = {key: [] for key in RESULT_LISTS}
        result.update(pruned=jobs.prune(sd, store, now), linger=fsio.linger_enabled(), systemdError=err.code,
                      _stop=[], _notices=[])
        return result
    run = _Pass(sd, store, now, units)
    for job in list(store["jobs"]):
        run.job_step(job)
    run.result["_stop"] = run.stale_units()
    run.result["pruned"] = jobs.prune(sd, store, now)                        # step 6
    store["reconciledAt"] = now
    run.result["linger"] = fsio.linger_enabled()
    return run.result
