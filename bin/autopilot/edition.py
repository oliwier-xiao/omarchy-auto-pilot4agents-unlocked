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

# OpenCode checks a permission inside each of its own tools, so a tool that a plugin or an MCP server
# adds, or a plugin's own tool of the same name (oh-my-openagent replaces task and adds a tmux shell),
# is never asked about: it runs whatever edit and bash say. So "*" first turns every tool off, and a
# tool that is off is never offered to the model. Only the read-only tools named next are back on;
# .env files stay unread as OpenCode has them by default, and so do MCP servers' resources, which
# OpenCode reads under the read permission as mcp:<server>:*. Nothing is left to ask: nobody is there
# to answer, and a tool that asks is still offered.
OPENCODE_READ_ONLY_TOOLS = ("read", "glob", "grep", "list", "todowrite", "skill")
_OPENCODE_READ_ONLY = ('{"*":"deny",'
                       '"read":{"*":"allow","*.env":"deny","*.env.*":"deny","*.env.example":"allow","mcp:*":"deny"},'
                       '"glob":"allow","grep":"allow","list":"allow","todowrite":"allow","skill":"allow",'
                       '"edit":"deny","bash":"deny","webfetch":"deny","websearch":"deny",'
                       '"task":"deny","external_directory":"deny","doom_loop":"deny"}')
# The rules an agent carries come after every global rule, OPENCODE_PERMISSION included, so the agent a
# run would otherwise get can turn a tool back on: your own default_agent, or one a plugin sets up
# (oh-my-openagent's default agent turns task back on). Plan and Unattended run an agent of the
# plugin's own instead, defined with the same rules in the config OpenCode reads last.
OPENCODE_READ_ONLY_AGENT = "autopilot-read-only"
_OPENCODE_READ_ONLY_CONFIG = ('{"agent":{"' + OPENCODE_READ_ONLY_AGENT + '":{"mode":"primary","permission":'
                              + _OPENCODE_READ_ONLY + '}}}')
_OPENCODE_READ_ONLY_ENV = {"OPENCODE_PERMISSION": _OPENCODE_READ_ONLY,
                           "OPENCODE_CONFIG_CONTENT": _OPENCODE_READ_ONLY_CONFIG}
# Plan and Unattended keep your plugins here, as Auto and Full do, so a model that a plugin provides
# (Anthropic through an auth plugin) runs at every level. A plugin's own code still runs inside
# OpenCode, outside every rule above, and writes where it likes (oh-my-openagent keeps a .omo folder
# in the working folder).
# Auto turns shell off for the same reason: left to ask, a command headed by cd with a redirection
# is never asked about and writes wherever it points, outside the working folder included.
_OPENCODE_AUTO = ('{"edit":"allow","bash":"deny","webfetch":"allow","websearch":"allow",'
                  '"task":"allow","external_directory":"deny","doom_loop":"deny"}')
_OPENCODE_FULL = ('{"edit":"allow","bash":"allow","webfetch":"allow","websearch":"allow",'
                  '"task":"allow","external_directory":"allow","doom_loop":"allow"}')
_PI_BASE = ["--offline", "--no-extensions", "--no-skills", "--no-prompt-templates", "--no-themes", "--no-approve"]
_PI_ENV = {"PI_OFFLINE": "1", "PI_TELEMETRY": "0", "PI_SKIP_VERSION_CHECK": "1"}

# Codex runs an MCP server's tools outside its sandbox, and calls one that says it only reads
# (readOnlyHint) without asking even where nothing may be approved: on 0.160.0 such a tool wrote a file
# outside the working folder of a read-only run. So at Plan and Unattended every MCP server in your Codex
# settings is turned off by name (harness.codex_mcp_off; an empty mcp_servers table does not replace
# yours), and so are ChatGPT apps and Codex plugins, which bring tools of their own. Plan also turns web
# search off, as Plan does for every other agent.
CODEX_MCP_OFF_LEVELS = ("plan", "unattended")
_CODEX_EXTRAS_OFF = ["--disable", "apps", "--disable", "plugins"]

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
                "caption": ("A read-only agent of the plugin's own, with your plugins loaded. Only reads run: edits, "
                            "shell, web, subagents and every tool from a plugin or an MCP server are off."),
                "argv": ["--agent", OPENCODE_READ_ONLY_AGENT],
                "env": dict(_OPENCODE_READ_ONLY_ENV),
                "initPermissionMode": None,
            },
            "codex": {
                "caption": ("Read-only sandbox, with MCP servers, apps, plugins and web search off. Codex can read "
                            "files but cannot write or reach the network."),
                "argv": ["-s", "read-only"] + _CODEX_EXTRAS_OFF + ["-c", 'web_search="disabled"'],
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
                "caption": ("A read-only agent of the plugin's own, with your plugins loaded. Edits, shell, web, "
                            "subagents and every tool from a plugin or an MCP server are off, since nobody is there "
                            "to approve them. Reads still work."),
                "argv": ["--agent", OPENCODE_READ_ONLY_AGENT],
                "env": dict(_OPENCODE_READ_ONLY_ENV),
                "initPermissionMode": None,
            },
            "codex": {
                "caption": ("Workspace-write sandbox, with MCP servers, apps and plugins off. Codex can edit inside "
                            "the working folder. Network stays off."),
                "argv": ["-s", "workspace-write"] + list(_CODEX_EXTRAS_OFF),
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
                "caption": "Edits, web and subagents run. Shell commands are off, since nobody is there to approve them.",
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
                "caption": "Auto edit. File edits and web fetch are approved. Shell, and edits to Gemini's own settings or a .env file, are denied.",
                "argv": ["--approval-mode", "auto_edit"] + list(_GEMINI_ISOLATION),
                "env": {},
                "initPermissionMode": None,
            },
            "pi": {
                "caption": "Pi can read and edit files: read, grep, find, ls, edit and write. It cannot run commands, but Pi keeps no edit inside the working folder: it can write any file you can.",
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
