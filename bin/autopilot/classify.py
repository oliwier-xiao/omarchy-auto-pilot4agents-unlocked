"""Reading an agent's output stream and deciding what the run's outcome was (R0 3.5, v2 section 7.3).

Structured events decide first (Claude's init, rate_limit_event and result; Codex and OpenCode
error and turn events; Cursor's init and result; Pi's session header and assistant message_end;
Gemini's final JSON document). A clean structured success is accepted before any text is
matched, so an answer that merely talks about an error message is never mistaken for that error
and sent again. Text matching is a fallback for failures only, and the exit code is only ever a
co-signal. Texts collected here are used for matching and then dropped: nothing from the agent's
output is persisted by this module or shown to the user.

feed_line answers None, "kill" (permission boundary), "kill_paid" (paid usage would be used),
"kill_overage" (usage credits would be used) or "drop" (the line echoes the prompt and must not
reach the run log).
"""

import json
import re

from . import consts, fsio, trigger

_TEXT_KEEP = 8
_TEXT_MAX = 512
_RESULT_MAX = 4096
_ERROR_MESSAGE_MAX = 4096
_TOP_KEYS_MAX = 16
_BODY_MAX = 65536
_TYPE_MAX = 64
_RATE_KEYS = ("status", "resetsAt", "rateLimitType", "utilization", "isUsingOverage", "overageStatus")
_CLAUDE_AUTH_ERRORS = ("authentication_failed", "oauth_org_not_allowed", "account_on_hold",
                       "verification_required", "billing_error")
_CLAUDE_TRANSIENT_ERRORS = ("overloaded", "server_error")
# apply_patch replaces edit and write for GPT-family models; multiedit is kept for older builds.
_OPENCODE_WRITE_TOOLS = ("edit", "write", "bash", "patch", "apply_patch", "multiedit")
_OPENCODE_CONTENT = ("text", "tool_use", "reasoning", "step_finish")
_DAY_S = 86400

# OpenCode billing classes of models served by OpenCode itself (Zen and Go), which sleep silently on
# a free-limit or plan-limit 429 instead of printing an error.
ZEN_BILLING = ("zen_free", "zen_paid", "go")

# rate_limit_info.rateLimitType (and transcript quotaLimits.rateLimitType) -> limit kind (LIMIT_KINDS).
RATE_KIND = {"five_hour": "session", "seven_day": "weekly", "seven_day_opus": "model_weekly",
             "seven_day_sonnet": "model_weekly", "seven_day_overage_included": "model_weekly",
             "overage": "other"}

_CLAUDE_LIMIT_TEXT = re.compile("You(?:'|’)ve hit your (?:[A-Za-z' ]+ )?limit|"
                                "You(?:'|’)re out of extra usage|"
                                "You(?:'|’)ve reached your [A-Za-z]+ limit")
_CLAUDE_MODEL_LIMIT = re.compile("You(?:'|’)ve reached your [A-Za-z]+ limit")
_GEMINI_RETRY = re.compile(r"Please retry in ([0-9]+(?:\.[0-9]+)?)(ms|s)\b")
_GEMINI_RESET_AFTER = re.compile(r"reset after ([0-9]+)s\b")
# A top-level key of Gemini's pretty-printed JSON document (exactly two spaces of indent). JSON
# strings cannot hold a raw newline, so such a line is always a real key at depth one.
_GEMINI_TOP_KEY = re.compile(rb'^  "([A-Za-z_]{1,40})"\s*:')

# OpenCode Zen and Go errors, read from the structured responseBody only (message text is shared).
_GO_LIMIT_KIND = {"5 hour": "session", "weekly": "weekly", "monthly": "monthly"}
_ZEN_BILLING_TYPES = ("CreditsError", "MonthlyLimitError", "UserLimitError")
_ZEN_RETRY_AFTER_MAX_S = 90000
_GO_RESET_MAX_S = 40 * _DAY_S
_RETRY_AFTER_RE = re.compile(r"^[0-9]{1,10}$")

# Cursor has no error event: rc 1 plus the stderr tail, matched in this order (R7 2.5).
_CURSOR_UNTRUSTED = re.compile(r"Workspace Trust Required")
_CURSOR_AUTH = re.compile(r"Authentication required|Please run '[^']+ login'|Not logged in")
_CURSOR_LIMIT = re.compile(r"hit your (free requests |usage )?limit|out of usage|not available in the slow pool|"
                           r"spend limit|Usage pricing required", re.I)
_CURSOR_CYCLE = re.compile(r"monthly cycle ends on (\d{1,2})/(\d{1,2})/(\d{4})")
_CURSOR_TRANSIENT = re.compile(r"High Load|experiencing high demand|Rate limited by model provider|"
                               r"trouble connecting to the model provider|ECONNREFUSED|ENETUNREACH", re.I)
_CURSOR_BUSY = re.compile(r"Chat .* may still be running|Could not attach")
# Cursor's own sandbox needs privileges the job's unit denies. Auto Pilot never asks for it, but a
# Cursor config that turns it on still hits this, and it is final: no retry can fix it.
_CURSOR_SANDBOX = re.compile(r"Sandbox mode is enabled but not available|Sandbox failed to start")

# Pi reports provider errors in the last assistant message_end and exits 0 (R7 3.5).
_PI_CODEX_LIMIT = re.compile(r"usage_limit_reached|You have hit your ChatGPT usage limit")
_PI_EXTRA_USAGE = re.compile(r"out of extra usage|draw from your extra usage", re.I)
_PI_QUOTA = re.compile(r"insufficient_quota|quota exceeded|billing|Monthly usage limit reached|available balance|"
                       r"out of budget|GoUsageLimitError|FreeUsageLimitError", re.I)
_PI_RATE = re.compile(r"^(?:429|529)[: ]|rate.?limit|too many requests|overloaded", re.I | re.M)
_PI_RESETS_AT = re.compile(r'resets_at\\?"?\s*:\s*([0-9]{9,11})\b')
_PI_RESETS_IN = re.compile(r'resets_in_seconds\\?"?\s*:\s*([0-9]{1,8})\b')
_PI_TRY_AGAIN = re.compile(r"Try again in ~([0-9]{1,5}) min")
_PI_TIMESTAMP_RE = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9:.]{1,20}(?:Z|[+-][0-9]{2}:?[0-9]{2})$")
_PI_STOP_MAX = 32

_PAID = {"loaded": False, "module": None}


def _paid_fn(name):
    """A pure verdict function of paid.py (H5), or None while that module is not available."""
    if not _PAID["loaded"]:
        _PAID["loaded"] = True
        try:
            from . import paid
            _PAID["module"] = paid
        except Exception:
            _PAID["module"] = None
    fn = getattr(_PAID["module"], name, None) if _PAID["module"] is not None else None
    return fn if callable(fn) else None


def new_stream_state(harness, expected_init_mode, *, allow_paid=False, provider=None, level_id="plan", billing=None):
    return {"harness": harness, "expectedMode": expected_init_mode, "initMode": None, "initSeen": False,
            "sessionId": None, "rateLimit": None, "result": None, "assistantError": None,
            "errorTexts": [], "toolViolations": 0, "turnCompleted": False, "turnFailed": False, "lines": 0,
            "errorEvents": 0, "contentEvents": 0, "document": None, "topKeys": [], "targetSession": None,
            "allowPaid": allow_paid is True, "provider": provider if isinstance(provider, str) else None,
            "levelId": level_id, "billing": billing if billing in consts.BILLING_CLASSES else None,
            "model": None, "killDetail": None, "apiKeySource": None, "apiError": None,
            "piHeader": None, "piHeaderChecked": False, "piAssistant": None, "piRetryFailed": False}


def _kill(state, detail, answer="kill"):
    if state["killDetail"] is None:
        state["killDetail"] = detail
    return answer


def _keep_text(state, text):
    if not isinstance(text, str) or not text:
        return
    texts = state["errorTexts"]
    texts.append(text[:_TEXT_MAX])
    if len(texts) > _TEXT_KEEP:
        del texts[0]


def _sid(state, value, grammar):
    if state["sessionId"] is None and isinstance(value, str) and grammar.match(value):
        state["sessionId"] = value


def _assistant_texts(message):
    out = []
    content = message.get("content") if isinstance(message, dict) else None
    if isinstance(content, list):
        for block in content[:16]:
            if isinstance(block, dict) and block.get("type") == "text" and isinstance(block.get("text"), str):
                out.append(block["text"])
    return out


def _has_block(message, block_type):
    content = message.get("content") if isinstance(message, dict) else None
    if not isinstance(content, list):
        return False
    return any(isinstance(b, dict) and b.get("type") == block_type for b in content)


def _claude_init_paid(source, allow_paid):
    """True when Claude would bill an API key or other paid source while paid usage is off."""
    fn = _paid_fn("claude_init_verdict")
    if fn is not None:
        try:
            return fn(source, allow_paid) == "paid"
        except Exception:
            pass
    return not allow_paid and source != "none"


def _claude_overage(info, allow_paid):
    fn = _paid_fn("claude_rate_event_verdict")
    if fn is not None:
        try:
            return fn(info, allow_paid) == "overage"
        except Exception:
            pass
    return not allow_paid and isinstance(info, dict) and info.get("isUsingOverage") is True


def _feed_claude(state, obj):
    kind = obj.get("type")
    if kind == "system" and obj.get("subtype") == "init":
        mode = obj.get("permissionMode")
        state["initMode"] = mode if isinstance(mode, str) else None
        state["initSeen"] = True
        _sid(state, obj.get("session_id"), consts.UUID_RE)
        source = obj.get("apiKeySource")
        state["apiKeySource"] = source[:32] if isinstance(source, str) else None
        if _claude_init_paid(source, state["allowPaid"]):
            return _kill(state, "api_key_source", "kill_paid")
        if state["expectedMode"] is not None and state["initMode"] != state["expectedMode"]:
            return _kill(state, "init_mode")
        return None
    if kind == "rate_limit_event":
        info = obj.get("rate_limit_info")
        if isinstance(info, dict):
            kept = {k: info.get(k) for k in _RATE_KEYS}
            current = state["rateLimit"]
            if _claude_overage(info, state["allowPaid"]):
                state["rateLimit"] = kept
                return _kill(state, "overage_blocked", "kill_overage")
            if kept.get("status") == "rejected" or current is None or current.get("status") != "rejected":
                state["rateLimit"] = kept
        return None
    if kind == "assistant":
        message = obj.get("message")
        # A tool call before the init event means the permission level was never confirmed.
        if state["expectedMode"] is not None and not state["initSeen"] and _has_block(message, "tool_use"):
            return _kill(state, "init_missing")
        error = obj.get("error")
        if not isinstance(error, str) and isinstance(message, dict):
            error = message.get("error")
        if isinstance(error, str):
            state["assistantError"] = error[:64]
            for text in _assistant_texts(message):
                _keep_text(state, text)
        return None
    if kind == "user":
        if state["expectedMode"] is not None and not state["initSeen"] and _has_block(obj.get("message"),
                                                                                       "tool_result"):
            return _kill(state, "init_missing")
        return None
    if kind == "result":
        errors = obj.get("errors")
        kept_errors = []
        if isinstance(errors, list):
            for item in errors[:_TEXT_KEEP]:
                if isinstance(item, str):
                    kept_errors.append(item[:_TEXT_MAX])
        status = obj.get("api_error_status")
        text = obj.get("result")
        state["result"] = {
            "subtype": obj.get("subtype") if isinstance(obj.get("subtype"), str) else None,
            "is_error": obj.get("is_error") is True,
            "api_error_status": status if isinstance(status, int) and not isinstance(status, bool) else None,
            "errors": kept_errors,
            "result": text[:_RESULT_MAX] if isinstance(text, str) else None,
        }
        _sid(state, obj.get("session_id"), consts.UUID_RE)
    return None


def _error_message(value):
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        parts = []
        for key in ("name", "type", "code", "codex_error_info", "codexErrorInfo", "message"):
            if isinstance(value.get(key), str):
                parts.append(value[key])
        data = value.get("data")
        if isinstance(data, dict) and isinstance(data.get("message"), str):
            parts.append(data["message"])
        return " ".join(parts)
    return ""


def _feed_codex(state, obj):
    kind = obj.get("type")
    if kind in ("thread.started", "session.created", "session_configured", "thread.created"):
        for key in ("thread_id", "threadId", "session_id", "sessionId"):
            _sid(state, obj.get(key), consts.UUID_RE)
    elif kind == "error":
        state["errorEvents"] += 1
        _keep_text(state, " ".join(filter(None, [_error_message(obj.get("message")),
                                                 _error_message(obj.get("codex_error_info")),
                                                 _error_message(obj.get("error"))])))
    elif kind == "turn.failed":
        state["errorEvents"] += 1
        state["turnFailed"] = True
        _keep_text(state, _error_message(obj.get("error")))
    elif kind == "turn.completed":
        state["turnCompleted"] = True
    return None


def _int_value(value):
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _opencode_api_error(error):
    """{type, status, retryAfter, limitName} of an APIError line, from its structured fields only."""
    if not isinstance(error, dict) or error.get("name") != "APIError":
        return None
    data = error.get("data")
    if not isinstance(data, dict):
        return None
    retry = None
    headers = data.get("responseHeaders")
    raw_retry = headers.get("retry-after") if isinstance(headers, dict) else None
    if isinstance(raw_retry, str) and _RETRY_AFTER_RE.fullmatch(raw_retry.strip()):
        retry = int(raw_retry.strip())
    elif _int_value(raw_retry) is not None and raw_retry >= 0:
        retry = raw_retry
    error_type = limit_name = None
    body = data.get("responseBody")
    if isinstance(body, str) and len(body) <= _BODY_MAX:
        try:
            document = json.loads(body)
        except (ValueError, RecursionError):
            document = None
        if isinstance(document, dict):
            inner = document.get("error")
            value = inner.get("type") if isinstance(inner, dict) else None
            if isinstance(value, str) and 0 < len(value) <= _TYPE_MAX:
                error_type = value
            meta = document.get("metadata")
            name = meta.get("limitName") if isinstance(meta, dict) else None
            if isinstance(name, str) and len(name) <= 32:
                limit_name = name
    return {"type": error_type, "status": _int_value(data.get("statusCode")), "retryAfter": retry,
            "limitName": limit_name}


def _feed_opencode(state, obj):
    _sid(state, obj.get("sessionID"), consts.OPENCODE_ID_RE)
    kind = obj.get("type")
    if kind == "error":
        state["errorEvents"] += 1
        error = obj.get("error")
        _keep_text(state, _error_message(error))
        api = _opencode_api_error(error)
        if api is not None:
            state["apiError"] = api
        return None
    if kind in _OPENCODE_CONTENT:
        state["contentEvents"] += 1
    if kind == "tool_use":
        part = obj.get("part")
        if isinstance(part, dict) and part.get("tool") in _OPENCODE_WRITE_TOOLS:
            st = part.get("state")
            if isinstance(st, dict) and st.get("status") == "completed":
                state["toolViolations"] += 1
    return None


def _feed_cursor(state, obj):
    kind = obj.get("type")
    if kind in ("user", "interaction_query"):
        # user events echo the prompt; interaction queries carry search and fetch payloads.
        return "drop"
    if kind == "system" and obj.get("subtype") == "init":
        state["initSeen"] = True
        _sid(state, obj.get("session_id"), consts.UUID_RE)
        source = obj.get("apiKeySource")
        state["apiKeySource"] = source[:32] if isinstance(source, str) else None
        # With the allowlisted environment only the stored login can be the source.
        if source != "login":
            return _kill(state, "api_key_source")
        return None
    if kind == "tool_call":
        call = obj.get("tool_call")
        if obj.get("subtype") == "completed" and isinstance(call, dict):
            for key in consts.CURSOR_WRITE_TOOLS:
                entry = call.get(key)
                result = entry.get("result") if isinstance(entry, dict) else None
                if isinstance(result, dict) and "success" in result:
                    state["toolViolations"] += 1
        return None
    if kind == "result":
        text = obj.get("result")
        state["result"] = {"subtype": obj.get("subtype") if isinstance(obj.get("subtype"), str) else None,
                           "is_error": obj.get("is_error") is True, "api_error_status": None, "errors": [],
                           "result": text[:_RESULT_MAX] if isinstance(text, str) else None}
        _sid(state, obj.get("session_id"), consts.UUID_RE)
    return None


def _pi_header(state, obj):
    header = {"id": None, "timestamp": None, "cwd": None}
    sid = obj.get("id")
    if isinstance(sid, str) and consts.UUID_RE.fullmatch(sid):
        header["id"] = sid
        _sid(state, sid, consts.UUID_RE)
    stamp = obj.get("timestamp")
    if isinstance(stamp, str) and _PI_TIMESTAMP_RE.fullmatch(stamp):
        header["timestamp"] = stamp
    cwd = obj.get("cwd")
    if isinstance(cwd, str) and cwd.startswith("/") and "\x00" not in cwd and "\n" not in cwd \
            and len(cwd.encode("utf-8", "surrogateescape")) <= consts.CWD_MAX_BYTES:
        header["cwd"] = cwd
    state["piHeader"] = header


def _feed_pi(state, obj):
    kind = obj.get("type")
    if kind in ("message_start", "message_update", "message_end"):
        message = obj.get("message")
        role = message.get("role") if isinstance(message, dict) else None
        if role != "assistant":
            # The user message is the prompt itself.
            return "drop"
        if kind == "message_end":
            if message.get("provider") != state["provider"]:
                return _kill(state, "provider_mismatch")
            stop = message.get("stopReason")
            error = message.get("errorMessage")
            state["piAssistant"] = {"stopReason": stop[:_PI_STOP_MAX] if isinstance(stop, str) else None,
                                    "errorMessage": error[:_ERROR_MESSAGE_MAX] if isinstance(error, str) else None}
        return None
    if kind == "tool_execution_start":
        if obj.get("toolName") not in consts.PI_TOOLS:
            return _kill(state, "tool_violation")
        return None
    if kind == "auto_retry_end":
        if obj.get("success") is False:
            state["piRetryFailed"] = True
        return None
    if kind == "agent_end":
        # agent_end repeats every message of the run, the prompt included.
        return "drop"
    return None


def _gemini_summary(obj):
    error = obj.get("error")
    sid = obj.get("session_id") or obj.get("sessionId")
    return {"keys": [k for k in list(obj)[:_TOP_KEYS_MAX] if isinstance(k, str)],
            "hasError": error is not None,
            "error": _error_message(error)[:_TEXT_MAX] if error is not None else "",
            "sessionId": sid if isinstance(sid, str) else None}


def _feed_gemini(state, line):
    stripped = line.strip()
    if stripped.startswith(b"{") and stripped.endswith(b"}"):
        try:
            obj = json.loads(stripped.decode("utf-8", "replace"))
        except (ValueError, RecursionError):
            obj = None
        if isinstance(obj, dict):
            state["document"] = _gemini_summary(obj)
            return
    match = _GEMINI_TOP_KEY.match(line)
    if match:
        key = match.group(1).decode("ascii")
        if key not in state["topKeys"] and len(state["topKeys"]) < _TOP_KEYS_MAX:
            state["topKeys"].append(key)


def feed_line(state, line):
    """Take one stdout line. Returns None, "kill", "kill_paid", "kill_overage" or "drop"."""
    state["lines"] += 1
    harness = state["harness"]
    if harness == "gemini":
        _feed_gemini(state, line)
        return None
    stripped = line.strip()
    obj = None
    if stripped.startswith(b"{"):
        try:
            obj = json.loads(stripped.decode("utf-8", "replace"))
        except (ValueError, RecursionError):
            obj = None
    if harness == "pi":
        if not state["piHeaderChecked"]:
            if not stripped:
                return None
            state["piHeaderChecked"] = True
            if not isinstance(obj, dict) or obj.get("type") != "session":
                return _kill(state, "no_header")
            _pi_header(state, obj)
            return None
        return _feed_pi(state, obj) if isinstance(obj, dict) else None
    if not isinstance(obj, dict):
        return None
    if harness == "claude":
        return _feed_claude(state, obj)
    if harness == "codex":
        return _feed_codex(state, obj)
    if harness == "cursor":
        return _feed_cursor(state, obj)
    return _feed_opencode(state, obj)


# ---------------------------------------------------------------- classification

def _outcome(outcome, detail, state, limit=None, reason=None):
    out = {"outcome": outcome, "detail": detail[:40], "sessionId": state.get("sessionId"), "limit": limit}
    if reason is not None:
        out["reason"] = reason
    return out


def _limit(kind, reset_epoch, source, overage=False, rearm=True):
    return {"kind": kind if kind in consts.LIMIT_KINDS else "other", "resetEpoch": reset_epoch, "source": source,
            "isUsingOverage": bool(overage), "rearm": bool(rearm)}


def _int_epoch(value):
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)) and consts.EPOCH_MIN <= value <= consts.EPOCH_MAX:
        return int(-(-value // 1))
    return None


def _next_utc_midnight(now):
    return (int(now) // _DAY_S + 1) * _DAY_S


def _stderr_text(run):
    tail = run.get("stderrTail") or b""
    return tail[-consts.STDERR_CAP:].decode("utf-8", "replace")


def _match_text(state, run):
    """Error texts, a failed result's text and the stderr tail. A successful answer is never matched."""
    parts = list(state["errorTexts"])
    result = state.get("result")
    if result:
        if result.get("is_error") and result.get("result"):
            parts.append(result["result"])
        parts.extend(result.get("errors") or [])
    parts.append(_stderr_text(run))
    return "\n".join(parts)


def _gemini_doc(state, run):
    if state.get("document") is not None:
        return state["document"]
    raw = run.get("stdoutTail") or b""
    start = raw.find(b"{")
    if start < 0:
        return None
    try:
        obj = json.loads(raw[start:].decode("utf-8", "replace"))
    except (ValueError, RecursionError):
        return None
    return _gemini_summary(obj) if isinstance(obj, dict) else None


def _gemini_flags(state, run):
    doc = _gemini_doc(state, run)
    if doc is not None:
        _sid(state, doc.get("sessionId"), consts.UUID_RE)
        return doc, "response" in doc["keys"], doc["hasError"]
    keys = state.get("topKeys") or []
    return None, "response" in keys, "error" in keys


def _success(state, run):
    """Detail code of a clean structured success, or None."""
    harness = state["harness"]
    rc = run.get("rc")
    if harness == "claude":
        result = state["result"]
        mode_ok = state["expectedMode"] is None or state["initMode"] == state["expectedMode"]
        if result and result["subtype"] == "success" and not result["is_error"] and mode_ok:
            return "success"
    elif harness == "codex":
        if rc == 0 and state["turnCompleted"] and not state["turnFailed"]:
            return "turn_completed"
    elif harness == "opencode":
        if rc == 0 and state["errorEvents"] == 0 and state["lines"] > 0:
            return "exit_clean"
    elif harness == "cursor":
        result = state["result"]
        if rc == 0 and result and result["subtype"] == "success" and not result["is_error"]:
            return "result"
    elif harness == "pi":
        # Exit 0 alone is never success in json mode: the last assistant message decides.
        assistant = state.get("piAssistant")
        if state.get("piHeader") and assistant and assistant["stopReason"] in ("stop", "length"):
            return "stop_reason"
    else:
        _doc, has_response, has_error = _gemini_flags(state, run)
        if rc == 0 and has_response and not has_error:
            return "exit_clean"
    return None


def _claude(state, run, text, rc, now):
    rate = state["rateLimit"]
    result = state["result"]
    a_err = state["assistantError"]
    if "No conversation found with session ID" in text:
        return _outcome("not_found", "session_missing", state)
    if a_err in _CLAUDE_AUTH_ERRORS or "Not logged in" in text or "Please run /login" in text \
            or "Login expired" in text or "Invalid API key" in text:
        return _outcome("auth", "auth", state)
    transient_text = "not your usage limit" in text
    if rate and rate.get("status") == "rejected":
        kind = RATE_KIND.get(rate.get("rateLimitType"), "other")
        return _outcome("limit", "rate_limit_event", state,
                        _limit(kind, _int_epoch(rate.get("resetsAt")), "event", rate.get("isUsingOverage") is True))
    limited = (a_err == "rate_limit" or (result and result["api_error_status"] == 429)
               or _CLAUDE_LIMIT_TEXT.search(text) is not None or "Rate limit reached" in text)
    if limited and not transient_text:
        banner = trigger.parse_claude_banner(text, now)
        if banner:
            return _outcome("limit", "banner", state, _limit(banner["kind"], banner["resetEpoch"], "banner"))
        if _CLAUDE_MODEL_LIMIT.search(text):
            return _outcome("limit", "model_limit", state, _limit("model_weekly", None, "backoff"))
        detail = "rate_limit_reached" if "Rate limit reached" in text else "rate_limit"
        return _outcome("limit", detail, state, _limit("other", None, "backoff"))
    if transient_text or a_err in _CLAUDE_TRANSIENT_ERRORS or "overloaded" in text.lower() or \
            (result and result["api_error_status"] in (500, 502, 503, 504, 529)):
        return _outcome("transient", "server_throttle" if transient_text else "overloaded", state)
    target = state.get("targetSession")
    if result is None and rc == 1 and target and "Error:" in text and target in text:
        return _outcome("busy", "session_held", state)
    if result and result["subtype"] == "error_max_turns":
        return _outcome("max_turns", "error_max_turns", state)
    if result and result["subtype"] == "error_max_budget_usd":
        return _outcome("budget", "error_max_budget_usd", state)
    if run.get("killedBy") == "deadline":
        return _outcome("timeout", "deadline", state)
    if state["expectedMode"] is not None and result is not None and state["initMode"] != state["expectedMode"]:
        return _outcome("boundary_mismatch", "init_missing", state)
    return _outcome("failed", "unclassified", state)


def _codex(state, run, text, rc, now):
    low = text.lower()
    if "no rollout found for thread id" in low:
        return _outcome("not_found", "session_missing", state)
    if "Not logged in" in text or "no Codex credentials were found" in text:
        return _outcome("auth", "not_logged_in", state)
    if "You've hit your usage limit" in text or "usage_limit_exceeded" in text:
        epoch = trigger.parse_codex_retry(text, now)
        return _outcome("limit", "usage_limit", state, _limit("other", epoch, "banner" if epoch else "backoff"))
    if "Selected model is at capacity" in text or "exceeded retry limit, last status: 429" in text \
            or "server_overloaded" in text or "rate_limit_exceeded" in text:
        return _outcome("transient", "capacity", state)
    if "Not inside a trusted directory" in text:
        return _outcome("untrusted", "git_gate", state)
    if run.get("killedBy") == "deadline":
        return _outcome("timeout", "deadline", state)
    return _outcome("failed", "unclassified", state)


def _opencode_structured(state, now):
    api = state.get("apiError")
    if not api:
        return None
    error_type, status, retry = api["type"], api["status"], api["retryAfter"]
    if status == 429 and error_type == "FreeUsageLimitError":
        if retry is not None and 0 < retry <= _ZEN_RETRY_AFTER_MAX_S:
            reset = now + retry
        else:
            reset = _next_utc_midnight(now)
        return _outcome("limit", "zen_free_limit", state, _limit("daily", reset, "retry_after"))
    if status == 429 and error_type == "GoUsageLimitError":
        reset = now + retry if retry is not None and 0 < retry <= _GO_RESET_MAX_S else None
        kind = _GO_LIMIT_KIND.get(api["limitName"], "other")
        return _outcome("limit", "go_limit", state, _limit(kind, reset, "retry_after"))
    if status == 429 and error_type == "RateLimitError":
        return _outcome("transient", "rate_limit", state)
    if status == 401 and error_type in _ZEN_BILLING_TYPES:
        return _outcome("failed", "zen_billing", state, reason="zen_billing")
    return None


def _opencode(state, run, text, rc, now):
    structured = _opencode_structured(state, now)
    if structured is not None:
        return structured
    if "Session not found" in text:
        return _outcome("not_found", "session_missing", state)
    if "ProviderAuthError" in text or "AuthError" in text:
        return _outcome("auth", "provider_auth", state)
    low = text.lower()
    if "FreeUsageLimitError" in text or "GoUsageLimitError" in text or "Free usage exceeded" in text \
            or "insufficient_quota" in low or (state["errorEvents"] and (
                "rate limit" in low or "429" in low or "too many requests" in low)):
        return _outcome("limit", "usage_limit", state, _limit("other", None, "backoff"))
    if run.get("killedBy") == "deadline":
        # OpenCode sleeps in-process on a provider retry-after; a run that produced no content before
        # the deadline was most likely waiting on a rate limit (R1 3.5).
        if state["contentEvents"] == 0:
            return _outcome("transient", "silent_timeout", state)
        return _outcome("timeout", "deadline", state)
    return _outcome("failed", "unclassified", state)


def _cursor(state, run, text, rc, now):
    if run.get("killedBy") == "deadline":
        return _outcome("timeout", "deadline", state)
    if rc == 0:
        return _outcome("failed", "no_result", state)
    if rc in (130, 143) or (rc is None and run.get("signal") in (2, 15)):
        return _outcome("interrupted", "interrupted", state)
    stderr = _stderr_text(run)
    if _CURSOR_UNTRUSTED.search(stderr):
        return _outcome("untrusted", "workspace_trust", state)
    if _CURSOR_AUTH.search(stderr):
        return _outcome("auth", "auth", state)
    if _CURSOR_LIMIT.search(stderr):
        reset = None
        cycle = _CURSOR_CYCLE.search(stderr)
        if cycle:
            reset = trigger.local_date_epoch(int(cycle.group(3)), int(cycle.group(1)), int(cycle.group(2)))
        return _outcome("limit", "monthly_limit", state, _limit("monthly", reset, "stderr", rearm=False))
    if _CURSOR_SANDBOX.search(stderr):
        return _outcome("failed", "cursor_sandbox", state)
    if _CURSOR_TRANSIENT.search(stderr):
        return _outcome("transient", "high_load", state)
    if _CURSOR_BUSY.search(stderr):
        return _outcome("busy", "chat_busy", state)
    return _outcome("failed", "unclassified", state)


def _pi_reset(message, now):
    match = _PI_RESETS_AT.search(message)
    if match:
        epoch = _int_epoch(int(match.group(1)))
        if epoch is not None and epoch > now - 60:
            return epoch
    match = _PI_RESETS_IN.search(message)
    if match and 0 < int(match.group(1)) <= consts.HORIZON_S:
        return now + int(match.group(1))
    match = _PI_TRY_AGAIN.search(message)
    if match and 0 < int(match.group(1)) <= consts.HORIZON_S // 60:
        return now + 60 * int(match.group(1))
    return None


def _pi(state, run, text, rc, now):
    if run.get("killedBy") == "deadline":
        return _outcome("timeout", "deadline", state)
    assistant = state.get("piAssistant")
    stop = (assistant or {}).get("stopReason")
    if stop == "aborted":
        return _outcome("interrupted", "aborted", state)
    if stop == "error":
        message = assistant.get("errorMessage") or ""
        if _PI_CODEX_LIMIT.search(message):
            # Kind and a missing reset come from the Codex record (runner).
            return _outcome("limit", "codex_limit", state, _limit("other", _pi_reset(message, now), "event"))
        if _PI_EXTRA_USAGE.search(message):
            return _outcome("failed", "extra_usage", state, reason="paid_blocked")
        if _PI_QUOTA.search(message):
            return _outcome("limit", "quota_final", state, _limit("other", None, "event", rearm=False))
        if state.get("piRetryFailed") and _PI_RATE.search(message):
            return _outcome("transient", "rate_limited", state)
        return _outcome("failed", "unclassified", state)
    if rc in (129, 143) or (rc is None and run.get("signal") in (1, 15)):
        return _outcome("interrupted", "interrupted", state)
    stderr = _stderr_text(run)
    if assistant is None and rc == 1 and ("No API key found for" in stderr or "No models available." in stderr):
        return _outcome("auth", "no_credentials", state)
    if rc == 0:
        return _outcome("failed", "no_result", state)
    return _outcome("failed", "unclassified", state)


def _gemini(state, run, text, rc, now):
    doc, _has_response, _has_error = _gemini_flags(state, run)
    etext = ((doc or {}).get("error") or "") + "\n" + text
    if rc == 42 or "Error resuming session" in etext:
        return _outcome("not_found", "session_missing", state)
    if rc == 41 or "Please set an Auth method" in etext:
        return _outcome("auth", "auth_method", state)
    retry = _GEMINI_RETRY.search(etext) or _GEMINI_RESET_AFTER.search(etext)
    if "TerminalQuotaError" in etext or "exhausted your daily quota" in etext:
        seconds = _retry_seconds(retry)
        if seconds is not None and seconds <= 86400 and "daily" not in etext:
            return _outcome("limit", "quota_retry_delay", state, _limit("other", now + seconds, "banner"))
        return _outcome("limit", "daily_quota", state, _limit("daily", None, "backoff"))
    if "RetryableQuotaError" in etext or "MODEL_CAPACITY_EXHAUSTED" in etext or retry is not None:
        return _outcome("transient", "retryable_quota", state)
    if rc == 55:
        return _outcome("untrusted", "folder_trust", state)
    if rc == 53:
        return _outcome("max_turns", "turn_limit", state)
    if run.get("killedBy") == "deadline":
        return _outcome("timeout", "deadline", state)
    return _outcome("failed", "unclassified", state)


def _retry_seconds(match):
    if match is None:
        return None
    try:
        value = float(match.group(1))
    except (ValueError, IndexError):
        return None
    if match.re is _GEMINI_RETRY and match.group(2) == "ms":
        value = value / 1000.0
    return int(-(-value // 1))


def _first_line(state, now):
    """The first-event watchdog stopped an OpenCode run that printed nothing."""
    model = state.get("model") or ""
    billing = state.get("billing")
    # An Agent-default job has no model of its own; the gate's class says what it resolved to.
    zen = model.startswith(("opencode/", "opencode-go/")) or (not model and billing in ZEN_BILLING)
    if state["harness"] == "opencode" and zen:
        if not model.startswith("opencode-go/") and billing == "zen_free":
            limit = _limit("daily", _next_utc_midnight(now), "computed")
        else:
            limit = _limit("other", None, "backoff")
        return _outcome("limit", "limit_suspected", state, limit, reason="limit_suspected")
    return _outcome("transient", "stalled", state, reason="stalled")


def pi_session_path(header):
    """Session file Pi writes for a run, derived from its header (R7b 2.5); None when incomplete."""
    if not isinstance(header, dict) or not (header.get("id") and header.get("timestamp") and header.get("cwd")):
        return None
    try:
        home = fsio.home()
    except Exception:
        return None
    folder = "--" + re.sub(r"[/\\:]", "-", header["cwd"].lstrip("/")) + "--"
    name = re.sub(r"[:.]", "-", header["timestamp"]) + "_" + header["id"] + ".jsonl"
    return home.rstrip("/") + "/" + consts.PI_SESSIONS_REL + "/" + folder + "/" + name


def classify(state, run, *, level_id, now=None):
    """Outcome of one run from the stream state and the supervisor result.

    Order: spawn failure, the stream guards (paid, overage, boundary), plan tool violations, clean
    structured success, SIGTERM, the first-event watchdog, then the harness's failure signatures.
    """
    result = _classify(state, run, level_id, now)
    if state["harness"] == "pi":
        path = pi_session_path(state.get("piHeader"))
        if path is not None:
            result["sessionPath"] = path
    return result


def _classify(state, run, level_id, now):
    if now is None:
        import time
        now = int(time.time())
    if run.get("error") == "spawn_failed":
        return _outcome("failed", "spawn_failed", state)
    killed = run.get("killedBy")
    if killed == "paid":
        return _outcome("failed", state.get("killDetail") or "api_key_source", state, reason="paid_blocked")
    if killed == "overage":
        rate = state.get("rateLimit") or {}
        limit = _limit(RATE_KIND.get(rate.get("rateLimitType"), "other"), _int_epoch(rate.get("resetsAt")), "event",
                       overage=True)
        return _outcome("limit", "overage_blocked", state, limit, reason="overage_blocked")
    if killed == "boundary":
        return _outcome("boundary_mismatch", state.get("killDetail") or "init_mode", state)
    if state["harness"] in ("opencode", "cursor") and level_id == "plan" and state["toolViolations"] > 0:
        return _outcome("boundary_mismatch", "tool_violation", state)
    success = _success(state, run)
    if success is not None:
        return _outcome("done", success, state)
    if killed == "sigterm":
        return _outcome("timeout", "sigterm", state)
    if killed == "first_line":
        return _first_line(state, now)
    text = _match_text(state, run)
    rc = run.get("rc")
    handler = {"claude": _claude, "codex": _codex, "opencode": _opencode, "gemini": _gemini, "cursor": _cursor,
               "pi": _pi}[state["harness"]]
    return handler(state, run, text, rc, now)
