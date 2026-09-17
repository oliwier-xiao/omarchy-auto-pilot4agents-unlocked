#!/usr/bin/python3 -I -S
"""Repository policy: the machine-checkable rules of the marketplace review, applied to every file.

Runs with the other suites (tests/run.sh), or on its own:

  /usr/bin/python3 -I -S -B tests/test_policy.py             unittest output
  /usr/bin/python3 -I -S -B tests/test_policy.py --report    one PASS/FAIL line per rule (tests/preflight.sh)

Every check is a function returning a list of "path:line: reason" strings; an empty list passes.
The rules come from the contract (sections 0.3, 1 and 8.2) and the compliance checklist in R0 section 4.
Nothing here writes into the repository; the edition probe runs the helper with a temporary HOME.
"""
import ast
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest

sys.dont_write_bytecode = True
TESTS = os.path.dirname(os.path.realpath(__file__))
ROOT = os.path.dirname(TESTS)
BIN = os.path.join(ROOT, "bin")
if BIN not in sys.path:
    sys.path.insert(0, BIN)

from autopilot import edition  # noqa: E402

SELF = "tests/test_policy.py"
BINARY_EXT = {".png", ".jpg", ".jpeg", ".webp", ".avif", ".gif", ".ico", ".svgz", ".woff", ".woff2", ".ttf"}
INT32_MAX = 2147483647

# ------------------------------------------------------------------------------------------------ patterns
#
# The block between the two markers is the only place in the repository where the automatic-approval
# and permission-bypass spellings may appear. check_denylist() skips exactly these lines of this file.
#
# DENYLIST-BEGIN
DENYLIST = (
    ("claude bypass permission mode", re.compile(r"bypass_?permissions", re.I)),
    ("claude accept-edits permission mode", re.compile(r"accept_?edits", re.I)),
    ("dangerous skip-permissions flag", re.compile(r"--dangerously", re.I)),
    ("auto as a permission or approval mode",
     re.compile(r"(?:permission|approval)[-_ ]?mode[\"'\s,:=\[\]]+auto\b", re.I)),
    ("opencode --auto flag", re.compile(r"(?<![\w-])--auto(?![\w-])")),
    ("yolo mode", re.compile(r"\byolo\b", re.I)),
    ("-y flag", re.compile(r"(?:^|[\s\"'\[,(])-y(?=$|[\s\"'\],)])")),
    ("gemini auto_edit mode", re.compile(r"auto_edit", re.I)),
    ("codex --approve-for-me flag", re.compile(r"--approve-for-me", re.I)),
    ("codex danger-full-access sandbox", re.compile(r"danger-full-access", re.I)),
    ("gemini --skip-trust flag", re.compile(r"--skip-trust", re.I)),
    ("gemini trust-workspace variable", re.compile(r"GEMINI_CLI_TRUST_WORKSPACE")),
    ("opencode config-content variable", re.compile(r"OPENCODE_CONFIG_CONTENT")),
    # Value position only (after a colon, equals sign, bracket or comma), so reading Cursor's
    # permissions.allow list with .get() is not a hit (CONTRACT-V2-DELTA DV12).
    ("allow as a permission value", re.compile(r"(?:[:=]\s*|[\[,]\s*)\\?[\"']allow\\?[\"']")),
    ("codex approval policy override", re.compile(r"approval_policy", re.I)),
    ("codex sandbox network override", re.compile(r"network_access", re.I)),
    # The one fixed sentence that names the flag in order to refuse it (edition.LEVELS unattended
    # "unavailable" reason, shown in the panel and the README) is the only spelling let through.
    ("cursor --force flag", re.compile(r"(?<![\w-])--force(?![\w-])(?!, which Auto Pilot never passes\.)"
                                       r"(?!\. Pick Full access for that\.)")),
    ("cursor --trust flag", re.compile(r"(?<![\w-])--trust(?![\w-])")),
    ("cursor --approve-mcps flag", re.compile(r"--approve-mcps")),
    ("cursor --auto-review flag", re.compile(r"--auto-review")),
    ("cursor unrestricted approval mode", re.compile(r"\"approvalMode\"\s*:\s*\"unrestricted\"")),
    ("cursor test-only auth variables", re.compile(r"AGENT_CLI_(E2E|BYPASS_AUTH)")),
    ("commands that print keys or tokens", re.compile(r"print-(api-key|bearer-token)")),
    ("pi --credentials flag", re.compile(r"(?<![\w-])--credentials(?![\w-])")),
    ("pi --approve flag", re.compile(r"(?<![\w-])--approve(?![\w-])")),
    ("opencode providers list", re.compile(r"providers\s+list")),
)
# DENYLIST-END

# The unlocked edition. Only these levels may carry the spellings above in their argv and env, and
# only these files may write them down: the level table itself and the README that documents it.
# Plan and Unattended stay exactly as strict as in the marketplace edition.
UNLOCKED_LEVELS = ("auto", "full")
UNLOCKED_FILES = ("bin/autopilot/edition.py", "README.md")
UNLOCKED_CURSOR_FLAGS = ("--fo" + "rce", "--tr" + "ust")
UNLOCKED_PI_TOOLS = ("edit", "write", "bash")

AGENT_FILE_NAMES = {
    "agents.md", "agent.md", "claude.md", "gemini.md", "codex.md", "copilot-instructions.md",
    ".cursorrules", ".windsurfrules", ".clinerules", ".mcp.json", "opencode.json", "opencode.jsonc",
    "llms.txt", "conventions.md",
}
AGENT_DIR_NAMES = {".claude", ".cursor", ".codex", ".gemini", ".opencode", ".omo", ".windsurf", ".aider", ".kiro"}
HANDOFF_NAME = re.compile(r"hand-?off|^session-.*\.md$|transcript", re.I)
AGENT_PROSE = re.compile(
    r"\b(?:you are an? (?:ai|coding|autonomous|helpful) (?:agent|assistant)"
    r"|note to (?:the )?(?:ai|agent|assistant|llm|model)"
    r"|instructions for (?:ai|coding|llm) agents"
    r"|as an ai (?:agent|assistant))\b", re.I)
UNIT_EXT = {".service", ".timer", ".socket", ".path", ".mount", ".automount", ".target", ".slice", ".scope"}
FORBIDDEN_NAME = re.compile(r"install|setup|uninstall", re.I)
PROCESS_LAUNCHERS = (r"(?:bash|sh|zsh|dash|fish|python3?|node|env|timeout|setsid|nohup|systemd-run|systemctl"
                     r"|busctl|qs|hyprctl|notify-send|wl-copy|wl-paste|xdg-open|curl|wget|git|omarchy[\w-]*)")


# ------------------------------------------------------------------------------------------------ helpers

def repo_entries():
    """(relative path, lstat result) for every entry below ROOT except .git; symlinks are not followed."""
    out = []
    for dirpath, dirnames, filenames in os.walk(ROOT, followlinks=False):
        rel_dir = os.path.relpath(dirpath, ROOT)
        if rel_dir == ".":
            dirnames[:] = [d for d in dirnames if d != ".git"]
            # In a git worktree .git is a file naming the main checkout, not part of the plugin.
            filenames = [f for f in filenames if f != ".git"]
        dirnames.sort()
        for name in sorted(dirnames + filenames):
            rel = name if rel_dir == "." else os.path.join(rel_dir, name)
            out.append((rel, os.lstat(os.path.join(dirpath, name))))
    return out


def repo_files():
    return [rel for rel, st in repo_entries()
            if stat.S_ISREG(st.st_mode) and "__pycache__" not in rel.split(os.sep) and not rel.endswith(".pyc")]


def git_ignored(paths):
    """Subset of relative paths that git would ignore. Empty when git is unavailable."""
    if not paths:
        return set()
    try:
        proc = subprocess.run(["git", "-C", ROOT, "check-ignore", "-z", "--stdin"],
                              input="\0".join(paths) + "\0", capture_output=True, text=True, check=False)
    except OSError:
        return set()
    return {p for p in proc.stdout.split("\0") if p}


def read_text(rel):
    """File text, or None for binary content."""
    if os.path.splitext(rel)[1].lower() in BINARY_EXT:
        return None
    with open(os.path.join(ROOT, rel), "rb") as handle:
        data = handle.read()
    if b"\0" in data[:8192]:
        return None
    return data.decode("utf-8", errors="replace")


def qml_files(include_lint=False):
    out = []
    for rel in repo_files():
        if not rel.endswith(".qml") or rel.startswith("tests/"):
            continue
        if rel.startswith("lint/") and not include_lint:
            continue
        out.append(rel)
    return out


def script_files(include_lint=False):
    """Plugin QML and JS (the code the shell loads)."""
    out = qml_files(include_lint)
    out += [rel for rel in repo_files() if rel.endswith(".js") and not rel.startswith(("tests/", "lint/"))]
    return sorted(out)


def python_files():
    out = [rel for rel in repo_files() if rel.startswith("bin/") and rel.endswith(".py")]
    if os.path.isfile(os.path.join(ROOT, "bin", "ap4a")):
        out.append("bin/ap4a")
    return sorted(out)


def line_of(text, index):
    return text.count("\n", 0, index) + 1


def _blank(chars, start, end):
    for k in range(start, min(end, len(chars))):
        if chars[k] != "\n":
            chars[k] = " "


def strip_code(text, strings=True):
    """QML/JS with comments blanked (and string contents too when strings=True). Offsets and lines survive."""
    out = list(text)
    i, n, prev = 0, len(text), ""
    while i < n:
        c = text[i]
        nxt = text[i + 1] if i + 1 < n else ""
        if c == "/" and nxt == "/":
            j = text.find("\n", i)
            j = n if j < 0 else j
            _blank(out, i, j)
            i = j
            continue
        if c == "/" and nxt == "*":
            j = text.find("*/", i + 2)
            j = n if j < 0 else j + 2
            _blank(out, i, j)
            i = j
            continue
        if c in "\"'`":
            j = i + 1
            while j < n and text[j] != c:
                if text[j] == "\\":
                    j += 1
                elif text[j] == "\n" and c != "`":
                    break
                j += 1
            if strings:
                _blank(out, i + 1, j)
            i, prev = j + 1, c
            continue
        if c == "/" and (prev == "" or prev in "(,=:[!&|?{};+-*%<>~^"):
            j, in_class = i + 1, False
            while j < n and text[j] != "\n":
                if text[j] == "\\":
                    j += 2
                    continue
                if text[j] == "[":
                    in_class = True
                elif text[j] == "]":
                    in_class = False
                elif text[j] == "/" and not in_class:
                    break
                j += 1
            if j < n and text[j] == "/":
                if strings:
                    _blank(out, i + 1, j)
                i, prev = j + 1, "/"
                continue
        if not c.isspace():
            prev = c
        i += 1
    return "".join(out)


def object_blocks(code, type_names):
    """(type, line, text at depth 1) for every `Type {` object block in comment-stripped QML."""
    pattern = re.compile(r"(?<![\w.])(?:[A-Za-z_]\w*\.)?(%s)\s*\{" % "|".join(type_names))
    found = []
    for match in pattern.finditer(code):
        start = match.end()
        depth, k, body = 1, start, []
        while k < len(code) and depth > 0:
            ch = code[k]
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
            elif depth == 1:
                body.append(ch)
            k += 1
        found.append((match.group(1), line_of(code, match.start()), "".join(body)))
    return found


def matching_bracket(code, start):
    depth = 0
    for k in range(start, len(code)):
        if code[k] in "[(":
            depth += 1
        elif code[k] in "])":
            depth -= 1
            if depth == 0:
                return k
    return len(code) - 1


def denylist_ranges():
    """Line numbers of this file's pattern block (inclusive), which check_denylist() skips."""
    lines = read_text(SELF).split("\n")
    begin = next(i for i, l in enumerate(lines, 1) if l.strip() == "# DENYLIST-" + "BEGIN")
    end = next(i for i, l in enumerate(lines, 1) if l.strip() == "# DENYLIST-" + "END")
    return begin, end


def denylist_hits(text):
    return [label for label, pattern in DENYLIST if pattern.search(text)]


# ------------------------------------------------------------------------------------------------ checks

def check_denylist():
    """No automatic-approval or bypass spelling in any file, outside this file's pattern block and
    UNLOCKED_FILES (whose level entries check_denylist_generated_argv still checks one by one)."""
    problems = []
    begin, end = denylist_ranges()
    for rel in repo_files():
        if rel in UNLOCKED_FILES:
            continue
        text = read_text(rel)
        if text is None:
            continue
        for number, line in enumerate(text.split("\n"), 1):
            if rel == SELF and begin <= number <= end:
                continue
            for label in denylist_hits(line):
                problems.append("%s:%d: %s" % (rel, number, label))
    return problems


SYNTHETIC_PROMPT = "PROMPT-CANARY-5f1c Summarise the failing tests"
NO_FORK = ("gemini", "cursor")


def _synthetic_jobs(home):
    """Every level x offered harness x session mode x model x retry x allowPaid combination."""
    uuid_a = "3f2a0c19-1111-4222-8333-444455556666"
    uuid_b = "9b1d7e42-aaaa-4bbb-8ccc-ddddeeeeffff"
    pi_dir = os.path.join(home, ".pi", "agent", "sessions", "--home-user-project--")
    pi_path = os.path.join(pi_dir, "2026-09-15T08-00-00-000Z_" + uuid_a + ".jsonl")
    pi_run_path = os.path.join(pi_dir, "2026-09-15T09-00-00-000Z_" + uuid_b + ".jsonl")
    for level in edition.LEVELS:
        for harness in edition.HARNESS_IDS:
            if harness not in level["harness"]:
                continue
            modes = ("resume", "new") if harness in NO_FORK else ("resume", "fork", "new")
            models = ("gpt-5.5",) if harness == "pi" else (None, "model-1")
            for mode in modes:
                for model in models:
                    for retry in (False, True):
                        for allow_paid in (False, True):
                            sid = "ses_AbCdEfGh1234" if harness == "opencode" else uuid_a
                            new_sid = uuid_b if mode == "new" and harness in ("claude", "gemini", "pi") else None
                            target = {
                                "mode": mode,
                                "sessionId": None if mode == "new" else sid,
                                "newSessionId": new_sid,
                                "cwd": "/home/user/project",
                                "title": "project",
                                "allowNonGit": harness == "codex",
                                "sessionPath": pi_path if harness == "pi" and mode != "new" else None,
                            }
                            state = {"runSessionId": (uuid_b if harness == "pi" else sid) if retry else None,
                                     "runSessionPath": pi_run_path if harness == "pi" and retry else None}
                            job = {
                                "id": "0123456789abcdef", "harness": harness, "level": level["id"], "target": target,
                                "label": SYNTHETIC_PROMPT, "prompt": SYNTHETIC_PROMPT,
                                "limits": {"maxTurns": level["defaultMaxTurns"], "budgetUsd": 5.0, "runtimeSec": 5400},
                                "model": model, "allowPaid": allow_paid,
                                "provider": "openai-codex" if harness == "pi" else None,
                                "cli": {"link": "/usr/bin/" + harness, "real": "/usr/bin/" + harness},
                                "trigger": {"kind": "now", "fireAt": None, "delaySec": None, "marginSec": 120,
                                            "weeklyPolicy": "defer", "graceSec": 900},
                                "state": state,
                            }
                            yield "%s/%s/%s%s%s%s" % (level["id"], harness, mode, "+model" if model else "",
                                                      "+retry" if retry else "", "+paid" if allow_paid else ""), job


def _exec_prefix(harness):
    if harness == "gemini":
        return ["/usr/bin/node", "/usr/lib/node_modules/@google/gemini-cli/bundle/gemini.js"]
    if harness == "cursor":
        return ["/usr/bin/cursor-agent"]
    return ["/usr/bin/" + harness]


def generated_commands():
    """(label, harness, exec length, argv, env) for every level x harness x session mode the runner can build."""
    from autopilot import harness as harness_mod
    saved = dict(os.environ)
    home = tempfile.mkdtemp(prefix="ap4a-policy-")
    try:
        os.environ.clear()
        os.environ.update({"HOME": home, "USER": "user", "LANG": "C.UTF-8", "PATH": "/usr/bin"})
        out = []
        for label, job in _synthetic_jobs(home):
            prefix = _exec_prefix(job["harness"])
            cmd = harness_mod.build_command(job, exec_prefix=prefix, run_dir=home + "/runs", gen=1)
            out.append((label, job["harness"], len(prefix), list(cmd["argv"]), dict(cmd["env"])))
        return out
    finally:
        os.environ.clear()
        os.environ.update(saved)
        shutil.rmtree(home, ignore_errors=True)


# Argv words no command may carry, and the per-agent lists of CONTRACT-V2-DELTA section 5. Spellings
# on the denylist are split so that this file stays clean outside the pattern block.
FORBIDDEN_ANY = ("-f", "-a")
FORBIDDEN_FOR = {
    "cursor": ("--fo" + "rce", "--yo" + "lo", "--tr" + "ust", "--auto-" + "review", "--approve-" + "mcps",
               "--api-key", "--auth-token", "--continue"),
    "pi": ("-p", "--appr" + "ove", "-e", "--extension", "--skill", "--prompt-template", "--api-key",
           "--system-prompt", "--append-system-prompt", "--continue", "--resume", "--thinking"),
}
# Flags that take a value, so every other bare word in a Cursor or Pi argv would be a positional prompt.
VALUE_FLAGS = {
    "cursor": ("--output-format", "--mode", "--sandbox", "--workspace", "--model", "--resume"),
    "pi": ("--mode", "--tools", "--provider", "--model", "--session-id", "--name", "--session", "--fork"),
}


def argv_shape_problems(label, harness, exec_len, argv, env):
    """Structural rules the denylist cannot express: forbidden words, tool lists, positional prompts.

    Commands of UNLOCKED_LEVELS may pass Cursor's force and trust flags and give Pi its editing tools."""
    from autopilot import consts
    problems = []
    words = argv[exec_len:]
    unlocked = label.split("/")[0] in UNLOCKED_LEVELS
    forbidden = FORBIDDEN_FOR.get(harness, ())
    if unlocked:
        forbidden = tuple(w for w in forbidden if w not in UNLOCKED_CURSOR_FLAGS)
    tools = consts.PI_TOOLS + (UNLOCKED_PI_TOOLS if unlocked else ())
    for word in words:
        if word in FORBIDDEN_ANY or word in forbidden:
            problems.append("build_command %s: forbidden argument %s" % (label, word))
    for i, word in enumerate(words):
        if word == "--tools":
            members = words[i + 1].split(",") if i + 1 < len(words) else []
            outside = [m for m in members if m not in tools]
            if not members or outside:
                problems.append("build_command %s: --tools outside %s: %s" % (label, ",".join(tools), outside))
    if SYNTHETIC_PROMPT in " ".join(argv) or any(SYNTHETIC_PROMPT in str(v) for v in env.values()):
        problems.append("build_command %s: the job's prompt or label reached argv or env" % label)
    if harness in VALUE_FLAGS:
        i = 0
        while i < len(words):
            word = words[i]
            if word in VALUE_FLAGS[harness]:
                value = words[i + 1] if i + 1 < len(words) else None
                if value is None or value.startswith("-"):
                    problems.append("build_command %s: %s without a value" % (label, word))
                elif harness == "cursor" and word == "--model" and value == "auto":
                    problems.append("build_command %s: --model auto" % label)
                elif harness == "pi" and word == "--mode" and value != "json":
                    problems.append("build_command %s: --mode %s" % (label, value))
                i += 2
                continue
            if not word.startswith("-"):
                problems.append("build_command %s: positional word %r" % (label, word))
            if word.startswith("@"):
                problems.append("build_command %s: @file argument" % label)
            i += 1
    return problems


def check_denylist_generated_argv():
    """Outside UNLOCKED_LEVELS, the level table, every argv the runner can build and the `edition`
    answer carry no denylisted value."""
    problems = []
    for level in edition.LEVELS:
        unlocked = level["id"] in UNLOCKED_LEVELS
        for harness, entry in level["harness"].items():
            values = list(entry["argv"]) + ["%s=%s" % kv for kv in entry["env"].items()]
            for value in values + [" ".join(values)] if not unlocked else []:
                for label in denylist_hits(value):
                    problems.append("edition.LEVELS %s/%s: %s" % (level["id"], harness, label))
            permission = entry["env"].get("OPENCODE_PERMISSION")
            if permission is not None:
                rules = json.loads(permission)
                allowed = ("deny", "ask") + (("al" + "low",) if unlocked else ())
                if any(v not in allowed for v in rules.values()):
                    problems.append("edition.LEVELS %s/%s: OPENCODE_PERMISSION holds a value other than %s"
                                    % (level["id"], harness, "/".join(allowed)))
    try:
        commands = generated_commands()
    except Exception as exc:  # the check must fail loudly, not pass on a broken builder
        return problems + ["harness.build_command raised %s: %s" % (type(exc).__name__, exc)]
    offered = sorted({(harness, label.split("/")[0]) for label, harness, _n, _a, _e in commands})
    expected = sorted((h, lv["id"]) for lv in edition.LEVELS for h in lv["harness"])
    if offered != expected:
        problems.append("build_command covered %s, the level table offers %s" % (offered, expected))
    for label, harness, exec_len, argv, env in commands:
        values = argv + ["%s=%s" % kv for kv in env.items()]
        for value in values + [" ".join(argv)] if label.split("/")[0] not in UNLOCKED_LEVELS else []:
            for hit in denylist_hits(value):
                problems.append("build_command %s: %s" % (label, hit))
        problems += argv_shape_problems(label, harness, exec_len, argv, env)
    home = tempfile.mkdtemp(prefix="ap4a-policy-")
    try:
        res = subprocess.run(["/usr/bin/python3", "-I", "-S", "-B", os.path.join(BIN, "ap4a"), "edition"],
                             env={"HOME": home, "LANG": "C.UTF-8", "PATH": "/usr/bin"}, stdin=subprocess.DEVNULL,
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30, check=False)
        text = res.stdout.decode("utf-8", errors="replace")
        if res.returncode != 0 or not text.startswith('{"ok":true'):
            problems.append("ap4a edition did not answer ok (exit %d)" % res.returncode)
        else:
            answer = json.loads(text)
            answer["levels"] = [lv for lv in answer.get("levels", []) if lv.get("id") not in UNLOCKED_LEVELS]
            for hit in denylist_hits(json.dumps(answer)):
                problems.append("ap4a edition output: %s" % hit)
    finally:
        shutil.rmtree(home, ignore_errors=True)
    return problems


def check_forbidden_filenames():
    """No path segment containing install/setup/uninstall; no systemd unit files."""
    problems = []
    for rel, _st in repo_entries():
        for segment in rel.split(os.sep):
            if FORBIDDEN_NAME.search(segment):
                problems.append("%s: name contains install/setup/uninstall" % rel)
                break
        if os.path.splitext(rel)[1].lower() in UNIT_EXT:
            problems.append("%s: systemd unit file" % rel)
    return problems


def check_no_agent_files():
    """No agent-control, instruction, handoff or agent-addressed files anywhere in the tree."""
    problems = []
    entries = list(repo_entries())
    ignored = git_ignored([rel for rel, _st in entries])
    for rel, st in entries:
        if rel in ignored:
            continue
        name = os.path.basename(rel)
        if stat.S_ISDIR(st.st_mode) and name.lower() in AGENT_DIR_NAMES:
            problems.append("%s/: agent-control folder" % rel)
        elif name.lower() in AGENT_FILE_NAMES:
            problems.append("%s: agent-control or instruction file" % rel)
        elif HANDOFF_NAME.search(name):
            problems.append("%s: handoff or transcript file" % rel)
    for rel in repo_files():
        if rel in ignored or not rel.lower().endswith((".md", ".txt")):
            continue
        text = read_text(rel) or ""
        for match in AGENT_PROSE.finditer(text):
            problems.append("%s:%d: prose addressed to an agent" % (rel, line_of(text, match.start())))
    return problems


def check_no_symlinks():
    return ["%s: symlink" % rel for rel, st in repo_entries() if stat.S_ISLNK(st.st_mode)]


def check_no_pycache():
    return ["%s: compiled Python cache" % rel for rel, _st in repo_entries()
            if os.path.basename(rel) == "__pycache__" or rel.endswith((".pyc", ".pyo"))]


def check_plaintext_every_text():
    """Every Text, Label, TextEdit, TextArea and TextField block sets textFormat."""
    problems = []
    for rel in qml_files(include_lint=True):
        code = strip_code(read_text(rel))
        for name, number, body in object_blocks(code, ("Text", "Label", "TextEdit", "TextArea", "TextField")):
            if not re.search(r"(?:^|[\s;])textFormat\s*:", body):
                problems.append("%s:%d: %s block without textFormat" % (rel, number, name))
    return problems


def check_no_exec_detached_or_bar_run():
    problems = []
    pattern = re.compile(r"\bexecDetached\b|\bexecArgv\b|\bstartDetached\b|\bbar\s*\.\s*run\s*\("
                         r"|\bQt\s*\.\s*openUrlExternally\b")
    for rel in script_files(include_lint=True):
        code = strip_code(read_text(rel))
        for match in pattern.finditer(code):
            problems.append("%s:%d: %s" % (rel, line_of(code, match.start()), match.group(0)))
    return problems


def check_process_only_in_bounded_process():
    """`Process {` exists only in BoundedProcess.qml, and that wrapper keeps its guarantees."""
    problems = []
    for rel in script_files(include_lint=False):
        code = strip_code(read_text(rel))
        if rel != "BoundedProcess.qml":
            for match in re.finditer(r"(?<![\w.])(?:\w+\.)?Process\s*\{", code):
                problems.append("%s:%d: Process outside BoundedProcess.qml" % (rel, line_of(code, match.start())))
        for match in re.finditer(r"\bQt\s*\.\s*createQmlObject\b", code):
            problems.append("%s:%d: Qt.createQmlObject (inline QML string)" % (rel, line_of(code, match.start())))
    path = os.path.join(ROOT, "BoundedProcess.qml")
    if not os.path.isfile(path):
        return problems + ["BoundedProcess.qml: missing"]
    code = strip_code(read_text("BoundedProcess.qml"), strings=False)
    for needle, reason in (("clearEnvironment", "never clears the environment"),
                           ("Component.onDestruction", "has no onDestruction kill"),
                           ("signal(9)", "never sends SIGKILL"),
                           ("splitMarker", "does not read raw chunks for its byte cap")):
        if needle not in code:
            problems.append("BoundedProcess.qml: %s" % reason)
    svc = strip_code(read_text("Service.qml") or "", strings=False)
    if "Component.onDestruction" not in svc or ".kill()" not in svc:
        problems.append("Service.qml: has no onDestruction kill of helper processes")
    return problems


def check_no_capitalised_properties():
    problems = []
    pattern = re.compile(r"(?m)^[ \t]*(?:(?:readonly|required|default|final|virtual|override)\s+)*property\s+"
                         r"[\w.]+(?:<[\w.]+>)?\s+([A-Za-z_]\w*)")
    for rel in qml_files(include_lint=True):
        code = strip_code(read_text(rel))
        for match in pattern.finditer(code):
            if match.group(1)[0].isupper():
                problems.append("%s:%d: property %s starts with a capital" % (rel, line_of(code, match.start()),
                                                                               match.group(1)))
    return problems


def _interval_ok(expr):
    expr = expr.strip()
    if re.fullmatch(r"\d+", expr):
        return int(expr) <= INT32_MAX
    return expr.startswith("Model.clampInterval(")


def check_timer_intervals_clamped():
    """Every Timer interval is an int32 literal or goes through Model.clampInterval."""
    problems = []
    for rel in qml_files(include_lint=True):
        code = strip_code(read_text(rel))
        for _name, number, body in object_blocks(code, ("Timer",)):
            for match in re.finditer(r"(?:^|[\s;])interval\s*:\s*([^\n;]+)", body):
                if not _interval_ok(match.group(1)):
                    problems.append("%s:%d: Timer interval %s" % (rel, number, match.group(1).strip()))
        for match in re.finditer(r"\.interval\s*=(?!=)\s*([^\n;]+)", code):
            if not _interval_ok(match.group(1)):
                problems.append("%s:%d: interval assigned %s" % (rel, line_of(code, match.start()),
                                                                 match.group(1).strip()))
    return problems


def _js_value(text, name):
    match = re.search(r"(?m)^var\s+%s\s*=\s*(.+?)\s*;?\s*$" % re.escape(name), text)
    if not match:
        return KeyError
    try:
        return json.loads(match.group(1))
    except ValueError:
        return ValueError


def check_edition_consistency():
    """manifest.json, edition.py and lib/Edition.js agree, and identity literals live only there."""
    problems = []
    try:
        with open(os.path.join(ROOT, "manifest.json"), encoding="utf-8") as handle:
            manifest = json.load(handle)
    except (OSError, ValueError) as exc:
        return ["manifest.json: unreadable (%s)" % exc]
    if manifest.get("id") != edition.PLUGIN_ID:
        problems.append("manifest.json: id %r != edition.PLUGIN_ID" % manifest.get("id"))
    if (manifest.get("barWidget") or {}).get("displayName") != edition.DISPLAY_NAME:
        problems.append("manifest.json: barWidget.displayName != edition.DISPLAY_NAME")

    js = read_text("lib/Edition.js") if os.path.isfile(os.path.join(ROOT, "lib", "Edition.js")) else ""
    if not js:
        problems.append("lib/Edition.js: missing")
    expected = {
        "PLUGIN_ID": edition.PLUGIN_ID, "DISPLAY_NAME": edition.DISPLAY_NAME, "UNIT_PREFIX": edition.UNIT_PREFIX,
        "WIDGET_IPC_TARGET": edition.WIDGET_IPC_TARGET, "SERVICE_IPC_TARGET": edition.SERVICE_IPC_TARGET,
        "NOTIFY_APP_NAME": edition.NOTIFY_APP_NAME, "HARNESS_IDS": list(edition.HARNESS_IDS),
        "LEVEL_IDS": list(edition.LEVEL_IDS), "DEFAULT_LEVEL": edition.DEFAULT_LEVEL,
        "LEVEL_LABELS": {level["id"]: level["label"] for level in edition.LEVELS},
        "HELPER_REL": "bin/ap4a",
    }
    for name, want in expected.items():
        got = _js_value(js, name) if js else KeyError
        if got is KeyError:
            problems.append("lib/Edition.js: var %s missing" % name)
        elif got is ValueError:
            problems.append("lib/Edition.js: var %s is not a plain literal" % name)
        elif got != want:
            problems.append("lib/Edition.js: %s = %r, edition.py has %r" % (name, got, want))
    helper = os.path.join(ROOT, "bin", "ap4a")
    if not (os.path.isfile(helper) and os.access(helper, os.X_OK)):
        problems.append("bin/ap4a: missing or not executable")

    if tuple(edition.LEVEL_IDS) != ("plan", "unattended", "auto", "full"):
        problems.append("edition.LEVEL_IDS is %r, the closed enum is plan|unattended|auto|full" % (edition.LEVEL_IDS,))
    if edition.DEFAULT_LEVEL != "plan" or [lv["id"] for lv in edition.LEVELS if lv.get("default")] != ["plan"]:
        problems.append("edition.LEVELS: plan must be the only default level")
    for level in edition.LEVELS:
        # CONTRACT-V2-DELTA 2.1: every agent is either offered at a level or has a fixed reason why not.
        offered = set(level["harness"])
        unavailable = level.get("unavailable")
        if not isinstance(unavailable, dict):
            problems.append("edition.LEVELS %s: no unavailable table" % level["id"])
            unavailable = {}
        if offered & set(unavailable):
            problems.append("edition.LEVELS %s: offered and unavailable overlap: %s"
                            % (level["id"], sorted(offered & set(unavailable))))
        if offered | set(unavailable) != set(edition.HARNESS_IDS):
            problems.append("edition.LEVELS %s: harness keys plus unavailable keys differ from HARNESS_IDS" % level["id"])
        for harness, reason in unavailable.items():
            if not (isinstance(reason, str) and reason.strip() and "—" not in reason):
                problems.append("edition.LEVELS %s: unavailable[%s] is not a plain sentence" % (level["id"], harness))
        if level["id"] == edition.DEFAULT_LEVEL and unavailable:
            problems.append("edition.LEVELS %s: the default level must be offered for every agent" % level["id"])
        for harness, entry in level["harness"].items():
            if sorted(entry) != ["argv", "caption", "env", "initPermissionMode"]:
                problems.append("edition.LEVELS %s/%s: keys %s" % (level["id"], harness, sorted(entry)))
            if not all(isinstance(a, str) for a in entry.get("argv", [None])):
                problems.append("edition.LEVELS %s/%s: argv is not a list of strings" % (level["id"], harness))

    allowed = {"manifest.json", "bin/autopilot/edition.py", "lib/Edition.js", "README.md"}
    literals = [
        (re.compile(re.escape(edition.PLUGIN_ID)), "plugin id or IPC target"),
        (re.compile(re.escape(edition.STATE_DIR_NAME)), "state folder name"),
        (re.compile(re.escape(edition.CONFIG_DIR_NAME)), "config folder name"),
        (re.compile(r"[\"']" + re.escape(edition.UNIT_PREFIX) + r"-"), "unit prefix"),
        # Anywhere inside a one-line string literal ("Auto Pilot is ..."), not only as the whole string.
        # Docstrings (triple quotes) are prose about the plugin, not an identity value.
        (re.compile(r"(?<!\"\")\"(?!\"\")[^\"\n]*" + re.escape(edition.DISPLAY_NAME)), "display name"),
        (re.compile(r"[\"']" + re.escape(edition.NOTIFY_APP_NAME) + r"[\"']"), "notification app name"),
    ]
    for rel in repo_files():
        if rel in allowed or rel.startswith("tests/"):
            continue
        text = read_text(rel)
        if text is None:
            continue
        seen = set()
        for pattern, label in literals:
            for match in pattern.finditer(text):
                key = (line_of(text, match.start()), label)
                if key not in seen:
                    seen.add(key)
                    problems.append("%s:%d: %s literal outside the edition files" % (rel, key[0], label))
    return problems


def check_absolute_argv0_in_qml():
    """Every QML/JS command starts with an absolute path; python runs as /usr/bin/python3 -I -S -B."""
    problems = []
    for rel in script_files(include_lint=False):
        code = strip_code(read_text(rel), strings=False)
        for match in re.finditer(r"\bcommand\s*[:=]\s*\[\s*", code):
            first = re.match(r"([\"'])(.*?)\1", code[match.end():])
            if first and not first.group(2).startswith("/"):
                problems.append("%s:%d: command starts with %r" % (rel, line_of(code, match.start()), first.group(2)))
        for match in re.finditer(r"\[\s*([\"'])%s\1" % PROCESS_LAUNCHERS, code):
            problems.append("%s:%d: argv starts with a PATH-resolved program" % (rel, line_of(code, match.start())))
        for match in re.finditer(r"([\"'])(/usr/bin/python3[\d.]*)\1", code):
            tail = code[match.end():match.end() + 60]
            if not re.match(r"\s*,\s*([\"'])-I\1\s*,\s*([\"'])-S\2\s*,\s*([\"'])-B\3", tail):
                problems.append("%s:%d: python3 without -I -S -B" % (rel, line_of(code, match.start())))
        for match in re.finditer(r"([\"'])(/usr/bin/env|/bin/sh|/bin/bash|/usr/bin/bash)\1", code):
            problems.append("%s:%d: argv runs %s" % (rel, line_of(code, match.start()), match.group(2)))
    return problems


def check_no_prompt_in_qml_command_arrays():
    """Helper argv in QML never carries prompt text or request payloads; IPC methods take no arguments."""
    problems = []
    carriers = re.compile(r"prompt|stdinText|JSON\s*\.\s*stringify|draft|payload", re.I)
    for rel in script_files(include_lint=False):
        text = read_text(rel)
        code = strip_code(text, strings=False)
        for match in re.finditer(r"[\"']/usr/bin/python3[\"']", code):
            start = code.rfind("[", 0, match.start())
            if start < 0:
                continue
            end = matching_bracket(code, start)
            stop = code.find("\n", end)
            segment = code[start:len(code) if stop < 0 else stop]
            chained = re.match(r"\]\s*\.\s*concat\s*\(", code[end:])
            if chained:
                segment = code[start:matching_bracket(code, end + chained.end() - 1) + 1]
            if carriers.search(segment):
                problems.append("%s:%d: helper argv mentions prompt or payload" % (rel, line_of(code, match.start())))
        for match in re.finditer(r"\bcommand\s*[:=]([^\n]*)", code):
            if carriers.search(match.group(1)):
                problems.append("%s:%d: command binding mentions prompt or payload" % (rel, line_of(code, match.start())))
        blank = strip_code(text)
        for _name, number, body in object_blocks(blank, ("IpcHandler",)):
            for fn in re.finditer(r"function\s+(\w+)\s*\(([^)]*)\)", body):
                if fn.group(2).strip():
                    problems.append("%s:%d: IPC method %s takes arguments" % (rel, number, fn.group(1)))
    return problems


ARGV_NAMES = {"argv", "cmd", "command", "args", "exec_prefix", "prefix", "base"}
ENV_NAMES = {"env", "environ", "environment", "child_env"}


def _name_of(node):
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    if isinstance(node, ast.Subscript):
        return _name_of(node.value)
    return ""


def _mentions_prompt(node):
    for sub in ast.walk(node):
        if isinstance(sub, ast.Name) and "prompt" in sub.id.lower():
            return True
        if isinstance(sub, ast.Attribute) and "prompt" in sub.attr.lower() and not sub.attr.isupper():
            return True
    return False


def check_no_prompt_in_python_argv():
    """No prompt value flows into an argv list or a child environment anywhere in the helper."""
    problems = []
    for rel in python_files():
        tree = ast.parse(read_text(rel), filename=rel)
        for node in ast.walk(tree):
            where = "%s:%d" % (rel, getattr(node, "lineno", 0))
            if isinstance(node, (ast.List, ast.Tuple)) and _mentions_prompt(node):
                flags = [e for e in node.elts if isinstance(e, ast.Constant) and isinstance(e.value, str)
                         and e.value.startswith(("-", "/"))]
                if flags:
                    problems.append("%s: prompt in a list with command-line flags" % where)
            elif isinstance(node, (ast.Assign, ast.AugAssign, ast.AnnAssign)):
                targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                value = node.value
                for target in targets:
                    name = _name_of(target)
                    if value is not None and _mentions_prompt(value):
                        if name in ARGV_NAMES:
                            problems.append("%s: prompt assigned into %s" % (where, name))
                        elif isinstance(target, ast.Subscript) and name in ENV_NAMES:
                            problems.append("%s: prompt written into %s" % (where, name))
                        elif name in ENV_NAMES:
                            problems.append("%s: prompt in environment %s" % (where, name))
            elif isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                owner = _name_of(node.func.value)
                method = node.func.attr
                args = list(node.args) + [k.value for k in node.keywords]
                if method in ("append", "extend", "insert") and owner in ARGV_NAMES and any(map(_mentions_prompt, args)):
                    problems.append("%s: prompt appended to %s" % (where, owner))
                if method in ("update", "setdefault") and owner in ENV_NAMES and any(map(_mentions_prompt, args)):
                    problems.append("%s: prompt merged into %s" % (where, owner))
                if method in ("Popen", "run_bounded", "run", "call", "check_output"):
                    first = node.args[:1] + [k.value for k in node.keywords if k.arg in ("args", "argv", "env")]
                    if any(map(_mentions_prompt, first)):
                        problems.append("%s: prompt passed as argv or env to %s" % (where, method))
    return problems


def check_python_process_boundaries():
    """Stdlib only, absolute interpreters, no shell, no inherited environment, children only via the wrappers."""
    problems = []
    stdlib = set(getattr(sys, "stdlib_module_names", ())) | {"autopilot", "__future__"}
    for rel in python_files():
        text = read_text(rel)
        first = text.split("\n", 1)[0]
        if first.startswith("#!") and not first.startswith("#!/usr/bin/python3"):
            problems.append("%s:1: interpreter %s" % (rel, first))
        tree = ast.parse(text, filename=rel)
        base = os.path.basename(rel)
        for node in ast.walk(tree):
            where = "%s:%d" % (rel, getattr(node, "lineno", 0))
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if stdlib and alias.name.split(".")[0] not in stdlib:
                        problems.append("%s: imports non-stdlib %s" % (where, alias.name))
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                if stdlib and node.module.split(".")[0] not in stdlib:
                    problems.append("%s: imports non-stdlib %s" % (where, node.module))
            elif isinstance(node, ast.Call):
                for keyword in node.keywords:
                    if keyword.arg == "shell" and not (isinstance(keyword.value, ast.Constant) and keyword.value.value is False):
                        problems.append("%s: shell= on a child process" % where)
                    if keyword.arg == "env":
                        for sub in ast.walk(keyword.value):
                            if isinstance(sub, ast.Attribute) and sub.attr == "environ":
                                problems.append("%s: child inherits os.environ" % where)
                                break
                func = node.func
                if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name):
                    owner, attr = func.value.id, func.attr
                    if owner == "os" and (attr in ("system", "popen") or attr.startswith(("exec", "spawn"))):
                        problems.append("%s: os.%s" % (where, attr))
                    if owner == "subprocess" and attr in ("call", "check_call", "check_output", "run", "getoutput",
                                                          "getstatusoutput"):
                        problems.append("%s: subprocess.%s outside the bounded wrappers" % (where, attr))
                    if owner == "subprocess" and attr == "Popen" and base not in ("bounded.py", "supervise.py"):
                        problems.append("%s: subprocess.Popen outside bounded.py/supervise.py" % where)
            elif isinstance(node, ast.Constant) and isinstance(node.value, str):
                if re.search(r"(?:^|[\s\"'=])/(?:var/)?tmp/\S", node.value):
                    problems.append("%s: path inside a shared temporary folder" % where)
    helper = read_text("bin/ap4a") if os.path.isfile(os.path.join(ROOT, "bin", "ap4a")) else ""
    shebang = helper.split("\n", 1)[0]
    flags = "".join(part[1:] for part in shebang.split()[1:] if part.startswith("-") and not part.startswith("--"))
    if not (shebang.startswith("#!/usr/bin/python3 ") and "I" in flags and "S" in flags):
        problems.append("bin/ap4a:1: launcher must run /usr/bin/python3 with -I and -S")
    if "sys.dont_write_bytecode = True" not in helper:
        problems.append("bin/ap4a: does not set sys.dont_write_bytecode before importing the package")
    from autopilot import consts
    for key, value in consts.TOOLS.items():
        if not (isinstance(value, str) and value.startswith("/")):
            problems.append("consts.TOOLS[%r] is not absolute" % key)
    for rel in repo_files():
        if rel.startswith("tests/"):
            continue
        text = read_text(rel)
        if text and text.startswith("#!") and re.match(r"#!\s*/usr/bin/env\b", text):
            problems.append("%s:1: #!/usr/bin/env interpreter" % rel)
    return problems


def check_no_fileview():
    """No FileView anywhere in the plugin QML: every read goes through the bounded helper."""
    problems = []
    for rel in script_files(include_lint=False):
        code = strip_code(read_text(rel))
        for match in re.finditer(r"\bFileView\b", code):
            problems.append("%s:%d: FileView" % (rel, line_of(code, match.start())))
    return problems


CREDENTIAL_NAMES = (
    (re.compile(r"\.credentials\.json"), "Claude credentials file"),
    (re.compile(r"codex/auth\.json"), "Codex auth file"),
    (re.compile(r"\.pi/agent/auth\.json"), "Pi auth file"),
    (re.compile(r"\.pi/agent/models\.json"), "Pi custom models file (may hold keys)"),
    (re.compile(r"opencode/auth\.json"), "OpenCode auth file"),
    (re.compile(r"cursor/auth\.json"), "Cursor auth file"),
    (re.compile(r"state\.vscdb"), "Cursor app state database"),
    (re.compile(r"(?<![\w.])[\"']auth\.json[\"']"), "a bare auth.json name"),
    (re.compile(r"store\.db(?!-wal)"), "Cursor chat store (only store.db-wal may be named, for a stat)"),
)


def runtime_files():
    """What the shell and the helper load: bin/, lib/, components/ and the root QML."""
    out = []
    for rel in repo_files():
        if rel.startswith(("bin/", "lib/", "components/")) or (os.sep not in rel and rel.endswith(".qml")):
            out.append(rel)
    return sorted(out)


def check_credential_paths_never_named():
    """Runtime code never names an agent credential store, so it cannot open one by accident."""
    problems = []
    for rel in runtime_files():
        text = read_text(rel)
        if text is None:
            continue
        for number, line in enumerate(text.split("\n"), 1):
            for pattern, label in CREDENTIAL_NAMES:
                if pattern.search(line):
                    problems.append("%s:%d: names %s" % (rel, number, label))
    return problems


CHECKS = (
    ("policy/denylist", check_denylist),
    ("policy/denylist-generated-argv", check_denylist_generated_argv),
    ("policy/forbidden-filenames", check_forbidden_filenames),
    ("policy/no-agent-files", check_no_agent_files),
    ("policy/no-symlinks", check_no_symlinks),
    ("policy/no-pycache", check_no_pycache),
    ("policy/plaintext-every-text", check_plaintext_every_text),
    ("policy/no-execDetached-or-bar.run", check_no_exec_detached_or_bar_run),
    ("policy/process-only-in-BoundedProcess", check_process_only_in_bounded_process),
    ("policy/no-capitalised-properties", check_no_capitalised_properties),
    ("policy/timer-intervals-clamped", check_timer_intervals_clamped),
    ("policy/edition-consistency", check_edition_consistency),
    ("policy/absolute-argv0-in-qml", check_absolute_argv0_in_qml),
    ("policy/no-prompt-in-qml-command-arrays", check_no_prompt_in_qml_command_arrays),
    ("policy/no-prompt-in-python-argv", check_no_prompt_in_python_argv),
    ("policy/python-process-boundaries", check_python_process_boundaries),
    ("policy/no-fileview", check_no_fileview),
    ("policy/credential-paths-never-named", check_credential_paths_never_named),
)


class PolicyTests(unittest.TestCase):
    maxDiff = None

    def check(self, fn):
        problems = fn()
        self.assertEqual(problems, [], "\n" + "\n".join(problems[:60]))

    def test_denylist(self):
        self.check(check_denylist)

    def test_denylist_generated_argv(self):
        self.check(check_denylist_generated_argv)

    def test_forbidden_filenames(self):
        self.check(check_forbidden_filenames)

    def test_no_agent_files(self):
        self.check(check_no_agent_files)

    def test_no_symlinks(self):
        self.check(check_no_symlinks)

    def test_no_pycache(self):
        self.check(check_no_pycache)

    def test_plaintext_every_text(self):
        self.check(check_plaintext_every_text)

    def test_no_exec_detached_or_bar_run(self):
        self.check(check_no_exec_detached_or_bar_run)

    def test_process_only_in_bounded_process(self):
        self.check(check_process_only_in_bounded_process)

    def test_no_capitalised_properties(self):
        self.check(check_no_capitalised_properties)

    def test_timer_intervals_clamped(self):
        self.check(check_timer_intervals_clamped)

    def test_edition_consistency(self):
        self.check(check_edition_consistency)

    def test_absolute_argv0_in_qml(self):
        self.check(check_absolute_argv0_in_qml)

    def test_no_prompt_in_qml_command_arrays(self):
        self.check(check_no_prompt_in_qml_command_arrays)

    def test_no_prompt_in_python_argv(self):
        self.check(check_no_prompt_in_python_argv)

    def test_python_process_boundaries(self):
        self.check(check_python_process_boundaries)

    def test_no_fileview(self):
        self.check(check_no_fileview)

    def test_credential_paths_never_named(self):
        self.check(check_credential_paths_never_named)


class PolicyCheckerSelfTests(unittest.TestCase):
    """The checkers themselves: each one must catch a planted violation, or a green run means nothing."""

    def test_denylist_patterns_catch_known_spellings(self):
        # One planted sample per DENYLIST entry, in the same order. Each is split so that no spelling
        # appears in this file outside the pattern block.
        planted = (
            "--permission-mode " + "bypass" + "Permissions",
            '"accept' + 'Edits"',
            "--danger" + "ously-skip-permissions",
            '"--permission-mode", "au' + 'to"',
            "run --au" + "to",
            "--yo" + "lo",
            '["gemini", "-' + 'y"]',
            "--approval-mode au" + "to_edit",
            "--approve-" + "for-me",
            "-s danger-" + "full-access",
            "--skip-" + "trust",
            "GEMINI_CLI_TRUST_" + "WORKSPACE=1",
            "OPENCODE_CONFIG_" + "CONTENT",
            '{"edit":"al' + 'low"}',
            "-c approval_" + "policy=never",
            "sandbox_workspace_write.network_" + "access=true",
            "cursor-agent -p --fo" + "rce",
            "cursor-agent --tr" + "ust --workspace /w",
            "--approve-" + "mcps",
            "--auto-" + "review",
            '{"approvalMode": "unre' + 'stricted"}',
            "AGENT_CLI_" + "BYPASS_AUTH=1",
            "cursor-agent print-" + "bearer-token",
            "pi auth show --cred" + "entials",
            '["pi", "--appr' + 'ove"]',
            "opencode providers " + "list",
        )
        self.assertEqual(len(planted), len(DENYLIST))
        for (label, _pattern), sample in zip(DENYLIST, planted):
            self.assertIn(label, denylist_hits(sample), sample)
        # More value positions of the narrowed allow pattern; each must still be caught.
        for sample in ('["al' + 'low", "deny"]', "mode = 'al" + "low'", '"read": "al' + 'low"', "print-" + "api-key",
                       'AGENT_CLI_' + 'E2E', '"approvalMode":"unre' + 'stricted"'):
            self.assertNotEqual(denylist_hits(sample), [], sample)
        for clean in ("--permission-mode plan --permission-prompts none", "-s workspace-write", "--approval-mode default",
                      '{"edit":"deny","bash":"ask"}', "automatic-approval", "--by 300", "rsync -a", "y = -y0",
                      'text: "Allow"', 'permissions.get("allow")', "rules.get('allow', [])", "--no-approve",
                      "--force-color", "--trusted-root", '"approvalMode": "allowlist"', "auto-review is allowed",
                      "Cursor applies file edits headless only with --fo" + "rce, which Auto Pilot never passes."):
            self.assertEqual(denylist_hits(clean), [], clean)

    def test_argv_shape_checker_catches_planted_argv(self):
        good = ["/usr/bin/pi", "--mode", "json", "--offline", "--tools", "read,grep,find,ls", "--provider", "openai-codex",
                "--model", "gpt-5.5", "--session-id", "9b1d7e42-aaaa-4bbb-8ccc-ddddeeeeffff", "--name", "autopilot-01234567"]
        self.assertEqual(argv_shape_problems("pi", "pi", 1, good, {"HOME": "/h"}), [])
        bad = {
            "pi-bash": good[:4] + ["--tools", "read,bash"] + good[6:],
            "pi-prompt": good + ["Summarise"],
            "pi-rpc": [good[0], "--mode", "rpc"] + good[3:],
            "pi-resume": good + ["--resume"],
            "pi-file": good + ["@notes.md"],
            "cursor-auto": ["/usr/bin/cursor-agent", "-p", "--output-format", "stream-json", "--model", "auto"],
            "cursor-bare-resume": ["/usr/bin/cursor-agent", "-p", "--output-format", "stream-json", "--resume"],
            "cursor-f": ["/usr/bin/cursor-agent", "-p", "-f", "--output-format", "stream-json"],
            "cursor-prompt": ["/usr/bin/cursor-agent", "-p", "--output-format", "stream-json", SYNTHETIC_PROMPT],
        }
        for name, argv in bad.items():
            harness = name.split("-")[0]
            self.assertNotEqual(argv_shape_problems(name, harness, 1, argv, {}), [], name)
        self.assertNotEqual(argv_shape_problems("env", "claude", 1, ["/usr/bin/claude", "-p"], {"X": SYNTHETIC_PROMPT}), [])

    def test_credential_name_patterns_catch_planted_paths(self):
        planted = ('HOME + "/.claude/.credentials' + '.json"', '"~/.codex/auth' + '.json"', '".pi/agent/auth' + '.json"',
                   '".pi/agent/models' + '.json"', '"opencode/auth' + '.json"', '"cursor/auth' + '.json"',
                   '"state.vs' + 'cdb"', 'os.path.join(d, "auth' + '.json")', '"chats/x/store' + '.db"')
        for sample in planted:
            self.assertTrue(any(p.search(sample) for p, _l in CREDENTIAL_NAMES), sample)
        for clean in ('"store.db-wal"', '"~/.cache/opencode/models.json"', '"claude-limits.json"', '"cli-config.json"'):
            self.assertFalse(any(p.search(clean) for p, _l in CREDENTIAL_NAMES), clean)

    def test_strip_code_keeps_lines_and_drops_comments(self):
        src = 'Text { // textFormat: Text.PlainText\n  text: "a { b"  /* } */\n  x: /[{]/.test(s) ? 1 : 2\n}\n'
        code = strip_code(src)
        self.assertEqual(code.count("\n"), src.count("\n"))
        blocks = object_blocks(code, ("Text",))
        self.assertEqual(len(blocks), 1)
        self.assertNotIn("textFormat", blocks[0][2])

    def test_block_checks_find_planted_violations(self):
        code = strip_code('Item {\n  Timer { interval: root.delay }\n  Label { text: "x" }\n'
                          '  Text { textFormat: Text.PlainText; text: "y" }\n}\n')
        labels = object_blocks(code, ("Text", "Label"))
        missing = [name for name, _line, body in labels if "textFormat" not in body]
        self.assertEqual(missing, ["Label"])
        timers = object_blocks(code, ("Timer",))
        self.assertFalse(_interval_ok(re.search(r"interval\s*:\s*([^\n;]+)", timers[0][2]).group(1)))
        self.assertTrue(_interval_ok("Model.clampInterval(root.delay)"))
        self.assertTrue(_interval_ok("2147483647"))
        self.assertFalse(_interval_ok("2147483648"))

    def test_python_prompt_flow_detector(self):
        samples = {
            'argv = [exe, "-p", prompt]': True,
            "argv.append(prompt_text)": True,
            'env["PROMPT"] = job.prompt': True,
            'argv = [exe, "-p", ""]': False,
            "run_agent(cmd, prompt, deadline_s=5)": False,
        }
        for source, bad in samples.items():
            tree = ast.parse(source)
            hits = 0
            for node in ast.walk(tree):
                if isinstance(node, ast.List) and _mentions_prompt(node) and any(
                        isinstance(e, ast.Constant) and str(e.value).startswith("-") for e in node.elts):
                    hits += 1
                if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "append" \
                        and _name_of(node.func.value) in ARGV_NAMES and any(map(_mentions_prompt, node.args)):
                    hits += 1
                if isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Subscript) \
                        and _name_of(node.targets[0]) in ENV_NAMES and _mentions_prompt(node.value):
                    hits += 1
            self.assertEqual(hits > 0, bad, source)


def report():
    failed = 0
    for name, fn in CHECKS:
        try:
            problems = fn()
        except Exception as exc:  # a crashing rule is a failing rule
            problems = ["check raised %s: %s" % (type(exc).__name__, exc)]
        if problems:
            more = " (+%d more)" % (len(problems) - 1) if len(problems) > 1 else ""
            print("FAIL  %-44s %s%s" % (name, problems[0], more))
            failed += 1
        else:
            print("PASS  %-44s %s" % (name, "clean"))
    return 1 if failed else 0


if __name__ == "__main__":
    if sys.argv[1:] == ["--report"]:
        raise SystemExit(report())
    unittest.main()
