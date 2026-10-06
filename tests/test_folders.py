"""Tests for the picker's folder reads: `dirs`, `find-dirs`, `folder`, `workspace` and `sessions --in`.

Every test runs against a temporary HOME built in code. Nothing here reads the real HOME or
makes a folder outside the temporary one. Run with PYTHONDONTWRITEBYTECODE=1.
"""

import json
import os
import stat
import sys
import time
import unittest

sys.dont_write_bytecode = True
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "bin"))
sys.path.insert(0, os.path.join(ROOT, "tests"))

from autopilot import cli_scan, consts, finder, folders, main, sessions  # noqa: E402
from autopilot.errors import ApError  # noqa: E402
from test_scan import DAY, ScanCase, uid  # noqa: E402


def names(answer):
    return [entry["name"] for entry in answer["entries"]]


class DirsTests(ScanCase):
    def test_subfolders_only_sorted_hidden_last_and_git(self):
        for rel in ("code/api/.git", "code/web", "Zeta", "alpha", ".config", "worktree"):
            self.mkdir(rel)
        self.write("worktree/.git", "gitdir: /elsewhere\n")
        self.write("notes.txt", "x")
        answer = folders.list_dirs(self.home)
        self.assertEqual(names(answer), ["alpha", "code", "worktree", "Zeta"])
        self.assertEqual((answer["hidden"], answer["hiddenCount"]), (False, 1))
        answer = folders.list_dirs(self.home, hidden=True)
        self.assertEqual(names(answer), ["alpha", "code", "worktree", "Zeta", ".config"])
        self.assertEqual((answer["hidden"], answer["hiddenCount"]), (True, 1))
        self.assertEqual((answer["path"], answer["home"], answer["parent"], answer["state"]),
                         (os.path.realpath(self.home), os.path.realpath(self.home), None, "ok"))
        by = {entry["name"]: entry for entry in answer["entries"]}
        self.assertEqual((by[".config"]["hidden"], by["alpha"]["hidden"]), (True, False))
        self.assertTrue(by["worktree"]["git"])
        self.assertFalse(by["code"]["git"])
        self.assertTrue(all(entry["own"] for entry in answer["entries"]))
        self.assertEqual(answer["workspace"], os.path.join(os.path.realpath(self.home), "AutoPilot"))

        code = folders.list_dirs(os.path.join(self.home, "code"))
        self.assertEqual(names(code), ["api", "web"])
        self.assertTrue(code["entries"][0]["git"])
        self.assertEqual(code["parent"], os.path.realpath(self.home))
        api = folders.list_dirs(os.path.join(self.home, "code", "api"))
        self.assertEqual((names(api), api["git"]), ([], True))

    def test_a_mounted_folder_is_listed_by_name_and_never_looked_at(self):
        # A mount whose server has gone away blocks every stat on it: the tree must not wait on one.
        self.mkdir("remote/.git")
        self.mkdir("local")
        real = os.path.realpath(self.home)
        self.patch(folders, "_mounts", frozenset({real + "/remote"}))
        looked = []
        real_stat = os.stat

        def counting_stat(path, *args, **kwargs):
            looked.append(path)
            return real_stat(path, *args, **kwargs)
        self.patch(os, "stat", counting_stat)
        by = {entry["name"]: entry for entry in folders.list_dirs(self.home)["entries"]}
        self.assertEqual((by["remote"]["mount"], by["remote"]["git"], by["remote"]["own"]), (True, False, None))
        self.assertNotIn("mount", by["local"])
        self.assertFalse([p for p in looked if isinstance(p, str) and "remote" in p], looked)
        # Opened on purpose, it is read like any other folder.
        self.assertEqual(folders.list_dirs(os.path.join(self.home, "remote"))["state"], "ok")

    def test_mountinfo_names_mount_points_with_their_escapes(self):
        text = ("22 1 0:21 / / rw,relatime shared:1 - btrfs /dev/x rw\n"
                "40 22 0:40 / /home/u/my\\040nas rw,nosuid shared:2 - fuse.sshfs h:/ rw\n"
                "41 22 0:41 / /home/u/back\\134slash rw - tmpfs t rw\n"
                "garbage\n")
        self.assertEqual(folders.parse_mountinfo(text), frozenset({"/", "/home/u/my nas", "/home/u/back\\slash"}))
        self.patch(folders, "_mounts", None)
        self.assertIn("/", folders.mount_points(), "this run's own mounts, read once")

    def test_links_inside_home_carry_their_target_and_others_none(self):
        target = self.mkdir("code/api")
        outside = os.path.join(self.tmp, "outside")
        os.makedirs(outside)
        self.mkdir("links")
        os.symlink(target, os.path.join(self.home, "links", "api"))
        os.symlink(outside, os.path.join(self.home, "links", "out"))
        os.symlink(os.path.join(self.home, "missing"), os.path.join(self.home, "links", "dangling"))
        self.write("links/file.txt", "x")
        os.symlink(os.path.join(self.home, "links", "file.txt"), os.path.join(self.home, "links", "to-file"))
        answer = folders.list_dirs(os.path.join(self.home, "links"))
        by = {entry["name"]: entry for entry in answer["entries"]}
        self.assertEqual(sorted(by), ["api", "out"])
        self.assertEqual((by["api"]["link"], by["api"]["target"], by["api"]["outside"]), (True, os.path.realpath(target), False))
        self.assertEqual((by["out"]["link"], by["out"]["target"], by["out"]["outside"]), (True, None, True))

    def test_a_link_to_an_unclean_name_is_answered_without_its_target(self):
        os.mkdir(os.fsencode(self.home) + b"/bad\xff")
        os.mkdir(os.path.join(self.home, "rlo\u202egpj"))
        self.mkdir("links")
        os.symlink(os.fsencode(self.home) + b"/bad\xff",
                   os.path.join(self.home, "links", "to-bad").encode())
        os.symlink(os.path.join(self.home, "rlo\u202egpj"), os.path.join(self.home, "links", "to-rlo"))
        answer = cli_scan.cmd_dirs(["--path", os.path.join(self.home, "links")], None)
        by = {entry["name"]: entry for entry in answer["entries"]}
        self.assertEqual(sorted(by), ["to-bad", "to-rlo"])
        self.assertTrue(all(row["target"] is None and row["outside"] is False for row in by.values()))
        self.assertEqual(answer["skipped"], 2)
        # The answer stays one JSON line the helper can write.
        self.assertTrue(json.dumps(answer, ensure_ascii=False).encode("utf-8"))
        with self.assertRaises(ApError):
            folders.list_dirs(os.path.join(self.home, "links", "to-rlo"))

    def test_the_answer_is_cut_to_its_output_cap_not_dropped(self):
        long_target = self.mkdir("t/" + "x" * 200 + "/" + "y" * 200)
        self.mkdir("many")
        for n in range(40):
            os.symlink(long_target, os.path.join(self.home, "many", "l%02d" % n + "z" * 200))
        self.patch(consts, "OUTPUT_CAP", dict(consts.OUTPUT_CAP, dirs=16384))
        answer = folders.list_dirs(os.path.join(self.home, "many"))
        self.assertTrue(answer["truncated"])
        self.assertLess(0, len(answer["entries"]))
        self.assertLess(len(answer["entries"]), 40)
        self.assertLess(len(json.dumps(dict({"ok": True}, **answer), ensure_ascii=False).encode()), 16384)

    def test_outside_home_missing_and_unprintable(self):
        for path in ("/", self.tmp, os.path.join(self.tmp, "elsewhere")):
            with self.assertRaises(ApError) as caught:
                folders.list_dirs(path)
            self.assertEqual(caught.exception.code, "invalid_cwd")
        escape = os.path.join(self.home, "escape")
        os.symlink(self.tmp, escape)
        with self.assertRaises(ApError):
            folders.list_dirs(escape)
        self.assertEqual(folders.list_dirs(os.path.join(self.home, "gone"))["state"], "missing")
        self.write("plain.txt", "x")
        self.assertEqual(folders.list_dirs(os.path.join(self.home, "plain.txt"))["state"], "missing")

        self.mkdir("odd/fine")
        os.mkdir(os.path.join(self.home, "odd", "bad\nname"))
        answer = folders.list_dirs(os.path.join(self.home, "odd"))
        self.assertEqual((names(answer), answer["skipped"]), (["fine"], 1))

    @unittest.skipIf(os.getuid() == 0, "root reads a folder whatever its mode")
    def test_unreadable_folder_is_denied(self):
        locked = self.mkdir("locked")
        os.chmod(locked, 0)
        try:
            self.assertEqual(folders.list_dirs(locked)["state"], "denied")
        finally:
            os.chmod(locked, 0o700)

    def test_bounds_cut_and_say_so(self):
        for n in range(6):
            self.mkdir("many/d%d" % n)
        self.patch(consts, "DIRS_LIST_MAX", 4)
        answer = folders.list_dirs(os.path.join(self.home, "many"))
        self.assertEqual((names(answer), answer["truncated"]), (["d0", "d1", "d2", "d3"], True))
        self.patch(consts, "DIRS_SCAN_ENTRIES", 2)
        answer = folders.list_dirs(os.path.join(self.home, "many"))
        self.assertEqual((len(answer["entries"]), answer["truncated"]), (2, True))

    def test_dirs_and_workspace_argv(self):
        self.mkdir("code")
        result = cli_scan.cmd_dirs(["--path", self.home], None)
        self.assertEqual((list(result)[0], names(result)), ("ok", ["code"]))
        self.assertEqual(cli_scan.cmd_dirs(["--path", self.home, "--hidden"], None)["hidden"], True)
        main.check_argv("dirs", ["--path", self.home, "--hidden"])
        for argv in ([], ["--path"], ["--path", "relative"], ["--path", self.home, "x"], ["--dir", self.home],
                     ["--path", self.home + "/\n"], ["--hidden", "--path", self.home],
                     ["--path", self.home, "--hidden", "--hidden"]):
            with self.assertRaises(ApError) as caught:
                cli_scan.cmd_dirs(argv, None)
            self.assertEqual(caught.exception.code, "bad_args")
            with self.assertRaises(main._ArgError):
                main.check_argv("dirs", argv)
        main.check_argv("dirs", ["--path", self.home])
        # The panel's form: the folder on stdin, never in argv.
        main.check_argv("dirs", ["--stdin"])
        main.check_argv("dirs", ["--hidden", "--stdin"])
        self.assertEqual(names(cli_scan.cmd_dirs(["--stdin"], {"path": self.home})), ["code"])
        self.assertEqual(cli_scan.cmd_dirs(["--hidden", "--stdin"], {"path": self.home})["hidden"], True)
        for argv, payload in ((["--stdin"], None), (["--stdin"], {}), (["--stdin"], {"path": "relative"}),
                              (["--stdin"], {"path": self.home, "x": 1}), (["--stdin", "--hidden"], {"path": self.home}),
                              (["--stdin"], {"path": self.home + "/\n"}), (["--path", self.home, "--stdin"], {"path": self.home})):
            with self.assertRaises(ApError, msg=(argv, payload)) as caught:
                cli_scan.cmd_dirs(argv, payload)
            self.assertEqual(caught.exception.code, "bad_args")
        main.check_argv("workspace", [])
        main.check_argv("workspace", ["--create"])
        for argv in (["--make"], ["--create", "--create"]):
            with self.assertRaises(main._ArgError):
                main.check_argv("workspace", argv)
            with self.assertRaises(ApError):
                cli_scan.cmd_workspace(argv, None)
        for verb in ("dirs", "workspace"):
            self.assertIn(verb, main.VERBS)
            self.assertIn(verb, consts.VERB_DEADLINE_S)


class FolderCheckTests(ScanCase):
    """`folder`: whether a job may run in one folder, and why not, before a session is drafted."""

    def check(self, path):
        answer = cli_scan.cmd_folder([], {"path": path})
        self.assertTrue(answer["ok"])
        self.assertEqual(answer["path"], path)
        return answer["state"], answer["reason"]

    def test_the_same_rule_as_check_cwd_with_its_reason(self):
        api = self.mkdir("code/api")
        self.assertEqual(self.check(api), ("ok", None))
        self.assertEqual(cli_scan.cmd_folder([], {"path": api})["real"], os.path.realpath(api))
        self.assertEqual(self.check(os.path.join(self.home, "code", "API")), ("missing", "missing"))
        self.assertEqual(self.check("/Projects-%d-nowhere" % os.getpid()), ("missing", "missing"))
        self.write("notes.txt", "x")
        self.assertEqual(self.check(os.path.join(self.home, "notes.txt")), ("missing", "not_dir"))
        self.assertEqual(self.check(self.home), ("refused", "home"))
        self.assertEqual(self.check("/"), ("refused", "root"))
        self.assertEqual(self.check("/run"), ("refused", "system"))
        shared = self.mkdir("shared")
        os.chmod(shared, 0o775)
        self.assertEqual(self.check(shared), ("refused", "shared"))
        os.chmod(shared, 0o755)
        self.write("shared/CLAUDE.md", "be kind\n", mode=0o666)
        self.assertEqual(self.check(shared), ("refused", "config"))
        # Whatever the reason, check_cwd refuses exactly the folders the check does not pass.
        for path in (api, shared, self.home, "/", os.path.join(self.home, "notes.txt")):
            real, reason = folders.jobs.cwd_verdict(path)
            if reason is None:
                self.assertEqual(folders.jobs.check_cwd(path), real)
            else:
                with self.assertRaises(ApError) as caught:
                    folders.jobs.check_cwd(path)
                self.assertEqual(caught.exception.code, "invalid_cwd")

    def test_protected_folders_and_their_parents_are_refused(self):
        # A job allowed to edit its working folder must not reach the plugin, the job store, an
        # agent's settings or the folders agent binaries start from: not from inside, not from above.
        for rel in folders.jobs.PROTECTED_CWD_REL:
            self.mkdir(rel + "/inner")
            self.assertEqual(self.check(os.path.join(self.home, rel)), ("refused", "protected"), rel)
            self.assertEqual(self.check(os.path.join(self.home, rel, "inner")), ("refused", "protected"), rel)
        for rel in (".config", ".local", ".local/share", ".local/state"):
            self.assertEqual(self.check(os.path.join(self.home, rel)), ("refused", "protected"), rel)
        for rel in (".config/nvim", ".local/share/notes", "code/api"):
            self.mkdir(rel)
            self.assertEqual(self.check(os.path.join(self.home, rel)), ("ok", None), rel)
        # A link that leads into a protected folder is judged by where it leads.
        os.symlink(os.path.join(self.home, ".claude"), os.path.join(self.home, "claude-settings"))
        self.assertEqual(self.check(os.path.join(self.home, "claude-settings")), ("refused", "protected"))
        # The plugin's own folder counts wherever it really is: a folder above it is refused too.
        above = os.path.dirname(folders.fsio.plugin_dir())
        self.assertIn(folders.jobs.cwd_verdict(above)[1], ("protected", "system", "root", "home"))
        with self.assertRaises(ApError):
            folders.jobs.check_cwd(os.path.join(self.home, ".config"))

    def test_a_link_is_judged_by_where_it_leads(self):
        api = self.mkdir("code/api")
        os.symlink(api, os.path.join(self.home, "api-link"))
        state, reason = self.check(os.path.join(self.home, "api-link"))
        self.assertEqual((state, reason), ("ok", None))
        os.symlink(self.home, os.path.join(self.home, "home-link"))
        self.assertEqual(self.check(os.path.join(self.home, "home-link")), ("refused", "home"))

    def test_folder_argv_and_payload(self):
        main.check_argv("folder", [])
        self.assertIn("folder", main.VERBS)
        self.assertTrue(main.VERBS["folder"][2], "it reads its folder from stdin")
        self.assertIn("folder", consts.VERB_DEADLINE_S)
        for argv in (["--path", self.home], ["--stdin"], [self.home]):
            with self.assertRaises(main._ArgError):
                main.check_argv("folder", argv)
            with self.assertRaises(ApError):
                cli_scan.cmd_folder(argv, {"path": self.home})
        for payload in (None, {}, {"path": "relative"}, {"path": self.home, "x": 1}, {"path": self.home + "/\n"},
                        {"path": 7}, {"cwd": self.home}):
            with self.assertRaises(ApError, msg=payload) as caught:
                cli_scan.cmd_folder([], payload)
            self.assertEqual(caught.exception.code, "bad_args")


class FindDirsTests(ScanCase):
    """`find-dirs`: folders by name anywhere under the home folder, for people who type no paths."""

    def find(self, query, known=()):
        answer = cli_scan.cmd_find_dirs([], {"q": query, "known": list(known)})
        self.assertTrue(answer["ok"])
        return answer

    def paths(self, query, known=()):
        home = os.path.realpath(self.home)
        return [entry["path"][len(home) + 1:] for entry in self.find(query, known)["entries"]]

    def test_words_find_folders_by_name_best_first(self):
        for rel in ("Projects", "Projects-old-backup", "code/Last-Man-Hooping/.git", "code/last-man-site", "Pictures",
                    "Documents/Łódź-zdjęcia", "notes"):
            self.mkdir(rel)
        self.assertEqual(self.paths("projects")[:2], ["Projects", "Projects-old-backup"])
        exact, prefix = self.find("projects")["entries"][:2]
        self.assertGreaterEqual(exact["score"] - prefix["score"], 150, "the whole name is a tier above its start")
        self.assertEqual(self.paths("Projects")[0], "Projects", "case does not matter")
        self.assertEqual(self.paths("projets")[0], "Projects", "a letter left out")
        self.assertEqual(self.paths("projcets")[0], "Projects", "two letters swapped")
        self.assertEqual(self.paths("piktures"), ["Pictures"], "a letter off")
        self.assertEqual(self.paths("pikturez"), [], "two letters off is noise")
        # Letters in order may start at a later word when the first place they occur starts none.
        self.assertGreater(finder.score_word("prt", "sprint-prototype")[0], 0)
        self.assertEqual(self.paths("last man")[:2], ["code/Last-Man-Hooping", "code/last-man-site"])
        self.assertEqual(set(self.paths("lastman")), {"code/Last-Man-Hooping", "code/last-man-site"}, "separators left out")
        self.assertEqual(self.paths("code last")[0], "code/Last-Man-Hooping", "a word may name a folder above it")
        self.assertEqual(self.paths("lodz zdjecia"), ["Documents/Łódź-zdjęcia"], "accents and ł fold")
        top = self.find("last man")["entries"][0]
        self.assertEqual([top["name"][a:b] for a, b in top["marks"]], ["Last", "Man"])
        self.assertEqual((top["git"], top["own"], top["depth"]), (True, True, 2))
        self.assertEqual(self.paths("zzzz"), [])
        self.assertEqual(self.paths("pr"), ["Projects", "Projects-old-backup"], "two letters only start a name")

    def test_checkouts_caches_and_noise(self):
        self.mkdir("code/api/.git")
        self.mkdir("code/api/src/api")
        self.mkdir("go/pkg/mod/github.com/x/api")
        self.mkdir("Pictures")
        self.mkdir("fixtures")
        self.assertEqual(self.paths("api")[:2], ["code/api", "code/api/src/api"], "a checkout ranks above a folder inside it")
        self.assertNotIn("go/pkg/mod/github.com/x/api", self.paths("api"), "Go's module cache is never entered")
        self.assertIn("go/pkg/mod", self.paths("mod"), "but listed by name")
        self.assertEqual(self.paths("pictures"), ["Pictures"], "no loose matches once a name matches outright")
        self.assertEqual(self.paths("x y"), [], "one-letter words are left out")
        self.assertEqual(self.paths("api x"), self.paths("api"))

    def test_folders_with_sessions_rank_higher_within_a_tier(self):
        self.mkdir("alpha-one")
        self.mkdir("alpha-two")
        self.assertEqual(self.paths("alpha")[0], "alpha-one")
        known = os.path.join(os.path.realpath(self.home), "alpha-two")
        self.assertEqual(self.paths("alpha", [known])[0], "alpha-two")

    def test_hidden_links_other_names_and_skipped_folders(self):
        self.mkdir(".secret/Projects")
        self.mkdir("node_modules/inner-pkg")
        self.mkdir("app/build/inner-out")
        self.mkdir("Projects")
        os.symlink(os.path.join(self.home, "Projects"), os.path.join(self.home, "projects-link"))
        outside = os.path.join(self.tmp, "outside-dir")
        os.makedirs(os.path.join(outside, "far-away"))
        os.symlink(outside, os.path.join(self.home, "out"))
        os.mkdir(os.fsencode(self.home) + b"/bad\xff")
        os.mkdir(os.fsencode(self.home) + b"/bad\xff/inside-bad")
        self.assertEqual(self.paths("projects"), ["Projects"], "never inside a hidden folder, never a link")
        self.assertEqual(self.paths("node modules"), ["node_modules"], "listed by name")
        self.assertEqual(self.paths("inner"), [], "but never entered")
        self.assertEqual(self.paths("far away"), [], "a link out of the home folder is never followed")
        self.assertEqual(self.paths("inside bad"), [], "a name that is not printable is never entered")
        self.assertGreaterEqual(self.find("zz yy")["skipped"], 1)

    def test_bounds_stop_early_and_say_so(self):
        for n in range(300):
            self.mkdir("many/d%03d" % n)
        self.mkdir("a/b/c/deep-target")
        self.patch(consts, "FIND_DEPTH", 3)
        self.assertEqual(self.paths("deep target"), [], "deeper than the walk reads")
        self.patch(consts, "FIND_DEPTH", 8)
        self.assertEqual(self.paths("deep target"), ["a/b/c/deep-target"])
        self.patch(consts, "FIND_FOLDERS", 5)
        self.assertEqual((self.find("d001")["truncated"], self.find("d001")["reason"]), (True, "folders"))
        self.patch(consts, "FIND_FOLDERS", 20000)
        self.patch(consts, "FIND_ENTRIES", 50)
        self.assertEqual(self.find("d001")["reason"], "entries")
        self.patch(consts, "FIND_ENTRIES", 200000)
        # One crowded folder may not use up the search: it is read only so far, and the walk goes on.
        self.patch(consts, "FIND_DIR_ENTRIES", 100)
        crowded = self.find("deep target")
        self.assertEqual(([e["path"][len(os.path.realpath(self.home)) + 1:] for e in crowded["entries"]], crowded["truncated"]),
                         (["a/b/c/deep-target"], False))
        self.assertGreaterEqual(crowded["cut"], 1)
        self.patch(consts, "FIND_DIR_ENTRIES", 5000)
        self.patch(consts, "FIND_DEADLINE_S", 0)
        self.assertEqual(self.find("d001")["reason"], "time")
        self.patch(consts, "FIND_DEADLINE_S", 1.5)
        self.patch(consts, "FIND_LIMIT", 3)
        self.assertEqual(len(self.find("d0")["entries"]), 3)
        self.patch(consts, "FIND_LIMIT", 40)
        self.patch(consts, "OUTPUT_CAP", dict(consts.OUTPUT_CAP, **{"find-dirs": folders._CAP_SLACK + 1500}))
        cut = self.find("d0")
        self.assertTrue(0 < len(cut["entries"]) < 40 and cut["truncated"] and cut["reason"] == "cap", cut["reason"])

    def test_a_mounted_folder_is_neither_entered_nor_answered(self):
        self.mkdir("remote/projects-far/.git")
        self.mkdir("Projects")
        real = os.path.realpath(self.home)
        self.patch(folders, "_mounts", frozenset({real + "/remote"}))
        opened = []
        real_open = os.open

        def counting_open(path, *args, **kwargs):
            opened.append(path)
            return real_open(path, *args, **kwargs)
        self.patch(os, "open", counting_open)
        self.assertEqual(self.paths("projects"), ["Projects"])
        self.assertEqual(self.paths("remote"), [])
        self.assertNotIn("remote", opened, "never even opened")

    def test_long_names_and_words_stay_inside_the_deadline(self):
        # Seven levels of 255-letter names and 1500 more below them, searched for an 80-letter
        # word: scoring is capped by word and name length, and the clock is read for every entry.
        deep = "/".join(["a" * 255] * 7)
        for n in range(1500):
            self.mkdir(deep + "/" + "a" * 251 + "%04d" % n)
        started = time.monotonic()
        answer = self.find("a" * 78 + "zz")
        self.assertLess(time.monotonic() - started, consts.FIND_DEADLINE_S + 1.0)
        self.assertTrue(answer["ok"])

    def test_find_dirs_argv_and_payload(self):
        self.mkdir("Projects")
        main.check_argv("find-dirs", [])
        self.assertIn("find-dirs", main.VERBS)
        self.assertTrue(main.VERBS["find-dirs"][2], "the words come on stdin, never in argv")
        self.assertIn("find-dirs", consts.VERB_DEADLINE_S)
        for argv in (["projects"], ["--q", "projects"], ["--stdin"]):
            with self.assertRaises(main._ArgError):
                main.check_argv("find-dirs", argv)
            with self.assertRaises(ApError):
                cli_scan.cmd_find_dirs(argv, {"q": "projects"})
        home = os.path.realpath(self.home)
        for payload in (None, {}, {"q": 7}, {"q": "a"}, {"q": " a "}, {"q": "x" * 81}, {"q": "a\nb"},
                        {"q": "ok", "known": ["relative"]}, {"q": "ok", "known": home}, {"q": "ok", "extra": 1},
                        {"q": "ok", "known": [home] * 65}, {"known": [home]}):
            with self.assertRaises(ApError, msg=payload) as caught:
                cli_scan.cmd_find_dirs([], payload)
            self.assertEqual(caught.exception.code, "bad_args")
        self.assertEqual(self.paths("projects"), ["Projects"])
        answer = cli_scan.cmd_find_dirs([], {"q": "projects"})
        self.assertEqual(sorted(answer), ["cut", "entries", "home", "ok", "q", "reason", "scanned", "skipped", "truncated"])
        # Zero-width joiners (inside emoji, pasted text) carry no letters and are left out.
        self.assertEqual(self.paths("proj\u200dects"), ["Projects"])


class WorkspaceTests(ScanCase):
    def path(self):
        return os.path.join(self.home, consts.WORKSPACE_NAME)

    def test_made_only_on_request_private_and_once(self):
        self.assertEqual(consts.WORKSPACE_NAME, "AutoPilot")
        answer = folders.workspace(False)
        self.assertEqual((answer["exists"], answer["created"]), (False, False))
        self.assertFalse(os.path.lexists(self.path()))

        answer = cli_scan.cmd_workspace(["--create"], None)
        self.assertEqual((answer["ok"], answer["exists"], answer["created"]), (True, True, True))
        self.assertEqual(answer["path"], os.path.realpath(self.path()))
        info = os.lstat(self.path())
        self.assertTrue(stat.S_ISDIR(info.st_mode))
        self.assertEqual(stat.S_IMODE(info.st_mode) & 0o077, 0)

        answer = folders.workspace(True)
        self.assertEqual((answer["exists"], answer["created"]), (True, False))
        self.assertEqual(folders.workspace(False)["exists"], True)

    def test_something_else_there_is_left_alone(self):
        self.write(consts.WORKSPACE_NAME, "not a folder")
        with self.assertRaises(ApError) as caught:
            folders.workspace(True)
        self.assertEqual(caught.exception.code, "invalid_cwd")
        self.assertEqual((folders.workspace(False)["exists"], folders.workspace(False)["refused"]), (False, True))
        with open(self.path()) as handle:
            self.assertEqual(handle.read(), "not a folder")

        # A link is refused wherever it leads: outside the home folder, or to a folder inside it.
        os.unlink(self.path())
        for target in (os.path.join(os.path.dirname(self.tmp), "ap4a-outside-%d" % os.getpid()),
                       self.mkdir("Projects/scratch"), self.mkdir(".ssh")):
            os.makedirs(target, exist_ok=True)
            os.symlink(target, self.path())
            try:
                with self.assertRaises(ApError):
                    folders.workspace(True)
                self.assertEqual(folders.workspace(False)["refused"], True)
                self.assertTrue(os.path.islink(self.path()))
            finally:
                os.unlink(self.path())
                if not target.startswith(self.home):
                    os.rmdir(target)
        os.symlink(os.path.join(self.home, "missing"), self.path())
        with self.assertRaises(ApError):
            folders.workspace(True)
        os.unlink(self.path())

    def test_a_folder_others_can_write_is_refused_and_a_private_one_used(self):
        os.mkdir(self.path(), 0o700)
        os.chmod(self.path(), 0o777)
        with self.assertRaises(ApError):
            folders.workspace(True)
        self.assertEqual(stat.S_IMODE(os.lstat(self.path()).st_mode), 0o777)
        os.chmod(self.path(), 0o755)
        answer = folders.workspace(True)
        self.assertEqual((answer["exists"], answer["created"], answer["refused"]), (True, False, False))


class SessionsInTests(ScanCase):
    """`sessions --in <folder>`: only that folder's sessions, for every agent."""

    def test_claude_reads_the_named_project_folder(self):
        api = self.mkdir("code/api")
        web = self.mkdir("code/web")
        named = sessions._claude_dir_name(api)
        self.assertEqual(named, api.replace("/", "-").replace(".", "-").replace("_", "-"))
        self.transcript(named, uid(1), self.claude_head(api, "Fix the flaky test"), age_s=60)
        self.transcript(named, uid(2), self.claude_head(api, "Old but listed"), age_s=60 * DAY)
        self.transcript(sessions._claude_dir_name(web), uid(3), self.claude_head(web, "Other folder"), age_s=30)
        result = sessions.list_sessions("claude", self.now, only=api)
        self.assertEqual([row["id"] for row in result["sessions"]], [uid(1), uid(2)])
        self.assertEqual((result["in"], result["cwd"]), (api, api))

        # Other projects are never read for one folder, even when no project folder is named after it.
        os.rename(os.path.join(self.home, ".claude", "projects", named),
                  os.path.join(self.home, ".claude", "projects", "renamed"))
        reads = []
        real_head = sessions._head_records
        self.patch(sessions, "_head_records", lambda path, *a: reads.append(path) or real_head(path, *a))
        result = sessions.list_sessions("claude", self.now, only=api)
        self.assertEqual((result["sessions"], reads), ([], []))

    def test_claude_long_folder_names_match_their_cut_project_folder(self):
        deep = self.mkdir("/".join(["segment%02d" % n for n in range(25)]))
        named = sessions._claude_dir_name(deep)
        self.assertGreater(len(named), 200)
        self.transcript(named[:200] + "-1a2b3c", uid(1), self.claude_head(deep, "Deep folder"), age_s=60)
        self.transcript(named[:150] + "-other", uid(2), self.claude_head("/w/x", "Elsewhere"), age_s=30)
        rows = sessions.list_sessions("claude", self.now, only=deep)["sessions"]
        self.assertEqual([row["id"] for row in rows], [uid(1)])

    def test_sqlite_agents_filter_in_the_query(self):
        base = self.now * 1000
        self.opencode_db([("ses_here000001", None, "/w/here", "here", base - 1000, None),
                          ("ses_there00001", None, "/w/there", "there", base - 500, None),
                          ("ses_herechild1", "ses_here000001", "/w/here", "child", base, None)])
        self.codex_db([(uid(1), "/w/here", "", 0, "Codex here", base - 1000, "", self.now),
                       (uid(2), "/w/there", "", 0, "Codex there", base, "", self.now)])
        result = sessions.list_sessions(None, self.now, only="/w/here")
        self.assertEqual(sorted((row["harness"], row["id"]) for row in result["sessions"]),
                         [("codex", uid(1)), ("opencode", "ses_here000001")])
        self.assertEqual(result["needsCwd"], [])

        # A healthy index with no thread for the folder is the answer; rollouts are not read.
        self.rollout(uid(10), "/w/here", "Rollout here", age_s=100)
        self.rollout(uid(11), "/w/there", "Rollout there", age_s=50)
        self.assertEqual(sessions.list_sessions("codex", self.now, only="/w/elsewhere")["sessions"], [])
        # Only an empty or absent index falls back to the rollout files.
        os.unlink(os.path.join(self.home, ".codex", "state_5.sqlite"))
        self.codex_db([])
        rows = sessions.list_sessions("codex", self.now, only="/w/here")["sessions"]
        self.assertEqual([row["id"] for row in rows], [uid(10)])
        os.unlink(os.path.join(self.home, ".codex", "state_5.sqlite"))
        rows = sessions.list_sessions("codex", self.now, only="/w/here")["sessions"]
        self.assertEqual([row["id"] for row in rows], [uid(10)])

    def test_gemini_and_pi_by_folder(self):
        root_a = self.mkdir("project-a")
        self.write(".gemini/tmp/project-a/.project_root", root_a)
        self.write(".gemini/projects.json", json.dumps({"projects": {"/w/b": "project-b"}}))
        user = [{"id": "1", "type": "user", "content": "Plan it"}]
        self.gemini_chat("project-a", uid(1), user, age_s=10)
        self.gemini_chat("project-b", uid(2), user, age_s=5)
        rows = sessions.list_sessions("gemini", self.now, only=root_a)["sessions"]
        self.assertEqual([row["id"] for row in rows], [uid(1)])

        stamp = "2026-09-15T14:18:58.355Z"
        record = {"type": "session", "version": 3, "id": uid(3), "timestamp": stamp, "cwd": "/w/pi"}
        self.write(sessions.pi_session_path("/w/pi", stamp, uid(3), self.home),
                   json.dumps(record) + "\n", mtime=self.now - 60)
        result = sessions.list_sessions(None, self.now, only="/w/pi/")
        self.assertEqual([(row["harness"], row["id"]) for row in result["sessions"]], [("pi", uid(3))])
        self.assertEqual(result["in"], "/w/pi")

    def test_cursor_rows_are_matched_on_their_folder(self):
        def rows(records, labels, cutoff_ms, only_id=None):
            return [sessions._row("cursor", uid(1), "here", "/w/here", self.now * 1000, None),
                    sessions._row("cursor", uid(2), "there", "/w/there", self.now * 1000, None)]
        self.patch(sessions, "_cursor_candidates", lambda budget, since: ([], {}))
        self.patch(sessions, "_cursor_rows", rows)
        listed = sessions.list_sessions("cursor", self.now, only="/w/here")["sessions"]
        self.assertEqual([row["id"] for row in listed], [uid(1)])
        self.assertEqual(len(sessions.list_sessions("cursor", self.now)["sessions"]), 2)

    def test_a_linked_folder_matches_sessions_recorded_by_its_real_path(self):
        real = self.mkdir("code/api")
        link = os.path.join(self.home, "api-link")
        os.symlink(real, link)
        self.transcript(sessions._claude_dir_name(real), uid(1), self.claude_head(real), age_s=60)
        rows = sessions.list_sessions("claude", self.now, only=link)["sessions"]
        self.assertEqual([row["id"] for row in rows], [uid(1)])

    def test_a_link_to_an_unclean_folder_lists_by_the_given_path_only(self):
        os.mkdir(os.fsencode(self.home) + b"/bad\xff")
        link = os.path.join(self.home, "bad-link")
        os.symlink(os.fsencode(self.home) + b"/bad\xff", link.encode())
        self.codex_db([(uid(1), link, "", 0, "By the link", self.now * 1000, "", self.now)])
        result = cli_scan.cmd_sessions(["--in", link], None)
        self.assertEqual([row["id"] for row in result["sessions"]], [uid(1)])

    def test_each_agent_gets_its_own_share_of_the_time(self):
        calls = []
        real_budget = sessions._Budget

        def budget(seconds, *rest):
            calls.append(seconds)
            return real_budget(seconds, *rest)
        self.patch(sessions, "_Budget", budget)
        sessions.list_sessions(None, self.now, only="/w/x")
        share = consts.SESSIONS_DEADLINE_S / len(consts.HARNESSES)
        self.assertEqual(calls[1:], [share] * len(consts.HARNESSES))

    def test_in_argv_and_refusals(self):
        result = cli_scan.cmd_sessions(["--harness", "gemini", "--in", "/w/x"], None)
        self.assertEqual((result["in"], result["counts"]), ("/w/x", {"gemini": 0}))
        self.assertIsNone(cli_scan.cmd_sessions(["--harness", "gemini"], None)["in"])
        for argv in (["--in"], ["--in", "relative"], ["--in", "/w/x", "--cwd", "/w/x"], ["--cwd", "/w/x", "--in", "/w/x"],
                     ["--in", "/w/x", "--harness", "claude"]):
            with self.assertRaises(ApError) as caught:
                cli_scan.cmd_sessions(argv, None)
            self.assertEqual(caught.exception.code, "bad_args")
            with self.assertRaises(main._ArgError):
                main.check_argv("sessions", argv)
        main.check_argv("sessions", ["--harness", "pi", "--in", "/w/x"])
        # The panel's form: {"in": ...} on stdin.
        main.check_argv("sessions", ["--stdin"])
        result = cli_scan.cmd_sessions(["--harness", "gemini", "--stdin"], {"in": "/w/x"})
        self.assertEqual(result["in"], "/w/x")
        for payload in ({"in": "/w/x", "cwd": "/w/x"}, {"in": "relative"}, {"in": 1}, {"where": "/w/x"}):
            with self.assertRaises(ApError, msg=payload) as caught:
                cli_scan.cmd_sessions(["--stdin"], payload)
            self.assertEqual(caught.exception.code, "bad_args")
        for value in ("relative", "/w/\x00", "/" + "d" * 2000):
            with self.assertRaises(ApError) as caught:
                sessions.list_sessions(None, self.now, only=value)
            self.assertEqual(caught.exception.code, "invalid_cwd")
        with self.assertRaises(ApError):
            sessions.list_sessions(None, self.now, cwd="/w/x", only="/w/x")


if __name__ == "__main__":
    unittest.main()
