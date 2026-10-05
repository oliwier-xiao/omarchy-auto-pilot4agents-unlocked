"""sessionbus against a real dbus-daemon: what a notification carries, and that nothing else does.

A private bus listens on a socket in a temporary folder, and tests/support/mockbus.py plays the
notification server on it. Skipped where dbus-daemon is not installed.
"""
import os
import re
import shutil
import socket
import struct
import subprocess
import sys
import tempfile
import threading
import time
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "bin"))
sys.path.insert(0, os.path.join(ROOT, "tests", "support"))
sys.dont_write_bytecode = True

from autopilot import consts, sessionbus  # noqa: E402

HAVE_BUS = os.access("/usr/bin/dbus-daemon", os.X_OK)
if HAVE_BUS:
    import mockbus  # noqa: E402


def _runtime_grammar(test, runtime):
    """Let a temporary folder pass for /run/user/<uid> for the length of one test."""
    test.addCleanup(setattr, consts, "XDG_RUNTIME_RE", consts.XDG_RUNTIME_RE)
    consts.XDG_RUNTIME_RE = re.compile("^" + re.escape(runtime) + "$")


@unittest.skipUnless(HAVE_BUS, "dbus-daemon is not installed")
class SessionBusTests(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.saved = {k: os.environ.get(k) for k in ("DBUS_SESSION_BUS_ADDRESS", "XDG_RUNTIME_DIR")}
        self.addCleanup(self._restore)
        self.proc, self.address, self.sock = mockbus.start_daemon(self.tmp)
        self.addCleanup(self._stop)
        os.environ["DBUS_SESSION_BUS_ADDRESS"] = self.address

    def _restore(self):
        for key, value in self.saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    def _stop(self):
        self.proc.terminate()
        self.proc.wait(5)

    def serve(self, accept=True):
        server = mockbus.Notifications(self.sock, accept=accept)
        server.start()
        self.assertTrue(server.ready.wait(5), "the stand-in did not get its name")
        return server

    def test_the_server_gets_exactly_what_was_sent(self):
        server = self.serve()
        label = '"Fix &amp; ship ż" finished in 14m.'
        self.assertTrue(sessionbus.notify("Auto Pilot", "Done", label, 6000))
        self.assertEqual(server.calls, [["Auto Pilot", 0, "", "Done", label, 0, 0, 6000]])

    def test_a_refusal_or_no_server_is_false(self):
        started = time.monotonic()
        self.assertFalse(sessionbus.notify("Auto Pilot", "Done", "x", 6000))
        self.assertLess(time.monotonic() - started, 3, "an absent server is an error reply, not a wait")
        self.serve(accept=False)
        self.assertFalse(sessionbus.notify("Auto Pilot", "Done", "x", 6000))

    def test_no_bus_is_false_and_quick(self):
        os.environ["DBUS_SESSION_BUS_ADDRESS"] = "unix:path=" + os.path.join(self.tmp, "nothing-here")
        os.environ.pop("XDG_RUNTIME_DIR", None)
        self.assertFalse(sessionbus.notify("a", "b", "c", 1))
        os.environ["DBUS_SESSION_BUS_ADDRESS"] = "tcp:host=localhost,port=1"
        self.assertFalse(sessionbus.notify("a", "b", "c", 1))

    def test_the_runtime_folder_bus_is_the_fallback(self):
        server = self.serve()
        os.environ.pop("DBUS_SESSION_BUS_ADDRESS")
        runtime = os.path.join(self.tmp, "run")
        os.mkdir(runtime, 0o700)
        _runtime_grammar(self, runtime)
        os.symlink(self.sock, os.path.join(runtime, "bus"))
        os.environ["XDG_RUNTIME_DIR"] = runtime
        self.assertTrue(sessionbus.notify("Auto Pilot", "Done", "x", 6000))
        self.assertEqual(len(server.calls), 1)

    def test_an_escaped_address_is_read_as_the_spec_says(self):
        self.serve()
        escaped = self.sock.replace("-", "%2d")
        os.environ["DBUS_SESSION_BUS_ADDRESS"] = "unix:path=" + escaped + ",guid=0123"
        self.assertTrue(sessionbus.notify("Auto Pilot", "Done", "x", 6000))
        os.environ["DBUS_SESSION_BUS_ADDRESS"] = "unix:path=/x%2"
        os.environ.pop("XDG_RUNTIME_DIR", None)
        self.assertFalse(sessionbus.notify("Auto Pilot", "Done", "x", 6000))

    def test_a_text_with_a_nul_is_refused_before_anything_is_sent(self):
        server = self.serve()
        self.assertFalse(sessionbus.notify("Auto Pilot", "Done", "a\0b", 6000))
        self.assertEqual(server.calls, [])

    def test_the_text_never_appears_in_any_command_line(self):
        server = self.serve()
        canary = "canary-%d-label" % os.getpid()
        seen = []
        stop = threading.Event()

        def scan():
            while not stop.is_set():
                for pid in os.listdir("/proc"):
                    if not pid.isdigit():
                        continue
                    try:
                        with open("/proc/%s/cmdline" % pid, "rb") as handle:
                            if canary.encode() in handle.read():
                                seen.append(pid)
                    except OSError:
                        pass

        thread = threading.Thread(target=scan, daemon=True)
        thread.start()
        try:
            for _ in range(20):
                self.assertTrue(sessionbus.notify("Auto Pilot", "Done", '"' + canary + '" finished.', 6000))
        finally:
            stop.set()
            thread.join(5)
        self.assertEqual(seen, [])
        self.assertEqual(len(server.calls), 20)
        self.assertIn(canary, server.calls[0][4])


class HostilePeerTests(unittest.TestCase):
    """A bus that misbehaves: each case is False, within the deadline, and never an exception."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        saved = {k: os.environ.get(k) for k in ("DBUS_SESSION_BUS_ADDRESS", "XDG_RUNTIME_DIR")}

        def restore():
            for key, value in saved.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value
        self.addCleanup(restore)
        os.environ.pop("XDG_RUNTIME_DIR", None)

    def peer(self, script):
        """A socket whose one connection script(conn) answers; the bus address points at it."""
        path = os.path.join(self.tmp, "bus")
        server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        server.bind(path)
        server.listen(1)
        self.addCleanup(server.close)

        def run():
            try:
                conn, _ = server.accept()
            except OSError:
                return
            with conn:
                try:
                    script(conn)
                except OSError:
                    pass
        thread = threading.Thread(target=run, daemon=True)
        thread.start()
        self.addCleanup(thread.join, 5)
        os.environ["DBUS_SESSION_BUS_ADDRESS"] = "unix:path=" + path

    def test_an_answer_sent_a_byte_at_a_time_ends_at_the_deadline(self):
        def trickle(conn):
            conn.recv(256)
            for _ in range(500):
                conn.sendall(b"O")
                time.sleep(0.009)
        self.peer(trickle)
        started = time.monotonic()
        self.assertFalse(sessionbus.notify("a", "b", "c", 1, deadline_s=0.5))
        self.assertLess(time.monotonic() - started, 1.5)

    def test_a_reply_cut_inside_its_header_fields_is_false(self):
        def cut(conn):
            conn.recv(256)
            conn.sendall(b"OK 0123456789abcdef0123456789abcdef\r\n")
            conn.recv(4096)
            # One byte of header fields: a field code and nothing after it.
            conn.sendall(b"l\x02\x00\x01" + struct.pack("<III", 0, 1, 1) + b"\x05")
            time.sleep(1)
        self.peer(cut)
        self.assertFalse(sessionbus.notify("a", "b", "c", 1, deadline_s=2))

    def test_a_silent_bus_ends_at_the_deadline(self):
        self.peer(lambda conn: time.sleep(2))
        started = time.monotonic()
        self.assertFalse(sessionbus.notify("a", "b", "c", 1, deadline_s=0.5))
        self.assertLess(time.monotonic() - started, 1.5)

    def test_an_unusable_address_gives_way_to_the_next(self):
        cases = {
            "unix:path=/x%zz;unix:path=/run/ok": "/run/ok",
            "unix:path=/a%00b;unix:abstract=name": "\0name",
            "unix:path=%2Frun%2Fbus": "/run/bus",
            "tcp:host=localhost,port=1;unix:path=/run/two": "/run/two",
            "unix:path=relative": None,
        }
        for address, expected in cases.items():
            os.environ["DBUS_SESSION_BUS_ADDRESS"] = address
            self.assertEqual(sessionbus._bus_address(), expected, address)

    def test_the_runtime_folder_counts_only_when_it_is_private(self):
        os.environ.pop("DBUS_SESSION_BUS_ADDRESS", None)
        runtime = os.path.join(self.tmp, "run")
        os.mkdir(runtime, 0o700)
        os.chmod(runtime, 0o700)
        os.environ["XDG_RUNTIME_DIR"] = runtime
        self.assertIsNone(sessionbus._bus_address(), "not a runtime folder's name")
        _runtime_grammar(self, runtime)
        self.assertEqual(sessionbus._bus_address(), runtime + "/bus")
        os.chmod(runtime, 0o755)
        self.assertIsNone(sessionbus._bus_address(), "a runtime folder others can enter")


if __name__ == "__main__":
    unittest.main()
