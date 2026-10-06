"""Identity of this edition of the plugin: Auto Pilot Unlocked.

The unlocked edition adds the Auto and Full access levels, which pass each agent's own
automatic-approval or permission-bypass flags. It is not the marketplace edition, and it
installs beside it: every name below differs from the marketplace edition's, so the two never
share a unit, a state folder or an IPC target.

Every name that could collide with a separately named copy of the plugin lives in this
file, manifest.json and lib/Edition.js. Everything else reads these constants.
"""

PLUGIN_ID = "oliwier.auto-pilot4agents-unlocked"
DISPLAY_NAME = "Auto Pilot Unlocked"
UNIT_PREFIX = "ap4u"
UNIT_DESCRIPTION = "Auto Pilot Unlocked job"
SYSLOG_IDENTIFIER = "ap4u"
STATE_DIR_NAME = "auto-pilot4agents-unlocked"      # $HOME/.local/state/omarchy/<name>
RUNTIME_DIR_NAME = "auto-pilot4agents-unlocked"    # $XDG_RUNTIME_DIR/<name>
CONFIG_DIR_NAME = "auto-pilot4agents-unlocked"     # $HOME/.config/omarchy/<name>
KILL_SWITCH_NAME = "DISABLED"                      # $HOME/.config/omarchy/<name>/DISABLED
WIDGET_IPC_TARGET = "oliwier.auto-pilot4agents-unlocked"
SERVICE_IPC_TARGET = "oliwier.auto-pilot4agents-unlocked.jobs"
NOTIFY_APP_NAME = "Auto Pilot Unlocked"
SESSION_NAME_PREFIX = "autopilot-"                 # Claude --name / OpenCode --title for new sessions
DEMO_STATE_ENV = "AP4U_STATE_DIR"                  # honoured only while the kill switch exists (R0 C14)
SCHEMA_VERSION = 1

HARNESS_IDS = ("claude", "opencode", "codex", "cursor", "pi", "gemini")

# A folder's Claude config (.claude/settings.json, settings.local.json, .mcp.json, agents) loads
# unprompted under -p and can run hooks, redirect the login with ANTHROPIC_BASE_URL or widen the
# run; a cloned folder is the user's own, so the private-folder check does not stop it. Every
# Claude run is pinned to the user's own settings only, takes no MCP from a folder, and writes no
# durable auto-memory or scheduled-task file that a later run would reload.
_CLAUDE_ISOLATION = ("--setting-sources", "user", "--strict-mcp-config")
_CLAUDE_ISOLATION_ENV = {"CLAUDE_CODE_DISABLE_AUTO_MEMORY": "1", "CLAUDE_CODE_DISABLE_CRON": "1"}
# Plan and Unattended name the only tools Claude may offer. Otherwise Plan hands shell, web and
# subagent calls to Claude's own auto-mode classifier, which can approve them, and tools such as
# EnterWorktree, RemoteTrigger and CronCreate need no permission at all. Unattended keeps every tool a
# user's own allow rules can open, but may not write Claude's own settings or memory, here or in the
# working folder, which Write and Edit can otherwise do with no rule. The runner checks that the
# tools Claude reports at start are among these (runner._spawn).
CLAUDE_PLAN_TOOLS = "Glob,Grep,Read"
CLAUDE_UNATTENDED_TOOLS = "Bash,Edit,Glob,Grep,NotebookEdit,Read,WebFetch,WebSearch,Write"
_CLAUDE_CONFIG_WRITES = "Write(~/.claude/**),Edit(~/.claude/**),Write(.claude/**),Edit(.claude/**)"


# A folder's Gemini extensions and MCP servers run their own code when Gemini starts, outside the
# approval policy. Naming only a sentinel that matches nothing enables no extension and allows no
# MCP server, so none of a project's load. Full access is unrestricted by design, so it keeps none
# of this (nor the admin policy); the other levels get it.
_GEMINI_NONE = "ap4a-none"
_GEMINI_ISOLATION = ("--extensions", _GEMINI_NONE, "--allowed-mcp-server-names", _GEMINI_NONE)

_OPENCODE_PLAN = ('{"edit":"deny","bash":"deny","webfetch":"deny","websearch":"deny",'
                  '"task":"deny","external_directory":"deny","doom_loop":"deny"}')
# Unattended turns the same tools off as Plan rather than leaving them to ask. Nobody is there to
# answer either way, but a tool that is off is never offered to the model, while one left to ask is
# offered and checked call by call, which OpenCode does not do for every command.
_OPENCODE_UNATTENDED = ('{"edit":"deny","bash":"deny","webfetch":"deny","websearch":"deny",'
                        '"task":"deny","external_directory":"deny","doom_loop":"deny"}')
# Auto allows the shell: the job runs confined by the OS sandbox (harness.sandbox_spec), so a
# command cannot write outside the working folder (external_directory stays denied too) and cannot
# reach the session bus to escape it. Reaching another folder would still ask, so it is rejected.
_OPENCODE_AUTO = ('{"edit":"allow","bash":"allow","webfetch":"allow","websearch":"allow",'
                  '"task":"allow","external_directory":"deny","doom_loop":"deny"}')
_OPENCODE_FULL = ('{"edit":"allow","bash":"allow","webfetch":"allow","websearch":"allow",'
                  '"task":"allow","external_directory":"allow","doom_loop":"allow"}')
_PI_BASE = ["--offline", "--no-extensions", "--no-skills", "--no-prompt-templates", "--no-themes", "--no-approve"]
_PI_ENV = {"PI_OFFLINE": "1", "PI_TELEMETRY": "0", "PI_SKIP_VERSION_CHECK": "1"}

# Closed enum. The helper refuses any level id that is not a key of this table,
# whether it comes from stdin, jobs.json or IPC (R0 D8).
LEVELS = (
    {
        "id": "plan",
        "label": "Plan",
        "default": True,
        "summary": "Reads and plans only.",
        "defaultMaxTurns": 15,
        "harness": {
            "claude": {
                "caption": "Plan mode. Claude reads and proposes a plan. It does not edit files or run commands.",
                "argv": (["--permission-mode", "plan", "--permission-prompts", "none", "--tools", CLAUDE_PLAN_TOOLS]
                         + list(_CLAUDE_ISOLATION)),
                "env": dict(_CLAUDE_ISOLATION_ENV),
                "initPermissionMode": "plan",
            },
            "opencode": {
                "caption": "Built-in plan agent without plugins. Edits, shell, web and subagents are denied.",
                "argv": ["--pure", "--agent", "plan"],
                "env": {"OPENCODE_PERMISSION": _OPENCODE_PLAN},
                "initPermissionMode": None,
            },
            "codex": {
                "caption": "Read-only sandbox. Codex can read files but cannot write or reach the network.",
                "argv": ["-s", "read-only"],
                "env": {},
                "initPermissionMode": None,
            },
            "gemini": {
                "caption": "Plan mode. Gemini reads and plans. It does not edit files or run commands.",
                "argv": ["--approval-mode", "plan"] + list(_GEMINI_ISOLATION),
                "env": {},
                "initPermissionMode": None,
            },
            "cursor": {
                # Cursor's own sandbox cannot start inside the job's hardened unit (it needs privileges
                # NoNewPrivileges denies), so the boundary here is ask mode: every tool that would ask
                # for approval is denied, and no edit is applied.
                "caption": ("Ask mode. Cursor reads and answers, and anything that would need your approval is "
                            "denied, so no file is edited."),
                "argv": ["--mode", "ask"],
                "env": {},
                "initPermissionMode": None,
            },
            "pi": {
                "caption": "Pi runs read-only here: read, grep, find and ls. It cannot edit files or run commands.",
                "argv": _PI_BASE + ["--tools", "read,grep,find,ls"],
                "env": dict(_PI_ENV),
                "initPermissionMode": None,
            },
        },
        "unavailable": {},
    },
    {
        "id": "unattended",
        "label": "Unattended",
        "default": False,
        "summary": "Runs without you. Anything that would ask is denied.",
        "defaultMaxTurns": 30,
        "harness": {
            "claude": {
                "caption": "Only what your own Claude permission rules already allow. Anything that would ask is denied.",
                "argv": (["--permission-mode", "dontAsk", "--permission-prompts", "none", "--tools", CLAUDE_UNATTENDED_TOOLS,
                          "--disallowedTools", _CLAUDE_CONFIG_WRITES] + list(_CLAUDE_ISOLATION)),
                "env": dict(_CLAUDE_ISOLATION_ENV),
                "initPermissionMode": "dontAsk",
            },
            "opencode": {
                "caption": "Edits, shell, web and subagents are off, since nobody is there to approve them. Reads still work.",
                "argv": [],
                "env": {"OPENCODE_PERMISSION": _OPENCODE_UNATTENDED},
                "initPermissionMode": None,
            },
            "codex": {
                "caption": "Workspace-write sandbox. Codex can edit inside the working folder. Network stays off.",
                "argv": ["-s", "workspace-write"],
                "env": {},
                "initPermissionMode": None,
            },
            "gemini": {
                "caption": "Only Gemini's own read, search and look-up tools run. Edits, shell and anything a settings file adds are denied.",
                "argv": ["--approval-mode", "default"] + list(_GEMINI_ISOLATION),
                "env": {},
                "initPermissionMode": None,
            },
        },
        # Harness id -> the fixed reason this level is not offered for it.
        "unavailable": {
            "cursor": "Cursor applies file edits headless only with --force. Pick Full access for that.",
            "pi": "Pi has no approval prompts. Auto lets it edit files, and Full access adds bash.",
        },
    },
    {
        "id": "auto",
        "label": "Auto",
        "default": False,
        "summary": "Runs without you. The agent's own automatic review approves what it judges safe.",
        "defaultMaxTurns": 40,
        "harness": {
            "claude": {
                "caption": ("Auto mode. Claude's classifier approves actions it judges safe and blocks risky ones. "
                            "Nothing asks you."),
                "argv": ["--permission-mode", "auto", "--permission-prompts", "none"] + list(_CLAUDE_ISOLATION),
                "env": dict(_CLAUDE_ISOLATION_ENV),
                "initPermissionMode": "auto",
            },
            "opencode": {
                "caption": ("Edits, shell, web and subagents run, confined to the working folder by the OS sandbox. "
                            "Reaching another folder would ask, so it is rejected."),
                "argv": [],
                "env": {"OPENCODE_PERMISSION": _OPENCODE_AUTO},
                "initPermissionMode": None,
            },
            "codex": {
                "caption": "Automatic review in the workspace-write sandbox. A reviewer approves or denies what would ask.",
                "argv": ["--approve-for-me"],
                "env": {},
                "initPermissionMode": None,
            },
            "gemini": {
                "caption": ("Auto edit. File edits, web fetch and shell commands run, confined to the working folder "
                            "by the OS sandbox. Edits to Gemini's own settings or a .env file are denied."),
                "argv": ["--approval-mode", "auto_edit"] + list(_GEMINI_ISOLATION),
                "env": {},
                "initPermissionMode": None,
            },
            "pi": {
                "caption": ("Pi reads, edits files and runs commands, confined to the working folder by the OS sandbox: "
                            "read, bash, edit, write, grep, find and ls."),
                "argv": _PI_BASE + ["--tools", "read,bash,edit,write,grep,find,ls"],
                "env": dict(_PI_ENV),
                "initPermissionMode": None,
            },
        },
        "unavailable": {
            "cursor": "Cursor has no automatic review of its own. Pick Full access to let it edit.",
        },
    },
    {
        "id": "full",
        "label": "Full access",
        "default": False,
        "summary": ("Runs without you and without permission checks. The agent can edit, run commands and "
                    "reach the network."),
        "defaultMaxTurns": 60,
        "harness": {
            "claude": {
                "caption": "Bypass permissions. Claude edits files and runs any command without asking.",
                "argv": ["--permission-mode", "bypassPermissions", "--permission-prompts", "none"] + list(_CLAUDE_ISOLATION),
                "env": dict(_CLAUDE_ISOLATION_ENV),
                "initPermissionMode": "bypassPermissions",
            },
            "opencode": {
                "caption": "Every permission is allowed, shell, web and folders outside the working folder included.",
                "argv": ["--auto"],
                "env": {"OPENCODE_PERMISSION": _OPENCODE_FULL},
                "initPermissionMode": None,
            },
            "codex": {
                "caption": "No approvals and no sandbox. Codex runs any command with your user's access.",
                "argv": ["--dangerously-bypass-approvals-and-sandbox"],
                "env": {},
                "initPermissionMode": None,
            },
            "gemini": {
                "caption": "YOLO mode. Every tool call is approved, shell commands included.",
                "argv": ["--approval-mode", "yolo"],
                "env": {},
                "initPermissionMode": None,
            },
            "cursor": {
                "caption": ("Force mode, workspace trusted, sandbox off. Cursor applies edits and runs commands "
                            "without asking."),
                "argv": ["--force", "--trust", "--sandbox", "disabled"],
                "env": {},
                "initPermissionMode": None,
            },
            "pi": {
                "caption": "Every built-in tool: read, bash, edit, write, grep, find and ls.",
                "argv": _PI_BASE + ["--tools", "read,bash,edit,write,grep,find,ls"],
                "env": dict(_PI_ENV),
                "initPermissionMode": None,
            },
        },
        "unavailable": {},
    },
)

LEVEL_IDS = tuple(level["id"] for level in LEVELS)
DEFAULT_LEVEL = "plan"
# Levels whose Gemini CLI jobs run without the admin policy (harness.GEMINI_POLICY_TEXT), which
# only stops a job from switching its own approval mode. Plan, Unattended and Auto keep it: a
# switch would take them past what they promise, all the way to approving every tool. Full access
# already approves every tool, so a switch widens nothing there.
GEMINI_POLICY_EXEMPT_LEVELS = ("full",)


def level(level_id):
    """Return the LEVELS entry for level_id, or None when it is not in the closed enum."""
    for entry in LEVELS:
        if entry["id"] == level_id:
            return entry
    return None
