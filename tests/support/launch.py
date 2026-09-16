#!/usr/bin/python3 -ISB
"""Test launcher for helper verbs against stub tools. Never used by runtime code.

  /usr/bin/python3 -I -S -B tests/support/launch.py [--tools <json>] [--runtime-re <regex>]
      [--candidates <json>] [--trust-root <dir>] -- <verb> [args]

--tools       JSON object {tool key: absolute path} merged into consts.TOOLS; the stubs belong to
              the tester, so consts.TOOL_OWNER_UIDS also accepts this user
--runtime-re  replaces consts.XDG_RUNTIME_RE so a temporary runtime folder is accepted
--candidates  JSON object {harness: [paths]} replacing consts.CLI_CANDIDATES
--trust-root  files below this folder skip the ancestor walk of fsio.check_trusted_file
              (temporary folders sit under the world-writable /tmp)
--gated       JSON list of harness ids replacing consts.GATED_HARNESSES (in this process only)
"""
import json
import os
import re
import sys

sys.dont_write_bytecode = True
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.realpath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "bin"))


def _load(path):
    with open(path) as handle:
        value = json.load(handle)
    if not isinstance(value, dict):
        raise SystemExit("launch.py: %s is not a JSON object" % path)
    return value


def main(args):
    opts = {}
    while args and args[0] != "--":
        if args[0] not in ("--tools", "--runtime-re", "--candidates", "--trust-root", "--gated") or len(args) < 2:
            sys.stderr.write("launch.py: bad option\n")
            return 2
        opts[args[0]] = args[1]
        args = args[2:]
    if not args:
        sys.stderr.write("launch.py: missing --\n")
        return 2
    verb_args = args[1:]

    from autopilot import consts, fsio, identity
    from autopilot import main as helper_main

    if "--tools" in opts:
        for key, value in _load(opts["--tools"]).items():
            consts.TOOLS[key] = value
        consts.TOOL_OWNER_UIDS = (0, os.getuid())
        # A stand-in node from --tools belongs to the tester as well.
        identity.NODE_OWNER_UIDS = (0, os.getuid())
    if "--runtime-re" in opts:
        consts.XDG_RUNTIME_RE = re.compile(opts["--runtime-re"])
    if "--candidates" in opts:
        consts.CLI_CANDIDATES = {k: list(v) for k, v in _load(opts["--candidates"]).items()}
    if "--trust-root" in opts:
        root = os.path.realpath(opts["--trust-root"]).rstrip("/") + "/"
        real_check = fsio.check_trusted_file

        def check(path, *, executable, check_ancestors=True):
            inside = isinstance(path, str) and path.startswith(root)
            return real_check(path, executable=executable, check_ancestors=check_ancestors and not inside)

        fsio.check_trusted_file = check
    if "--gated" in opts:
        gated = json.loads(opts["--gated"])
        if not isinstance(gated, list) or not all(isinstance(h, str) for h in gated):
            sys.stderr.write("launch.py: --gated is not a JSON list of harness ids\n")
            return 2
        consts.GATED_HARNESSES = tuple(gated)
    return helper_main.main(verb_args)


raise SystemExit(main(sys.argv[1:]))
