"""Small v2 grammar helpers shared by the paid, models, sessions and agents modules.

const() reads a CONTRACT-V2-DELTA 2.2 constant from consts; the model and Pi provider checks
apply the v2 grammar (brackets allowed, at most MODEL_MAX characters).
"""
from . import consts


def const(name):
    """consts.<name> (every name used here is part of consts since v2)."""
    return getattr(consts, name)


def model_ok(value):
    """True for a model id of the v2 grammar (brackets allowed, at most MODEL_MAX characters)."""
    return isinstance(value, str) and len(value) <= consts.MODEL_MAX and bool(consts.MODEL_RE.match(value))


def pi_provider_ok(value):
    return isinstance(value, str) and bool(consts.PI_PROVIDER_RE.match(value))
