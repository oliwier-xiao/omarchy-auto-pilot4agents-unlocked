#!/bin/bash
# tests/canary.sh: the prompt travels only on stdin, end to end.
#
# A random canary prompt goes through preview, job-create, list, arm (the stub systemd-run fires the
# job at once, so the runner and the stub agent start from that call) and job-get, with stub tools
# and a stub agent CLI, for three agents:
#   Claude Code         the whole way to done
#   canary_pi_fire      a Pi job the whole way to done (stand-in pi: sign-in check, model list, stub run)
#   canary_cursor_fire  a Cursor Agent job the whole way to done (trusted folder, ask mode, stub run)
#   canary_models_usage_timeline_outputs
#                       usage, timeline, models, sessions, list and agents answers after the runs
# For the whole run a scanner thread reads /proc/<pid>/cmdline and
# /proc/<pid>/environ of every process on the machine, so a runner that outlives the helper call that
# started it is still covered. After the job reaches its final state every file the run left behind
# is searched: tool argv and environments (calls.jsonl), notification argv, runner stderr (the
# journal stand-in), helper answers, run records and logs. The only allowed places are
# prompts/<id>.txt until the job is final, and the stub agent's stdin hash.
#
# Everything happens under a temporary HOME and runtime folder in $TMPDIR. The real HOME, the real
# state folder and the real user manager are never touched, and no model CLI is ever started.
set -uo pipefail
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PY=/usr/bin/python3

if [ ! -x "$PY" ]; then
  echo "  FAIL canary: /usr/bin/python3 is required"
  exit 1
fi

T="$(mktemp -d "${TMPDIR:-/tmp}/ap4a-canary.XXXXXX")" || exit 2
trap 'rm -rf "$T"' EXIT INT TERM

echo "=== prompt canary (stub tools, stub agent) ==="
PYTHONDONTWRITEBYTECODE=1 "$PY" -I -S -B - "$REPO" "$T" <<'PY'
import hashlib
import json
import os
import re
import secrets
import shutil
import subprocess
import sys
import threading
import time

REPO, TMP = sys.argv[1], os.path.realpath(sys.argv[2])
sys.path.insert(0, os.path.join(REPO, "bin"))
from autopilot import edition  # noqa: E402

PY = "/usr/bin/python3"
FINAL = ("done", "failed", "limit", "skipped", "gave_up", "missed", "interrupted", "paused", "needs_confirm",
         "busy", "disarmed")
counts = {"pass": 0, "fail": 0}


def ok(msg):
    print("  ok   " + msg)
    counts["pass"] += 1


def no(msg, detail=""):
    print("  FAIL " + msg + ("\n         " + detail if detail else ""))
    counts["fail"] += 1


def expect(cond, msg, detail=""):
    if cond:
        ok(msg)
    else:
        no(msg, detail)


def write_json(path, value, mode=0o600):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as handle:
        json.dump(value, handle)
    os.chmod(path, mode)


# ---------------------------------------------------------------- sandbox
uid = os.getuid()
home = os.path.join(TMP, "home")
runtime = os.path.join(TMP, "run", "user", str(uid))
for folder in (home, runtime):
    os.makedirs(folder, mode=0o700, exist_ok=True)
    os.chmod(folder, 0o700)
stubs = os.path.join(REPO, "tests", "stubs")
launch = os.path.join(REPO, "tests", "support", "launch.py")
conf = os.path.join(TMP, "conf")
tools_json = os.path.join(conf, "tools.json")
cand_json = os.path.join(conf, "candidates.json")
write_json(tools_json, {
    "systemd_run": os.path.join(stubs, "systemd-run"), "systemctl": os.path.join(stubs, "systemctl"),
    "busctl": os.path.join(stubs, "busctl"), "qs": os.path.join(stubs, "qs"),
    "timedatectl": os.path.join(stubs, "timedatectl"), "node": os.path.join(TMP, "no-node"),
})
# Discovery only ever sees the sandbox: no real agent CLI can be picked up.
write_json(cand_json, {
    "claude": ["~/.local/bin/claude"], "opencode": ["~/.opencode/bin/opencode"],
    "codex": ["~/.local/bin/codex"], "gemini": [os.path.join(TMP, "sys", "gemini.js")],
    "cursor": ["~/.local/bin/agent"], "pi": ["~/.local/bin/pi"],
})
runtime_re = "^" + re.escape(runtime) + "$"
fakesys = os.path.join(home, ".fakesys")
write_json(os.path.join(fakesys, "config.json"), {
    "fire": True, "tools": tools_json, "runtimeRe": runtime_re,
    "launchArgs": ["--candidates", cand_json, "--trust-root", TMP],
})
agent = os.path.join(home, ".local", "bin", "claude")
os.makedirs(os.path.dirname(agent), exist_ok=True)
shutil.copy(os.path.join(stubs, "fake-agent"), agent)
os.chmod(agent, 0o755)
agent_log = os.path.join(home, "fake-agent.log")
write_json(os.path.join(home, ".fake-agent.json"), {"mode": "done", "log": agent_log})
write_json(os.path.join(home, ".config", "omarchy", "shell.json"),
           {"bar": {"layout": {"left": [], "center": [], "right": [edition.PLUGIN_ID]}}})
project = os.path.join(home, "proj")
os.makedirs(project, exist_ok=True)
state = os.path.join(home, ".local", "state", "omarchy", edition.STATE_DIR_NAME)

canary = "CANARY-" + secrets.token_hex(16)
# The canary opens the prompt and the draft has no label, so a label taken from the prompt's
# first line would carry it into list output, the notification body and busctl's argv.
prompt = canary + " Read the build log, then tell me what broke.\nKeep it short."
needle = canary.encode()

# ---------------------------------------------------------------- /proc scanner for the whole run
leaks = set()
phase = ["setup"]
rounds = [0]
stop_scan = threading.Event()


def scan_every_process():
    while not stop_scan.is_set():
        for name in os.listdir("/proc"):
            if not name.isdigit():
                continue
            for part in ("cmdline", "environ"):
                try:
                    with open("/proc/%s/%s" % (name, part), "rb") as handle:
                        data = handle.read()
                except OSError:
                    continue
                if needle in data:
                    leaks.add("/proc/%s/%s during %s" % (name, part, phase[0]))
        rounds[0] += 1


scanner = threading.Thread(target=scan_every_process, daemon=True)
scanner.start()


def helper(verb, *args, payload=None):
    phase[0] = verb
    argv = [PY, "-I", "-S", "-B", launch, "--tools", tools_json, "--runtime-re", runtime_re,
            "--candidates", cand_json, "--trust-root", TMP, "--", verb] + list(args)
    env = {"HOME": home, "USER": os.environ.get("USER", "tester"), "XDG_RUNTIME_DIR": runtime,
           "LANG": "C.UTF-8", "PATH": "/usr/bin", "PYTHONDONTWRITEBYTECODE": "1"}
    data = None if payload is None else (json.dumps(payload, separators=(",", ":")) + "\n").encode()
    try:
        res = subprocess.run(argv, env=env, cwd=TMP, input=data,
                             stdin=None if data is not None else subprocess.DEVNULL,
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=240, check=False)
    except subprocess.TimeoutExpired:
        return 124, None, "timed out"
    if needle in res.stdout:
        leaks.add("stdout of %s" % verb)
    if needle in res.stderr:
        leaks.add("stderr of %s" % verb)
    try:
        answer = json.loads(res.stdout.decode("utf-8"))
    except ValueError:
        answer = None
    return res.returncode, answer, res.stdout.decode("utf-8", errors="replace")


def status_of(job_id):
    rc, res, _raw = helper("list")
    listed = [j for j in ((res or {}).get("jobs") or []) if j.get("id") == job_id]
    return listed[0]["state"]["status"] if listed else None


draft = {
    "harness": "claude",
    "target": {"mode": "new", "sessionId": None, "cwd": project, "allowNonGit": False},
    "level": "plan",
    "limits": {},
    "model": None,
    "trigger": {"kind": "now", "fireAt": None, "delaySec": None, "marginSec": 120, "weeklyPolicy": "defer"},
    "prompt": prompt,
}

rc, res, raw = helper("preview", payload=draft)
expect(rc == 0 and isinstance(res, dict) and res.get("ok") is True,
       "preview answers ok with the prompt present in the request", raw[:300])
preview = (res or {}).get("preview") or {}
expect(str(preview.get("display", "")).endswith("<stdin>"), "the Will run line ends with <stdin>", str(preview.get("display")))
expect(re.fullmatch(r"[0-9a-f]{64}", str(preview.get("commandDigest", ""))) is not None, "preview carries a command digest")

rc, res, raw = helper("job-create", payload=dict(draft, expectCommandDigest=preview.get("commandDigest")))
expect(rc == 0 and isinstance(res, dict) and res.get("ok") is True, "job-create stores the job", raw[:300])
job_id = str((res or {}).get("id", ""))
digest = str((res or {}).get("digest", ""))
prompt_file = os.path.join(state, "prompts", job_id + ".txt")
try:
    with open(prompt_file, "rb") as handle:
        stored = handle.read()
    mode = os.stat(prompt_file).st_mode & 0o777
except OSError:
    stored, mode = b"", None
expect(needle in stored and mode == 0o600, "the prompt is stored once, in prompts/<id>.txt with mode 0600",
       "mode=%s present=%s" % (oct(mode) if mode is not None else None, needle in stored))
expect(status_of(job_id) == "draft", "list shows the new job as a draft")

rc, res, raw = helper("arm", job_id, "--digest", digest)
expect(rc == 0 and isinstance(res, dict) and res.get("ok") is True, "arm fires the job through the stub scheduler", raw[:300])

# The runner may still be finishing when arm answers; wait for the final state (bounded).
phase[0] = "run"
status, started = None, time.monotonic()
while time.monotonic() - started < 90:
    status = status_of(job_id)
    if status in FINAL:
        break
    time.sleep(0.2)

rc, res, raw = helper("job-get", job_id)
got = res or {}
full = (got.get("job") or {}).get("state") or {}
events = " ".join("%s%s" % (h.get("event"), "(%s)" % h["detail"] if h.get("detail") else "")
                  for h in full.get("history") or [])
expect(status == "done", "the job ran to done within 90 s",
       "list status=%s; history: %s" % (status, events))
expect(rc == 0 and got.get("prompt") is None and got.get("promptAvailable") is False,
       "job-get reports the prompt deleted after the run", raw[:300])
runs = got.get("runs") or []
expect(bool(runs) and runs[0].get("outcome") == "done", "the run record says done")
expect(not os.path.exists(prompt_file), "prompts/<id>.txt is gone once the job is final")

# ---------------------------------------------------------------- what the stub agent saw
entries = []
try:
    with open(agent_log) as handle:
        entries = [json.loads(line) for line in handle if line.strip()]
except (OSError, ValueError):
    pass
runs_seen = [e for e in entries if "-p" in (e.get("argv") or [])]
expect(len(runs_seen) == 1, "the stub agent ran exactly once", "%d run(s) logged" % len(runs_seen))
if runs_seen:
    entry = runs_seen[0]
    expect(entry.get("stdinSha256") == hashlib.sha256(prompt.encode()).hexdigest(),
           "the agent received the whole prompt on stdin (sha256 matches)")
    expect(needle not in json.dumps([entry.get("argv"), entry.get("env")]).encode(),
           "the agent's argv and environment carry no prompt text")
    argv = entry.get("argv") or []
    expect("--permission-mode" in argv and argv[argv.index("--permission-mode") + 1] == "plan",
           "the agent started in the plan level")

# ---------------------------------------------------------------- scheduler, notification and journal stand-ins
calls = []
try:
    with open(os.path.join(fakesys, "calls.jsonl")) as handle:
        calls = [json.loads(line) for line in handle if line.strip()]
except (OSError, ValueError):
    pass
unit_re = re.compile(r"^--unit=" + re.escape(edition.UNIT_PREFIX) + "-" + re.escape(job_id) + r"-g[0-9]+$")
expect(any(c["tool"] == "systemd-run" and any(unit_re.match(a) for a in c["argv"]) for c in calls),
       "systemd-run was asked for this job's unit")
expect(any(c["tool"] == "busctl" and "Done" in c["argv"] for c in calls), "a Done notification was sent")
expect(any(c["tool"] == "qs" and "changed" in c["argv"] for c in calls), "the shell was pinged with changed")
expect(needle not in json.dumps(calls).encode(), "no tool argv or environment carried the prompt (%d calls)" % len(calls))
try:
    with open(os.path.join(fakesys, "runner-stderr.log"), "rb") as handle:
        journal = handle.read()
except OSError:
    journal = b""
expect(needle not in journal and all(l.startswith(b"ap4a: ") for l in journal.splitlines() if l.strip()),
       "runner stderr holds only fixed diagnostic lines")


def agent_entries():
    try:
        with open(agent_log) as handle:
            return [json.loads(line) for line in handle if line.strip()]
    except (OSError, ValueError):
        return []


def wait_final(job_id, label):
    phase[0] = "run " + label
    status, started = None, time.monotonic()
    while time.monotonic() - started < 90:
        status = status_of(job_id)
        if status in FINAL:
            break
        time.sleep(0.2)
    return status


def stored_prompt(job_id):
    path = os.path.join(state, "prompts", job_id + ".txt")
    try:
        with open(path, "rb") as handle:
            return path, handle.read(), os.stat(path).st_mode & 0o777
    except OSError:
        return path, b"", None


fake_agent = os.path.join(stubs, "fake-agent")
now_trigger = {"kind": "now", "fireAt": None, "delaySec": None, "marginSec": 120, "weeklyPolicy": "defer"}

# ---------------------------------------------------------------- canary_pi_fire
# The stand-in pi answers its sign-in check (a ChatGPT sign-in) and its model listing itself, and
# hands every other call to the stub agent, which plays Pi's json stream (mode pi_done) without a model.
print("\n  -- canary_pi_fire --")
pi_bin = os.path.join(home, ".local", "bin", "pi")
with open(pi_bin, "w") as handle:
    # One shebang word: the kernel hands "-I -S" to python as a single, unknown option.
    handle.write("""#!/usr/bin/python3 -IS
import json
import os
import sys

args = sys.argv[1:]
if args[:2] == ["auth", "check"] and "--provider" in args:
    provider = args[args.index("--provider") + 1]
    sys.stdout.write(json.dumps({"status": "ready", "provider": provider, "authType": "oauth"}) + "\\n")
    sys.exit(0)
if "--list-models" in args:
    sys.stdout.write("provider      model    context  max-out  thinking  images\\n")
    sys.stdout.write("openai-codex  gpt-5.5  400K     128K     yes       yes\\n")
    sys.exit(0)
if args == ["--version"]:
    sys.stdout.write("0.74.0\\n")
    sys.exit(0)
os.execv("/usr/bin/python3", ["/usr/bin/python3", "-I", "-S", %r] + args)
""" % fake_agent)
os.chmod(pi_bin, 0o755)
write_json(os.path.join(home, ".fake-agent.json"), {"mode": "pi_done", "log": agent_log})
pi_prompt = canary + " List the TODO comments in this folder.\nOne line each."
pi_draft = {
    "harness": "pi", "provider": "openai-codex", "model": "gpt-5.5", "allowPaid": False,
    "target": {"mode": "new", "sessionId": None, "cwd": project, "allowNonGit": False},
    "level": "plan", "limits": {}, "trigger": dict(now_trigger), "prompt": pi_prompt,
}
rc, res, raw = helper("preview", payload=pi_draft)
expect(rc == 0 and isinstance(res, dict) and res.get("ok") is True, "pi: preview answers ok", raw[:300])
preview = (res or {}).get("preview") or {}
display = str(preview.get("display", ""))
expect(display.startswith("pi --mode json --offline --no-extensions") and display.endswith("<stdin>")
       and "--tools read,grep,find,ls" in display, "pi: the Will run line is the Plan template ending in <stdin>", display)
gate = preview.get("gate") or {}
expect(gate.get("code") is None, "pi: the paid-usage gate lets a ChatGPT sign-in through with paid usage off",
       json.dumps(gate))
rc, res, raw = helper("job-create", payload=dict(pi_draft, expectCommandDigest=preview.get("commandDigest")))
expect(rc == 0 and isinstance(res, dict) and res.get("ok") is True, "pi: job-create stores the job", raw[:300])
pi_id = str((res or {}).get("id", ""))
pi_digest = str((res or {}).get("digest", ""))
pi_prompt_file, stored, mode = stored_prompt(pi_id)
expect(needle in stored and mode == 0o600, "pi: the prompt is stored once, in prompts/<id>.txt with mode 0600")
before = len(agent_entries())
rc, res, raw = helper("arm", pi_id, "--digest", pi_digest)
expect(rc == 0 and isinstance(res, dict) and res.get("ok") is True, "pi: arm fires the job through the stub scheduler",
       raw[:300])
status = wait_final(pi_id, "pi")
rc, res, raw = helper("job-get", pi_id)
got = res or {}
full = (got.get("job") or {}).get("state") or {}
events = " ".join("%s%s" % (h.get("event"), "(%s)" % h["detail"] if h.get("detail") else "")
                  for h in full.get("history") or [])
expect(status == "done", "pi: the job ran to done within 90 s", "list status=%s; history: %s" % (status, events))
expect(got.get("prompt") is None and got.get("promptAvailable") is False and not os.path.exists(pi_prompt_file),
       "pi: the prompt is deleted once the job is final")
runs = got.get("runs") or []
expect(bool(runs) and runs[0].get("outcome") == "done", "pi: the run record says done")
session_root = os.path.join(home, ".pi", "agent", "sessions") + "/"
expect(str(full.get("runSessionPath") or "").startswith(session_root),
       "pi: the run's session file is remembered inside ~/.pi/agent/sessions", str(full.get("runSessionPath")))
pi_runs = [e for e in agent_entries()[before:] if "--provider" in (e.get("argv") or [])]
expect(len(pi_runs) == 1, "pi: the stub agent ran exactly once", "%d run(s) logged" % len(pi_runs))
if pi_runs:
    entry = pi_runs[0]
    argv = entry.get("argv") or []
    expect(entry.get("stdinSha256") == hashlib.sha256(pi_prompt.encode()).hexdigest(),
           "pi: the agent received the whole prompt on stdin (sha256 matches)")
    expect(needle not in json.dumps([argv, entry.get("env")]).encode(),
           "pi: the agent's argv and environment carry no prompt text")
    expect("--offline" in argv and "--tools" in argv and argv[argv.index("--tools") + 1] == "read,grep,find,ls"
           and argv[argv.index("--provider") + 1] == "openai-codex", "pi: the agent started read-only on the armed provider")
    allowed = {"HOME", "PATH", "LANG", "TERM", "PI_OFFLINE", "PI_TELEMETRY", "PI_SKIP_VERSION_CHECK", "LC_CTYPE"}
    extra = sorted(set(entry.get("env") or {}) - allowed)
    expect(not extra, "pi: the agent's environment is the Pi allowlist", ", ".join(extra))

# ---------------------------------------------------------------- canary_cursor_fire
# Cursor Agent runs like the others since its one-time live check passed: the job is previewed,
# stored, armed through the stub scheduler, and the stub cursor-agent plays its stream. Cursor only
# starts in a folder it already trusts, so the marker it looks for is written first.
print("\n  -- canary_cursor_fire --")
cursor_bin = os.path.join(home, ".local", "bin", "agent")
shutil.copy(fake_agent, cursor_bin)
os.chmod(cursor_bin, 0o755)
cursor_slug = re.sub(r"-+", "-", re.sub(r"[^a-zA-Z0-9]", "-", os.path.realpath(project))).strip("-")
write_json(os.path.join(home, ".cursor", "projects", cursor_slug, ".workspace-trusted"),
           {"trustedAt": int(time.time() * 1000), "workspacePath": os.path.realpath(project)})
write_json(os.path.join(home, ".fake-agent.json"), {"mode": "cursor_ok", "log": agent_log})
cursor_prompt = canary + " Explain how the build is set up."
cursor_draft = {
    "harness": "cursor", "allowPaid": False, "model": None,
    "target": {"mode": "new", "sessionId": None, "cwd": project, "allowNonGit": False},
    "level": "plan", "limits": {}, "trigger": dict(now_trigger), "prompt": cursor_prompt,
}
before = len(agent_entries())
rc, res, raw = helper("preview", payload=cursor_draft)
expect(rc == 0 and isinstance(res, dict) and res.get("ok") is True, "cursor: preview answers ok", raw[:300])
preview = (res or {}).get("preview") or {}
display = str(preview.get("display", ""))
expect(display.startswith("cursor-agent -p --output-format stream-json --mode ask --workspace ")
       and display.endswith("<stdin>"), "cursor: the Will run line is the ask-mode template ending in <stdin>", display)
gate = preview.get("gate") or {}
expect(gate.get("code") is None, "cursor: the gate lets a trusted folder through with paid usage off",
       json.dumps(gate))
rc, res, raw = helper("job-create", payload=dict(cursor_draft, expectCommandDigest=preview.get("commandDigest")))
expect(rc == 0 and isinstance(res, dict) and res.get("ok") is True, "cursor: job-create stores the job", raw[:300])
cursor_id = str((res or {}).get("id", ""))
cursor_digest = str((res or {}).get("digest", ""))
cursor_prompt_file, stored, mode = stored_prompt(cursor_id)
expect(needle in stored and mode == 0o600 and status_of(cursor_id) == "draft",
       "cursor: the draft and its 0600 prompt file are stored")
rc, res, raw = helper("arm", cursor_id, "--digest", cursor_digest)
expect(rc == 0 and isinstance(res, dict) and res.get("ok") is True,
       "cursor: arm fires the job through the stub scheduler", raw[:300])
status = wait_final(cursor_id, "cursor")
rc, res, raw = helper("job-get", cursor_id)
got = res or {}
expect(status == "done", "cursor: the job ran to done within 90 s", "list status=%s" % status)
expect(got.get("prompt") is None and not os.path.exists(cursor_prompt_file),
       "cursor: the prompt is deleted once the job is final")
runs = got.get("runs") or []
expect(bool(runs) and runs[0].get("outcome") == "done", "cursor: the run record says done")
cursor_runs = [e for e in agent_entries()[before:] if e.get("harness") == "cursor" and "-p" in (e.get("argv") or [])]
expect(len(cursor_runs) == 1, "cursor: the stub agent ran exactly once", "%d run(s) logged" % len(cursor_runs))
if cursor_runs:
    entry = cursor_runs[0]
    argv = entry.get("argv") or []
    expect(entry.get("stdinSha256") == hashlib.sha256(cursor_prompt.encode()).hexdigest(),
           "cursor: the agent received the whole prompt on stdin (sha256 matches)")
    expect(needle not in json.dumps([argv, entry.get("env")]).encode(),
           "cursor: the agent's argv and environment carry no prompt text")
    placed = all(flag in argv for flag in ("--mode", "--workspace"))
    expect(placed and argv[argv.index("--mode") + 1] == "ask"
           and argv[argv.index("--workspace") + 1] == project,
           "cursor: the agent started in ask mode, in the job's folder", " ".join(argv))
    # An allowlist, so any flag Auto Pilot does not build itself (an approval or trust bypass above all)
    # fails here without this file naming one.
    allowed_flags = {"-p", "--output-format", "--mode", "--sandbox", "--workspace", "--model", "--resume"}
    used_flags = sorted(a for a in argv if a.startswith("-"))
    expect(set(used_flags) <= allowed_flags, "cursor: the agent got only the flags Auto Pilot builds",
           " ".join(used_flags))
rc, res, raw = helper("job-delete", cursor_id)
expect(rc == 0 and isinstance(res, dict) and res.get("ok") is True,
       "cursor: the finished job can be deleted", raw[:300])

# ---------------------------------------------------------------- canary_models_usage_timeline_outputs
print("\n  -- canary_models_usage_timeline_outputs --")
now = int(time.time())
for verb, args in (("usage", []), ("timeline", ["--from", str(now - 86400), "--to", str(now + 3600)]),
                   ("models", ["--harness", "pi"]), ("models", ["--harness", "claude"]),
                   ("sessions", ["--harness", "pi", "--cwd", project]), ("list", []), ("agents", [])):
    rc, res, raw = helper(verb, *args)
    name = " ".join([verb] + args[:2])
    expect(rc == 0 and isinstance(res, dict) and res.get("ok") is True and needle not in raw.encode(),
           "%s answers ok and carries no prompt text" % name, raw[:300])
    if verb == "timeline":
        listed = (res or {}).get("runs") or []
        expect({job_id, pi_id} <= {r.get("jobId") for r in listed},
               "timeline lists the Claude Code and Pi runs of the day", json.dumps(listed)[:300])
run_files, run_hits = 0, []
for dirpath, _dirs, files in os.walk(os.path.join(state, "runs")):
    for name in files:
        run_files += 1
        with open(os.path.join(dirpath, name), "rb") as handle:
            if needle in handle.read():
                run_hits.append(name)
expect(run_files > 0 and not run_hits, "no run log or run record holds the prompt (%d files)" % run_files,
       ", ".join(run_hits))
try:
    with open(os.path.join(fakesys, "runner-stderr.log"), "rb") as handle:
        journal = handle.read()
except OSError:
    journal = b""
expect(needle not in journal and all(l.startswith(b"ap4a: ") for l in journal.splitlines() if l.strip()),
       "runner stderr still holds only fixed diagnostic lines after every run")

# ---------------------------------------------------------------- every file left behind, every process seen
time.sleep(0.5)
stop_scan.set()
scanner.join()
found = []
for dirpath, _dirs, files in os.walk(TMP):
    for name in files:
        path = os.path.join(dirpath, name)
        if os.path.islink(path):
            continue
        try:
            with open(path, "rb") as handle:
                if needle in handle.read(64 * 1024 * 1024):
                    found.append(os.path.relpath(path, TMP))
        except OSError:
            continue
expect(not found, "no file under the sandbox holds the prompt after the run", ", ".join(found))
expect(rounds[0] > 0 and not leaks,
       "no process cmdline or environment and no helper answer carried the prompt (%d full /proc scans)" % rounds[0],
       "; ".join(sorted(leaks)[:5]))

print("\n%d passed, %d failed" % (counts["pass"], counts["fail"]))
sys.exit(1 if counts["fail"] else 0)
PY
