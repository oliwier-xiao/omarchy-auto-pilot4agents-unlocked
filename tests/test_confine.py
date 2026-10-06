"""Tests for the Auto-level OS confinement (bin/autopilot/confine.py).

The behaviour tests run only where the kernel offers Landlock (CI and any recent Linux); they are
skipped otherwise, since the module is a no-op fallback the helper gates on with available().
"""

import os
import subprocess
import sys
import tempfile
import unittest

sys.dont_write_bytecode = True
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "bin"))

from autopilot import confine  # noqa: E402

_RO = ["/usr", "/bin", "/sbin", "/lib", "/lib64", "/etc", "/proc"]


class ConfineTests(unittest.TestCase):
    def test_abi_and_available_agree(self):
        version = confine.abi()
        self.assertIsInstance(version, int)
        self.assertGreaterEqual(version, 0)
        self.assertEqual(confine.available(), version >= 1 and confine.seccomp_arch() is not None)

    def test_seccomp_arch_known_here(self):
        # The test machine is x86_64 or aarch64; both are supported.
        self.assertIsNotNone(confine.seccomp_arch())

    def confined(self, spec, snippet):
        """Run a short python child under spec and return its stripped stdout."""
        result = subprocess.run([sys.executable, "-I", "-c", snippet], preexec_fn=confine.preexec(spec),
                                capture_output=True, text=True)
        return result.stdout.strip().splitlines()

    def test_writes_reads_and_sockets_are_confined(self):
        if not confine.available():
            self.skipTest("Landlock is not available on this kernel")
        work = os.path.realpath(tempfile.mkdtemp(prefix="confine-"))
        outside = os.path.realpath(tempfile.mkdtemp(prefix="outside-"))
        secret = os.path.join(outside, "secret")
        with open(secret, "w") as handle:
            handle.write("TOP")
        lock_dir = os.path.join(work, "cfg")
        os.makedirs(lock_dir)
        spec = {"rw": [os.path.join(work, "data")], "ro": _RO + [lock_dir], "mkdir": [lock_dir],
                "rwFiles": ["/dev/null"]}
        os.makedirs(os.path.join(work, "data"))
        snippet = (
            "import os, socket\n"
            "def tryit(label, fn):\n"
            "    try:\n"
            "        fn(); print(label + ':ok')\n"
            "    except Exception as exc:\n"
            "        print(label + ':blocked')\n"
            "tryit('rw', lambda: open(%r, 'w').write('x'))\n"
            "tryit('outside', lambda: open(%r, 'w').write('x'))\n"
            "tryit('read_secret', lambda: open(%r).read())\n"
            "tryit('lock_file', lambda: open(%r, 'w').write('x'))\n"
            "tryit('lock_mkdir', lambda: os.mkdir(%r))\n"
            "tryit('unix', lambda: socket.socket(socket.AF_UNIX, socket.SOCK_STREAM))\n"
            "tryit('inet', lambda: socket.socket(socket.AF_INET, socket.SOCK_STREAM).close())\n"
            % (os.path.join(work, "data", "f"), os.path.join(outside, "f"), secret,
               os.path.join(lock_dir, "f"), os.path.join(lock_dir, "d"))
        )
        out = dict(line.split(":") for line in self.confined(spec, snippet))
        self.assertEqual(out["rw"], "ok")
        self.assertEqual(out["outside"], "blocked")      # writing outside the granted tree
        self.assertEqual(out["read_secret"], "blocked")  # reading a folder that was never granted
        self.assertEqual(out["lock_file"], "blocked")    # a mkdir-only folder takes no file
        self.assertEqual(out["lock_mkdir"], "ok")        # but it takes a sub-folder (a lock)
        self.assertEqual(out["unix"], "blocked")         # the escape: no AF_UNIX socket
        self.assertEqual(out["inet"], "ok")              # ordinary network stays open

    def test_apply_fails_closed_when_a_root_is_bogus_is_skipped(self):
        # A path that is not present is skipped, not fatal: confinement still applies.
        if not confine.available():
            self.skipTest("Landlock is not available on this kernel")
        work = os.path.realpath(tempfile.mkdtemp(prefix="confine-"))
        spec = {"rw": [work, "/does/not/exist/anywhere"], "ro": _RO, "rwFiles": ["/dev/null"]}
        out = self.confined(spec, "import os; open(%r,'w').write('x'); print('ok')"
                            % os.path.join(work, "f"))
        self.assertEqual(out, ["ok"])


if __name__ == "__main__":
    unittest.main()
