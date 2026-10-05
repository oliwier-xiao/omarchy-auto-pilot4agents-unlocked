"""One method call on the session bus, written to its socket rather than handed to a program.

A notification's text names a job, and a label is typed by somebody or built from a folder
name. Passed to busctl it sat in that process's command line, which every account on the
computer can read. Here it goes from this process to the bus over the bus's own socket, so it
is never an argument of anything.

Only what org.freedesktop.Notifications.Notify needs is implemented: the EXTERNAL
authentication of the D-Bus specification, Hello, one method call, and reading replies until
the one for that call arrives. Little-endian only on the way out, either order on the way in.
Every read is bounded in bytes and the whole exchange in time; any failure is False, never an
exception, because a notification is never worth failing a run over.
"""

import os
import socket
import string
import struct
import time

from . import fsio

_MAX_MESSAGE = 65536      # a Notify reply is a few dozen bytes; NameAcquired about a hundred
_MAX_READ = 262144        # everything read before the reply, all messages together
_ADDRESS_MAX = 4096

_METHOD_CALL, _METHOD_RETURN, _ERROR = 1, 2, 3
_FIELD_PATH, _FIELD_INTERFACE, _FIELD_MEMBER, _FIELD_ERROR = 1, 2, 3, 4
_FIELD_REPLY_SERIAL, _FIELD_DESTINATION, _FIELD_SIGNATURE = 5, 6, 8


class _Fail(Exception):
    pass


# --- writing ----------------------------------------------------------------------------------

class _Out:
    """Little-endian marshalling; alignment is counted from the start of what is written here."""

    def __init__(self):
        self.data = bytearray()

    def pad(self, n):
        self.data += b"\0" * ((-len(self.data)) % n)

    def byte(self, value):
        self.data.append(value)

    def u32(self, value):
        self.pad(4)
        self.data += struct.pack("<I", value)

    def i32(self, value):
        self.pad(4)
        self.data += struct.pack("<i", value)

    def string(self, text):
        raw = text.encode("utf-8")
        if b"\0" in raw:
            raise _Fail()
        self.u32(len(raw))
        self.data += raw + b"\0"

    def signature(self, text):
        raw = text.encode("ascii")
        self.byte(len(raw))
        self.data += raw + b"\0"


def _message(serial, path, interface, member, destination, signature, body):
    out = _Out()
    out.byte(ord("l"))
    out.byte(_METHOD_CALL)
    out.byte(0)
    out.byte(1)
    out.u32(len(body))
    out.u32(serial)
    length_at = len(out.data)
    out.u32(0)
    out.pad(8)
    start = len(out.data)
    fields = [(_FIELD_PATH, "o", path), (_FIELD_INTERFACE, "s", interface), (_FIELD_MEMBER, "s", member),
              (_FIELD_DESTINATION, "s", destination)]
    if signature:
        fields.append((_FIELD_SIGNATURE, "g", signature))
    for code, kind, value in fields:
        out.pad(8)
        out.byte(code)
        out.signature(kind)
        if kind == "g":
            out.signature(value)
        else:
            out.string(value)
    struct.pack_into("<I", out.data, length_at, len(out.data) - start)
    out.pad(8)
    return bytes(out.data) + body


def notify_body(app_name, summary, body, timeout_ms):
    """The arguments of Notify (susssasa{sv}i): no replaced id, no icon, no actions, no hints."""
    out = _Out()
    out.string(app_name)
    out.u32(0)
    out.string("")
    out.string(summary)
    out.string(body)
    out.u32(0)              # as: empty
    out.u32(0)              # a{sv}: empty; the padding to its 8-byte entries is still written
    out.pad(8)
    out.i32(int(timeout_ms))
    return bytes(out.data)


# --- reading ----------------------------------------------------------------------------------

def _parse_header(raw):
    """(type, reply serial or None, header length, total length) of the message starting raw."""
    if len(raw) < 16:
        return None
    order = {ord("l"): "<", ord("B"): ">"}.get(raw[0])
    if order is None or raw[3] != 1:
        raise _Fail()
    body_len, _serial, fields_len = struct.unpack_from(order + "III", raw, 4)
    header_len = 16 + fields_len
    header_len += (-header_len) % 8
    total = header_len + body_len
    if total > _MAX_MESSAGE:
        raise _Fail()
    if len(raw) < 16 + fields_len:
        return None
    reply_serial = None
    pos, end = 16, 16 + fields_len
    while pos < end:
        pos += (-pos) % 8
        if pos >= end:
            break
        code = raw[pos]
        sig_len = raw[pos + 1]
        kind = raw[pos + 2:pos + 2 + sig_len]
        pos += 3 + sig_len
        if kind in (b"s", b"o"):
            pos += (-pos) % 4
            (length,) = struct.unpack_from(order + "I", raw, pos)
            pos += 4 + length + 1
        elif kind == b"g":
            pos += 1 + raw[pos] + 1
        elif kind == b"u":
            pos += (-pos) % 4
            (value,) = struct.unpack_from(order + "I", raw, pos)
            pos += 4
            if code == _FIELD_REPLY_SERIAL:
                reply_serial = value
        else:
            raise _Fail()
    return raw[1], reply_serial, header_len, total


def _bus_address():
    """The session bus socket: the first usable unix address in DBUS_SESSION_BUS_ADDRESS, else
    $XDG_RUNTIME_DIR/bus when that is a runtime folder of ours that nobody else can enter."""
    address = os.environ.get("DBUS_SESSION_BUS_ADDRESS", "")
    if address and len(address) <= _ADDRESS_MAX and address.isprintable():
        for entry in address.split(";"):
            transport, _, params = entry.partition(":")
            if transport != "unix":
                continue
            values = dict(part.partition("=")[::2] for part in params.split(",") if "=" in part)
            try:
                if values.get("path"):
                    path = _unescape(values["path"])
                    if path.startswith("/") and "\0" not in path:
                        return path
                elif values.get("abstract"):
                    return "\0" + _unescape(values["abstract"])
            except (ValueError, UnicodeError):
                continue
    runtime = os.environ.get("XDG_RUNTIME_DIR", "")
    if fsio.runtime_dir_valid(runtime):
        return runtime + "/bus"
    return None


def _unescape(value):
    """D-Bus address values escape bytes as %xx."""
    out, i = bytearray(), 0
    while i < len(value):
        if value[i] == "%":
            pair = value[i + 1:i + 3]
            if len(pair) != 2 or not all(c in string.hexdigits for c in pair):
                raise ValueError("bad escape")
            out.append(int(pair, 16))
            i += 3
        else:
            out += value[i].encode("utf-8")
            i += 1
    return out.decode("utf-8")


def _recv_line(sock, deadline):
    buf = bytearray()
    while not buf.endswith(b"\r\n"):
        if len(buf) > 512 or time.monotonic() >= deadline:
            raise _Fail()
        sock.settimeout(max(0.01, deadline - time.monotonic()))
        chunk = sock.recv(1)
        if not chunk:
            raise _Fail()
        buf += chunk
    return bytes(buf)


def notify(app_name, summary, body, timeout_ms, *, deadline_s=5.0):
    """Send one Notify call; True when the notification server answered it with a return."""
    deadline = time.monotonic() + max(0.1, float(deadline_s))
    try:
        address = _bus_address()
        if address is None:
            return False
        call = _message(2, "/org/freedesktop/Notifications", "org.freedesktop.Notifications", "Notify",
                        "org.freedesktop.Notifications", "susssasa{sv}i",
                        notify_body(app_name, summary, body, timeout_ms))
        hello = _message(1, "/org/freedesktop/DBus", "org.freedesktop.DBus", "Hello", "org.freedesktop.DBus",
                         "", b"")
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            sock.settimeout(max(0.01, deadline - time.monotonic()))
            sock.connect(address)
            uid = str(os.getuid()).encode("ascii").hex().encode("ascii")
            sock.sendall(b"\0AUTH EXTERNAL " + uid + b"\r\n")
            if not _recv_line(sock, deadline).startswith(b"OK "):
                return False
            sock.sendall(b"BEGIN\r\n" + hello + call)
            buf = bytearray()
            seen = 0
            while True:
                parsed = _parse_header(bytes(buf))
                if parsed is not None and len(buf) >= parsed[3]:
                    kind, reply_serial, _hlen, total = parsed
                    if reply_serial == 2 and kind in (_METHOD_RETURN, _ERROR):
                        return kind == _METHOD_RETURN
                    del buf[:total]
                    continue
                if seen > _MAX_READ or time.monotonic() >= deadline:
                    return False
                sock.settimeout(max(0.01, deadline - time.monotonic()))
                chunk = sock.recv(4096)
                if not chunk:
                    return False
                seen += len(chunk)
                buf += chunk
        finally:
            sock.close()
    except (OSError, ValueError, UnicodeError, IndexError, struct.error, _Fail):
        return False
