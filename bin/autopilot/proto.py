"""Request and answer framing.

A request is one compact JSON object on one line of stdin. The line is read as raw bytes
with a byte cap, an idle timeout and a total timeout, so a caller that stalls or floods
cannot hold the helper. An answer is exactly one JSON object and a newline on stdout,
refused at the producer when it would exceed the verb's cap.
"""
import json
import os
import select
import time

from . import consts
from .errors import ApError, error_object


def _reject_constant(_name):
    raise ValueError("non-finite number")


def _no_duplicates(pairs):
    obj = {}
    for key, value in pairs:
        if key in obj:
            raise ValueError("duplicate key")
        obj[key] = value
    return obj


def parse_json_bytes(data):
    """Strict UTF-8 JSON: no NaN/Infinity, no duplicate keys. Raises ValueError."""
    text = data.decode("utf-8", errors="strict")
    try:
        return json.loads(text, parse_constant=_reject_constant, object_pairs_hook=_no_duplicates)
    except RecursionError:
        raise ValueError("nested too deeply") from None


def read_stdin_json(fd=0):
    """Read one JSON object line from stdin (first newline or EOF)."""
    cap = consts.STDIN_MAX_BYTES
    buf = bytearray()
    start = time.monotonic()
    last = start
    while True:
        newline = buf.find(b"\n")
        if newline >= 0:
            if newline > cap:
                raise ApError("stdin_too_large")
            line = bytes(buf[:newline])
            break
        if len(buf) > cap:
            raise ApError("stdin_too_large")
        now = time.monotonic()
        wait = min(consts.STDIN_IDLE_S - (now - last), consts.STDIN_TOTAL_S - (now - start))
        if wait <= 0:
            raise ApError("stdin_timeout")
        try:
            ready, _, _ = select.select([fd], [], [], wait)
        except InterruptedError:
            continue
        if not ready:
            raise ApError("stdin_timeout")
        try:
            chunk = os.read(fd, min(65536, cap + 1 - len(buf)))
        except BlockingIOError:
            continue
        except OSError:
            raise ApError("bad_input") from None
        if not chunk:
            line = bytes(buf)
            break
        buf += chunk
        last = time.monotonic()
    try:
        obj = parse_json_bytes(line)
    except (UnicodeDecodeError, ValueError):
        raise ApError("bad_input") from None
    if not isinstance(obj, dict):
        raise ApError("bad_input")
    return obj


def _write_all(fd, data):
    view = memoryview(data)
    while view:
        try:
            written = os.write(fd, view)
        except InterruptedError:
            continue
        except OSError:
            return False
        view = view[written:]
    return True


def serialize(obj):
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8")


def emit(obj, cap):
    """Write obj as one JSON line to fd 1, or output_too_large when it exceeds cap bytes.

    Returns True when obj itself was written, False when an error object replaced it.
    """
    try:
        data = serialize(obj)
    except (TypeError, ValueError):
        emit_error(ApError("internal"))
        return False
    if len(data) > cap:
        emit_error(ApError("output_too_large"))
        return False
    return _write_all(1, data + b"\n")


def emit_error(err):
    _write_all(1, serialize(error_object(err)) + b"\n")
    _write_all(2, ("ap4a: %s\n" % err.code).encode("ascii"))
