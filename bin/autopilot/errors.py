"""The closed set of helper error codes and the fixed sentence shown for each.

Messages never carry dynamic text: a path, a prompt or a CLI's own output could end up in
a toast or in the journal otherwise. Detail is limited to a few typed keys.
"""
import re

from . import edition

MESSAGES = {
    "bad_args": "The request was not understood.",
    "bad_input": "The request data is not valid.",
    "bad_env": "The helper environment is not usable.",
    "stdin_too_large": "The request is too large.",
    "stdin_timeout": "The request did not arrive in time.",
    "output_too_large": "The answer would be too large.",
    "state_refused": "The state folder failed a safety check.",
    "state_too_large": "The job store is too large to read.",
    "state_corrupt": "The job store could not be read.",
    "runtime_dir": "The runtime folder failed a safety check.",
    "lock_busy": "Another change is still running. Try again.",
    "not_found": "That job no longer exists.",
    "bad_status": "That job cannot do this in its current state.",
    "store_full": "The job store is full. Delete drafts in the Queue, then try again.",
    "invalid_harness": "Unknown agent.",
    "invalid_level": "Unknown permission level.",
    "invalid_target": "The session choice is not valid.",
    "invalid_session": "The session id is not valid.",
    "session_not_found": "That session could not be found.",
    "invalid_cwd": "That working folder is not allowed.",
    "invalid_model": "The model name is not valid.",
    "invalid_label": "The label is not valid.",
    "invalid_limits": "The limits are out of range.",
    "invalid_trigger": "The schedule is not valid.",
    "trigger_unsupported": "That reset does not apply to this agent.",
    "fork_unsupported": "This agent cannot fork a session.",
    "prompt_empty": "Write a prompt first.",
    "prompt_too_large": "The prompt is longer than 64 KiB.",
    "prompt_missing": "The prompt was deleted after the run. Write it again.",
    "time_past": "That time has already passed.",
    "time_too_far": "That time is more than 8 days away.",
    "no_reset_data": "No usage data for that reset. Pick a time instead.",
    "reset_not_open": "No limit window is open, so the quota is already fresh.",
    "weekly_exhausted": "The weekly limit is used up.",
    "cli_missing": "The agent command was not found. Install it or pick another agent.",
    "cli_untrusted": "The agent command failed a safety check.",
    "not_logged_in": "The agent is not signed in. Sign in with its own command first.",
    "digest_mismatch": "The job changed since you reviewed it. Check it again.",
    "preview_stale": "The command changed since the preview. Check it again.",
    # Fixed sentences built once at import; the display name comes from the edition file.
    "kill_switch": (edition.DISPLAY_NAME + " is switched off by its kill switch file. Delete ~/.config/omarchy/"
                    + edition.CONFIG_DIR_NAME + "/" + edition.KILL_SWITCH_NAME + " to switch it on."),
    "plugin_disabled": edition.DISPLAY_NAME + " is not enabled in the bar.",
    "plugin_identity": "The plugin folder does not match its manifest.",
    "systemd_failed": "The system scheduler refused the job.",
    "systemd_timeout": "The system scheduler did not answer in time.",
    "stop_unverified": "The job could not be confirmed as stopped.",
    "too_many_ids": "Too many jobs selected.",
    "not_systemd": "This command only runs from the scheduler.",
    "internal": "The helper hit an unexpected error. Try again.",
    "paid_blocked": "This agent is signed in with an API key. Allow paid usage to run it.",
    "paid_zen": "This model bills your OpenCode Zen balance. Allow paid usage to run it.",
    "paid_opencode_claude": "Claude models in OpenCode bill API or extra usage, not your Claude plan.",
    "paid_pi_claude": "Pi bills Claude through extra usage, which is off for this job.",
    "paid_pi_key": "Pi uses an API key for this provider. Allow paid usage to run it.",
    "pi_slash_prompt": "Pi reads a prompt that starts with / as a command. Start it with a word.",
    "pi_model_required": "Pi needs a provider and a model. Pick one from the list.",
    "pi_auth_invalid": "Pi could not check the sign-in for that provider.",
    "level_unavailable": "That permission level is not offered for this agent.",
    "harness_gated": "Cursor support is waiting for a one-time check.",
    "cursor_autorun_config": "Cursor is set to Run Everything, which would approve every tool.",
    "cursor_network_config": "Cursor's sandbox allows all network access.",
    "cursor_project_rules": "This folder has its own Cursor or Claude allow rules, which Cursor would apply.",
    "cursor_untrusted": ("Cursor does not trust this folder yet. Open cursor-agent in this folder once and choose "
                         "Trust this workspace."),
}

DETAIL_KEYS = ("until", "partial", "fireAt", "status", "provider")
# detail.provider is a Pi provider id; anything else is dropped before it reaches the JSON answer.
_PROVIDER_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,39}$")


class ApError(Exception):
    """A handled helper failure. `code` is always a key of MESSAGES."""

    def __init__(self, code, field=None, detail=None):
        if code not in MESSAGES:
            code = "internal"
        super().__init__(code)
        self.code = code
        self.field = field if isinstance(field, str) and field else None
        self.detail = detail if isinstance(detail, dict) and detail else None


def error_object(err):
    """The JSON error object for err: ok, code, fixed message, and optional field and detail."""
    obj = {"ok": False, "code": err.code, "message": MESSAGES[err.code]}
    if err.field:
        obj["field"] = err.field
    if err.detail:
        detail = {k: v for k, v in err.detail.items() if k in DETAIL_KEYS}
        if "provider" in detail and not (isinstance(detail["provider"], str)
                                         and _PROVIDER_RE.fullmatch(detail["provider"])):
            del detail["provider"]
        if detail:
            obj["detail"] = detail
    return obj
