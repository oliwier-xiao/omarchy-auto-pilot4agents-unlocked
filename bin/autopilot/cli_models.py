"""Verb `models --harness <h> [--refresh]`: the model list the Compose picker shows.

It takes no stdin, holds no jobs lock and changes nothing but the models cache in the state
folder. A detection failure is an ok answer with models [] and a fixed reason.
"""

from . import bounded, consts, models, timeutil
from .errors import ApError

_DEFAULT_DEADLINE_S = 23.0
_EXIT_MARGIN_S = 1.0


def cmd_models(argv, payload):
    """models --harness <h> [--refresh] -> Models (3.10)."""
    if not isinstance(argv, list) or len(argv) not in (2, 3) or argv[0] != "--harness" \
            or argv[1] not in consts.HARNESSES or (len(argv) == 3 and argv[2] != "--refresh"):
        raise ApError("bad_args")
    remaining = bounded.remaining_budget()
    deadline = _DEFAULT_DEADLINE_S if remaining is None else max(0.0, remaining - _EXIT_MARGIN_S)
    result = models.detect(argv[1], refresh=len(argv) == 3, now=timeutil.now(), deadline_s=deadline)
    return dict({"ok": True}, **result)
