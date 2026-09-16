.pragma library
// Identity of this edition. The full permission-level table (argv, env, captions) lives in
// bin/autopilot/edition.py and reaches the UI through `ap4a edition`; this file only carries what
// the QML needs before that first answer lands. tests/test_policy.py keeps both files in step.

var PLUGIN_ID = "oliwier.auto-pilot4agents-unlocked"
var DISPLAY_NAME = "Auto Pilot Unlocked"
var UNIT_PREFIX = "ap4u"
var WIDGET_IPC_TARGET = "oliwier.auto-pilot4agents-unlocked"
var SERVICE_IPC_TARGET = "oliwier.auto-pilot4agents-unlocked.jobs"
var NOTIFY_APP_NAME = "Auto Pilot Unlocked"
var HELPER_REL = "bin/ap4a"
var HARNESS_IDS = ["claude", "opencode", "codex", "gemini", "cursor", "pi"]
var LEVEL_IDS = ["plan", "unattended", "auto", "full"]
var LEVEL_LABELS = { "plan": "Plan", "unattended": "Unattended", "auto": "Auto", "full": "Full access" }
// How loud a level's chip is: amber for levels that run with nobody there, red for no permission checks.
var LEVEL_TONES = { "plan": "accent", "unattended": "warn", "auto": "warn", "full": "bad" }
var DEFAULT_LEVEL = "plan"
