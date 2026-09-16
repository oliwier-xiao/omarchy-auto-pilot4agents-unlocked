.pragma library
// Identity of this edition. The full permission-level table (argv, env, captions) lives in
// bin/autopilot/edition.py and reaches the UI through `ap4a edition`; this file only carries what
// the QML needs before that first answer lands. tests/test_policy.py keeps both files in step.

var PLUGIN_ID = "oliwier.auto-pilot4agents"
var DISPLAY_NAME = "Auto Pilot"
var UNIT_PREFIX = "ap4a"
var WIDGET_IPC_TARGET = "oliwier.auto-pilot4agents"
var SERVICE_IPC_TARGET = "oliwier.auto-pilot4agents.jobs"
var NOTIFY_APP_NAME = "Auto Pilot"
var HELPER_REL = "bin/ap4a"
var HARNESS_IDS = ["claude", "opencode", "codex", "gemini", "cursor", "pi"]
var LEVEL_IDS = ["plan", "unattended"]
var LEVEL_LABELS = { "plan": "Plan", "unattended": "Unattended" }
var DEFAULT_LEVEL = "plan"
