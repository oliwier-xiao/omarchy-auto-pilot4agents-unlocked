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

HARNESS_IDS = ("claude", "opencode", "codex", "gemini", "cursor", "pi")

_OPENCODE_PLAN = ('{"edit":"deny","bash":"deny","webfetch":"deny","websearch":"deny",'
                  '"task":"deny","external_directory":"deny","doom_loop":"deny"}')
_OPENCODE_UNATTENDED = ('{"edit":"ask","bash":"ask","webfetch":"ask","websearch":"ask",'
                        '"task":"ask","external_directory":"deny","doom_loop":"deny"}')
_OPENCODE_AUTO = ('{"edit":"allow","bash":"ask","webfetch":"allow","websearch":"allow",'
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
                "argv": ["--permission-mode", "plan", "--permission-prompts", "none"],
                "env": {},
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
                "argv": ["--approval-mode", "plan"],
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
                "argv": ["--permission-mode", "dontAsk", "--permission-prompts", "none"],
                "env": {},
                "initPermissionMode": "dontAsk",
            },
            "opencode": {
                "caption": "Edits, shell, web and subagents would ask, so they are rejected. Reads still work.",
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
                "caption": "Default approval. Tools that would ask are denied because nobody is there to answer.",
                "argv": ["--approval-mode", "default"],
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
                "argv": ["--permission-mode", "auto", "--permission-prompts", "none"],
                "env": {},
                "initPermissionMode": "auto",
            },
            "opencode": {
                "caption": "Edits, web and subagents run. Shell commands would ask, so they are rejected.",
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
                "caption": "Auto edit. File edits are approved. Shell commands would ask, so they are denied.",
                "argv": ["--approval-mode", "auto_edit"],
                "env": {},
                "initPermissionMode": None,
            },
            "pi": {
                "caption": "Pi can read and edit files: read, grep, find, ls, edit and write. It cannot run commands.",
                "argv": _PI_BASE + ["--tools", "read,grep,find,ls,edit,write"],
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
                "argv": ["--permission-mode", "bypassPermissions", "--permission-prompts", "none"],
                "env": {},
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


def level(level_id):
    """Return the LEVELS entry for level_id, or None when it is not in the closed enum."""
    for entry in LEVELS:
        if entry["id"] == level_id:
            return entry
    return None
