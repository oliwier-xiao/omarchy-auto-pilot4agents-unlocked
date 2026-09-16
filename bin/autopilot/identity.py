"""Which executable a job runs, and whether this plugin copy may fire at all.

A job stores two paths for its CLI: the stable link it was discovered at (for example the
mise "latest" folder) and the real file behind it. At fire time the real file is used when
it still passes the trust checks; otherwise the link is resolved again and the change is
recorded, so an upgrade does not break armed jobs and a swapped binary never runs silently
(R0 D10). PATH is never consulted.
"""

import os

from . import consts, edition, fsio
from .errors import ApError

# Owners allowed for the node interpreter that runs Gemini's bundle. Tests replace this in-process.
NODE_OWNER_UIDS = (0,)


def _expand(candidate, home):
    if candidate == "~":
        return home
    if candidate.startswith("~/"):
        return home + candidate[1:]
    return candidate


def _under_shims(path, home):
    shims = os.path.join(home, consts.MISE_SHIMS_REL)
    return path == shims or path.startswith(shims + "/")


def _node_ok():
    node = consts.TOOLS["node"]
    try:
        fsio.check_trusted_file(node, executable=True)
        st = os.stat(node)
    except (ApError, OSError):
        return False
    return st.st_uid in NODE_OWNER_UIDS


def _exec_for(harness, real):
    if harness == "gemini":
        return [consts.TOOLS["node"], real]
    return [real]


def _check_real(harness, real, home):
    """Raise cli_untrusted unless real is a usable CLI file for harness."""
    if not isinstance(real, str) or not real.startswith("/") or _under_shims(real, home):
        raise ApError("cli_untrusted")
    fsio.check_trusted_file(real, executable=(harness != "gemini"))
    if harness == "gemini" and not _node_ok():
        raise ApError("cli_untrusted")


def discover_cli(harness):
    """First existing candidate path for harness, with its realpath and trust verdict."""
    result = {"harness": harness, "link": None, "real": None, "exec": [], "ok": False, "reason": "not_found"}
    if harness not in consts.CLI_CANDIDATES:
        return result
    home = fsio.home()
    for candidate in consts.CLI_CANDIDATES[harness]:
        path = _expand(candidate, home)
        try:
            os.lstat(path)
        except OSError:
            continue
        real = os.path.realpath(path)
        if not os.path.lexists(real):
            continue  # a dangling link (for example a removed mise "latest") is not a CLI
        result["link"] = path
        result["real"] = real
        if _under_shims(path, home) or _under_shims(real, home):
            result["reason"] = "shim"
            return result
        try:
            _check_real(harness, real, home)
        except ApError:
            result["reason"] = "untrusted"
            return result
        result["exec"] = _exec_for(harness, real)
        result["ok"] = True
        result["reason"] = None
        return result
    return result


def resolve_for_fire(job):
    """Executable to run now (D10). Raises cli_missing or cli_untrusted."""
    harness = job["harness"]
    home = fsio.home()
    cli = job.get("cli") or {}
    stored_real = cli.get("real")
    link = cli.get("link")
    if isinstance(stored_real, str) and stored_real.startswith("/"):
        try:
            os.lstat(stored_real)
            _check_real(harness, stored_real, home)
            return {"exec": _exec_for(harness, stored_real), "real": stored_real, "changed": False,
                    "from": None, "to": None}
        except (OSError, ApError):
            pass
    if not isinstance(link, str) or not link.startswith("/"):
        raise ApError("cli_missing")
    if _under_shims(link, home):
        raise ApError("cli_untrusted")
    try:
        os.lstat(link)
    except OSError:
        raise ApError("cli_missing") from None
    real = os.path.realpath(link)
    if not os.path.exists(real):
        raise ApError("cli_missing")
    _check_real(harness, real, home)
    changed = real != stored_real
    return {"exec": _exec_for(harness, real), "real": real, "changed": changed,
            "from": stored_real if changed else None, "to": real if changed else None}


def check_plugin_identity(job):
    """D20 refusal reason for firing from this plugin copy, or None when it may fire."""
    if fsio.kill_switch_present():
        return "kill_switch"
    state = job.get("state") or {}
    if fsio.plugin_dir() != state.get("pluginDir") or fsio.manifest_id() != edition.PLUGIN_ID \
            or not fsio.plugin_code_trusted():
        return "plugin_identity"
    if fsio.plugin_enabled_in_shell() is not True:
        return "plugin_disabled"
    return None
