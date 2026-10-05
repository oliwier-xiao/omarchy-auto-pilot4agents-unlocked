"""A private dbus-daemon and a stand-in org.freedesktop.Notifications, for tests only.

Never used by runtime code. The stand-in answers Notify with an id, or with an error, and keeps
what each call carried, decoded from the wire, so a test can check exactly what reached the bus.
"""
import os
import socket
import struct
import subprocess
import threading
import time

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.realpath(__file__))))
DBUS_DAEMON = "/usr/bin/dbus-daemon"

from autopilot import sessionbus as sb  # noqa: E402  (the caller puts bin/ on sys.path)


def start_daemon(tmp, sock_path=None):
    """(process, address, socket path) of a session bus listening only on a socket in tmp."""
    cfg = os.path.join(tmp, "bus.conf")
    sock = sock_path or os.path.join(tmp, "bus.sock")
    with open(cfg, "w") as handle:
        handle.write(
            '<!DOCTYPE busconfig PUBLIC "-//freedesktop//DTD D-Bus Bus Configuration 1.0//EN" '
            '"http://www.freedesktop.org/standards/dbus/1.0/busconfig.dtd">'
            "<busconfig><type>session</type><listen>unix:path=%s</listen><auth>EXTERNAL</auth>"
            '<policy context="default"><allow send_destination="*"/><allow receive_sender="*"/><allow own="*"/></policy>'
            "</busconfig>" % sock)
    proc = subprocess.Popen([DBUS_DAEMON, "--config-file=" + cfg, "--nofork", "--nopidfile"],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for _ in range(200):
        if os.path.exists(sock):
            break
        time.sleep(0.025)
    return proc, "unix:path=" + sock, sock


def _read(conn, buf):
    while True:
        parsed = sb._parse_header(bytes(buf))
        if parsed is not None and len(buf) >= parsed[3]:
            msg = bytes(buf[:parsed[3]])
            del buf[:parsed[3]]
            return msg, parsed
        chunk = conn.recv(4096)
        if not chunk:
            raise EOFError()
        buf += chunk


def _fields(msg):
    (length,) = struct.unpack_from("<I", msg, 12)
    pos, end, out = 16, 16 + length, {}
    while pos < end:
        pos += (-pos) % 8
        code, size = msg[pos], msg[pos + 1]
        kind = msg[pos + 2:pos + 2 + size]
        pos += 3 + size
        if kind in (b"s", b"o"):
            pos += (-pos) % 4
            (n,) = struct.unpack_from("<I", msg, pos)
            out[code] = msg[pos + 4:pos + 4 + n].decode("utf-8")
            pos += 5 + n
        elif kind == b"g":
            out[code] = msg[pos + 1:pos + 1 + msg[pos]].decode("ascii")
            pos += 2 + msg[pos]
        elif kind == b"u":
            pos += (-pos) % 4
            (out[code],) = struct.unpack_from("<I", msg, pos)
            pos += 4
    return out


def decode_notify(body):
    """[app, replaces id, icon, summary, body, actions, hints, timeout] of a Notify body."""
    pos = 0

    def u32():
        nonlocal pos
        pos += (-pos) % 4
        (value,) = struct.unpack_from("<I", body, pos)
        pos += 4
        return value

    def text():
        nonlocal pos
        n = u32()
        value = body[pos:pos + n].decode("utf-8")
        pos += n + 1
        return value

    head = [text(), u32(), text(), text(), text()]
    actions = u32()
    hints = u32()
    pos += (-pos) % 8
    pos += (-pos) % 4
    (timeout,) = struct.unpack_from("<i", body, pos)
    return head + [actions, hints, timeout]


def _reply(serial, destination, ok):
    body = struct.pack("<I", 42) if ok else b""
    out = sb._Out()
    out.byte(ord("l"))
    out.byte(2 if ok else 3)
    out.byte(0)
    out.byte(1)
    out.u32(len(body))
    out.u32(1000 + serial)
    at = len(out.data)
    out.u32(0)
    out.pad(8)
    start = len(out.data)
    fields = [(5, "u", serial), (6, "s", destination)]
    fields.append((8, "g", "u") if ok else (4, "s", "org.example.Refused"))
    for code, kind, value in fields:
        out.pad(8)
        out.byte(code)
        out.signature(kind)
        if kind == "u":
            out.u32(value)
        elif kind == "g":
            out.signature(value)
        else:
            out.string(value)
    struct.pack_into("<I", out.data, at, len(out.data) - start)
    out.pad(8)
    return bytes(out.data) + body


class Notifications(threading.Thread):
    """Owns org.freedesktop.Notifications on the bus at sock_path and answers every Notify."""

    def __init__(self, sock_path, accept=True):
        super().__init__(daemon=True)
        self.sock_path = sock_path
        self.accept = accept
        self.calls = []
        self.ready = threading.Event()

    def run(self):
        conn = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        conn.connect(self.sock_path)
        conn.sendall(b"\0AUTH EXTERNAL " + str(os.getuid()).encode("ascii").hex().encode("ascii") + b"\r\n")
        sb._recv_line(conn, time.monotonic() + 5)
        name = sb._Out()
        name.string("org.freedesktop.Notifications")
        name.u32(4)
        conn.sendall(b"BEGIN\r\n"
                     + sb._message(1, "/org/freedesktop/DBus", "org.freedesktop.DBus", "Hello",
                                   "org.freedesktop.DBus", "", b"")
                     + sb._message(2, "/org/freedesktop/DBus", "org.freedesktop.DBus", "RequestName",
                                   "org.freedesktop.DBus", "su", bytes(name.data)))
        buf = bytearray()
        try:
            while True:
                msg, parsed = _read(conn, buf)
                fields = _fields(msg)
                if parsed[0] == 2 and fields.get(5) == 2:
                    self.ready.set()
                elif parsed[0] == 1 and fields.get(3) == "Notify":
                    self.calls.append(decode_notify(msg[parsed[2]:]))
                    (serial,) = struct.unpack_from("<I", msg, 8)
                    conn.sendall(_reply(serial, fields[7], self.accept))
        except (EOFError, OSError):
            pass
        finally:
            conn.close()
