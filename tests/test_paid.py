"""Tests for the paid-usage gate (bin/autopilot/paid.py).

Covers the Claude stream verdicts, the Codex login lines, the Gemini auth type, the OpenCode
billing classifier (verbose blocks, catalogue, project config), Pi auth check answers, the
Cursor config preflights, the pre-fire defer table and check_job's order, codes and notes.
Every test runs against a temporary HOME with tests/stubs/fake-cli standing in for the agent
CLIs (see tests/h5_support.py). No real CLI, no network, no model, no systemd.
"""

import json
import os
import sys
import unittest

sys.dont_write_bytecode = True
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import h5_support as S  # noqa: E402
from h5_support import DAY, H5Case  # noqa: E402
from autopilot import consts, models, paid  # noqa: E402
from autopilot.errors import ApError  # noqa: E402

V2_REASONS = set(consts.REASONS) | {
    "paid_blocked", "overage_blocked", "paid_defer", "paid_exhausted", "limit_full", "zen_billing",
    "limit_suspected", "stalled", "cursor_autorun_config", "cursor_network_config", "cursor_project_rules",
    "harness_gated", "monthly_limit", "quota_final"}
GATE_KEYS = ["billing", "code", "defer", "detail", "notes", "ok", "pending", "provider", "resetAtMs"]


class PaidCase(H5Case):
    def setUp(self):
        super().setUp()
        self.work = self.mkdir("proj")
        self.patch(consts, "GATED_HARNESSES", ())
        self.gemini_system = os.path.join(self.tmp, "etc-gemini-cli")
        self.patch(paid, "_GEMINI_SYSTEM_DIR", self.gemini_system)

    def job(self, harness_id, **extra):
        job = {"id": "0123456789abcdef", "harness": harness_id, "level": "plan", "allowPaid": False,
               "provider": None, "model": None,
               "target": {"mode": "new", "cwd": self.work, "sessionId": None, "sessionPath": None},
               "trigger": {"kind": "now", "fireAt": None, "delaySec": None, "marginSec": 120},
               "limits": {"maxTurns": 15, "budgetUsd": 5.0, "runtimeSec": 5400}}
        job.update(extra)
        return job

    def gate(self, job, phase="arm", usage=None, **kwargs):
        kwargs.setdefault("exec_prefix", self.exec_prefix())
        result = paid.check_job(job, phase=phase, now=self.now, usage=usage, **kwargs)
        self.assertEqual(sorted(result), GATE_KEYS)
        return result

    def trust(self, folder):
        self.write(".cursor/projects/%s/.workspace-trusted" % paid.cursor_trust_slug(os.path.realpath(folder)), "")

    def pi_ready(self, provider, auth_type="oauth"):
        self.answer(["auth", "check", "--provider", provider, "--json", "--no-refresh"],
                    stdout=json.dumps({"status": "ready", "provider": provider, "authType": auth_type}))


# --- Claude, Codex, Gemini ----------------------------------------------------------------

class ClaudeCodexGeminiTests(PaidCase):
    def test_claude_init_api_key_source_values(self):
        self.assertIsNone(paid.claude_init_verdict("none", False))
        for value in ("ANTHROPIC_API_KEY", "apiKeyHelper", "/login managed key", None, "", "None", 0):
            self.assertEqual(paid.claude_init_verdict(value, False), "paid", value)
            self.assertIsNone(paid.claude_init_verdict(value, True), value)

    def test_claude_overage_event_verdict(self):
        self.assertEqual(paid.claude_rate_event_verdict({"isUsingOverage": True, "status": "allowed_warning"},
                                                        False), "overage")
        for info in ({"isUsingOverage": False}, {}, {"isUsingOverage": "true"}, {"isUsingOverage": 1}, None, []):
            self.assertIsNone(paid.claude_rate_event_verdict(info, False), info)
        self.assertIsNone(paid.claude_rate_event_verdict({"isUsingOverage": True}, True))

    def test_codex_login_lines_table(self):
        table = {
            "Logged in using ChatGPT": "chatgpt",
            "Logged in using an API key": "api_key",
            "Logged in using access token": "access_token",
            "Logged in using personal access token": "personal_access_token",
            "Logged in using Amazon Bedrock API key": "bedrock_api_key",
            "Logged in using Amazon Bedrock AWS access keys": "bedrock_aws",
            "Not logged in": "not_logged_in",
            "Logged in using ChatGPT.\n": "chatgpt",
            "WARNING: config note\nLogged in using ChatGPT\n": "chatgpt",
            "Logged in using ChatGPT Enterprise": "api_key",
            "Logged in using a method added later": "api_key",
            "Not logged in\nLogged in using ChatGPT": "not_logged_in",
            "status unavailable": None,
            "Logged in": None,
            "": None,
        }
        for text, kind in table.items():
            self.assertEqual(paid.codex_login_kind(text), kind, text)
        self.assertIsNone(paid.codex_login_kind(None))

    def test_codex_probe_api_key_refused_off_allowed_on(self):
        job = self.job("codex")
        self.answer(["login", "status"], stderr="Logged in using an API key\n")
        off = self.gate(job)
        self.assertEqual((off["ok"], off["code"], off["notes"]), (False, "paid_blocked", ["subscription_only"]))
        on = self.gate(dict(job, allowPaid=True))
        self.assertEqual((on["ok"], on["code"], on["notes"]), (True, None, ["paid_on"]))

        self.answer(["login", "status"], stderr="Logged in using ChatGPT\n")
        chatgpt = self.gate(job)
        self.assertEqual((chatgpt["ok"], chatgpt["notes"], chatgpt["pending"]), (True, ["subscription_only"], False))
        self.assertEqual(paid.codex_login_probe(self.exec_prefix(), 10), "chatgpt")

        self.answer(["login", "status"], stderr="Not logged in\n", rc=1)
        self.assertEqual(self.gate(job)["code"], "not_logged_in")
        self.assertEqual(self.gate(dict(job, allowPaid=True))["code"], "not_logged_in")
        # A logged-in line with a failing exit is not trusted.
        self.answer(["login", "status"], stderr="Logged in using ChatGPT\n", rc=3)
        self.assertIsNone(paid.codex_login_probe(self.exec_prefix(), 10))

        calls = self.calls()
        self.assertTrue(calls)
        self.assertTrue(all(c[0] == "codex" and c[1] == ["login", "status"] for c in calls))
        self.assertTrue(all(set(c[2]) <= set(self.fake_env("codex", "plan")) | {"LC_CTYPE"} for c in calls))

        # A late answer: preview is pending, arm cannot confirm ChatGPT while off.
        self.patch(paid, "_LOGIN_DEADLINE_S", 1.0)
        self.answer(["login", "status"], stderr="Logged in using ChatGPT\n", sleep=5)
        preview = self.gate(job, phase="preview")
        self.assertEqual((preview["ok"], preview["pending"]), (True, True))
        self.assertEqual(self.gate(job)["code"], "not_logged_in")
        self.assertTrue(self.gate(dict(job, allowPaid=True))["ok"])
        self.assertEqual(self.gate(job, exec_prefix=None, phase="preview")["pending"], True)

    def test_gemini_selected_type_table(self):
        settings = ".gemini/settings.json"
        self.assertIsNone(paid.gemini_selected_type(self.home))
        table = {"oauth-personal": None, "gemini-api-key": "paid_blocked", "vertex-ai": "paid_blocked",
                 "gateway": "paid_blocked", "added-later": "paid_blocked",
                 "cloud-shell": None, "compute-default-credentials": None}
        for value, verdict in table.items():
            self.write(settings, {"security": {"auth": {"selectedType": value}}, "model": {"name": "pro"}})
            self.assertEqual(paid.gemini_selected_type(self.home), value)
            self.assertEqual(paid.gemini_verdict(value, False), verdict, value)
            self.assertIsNone(paid.gemini_verdict(value, True))

        self.write(settings, {"security": {"auth": {"selectedType": "gemini-api-key"}}})
        job = self.job("gemini")
        off = self.gate(job, exec_prefix=None)
        self.assertEqual((off["code"], off["notes"]), ("paid_blocked", ["subscription_only"]))
        on = self.gate(dict(job, allowPaid=True), exec_prefix=None)
        self.assertEqual((on["ok"], on["notes"]), (True, ["paid_on"]))
        self.write(settings, {"security": {"auth": {"selectedType": "oauth-personal"}}})
        self.assertTrue(self.gate(job, exec_prefix=None)["ok"])

        # Gemini strips comments before parsing: a commented API-key file is still an API key.
        self.write(settings, '{\n  // switched to the team key\n  "security": {"auth": {"selectedType": "gemini-api-key"}}\n}\n')
        self.assertEqual(paid.gemini_selected_type(self.home), "gemini-api-key")
        self.assertEqual(self.gate(job, exec_prefix=None)["code"], "paid_blocked")
        # The pre-migration top-level key still names the type.
        self.write(settings, {"selectedAuthType": "gemini-api-key"})
        self.assertEqual(self.gate(job, exec_prefix=None)["code"], "paid_blocked")

        # No type anywhere: Gemini would take GEMINI_API_KEY from a .env file, so nothing is confirmed.
        for body in (json.dumps({"security": {}}), json.dumps({"model": {"name": "pro"}}), json.dumps({})):
            self.write(settings, body)
            self.write(".env", "GEMINI_API_KEY=placeholder\n")
            self.assertIsNone(paid.gemini_selected_type(self.home), body)
            refused = self.gate(job, exec_prefix=None)
            self.assertEqual((refused["ok"], refused["code"]), (False, "not_logged_in"), body)
            self.assertTrue(self.gate(dict(job, allowPaid=True), exec_prefix=None)["ok"])
        os.remove(self.path(settings))
        self.assertEqual(self.gate(job, exec_prefix=None)["code"], "not_logged_in")

        # A file that exists but cannot be parsed or vouched for is treated as paid.
        for body in ("{not json", json.dumps({"security": {"auth": {"selectedType": 42}}}), json.dumps([1, 2])):
            self.write(settings, body)
            self.assertEqual(paid.gemini_selected_type(self.home), "unreadable", body)
            self.assertEqual(self.gate(job, exec_prefix=None)["code"], "paid_blocked", body)
        target = self.write("elsewhere/settings.json", {"security": {"auth": {"selectedType": "oauth-personal"}}})
        os.remove(self.path(settings))
        os.symlink(target, self.path(settings))
        self.assertEqual(paid.gemini_selected_type(self.home), "unreadable")
        self.assertEqual(self.gate(job, exec_prefix=None)["code"], "paid_blocked")
        os.remove(self.path(settings))
        self.write(settings, " " * (64 * 1024 + 1))
        self.assertEqual(paid.gemini_verdict(paid.gemini_selected_type(self.home), False), "paid_blocked")

    def test_gemini_workspace_and_system_settings_merge(self):
        settings = ".gemini/settings.json"
        job = self.job("gemini")
        workspace = os.path.join(self.work, ".gemini", "settings.json")
        self.write(settings, {"security": {"auth": {"selectedType": "oauth-personal"}}})
        self.assertTrue(self.gate(job, exec_prefix=None)["ok"])

        # The folder's own settings override the user's type.
        os.makedirs(os.path.dirname(workspace))
        with open(workspace, "w") as handle:
            json.dump({"security": {"auth": {"selectedType": "gemini-api-key"}}}, handle)
        self.assertEqual(paid.gemini_selected_type(self.home, self.work), "gemini-api-key")
        self.assertEqual(self.gate(job, exec_prefix=None)["code"], "paid_blocked")
        # A workspace Google sign-in does not hide a user API key (an untrusted folder's file is ignored).
        with open(workspace, "w") as handle:
            json.dump({"security": {"auth": {"selectedType": "oauth-personal"}}}, handle)
        self.write(settings, {"security": {"auth": {"selectedType": "vertex-ai"}}})
        self.assertEqual(self.gate(job, exec_prefix=None)["code"], "paid_blocked")
        # A workspace type alone confirms the sign-in; the home folder is never read twice as a workspace.
        os.remove(self.path(settings))
        self.assertTrue(self.gate(job, exec_prefix=None)["ok"])
        self.assertEqual(len(paid.gemini_settings_paths(self.home, self.home)), 3)
        self.assertEqual(len(paid.gemini_settings_paths(self.home, self.work)), 4)
        os.remove(workspace)

        # System settings (and system defaults) are merged too and may be owned by root.
        self.write(settings, {"security": {"auth": {"selectedType": "oauth-personal"}}})
        os.makedirs(self.gemini_system)
        for name in ("settings.json", "system-defaults.json"):
            path = os.path.join(self.gemini_system, name)
            with open(path, "w") as handle:
                handle.write('{ /* fleet */ "security": {"auth": {"selectedType": "gemini-api-key"}} }')
            self.assertEqual(self.gate(job, exec_prefix=None)["code"], "paid_blocked", name)
            os.remove(path)
        self.assertTrue(self.gate(job, exec_prefix=None)["ok"])


# --- OpenCode classifier ------------------------------------------------------------------

class OpenCodeTests(PaidCase):
    def catalogue(self, document, mtime=None):
        path = self.write(".cache/opencode/models.json", document, mtime=mtime)
        return path

    def test_opencode_verbose_parser_7_free_fixture(self):
        text = S.verbose_text(S.seven_free_blocks())
        parsed = paid.parse_verbose_blocks(text)
        self.assertEqual(sorted(parsed), sorted("opencode/" + m for m in S.FREE_IDS))
        pickle = parsed["opencode/big-pickle"]
        self.assertEqual(pickle["cost"], {"input": 0.0, "output": 0.0, "cache.read": 0.0, "cache.write": 0.0})
        self.assertEqual((pickle["status"], pickle["name"]), ("active", "Big Pickle"))
        noisy = "Using cached catalogue\n" + text + "opencode/broken-block\n{not json\n"
        self.assertEqual(sorted(paid.parse_verbose_blocks(noisy)), sorted(parsed))
        self.assertEqual(paid.parse_verbose_blocks(""), {})
        self.assertEqual(paid.parse_verbose_blocks(None), {})

    def test_opencode_verbose_parser_69_keyed_fixture(self):
        blocks = S.keyed_69_blocks()
        tiered_cost = {"input": 0, "output": 0, "cache": {"read": 0, "write": 0},
                       "tiers": [{"input": 1.25, "output": 5, "tier": {"type": "context", "size": 200000}}]}
        blocks.append(("opencode", "tiered-model", S.verbose_model("opencode", "tiered-model", tiered_cost)))
        parsed = paid.parse_verbose_blocks(S.verbose_text(blocks))
        self.assertEqual(len(parsed), 70)
        charged = sorted(k for k, v in parsed.items() if any(x != 0 for x in v["cost"].values()))
        self.assertEqual(len(charged), 63)
        tiered = parsed["opencode/tiered-model"]["cost"]
        self.assertEqual(tiered["tiers.0.input"], 1.25)
        self.assertNotIn("tiers.0.tier.size", tiered)
        many = [("opencode", "m-%04d" % n, S.verbose_model("opencode", "m-%04d" % n, S.zero_cost_verbose()))
                for n in range(1005)]
        self.assertEqual(len(paid.parse_verbose_blocks(S.verbose_text(many))), 1000)

    def test_opencode_catalogue_join_tiers_deprecated(self):
        zero = S.zero_cost_catalogue()
        document = S.catalogue_document([
            S.cat_model("big-pickle", zero, name="Big Pickle"),
            S.cat_model("hy3-free", zero, status="deprecated"),
            S.cat_model("tiered", dict(zero, tiers=[{"input": 0.5, "output": 1, "tier": {"type": "context",
                                                                                         "size": 200000}}])),
            S.cat_model("over-200k", dict(zero, context_over_200k={"input": 2, "output": 8})),
        ], go_models=[S.cat_model("muse-spark-1.3-contributor", {"input": 0.10, "output": 0.20})])
        path = self.catalogue(document)
        catalogue, mtime = paid.read_catalogue(self.home)
        self.assertEqual(sorted(catalogue), ["opencode-go/muse-spark-1.3-contributor", "opencode/big-pickle",
                                             "opencode/hy3-free", "opencode/over-200k", "opencode/tiered"])
        self.assertEqual(mtime, int(os.stat(path).st_mtime))
        self.assertEqual(catalogue["opencode/hy3-free"]["status"], "deprecated")
        verbose = paid.parse_verbose_blocks(S.verbose_text(
            [("opencode", m, S.verbose_model("opencode", m, S.zero_cost_verbose()))
             for m in ("big-pickle", "hy3-free", "tiered", "over-200k")]))

        def classify(model):
            return paid.classify_opencode(model, verbose, catalogue, mtime, self.now, False, None)

        self.assertEqual(classify("opencode/big-pickle"), "zen_free")
        self.assertEqual(classify("opencode/hy3-free"), "unknown")
        self.assertEqual(classify("opencode/tiered"), "zen_paid")
        self.assertEqual(classify("opencode/over-200k"), "zen_paid")

        self.patch(paid, "_CATALOGUE_CAP", 64)
        self.assertEqual(paid.read_catalogue(self.home), (None, None))
        self.patch(paid, "_CATALOGUE_CAP", 8 * 1024 * 1024)
        self.write(".cache/opencode/models.json", "{not json")
        self.assertEqual(paid.read_catalogue(self.home), (None, None))
        target = self.write("elsewhere/models.json", document)
        os.remove(path)
        os.symlink(target, path)
        self.assertEqual(paid.read_catalogue(self.home), (None, None))

    def test_opencode_big_pickle_free_without_suffix(self):
        self.catalogue(S.free_catalogue(extra=[S.cat_model("limited-offer-free", {"input": 0.3, "output": 1})]))
        catalogue, mtime = paid.read_catalogue(self.home)
        verbose = paid.parse_verbose_blocks(S.verbose_text(S.seven_free_blocks()))

        def classify(model):
            return paid.classify_opencode(model, verbose, catalogue, mtime, self.now, False, None)

        self.assertEqual(classify("opencode/big-pickle"), "zen_free")
        self.assertEqual(classify("opencode/nemotron-3-ultra-free"), "zen_free")
        self.assertEqual(classify("opencode/limited-offer-free"), "zen_paid")
        self.assertEqual(classify("opencode/renamed-model-free"), "unknown")
        self.assertEqual(paid.classify_opencode("opencode/big-pickle", verbose, None, None, self.now, False, None),
                         "unknown")
        self.assertEqual(paid.classify_opencode("opencode/big-pickle", None, catalogue, mtime, self.now, False, None),
                         "unknown")

    def test_opencode_go_contributor_name_trap(self):
        go_cost = {"input": 0.10, "output": 0.20}
        self.catalogue(S.free_catalogue(go_models=[S.cat_model("muse-spark-1.3-contributor", go_cost)]))
        catalogue, mtime = paid.read_catalogue(self.home)
        verbose = paid.parse_verbose_blocks(S.verbose_text(S.seven_free_blocks() + [
            ("opencode-go", "muse-spark-1.3-contributor",
             S.verbose_model("opencode-go", "muse-spark-1.3-contributor", go_cost))]))

        def classify(model, default=None):
            return paid.classify_opencode(model, verbose, catalogue, mtime, self.now, False, default)

        self.assertEqual(classify("opencode-go/muse-spark-1.3-contributor"), "go")
        self.assertEqual(classify("opencode/muse-spark-1.3-contributor"), "unknown")
        self.assertEqual(classify("opencode/muse-spark-1.3-contributor-free"), "zen_free")
        self.assertEqual(classify("anthropic/claude-fable-5"), "anthropic")
        self.assertEqual(classify("openrouter/anthropic/claude-3.7"), "other")
        self.assertEqual(classify("no-provider"), "unknown")
        self.assertEqual(classify(None), "unknown")
        self.assertEqual(classify(None, default="opencode-go/muse-spark-1.3-contributor"), "go")

    def test_opencode_stale_catalogue_unknown(self):
        catalogue = {"opencode/big-pickle": {"cost": {"input": 0.0, "output": 0.0}, "status": None, "name": None}}
        verbose = paid.parse_verbose_blocks(S.verbose_text(S.seven_free_blocks()))
        for mtime, expected in ((self.now - 8 * DAY, "unknown"), (self.now - 6 * DAY, "zen_free"),
                                (self.now + 3600, "unknown"), (None, "unknown")):
            self.assertEqual(paid.classify_opencode("opencode/big-pickle", verbose, catalogue, mtime, self.now,
                                                    False, None), expected, mtime)
        self.catalogue(S.free_catalogue(), mtime=self.now - 10 * DAY)
        self.answer(["models", "opencode", "--verbose"], stdout=S.verbose_text(S.seven_free_blocks()))
        found = paid.opencode_billing("opencode/big-pickle", self.work, now=self.now, allow_probe=True, deadline_s=10)
        self.assertEqual(found, {"billing": "unknown", "resolvedModel": "opencode/big-pickle", "pending": False})

    def test_opencode_project_config_unknown(self):
        self.mkdir("repo/.git")
        work = self.mkdir("repo/sub/work")
        self.write(".config/opencode/opencode.jsonc",
                   '{\n  // the default model\n  "model": "opencode/big-pickle", /* inline */\n'
                   '  "share": "https://example.invalid//x",\n  "theme": "system",\n}\n')
        self.assertEqual(paid.opencode_default_model(self.home), "opencode/big-pickle")
        self.write(".config/opencode/config.json", {"model": "anthropic/claude-fable-5"})
        self.assertEqual(paid.opencode_default_model(self.home), "opencode/big-pickle")
        self.assertFalse(paid.opencode_project_config(work))

        self.catalogue(S.free_catalogue())
        catalogue, mtime = paid.read_catalogue(self.home)
        verbose = paid.parse_verbose_blocks(S.verbose_text(S.seven_free_blocks()))
        self.assertEqual(paid.classify_opencode(None, verbose, catalogue, mtime, self.now, False,
                                                "opencode/big-pickle"), "zen_free")
        self.assertEqual(paid.classify_opencode(None, verbose, catalogue, mtime, self.now, True,
                                                "opencode/big-pickle"), "unknown")

        for rel in ("repo/opencode.json", "repo/sub/opencode.jsonc", "repo/sub/work/.opencode/x"):
            path = self.write(rel, "{}")
            self.assertTrue(paid.opencode_project_config(work), rel)
            found = paid.opencode_billing(None, work, now=self.now, allow_probe=True, deadline_s=5)
            self.assertEqual(found, {"billing": "unknown", "resolvedModel": None, "pending": False})
            os.remove(path)
            if rel.endswith("/.opencode/x"):
                os.rmdir(os.path.dirname(path))
        # A config above the repository root is not the project's.
        above = self.write("opencode.json", "{}")
        self.assertFalse(paid.opencode_project_config(work))
        os.remove(above)
        self.assertTrue(paid.opencode_project_config("relative/path"))

        self.answer(["models", "opencode", "--verbose"], stdout=S.verbose_text(S.seven_free_blocks()))
        found = paid.opencode_billing(None, work, now=self.now, allow_probe=True, deadline_s=10)
        self.assertEqual(found, {"billing": "zen_free", "resolvedModel": "opencode/big-pickle", "pending": False})

    def test_opencode_off_refuses_zen_paid_and_anthropic(self):
        self.catalogue(S.free_catalogue(extra=[S.cat_model("paid-model-01", {"input": 1.5, "output": 2})]))
        self.answer(["models", "opencode", "--verbose"], stdout=S.verbose_text(S.keyed_69_blocks()))
        job = self.job("opencode", model="opencode/paid-model-01")
        gate = self.gate(job)
        self.assertEqual((gate["ok"], gate["code"], gate["billing"], gate["notes"]),
                         (False, "paid_zen", "zen_paid", ["subscription_only"]))
        self.assertEqual(self.calls()[-1][1], ["models", "opencode", "--verbose"])
        self.assertEqual(paid.REASON_FOR_CODE["paid_zen"], "paid_blocked")
        self.assertEqual(self.gate(job, phase="prefire")["code"], "paid_zen")

        count = len(self.calls())
        claude = self.job("opencode", model="anthropic/claude-fable-5")
        gate = self.gate(claude)
        self.assertEqual((gate["ok"], gate["code"], gate["billing"], gate["notes"]),
                         (False, "paid_opencode_claude", "anthropic", ["subscription_only"]))
        self.assertEqual(len(self.calls()), count)

        for model, billing in (("opencode/paid-model-01", "zen_paid"), ("anthropic/claude-fable-5", "anthropic")):
            on = self.gate(self.job("opencode", model=model, allowPaid=True))
            self.assertEqual((on["ok"], on["code"], on["billing"], on["notes"]), (True, None, billing, ["paid_on"]))

    def test_opencode_off_allows_zen_free_and_go_with_notes(self):
        self.catalogue(S.free_catalogue())
        self.answer(["models", "opencode", "--verbose"], stdout=S.verbose_text(S.seven_free_blocks()))
        midnight_ms = (self.now // DAY + 1) * DAY * 1000
        free = self.gate(self.job("opencode", model="opencode/big-pickle"))
        self.assertEqual((free["ok"], free["billing"], free["notes"], free["resetAtMs"]),
                         (True, "zen_free", ["subscription_only", "zen_free"], midnight_ms))
        go = self.gate(self.job("opencode", model="opencode-go/muse-spark-1.3-contributor"))
        self.assertEqual((go["ok"], go["billing"], go["notes"], go["resetAtMs"]),
                         (True, "go", ["subscription_only", "go_plan"], None))
        other = self.gate(self.job("opencode", model="openai/gpt-5"))
        self.assertEqual((other["billing"], other["notes"]), ("other", ["subscription_only", "opencode_provider"]))
        default = self.gate(self.job("opencode"))
        self.assertEqual((default["ok"], default["billing"], default["notes"]),
                         (True, "unknown", ["subscription_only", "opencode_provider"]))
        on_free = self.gate(self.job("opencode", model="opencode/big-pickle", allowPaid=True))
        self.assertEqual(on_free["notes"], ["paid_on", "zen_free"])
        on_go = self.gate(self.job("opencode", model="opencode-go/muse-spark-1.3-contributor", allowPaid=True))
        self.assertEqual(on_go["notes"], ["paid_on", "go_plan"])


# --- Pi ------------------------------------------------------------------------------

class PiTests(PaidCase):
    def test_pi_auth_check_json_variants_exit_0_1_2(self):
        table = [
            (0, {"status": "ready", "provider": "anthropic", "authType": "oauth"}, ("ready", "anthropic", "oauth")),
            (0, {"status": "ready", "provider": "openai", "authType": "api_key"}, ("ready", "openai", "api_key")),
            (0, {"status": "ready", "provider": "google", "authType": "api_key"}, ("ready", "google", "api_key")),
            (1, {"status": "not_ready", "provider": "openai-codex", "reason": "credentials_not_configured"},
             ("not_ready", "openai-codex", None)),
            (1, {"status": "not_ready", "provider": "nosuch", "reason": "provider_not_found"},
             ("not_ready", "nosuch", None)),
            (2, {"status": "invalid", "provider": "nosuch/zzz", "reason": "invalid_state"}, ("invalid", None, None)),
            (0, {"status": "ready", "provider": "xai", "authType": "oauth", "extra": "dropped"}, ("ready", "xai", "oauth")),
        ]
        for rc, document, expected in table:
            parsed = paid.pi_auth_parse(rc, json.dumps(document).encode())
            self.assertEqual((parsed["status"], parsed["provider"], parsed["authType"]), expected, document)
            self.assertEqual(sorted(parsed), ["authType", "provider", "status"])
        self.assertEqual(paid.pi_auth_parse(2, b"")["status"], "invalid")
        ready = {"status": "ready", "provider": "openai-codex", "authType": "oauth"}
        garbage = [(1, b"not_ready\n"), (0, b"ready\n"), (0, b"[1, 2]"), (0, b""),
                   (0, json.dumps(dict(ready, authType="token")).encode()),
                   (0, json.dumps(dict(ready, provider="Bad Provider")).encode()),
                   (1, json.dumps(ready).encode()),
                   (0, json.dumps({"status": "not_ready", "provider": "x"}).encode()),
                   (None, json.dumps(ready).encode()),
                   (0, json.dumps(dict(ready, pad="x" * 4096)).encode()),
                   (0, "ÿ".encode("latin-1"))]
        for rc, stdout in garbage:
            self.assertEqual(paid.pi_auth_parse(rc, stdout)["status"], "garbage", (rc, stdout[:40]))

    def test_pi_verdict_table(self):
        def ready(provider, auth_type):
            return {"status": "ready", "provider": provider, "authType": auth_type}

        table = [
            ("openai-codex", "oauth", (None, None, ["subscription_only"])),
            ("anthropic", "oauth", ("paid_pi_claude", None, ["subscription_only"])),
            ("anthropic", "api_key", ("paid_pi_key", {"provider": "anthropic"}, ["subscription_only"])),
            ("github-copilot", "oauth", (None, None, ["subscription_only", "pi_subscription"])),
            ("xai", "oauth", (None, None, ["subscription_only", "pi_subscription"])),
            ("kimi-coding", "oauth", (None, None, ["subscription_only", "pi_subscription"])),
            ("openrouter", "oauth", ("paid_pi_key", {"provider": "openrouter"}, ["subscription_only"])),
            ("radius", "oauth", ("paid_pi_key", {"provider": "radius"}, ["subscription_only"])),
            ("openai", "api_key", ("paid_pi_key", {"provider": "openai"}, ["subscription_only"])),
            ("opencode", "api_key", ("paid_pi_key", {"provider": "opencode"}, ["subscription_only"])),
            ("zai", "oauth", (None, None, ["subscription_only", "pi_subscription"])),
        ]
        for provider, auth_type, expected in table:
            verdict = paid.pi_verdict(provider, ready(provider, auth_type), False)
            self.assertEqual((verdict["code"], verdict["detail"], verdict["notes"]), expected, provider)
            on = paid.pi_verdict(provider, ready(provider, auth_type), True)
            self.assertEqual((on["code"], on["detail"], on["notes"]), (None, None, ["paid_on"]), provider)
        for allow in (False, True):
            self.assertEqual(paid.pi_verdict("xai", {"status": "not_ready"}, allow)["code"], "not_logged_in")
            for status in ("invalid", "garbage"):
                self.assertEqual(paid.pi_verdict("xai", {"status": status}, allow)["code"], "pi_auth_invalid")
            self.assertEqual(paid.pi_verdict("xai", None, allow)["code"], "pi_auth_invalid")

    def test_pi_auth_probe_argv_exact(self):
        argv = ["auth", "check", "--provider", "openai-codex", "--json", "--no-refresh"]
        self.pi_ready("openai-codex")
        parsed = paid.pi_auth_probe(self.exec_prefix(), "openai-codex", 10)
        self.assertEqual(parsed, {"status": "ready", "provider": "openai-codex", "authType": "oauth",
                                  "timedOut": False})
        calls = self.calls()
        self.assertEqual([(c[0], c[1]) for c in calls], [("pi", argv)])
        self.assertNotIn("--" + "credentials", calls[0][1])

        self.answer(argv, stdout=json.dumps({"status": "ready", "provider": "anthropic", "authType": "oauth"}))
        self.assertEqual(paid.pi_auth_probe(self.exec_prefix(), "openai-codex", 10)["status"], "garbage")
        count = len(self.calls())
        for bad in ("Anthropic", "a b", "-x", "", None, "x" * 41, "../x", "--json"):
            self.assertEqual(paid.pi_auth_probe(self.exec_prefix(), bad, 10)["status"], "garbage", bad)
        self.assertEqual(paid.pi_auth_probe(["relative/pi"], "xai", 10)["status"], "garbage")
        self.assertEqual(len(self.calls()), count)

        self.answer(argv, stdout=json.dumps({"status": "ready", "provider": "openai-codex", "authType": "oauth",
                                             "pad": "x" * 8192}))
        self.assertEqual(paid.pi_auth_probe(self.exec_prefix(), "openai-codex", 10)["status"], "garbage")

        self.patch(paid, "_PI_AUTH_DEADLINE_S", 1.0)
        self.pi_ready("openai-codex")
        self.answer(argv, stdout=json.dumps({"status": "ready", "provider": "openai-codex", "authType": "oauth"}),
                    sleep=5)
        late = paid.pi_auth_probe(self.exec_prefix(), "openai-codex", 10)
        self.assertEqual((late["status"], late["timedOut"]), ("garbage", True))
        job = self.job("pi", provider="openai-codex", model="gpt-5.5")
        preview = self.gate(job, phase="preview")
        self.assertEqual((preview["ok"], preview["pending"], preview["provider"]), (True, True, "openai-codex"))
        self.assertEqual(self.gate(job)["code"], "pi_auth_invalid")


# --- Cursor preflights ---------------------------------------------------------------------

class CursorTests(PaidCase):
    def setUp(self):
        super().setUp()
        self.env = {"HOME": self.home, "XDG_CONFIG_HOME": self.home + "/.config"}

    def test_cursor_cli_config_autorun_network(self):
        verdict = paid.cursor_cli_config_verdict
        self.assertEqual(verdict({"approvalMode": S.UNRESTRICTED}), "cursor_autorun_config")
        self.assertEqual(verdict({"sandbox": {"networkAccess": "allow_all"}}), "cursor_network_config")
        self.assertEqual(verdict({"approvalMode": S.UNRESTRICTED, "sandbox": {"networkAccess": "allow_all"}}),
                         "cursor_autorun_config")
        for fine in ({}, {"approvalMode": "allowlist"}, {"approvalMode": S.AUTO_REVIEW},
                     {"sandbox": {"networkAccess": "allowlist"}}, {"sandbox": "allow_all"}, None, []):
            self.assertIsNone(verdict(fine), fine)

        self.assertEqual(paid.cursor_config_dir(self.env), self.home + "/.config/cursor")
        self.assertEqual(paid.cursor_config_dir({"HOME": self.home}), self.home + "/.cursor")
        self.assertEqual(paid.cursor_config_dir({"HOME": self.home, "XDG_CONFIG_HOME": "relative"}),
                         self.home + "/.cursor")
        self.assertEqual(paid.cursor_config_dir({"HOME": self.home, "XDG_CONFIG_HOME": "/x\n"}), self.home + "/.cursor")

        self.trust(self.work)
        self.assertIsNone(paid.cursor_preflight(self.work, self.home, self.env))
        config = ".config/cursor/cli-config.json"
        self.write(config, {"version": 1, "approvalMode": S.UNRESTRICTED})
        self.assertEqual(paid.cursor_preflight(self.work, self.home, self.env), "cursor_autorun_config")
        # The other folder is not the one this environment selects.
        self.assertIsNone(paid.cursor_preflight(self.work, self.home, {"HOME": self.home}))
        self.write(".cursor/cli-config.json", {"approvalMode": S.UNRESTRICTED})
        self.assertEqual(paid.cursor_preflight(self.work, self.home, {"HOME": self.home}), "cursor_autorun_config")
        self.write(config, {"approvalMode": "allowlist", "sandbox": {"mode": "enabled", "networkAccess": "allow_all"}})
        self.assertEqual(paid.cursor_preflight(self.work, self.home, self.env), "cursor_network_config")
        self.write(config, {"approvalMode": S.AUTO_REVIEW, "permissions": {S.ALLOW: ["Shell(ls)"]}})
        self.assertIsNone(paid.cursor_preflight(self.work, self.home, self.env))
        for broken in ("{broken", " " * (256 * 1024 + 1)):
            self.write(config, broken)
            self.assertEqual(paid.cursor_preflight(self.work, self.home, self.env), "cursor_autorun_config")

    def test_cursor_project_rules_walk_and_claude_allow(self):
        self.mkdir("code/repo/.git")
        work = self.mkdir("code/repo/pkg/app")
        self.trust(work)
        self.assertIsNone(paid.cursor_preflight(work, self.home, self.env))
        for rel in ("code/repo/.cursor/cli.json", "code/repo/pkg/.cursor/cli.json", "code/repo/pkg/app/.cursor/cli.json"):
            path = self.write(rel, "{}")
            self.assertEqual(paid.cursor_preflight(work, self.home, self.env), "cursor_project_rules", rel)
            os.remove(path)
        above = self.write("code/.cursor/cli.json", "{}")
        self.assertIsNone(paid.cursor_preflight(work, self.home, self.env))
        os.remove(above)

        settings = "code/repo/.claude/settings.json"
        for body, expected in (({"permissions": {S.ALLOW: ["Bash(ls)"]}}, "cursor_project_rules"),
                               ({"permissions": {S.ALLOW: []}, "hooks": {}}, None),
                               ({"permissions": {"deny": ["Bash"]}}, None),
                               ("{broken", "cursor_project_rules")):
            self.write(settings, body)
            self.assertEqual(paid.cursor_preflight(work, self.home, self.env), expected, body)
        os.remove(self.path(settings))
        self.write("code/repo/pkg/app/.claude/settings.json", {"permissions": {S.ALLOW: ["Bash(ls)"]}})
        self.assertIsNone(paid.cursor_preflight(work, self.home, self.env))

        plain = self.mkdir("plain/deep")
        self.trust(plain)
        self.write("plain/.cursor/cli.json", "{}")
        self.assertIsNone(paid.cursor_preflight(plain, self.home, self.env))
        self.write("plain/deep/.cursor/cli.json", "{}")
        self.assertEqual(paid.cursor_preflight(plain, self.home, self.env), "cursor_project_rules")

        # The user's own ~/.claude/settings.json is accepted, even with HOME as the repository root.
        self.mkdir(".git")
        notes = self.mkdir("notes")
        self.trust(notes)
        self.write(".claude/settings.json", {"permissions": {S.ALLOW: ["Bash(git status)"]}})
        self.assertIsNone(paid.cursor_preflight(notes, self.home, self.env))

        allow = paid.claude_project_allow_nonempty
        self.assertTrue(allow({"permissions": {S.ALLOW: ["x"]}}))
        self.assertTrue(allow({"permissions": {S.ALLOW: "Bash"}}))
        for value in ({}, None, {"permissions": None}, {"permissions": {S.ALLOW: []}}, []):
            self.assertFalse(allow(value), value)

    def test_cursor_trust_slug_and_ancestor_rule(self):
        slug = paid.cursor_trust_slug
        self.assertEqual(slug("/home/u/My Proj.v2/"), "home-u-My-Proj-v2")
        self.assertEqual(slug("/home/u/a--b__c"), "home-u-a-b-c")
        self.assertEqual(slug("/srv/zażółć/x"), "srv-za-x")

        work = os.path.realpath(self.mkdir("src/team/app/sub"))
        home = os.path.realpath(self.home)
        self.assertFalse(paid.cursor_trusted(work, home))
        self.trust(work)
        self.assertTrue(paid.cursor_trusted(work, home))
        os.remove(self.path(".cursor/projects/%s/.workspace-trusted" % slug(work)))
        self.trust(os.path.join(home, "src", "team"))
        self.assertTrue(paid.cursor_trusted(work, home))
        self.assertEqual(paid.cursor_preflight(work, home, self.env), None)
        os.remove(self.path(".cursor/projects/%s/.workspace-trusted" % slug(os.path.join(home, "src", "team"))))
        self.mkdir(".cursor/projects/%s/.workspace-trusted" % slug(work))
        self.assertFalse(paid.cursor_trusted(work, home))
        self.assertEqual(paid.cursor_preflight(work, home, self.env), "cursor_untrusted")
        self.assertEqual(paid.REASON_FOR_CODE["cursor_untrusted"], "untrusted")

        trusted = set()
        self.patch(paid, "_trust_marker", lambda _home, path: slug(path) in trusted)
        trusted.update({"home-u", "home"})
        self.assertFalse(paid.cursor_trusted("/home/u/a/b", "/home/u"))
        trusted.add("home-u-a")
        self.assertTrue(paid.cursor_trusted("/home/u/a/b", "/home/u"))
        trusted.clear()
        trusted.add("srv")
        self.assertFalse(paid.cursor_trusted("/srv/x", "/home/u"))
        trusted.add("srv-x")
        self.assertTrue(paid.cursor_trusted("/srv/x", "/home/u"))
        trusted.clear()
        trusted.add("root-a")
        self.assertFalse(paid.cursor_trusted("/root/a/b", "/root"))
        trusted.add("root-a-b")
        self.assertTrue(paid.cursor_trusted("/root/a/b", "/root"))

    def test_cursor_preflight_opens_only_config_paths(self):
        self.mkdir("r/.git")
        work = self.mkdir("r/w")
        self.trust(work)
        config = self.write(".config/cursor/cli-config.json", {"approvalMode": "allowlist"})
        settings = self.write("r/.claude/settings.json", {"permissions": {}})
        for rel in (".config/cursor/auth.json", ".config/cursor/chats/abc/def/store.db", ".cursor/projects/x/worker.log",
                    "r/.claude/settings.local.json", ".claude/settings.json"):
            self.write(rel, "{}")
        opened = []
        real_open = os.open

        def recording_open(path, *args, **kwargs):
            opened.append(path if isinstance(path, int) else os.fsdecode(path))
            return real_open(path, *args, **kwargs)

        self.patch(os, "open", recording_open)
        verdict = paid.cursor_preflight(work, self.home, self.env)
        setattr(os, "open", real_open)
        self.assertIsNone(verdict)
        self.assertEqual(sorted(opened), sorted([config, os.path.realpath(settings)]))


# --- pre-fire defer ------------------------------------------------------------------------

class DeferTests(PaidCase):
    def test_paid_defer_claude_session_weekly_full(self):
        now = self.now
        session = S.window("session", 1.0, now + 3600, "5-hour")
        weekly = S.window("weekly", 0.4, now + 3 * DAY, "Weekly")
        job = self.job("claude")
        usage = S.usage_of(S.provider("claude", [session, weekly]))
        self.assertEqual(paid.paid_defer(job, usage, now),
                         {"action": "rearm", "fireAt": now + 3600 + 120, "reason": "window_exhausted",
                          "resetEpoch": now + 3600})
        weekly["percent"] = 1.3
        self.assertEqual(paid.paid_defer(job, usage, now),
                         {"action": "rearm", "fireAt": now + 3 * DAY + 120, "reason": "weekly_exhausted",
                          "resetEpoch": now + 3 * DAY})
        at_job = self.job("claude", trigger={"kind": "at", "fireAt": now + 60, "delaySec": None, "marginSec": 300})
        self.assertEqual(paid.paid_defer(at_job, usage, now)["fireAt"], now + 3 * DAY + 300)
        session["percent"], weekly["percent"] = 0.99, 0.5
        self.assertIsNone(paid.paid_defer(job, usage, now))
        session["percent"] = 1.0
        for stale_usage in (S.usage_of(S.provider("claude", [session], stale=True)),
                            S.usage_of(S.provider("claude", [session], readable=False)), None, {}):
            self.assertIsNone(paid.paid_defer(job, stale_usage, now))
        self.assertIsNone(paid.paid_defer(dict(job, allowPaid=True), usage, now))
        model_weekly = S.window("model_weekly", 1.0, now + DAY, "Fable weekly")
        self.assertIsNone(paid.paid_defer(job, S.usage_of(S.provider("claude", [model_weekly])), now))

    def test_paid_defer_codex_and_pi_codex(self):
        now = self.now
        full = S.window("session", 1.0, now + 7200, "5-hour")
        usage = S.usage_of(S.provider("codex", [full, S.window("weekly", 0.3, now + 4 * DAY, "Weekly")]))
        expected = {"action": "rearm", "fireAt": now + 7200 + 120, "reason": "limit_full", "resetEpoch": now + 7200}
        self.assertEqual(paid.paid_defer(self.job("codex"), usage, now), expected)
        self.assertEqual(paid.paid_defer(self.job("pi", provider="openai-codex", model="gpt-5.5"), usage, now), expected)
        self.assertIsNone(paid.paid_defer(self.job("pi", provider="anthropic", model="claude-fable-5"), usage, now))
        monthly = S.usage_of(S.provider("codex", [S.window("monthly", 1.0, now + 20 * DAY, "30-day", bindable=False)]))
        self.assertEqual(paid.paid_defer(self.job("codex"), monthly, now),
                         {"action": "skip", "fireAt": None, "reason": "limit_full", "resetEpoch": now + 20 * DAY})
        sliding = S.usage_of(S.provider("codex", [S.window("session", 1.0, now + 5 * 3600, "5-hour", sliding=True)]))
        self.assertIsNone(paid.paid_defer(self.job("codex"), sliding, now))
        self.assertIsNone(paid.paid_defer(self.job("codex"), S.usage_of(S.provider("codex", [full], stale=True)), now))

    def test_paid_defer_cursor_included_pool_fresh_only(self):
        now = self.now
        included = S.window("billing_total", 0.4, now + 5 * DAY, "Included")
        cursor_pool = S.window("billing_pool", 1.0, now + 5 * DAY, "Cursor models")
        other_pool = S.window("billing_pool", 0.2, now + 5 * DAY, "Other models")
        usage = S.usage_of(S.provider("cursor", [included, cursor_pool, other_pool]))
        rearm = {"action": "rearm", "fireAt": now + 5 * DAY + 120, "reason": "paid_defer", "resetEpoch": now + 5 * DAY}
        for model in ("composer-1", "auto", "grok-code-fast", None):
            self.assertEqual(paid.paid_defer(self.job("cursor", model=model), usage, now), rearm, model)
        self.assertIsNone(paid.paid_defer(self.job("cursor", model="claude-4.5-sonnet"), usage, now))
        included["percent"] = 1.0
        self.assertEqual(paid.paid_defer(self.job("cursor", model="claude-4.5-sonnet"), usage, now), rearm)
        included["resetsAt"] = cursor_pool["resetsAt"] = other_pool["resetsAt"] = now + 20 * DAY
        self.assertEqual(paid.paid_defer(self.job("cursor"), usage, now),
                         {"action": "skip", "fireAt": None, "reason": "paid_exhausted", "resetEpoch": now + 20 * DAY})
        included["resetsAt"] = None
        self.assertEqual(paid.paid_defer(self.job("cursor"), usage, now),
                         {"action": "skip", "fireAt": None, "reason": "paid_exhausted", "resetEpoch": None})
        stale = S.usage_of(S.provider("cursor", [included, cursor_pool], stale=True))
        self.assertIsNone(paid.paid_defer(self.job("cursor"), stale, now))
        self.assertIsNone(paid.paid_defer(self.job("cursor", allowPaid=True), usage, now))

    def test_paid_defer_go_window_full(self):
        now = self.now
        go = S.window("session", 1.0, now + 3 * 3600, "5-hour")
        usage = S.usage_of(S.provider("opencode-go", [go, S.window("weekly", 0.2, now + 2 * DAY, "Weekly")]))
        job = self.job("opencode", model="opencode-go/kimi-k2")
        self.assertEqual(paid.paid_defer(job, usage, now, billing="go"),
                         {"action": "rearm", "fireAt": now + 3 * 3600 + 120, "reason": "limit_full",
                          "resetEpoch": now + 3 * 3600})
        go["resetsAt"] = now + 12 * DAY
        self.assertEqual(paid.paid_defer(job, usage, now, billing="go")["action"], "skip")
        go["resetsAt"] = None
        self.assertEqual(paid.paid_defer(job, usage, now, billing="go"),
                         {"action": "skip", "fireAt": None, "reason": "limit_full", "resetEpoch": None})
        go.update(resetsAt=now + 3600, sliding=True)
        self.assertIsNone(paid.paid_defer(job, usage, now, billing="go"))
        go["sliding"] = False
        self.assertIsNone(paid.paid_defer(job, S.usage_of(S.provider("opencode-go", [go], stale=True)), now,
                                          billing="go"))
        self.assertIsNone(paid.paid_defer(self.job("opencode", model="opencode/big-pickle"), usage, now,
                                          billing="zen_free"))

    def test_paid_defer_zen_free_marked_day(self):
        now = self.now
        until = (now // DAY + 1) * DAY
        job = self.job("opencode", model="opencode/big-pickle")
        self.assertEqual(paid.paid_defer(job, None, now, billing="zen_free", zen_limited_until=until),
                         {"action": "rearm", "fireAt": until + 120, "reason": "limit_full", "resetEpoch": until})
        self.assertIsNone(paid.paid_defer(job, None, now, billing="zen_free", zen_limited_until=now - 10))
        self.assertIsNone(paid.paid_defer(job, None, now, billing="unknown", zen_limited_until=until))
        self.assertIsNone(paid.paid_defer(dict(job, allowPaid=True), None, now, billing="zen_free",
                                          zen_limited_until=until))

        self.patch(models, "cached_billing", lambda model, _now: "zen_free")
        seen = []
        self.patch(paid, "_zen_pending", lambda sd, at: seen.append((sd, at)) or until)
        marker = object()
        gate = self.gate(job, phase="prefire", sd=marker)
        self.assertEqual(gate["defer"], {"action": "rearm", "fireAt": until + 120, "reason": "limit_full",
                                         "resetEpoch": until})
        self.assertEqual(seen, [(marker, now)])
        self.assertIsNone(self.gate(job, phase="arm", sd=marker)["defer"])


# --- check_job ---------------------------------------------------------------------------

class GateTests(PaidCase):
    def test_check_job_gate_order_and_codes(self):
        self.env_extra["cursor"] = {"XDG_CONFIG_HOME": self.home + "/.config"}
        self.write(".config/cursor/cli-config.json", {"approvalMode": S.UNRESTRICTED})
        self.write("proj/.cursor/cli.json", "{}")
        cursor = self.job("cursor")

        preflights = []
        self.patch(paid, "cursor_preflight", lambda *args: preflights.append(args) or _REAL_PREFLIGHT(*args))
        self.patch(consts, "GATED_HARNESSES", ("cursor",))
        gate = self.gate(cursor, phase="preview")
        self.assertEqual((gate["ok"], gate["code"], gate["pending"], preflights),
                         (False, "harness_gated", False, []))
        self.assertEqual(paid.REASON_FOR_CODE["harness_gated"], "harness_gated")

        self.patch(consts, "GATED_HARNESSES", ())
        self.assertEqual(self.gate(cursor)["code"], "cursor_autorun_config")
        os.remove(self.path(".config/cursor/cli-config.json"))
        self.assertEqual(self.gate(cursor)["code"], "cursor_project_rules")
        os.remove(self.path("proj/.cursor/cli.json"))
        self.assertEqual(self.gate(cursor, phase="prefire")["code"], "cursor_untrusted")
        self.trust(self.work)
        gate = self.gate(cursor)
        self.assertEqual((gate["ok"], gate["notes"]), (True, ["cursor_on_demand"]))

        pi = self.job("pi", provider="xai", model="grok-4")
        argv = ["auth", "check", "--provider", "xai", "--json", "--no-refresh"]
        self.answer(argv, stdout=json.dumps({"status": "not_ready", "provider": "xai"}), rc=1)
        self.assertEqual(self.gate(pi)["code"], "not_logged_in")
        self.answer(argv, stdout=json.dumps({"status": "invalid", "provider": "xai"}), rc=2)
        self.assertEqual(self.gate(pi)["code"], "pi_auth_invalid")
        self.answer(argv, stdout=json.dumps({"status": "ready", "provider": "xai", "authType": "api_key"}))
        gate = self.gate(pi)
        self.assertEqual((gate["code"], gate["detail"], gate["provider"]), ("paid_pi_key", {"provider": "xai"}, "xai"))
        self.assertEqual(self.gate(dict(pi, provider=None))["code"], "pi_auth_invalid")

        for code in ("paid_blocked", "paid_zen", "paid_opencode_claude", "paid_pi_claude", "paid_pi_key",
                     "cursor_autorun_config", "cursor_network_config", "cursor_project_rules", "cursor_untrusted",
                     "harness_gated", "not_logged_in", "pi_auth_invalid"):
            self.assertIn(paid.REASON_FOR_CODE[code], V2_REASONS, code)

        session = S.window("session", 1.0, self.now + 3600, "5-hour")
        usage = S.usage_of(S.provider("claude", [session]))
        claude = self.job("claude")
        self.assertIsNone(self.gate(claude, usage=usage)["defer"])
        self.assertIsNone(self.gate(claude, phase="preview", usage=usage)["defer"])
        self.assertEqual(self.gate(claude, phase="prefire", usage=usage)["defer"]["reason"], "window_exhausted")
        self.assertIsNone(self.gate(dict(claude, allowPaid=True), phase="prefire", usage=usage)["defer"])
        with self.assertRaises(ApError):
            paid.check_job(claude, phase="later", now=self.now, usage=None)

    def test_check_job_preview_never_probes_catalogue(self):
        def boom(*_args, **_kwargs):
            raise AssertionError("the catalogue or the verbose list was read in preview")

        self.patch(paid, "read_catalogue", boom)
        self.patch(paid, "opencode_verbose", boom)
        job = self.job("opencode", model="opencode/big-pickle")
        gate = self.gate(job, phase="preview")
        self.assertEqual((gate["ok"], gate["billing"], gate["pending"], gate["notes"]),
                         (True, "unknown", True, ["subscription_only", "opencode_provider"]))
        self.assertEqual(self.calls(), [])

        self.patch(models, "cached_billing", lambda model, _now: "zen_free" if model == "opencode/big-pickle" else None)
        gate = self.gate(job, phase="preview")
        self.assertEqual((gate["billing"], gate["pending"], gate["notes"]),
                         ("zen_free", False, ["subscription_only", "zen_free"]))
        self.patch(models, "cached_billing", lambda model, _now: "zen_paid")
        self.assertEqual(self.gate(job, phase="preview")["code"], "paid_zen")

        probed = []
        self.patch(models, "cached_billing", lambda model, _now: None)
        self.patch(paid, "read_catalogue", lambda home: probed.append("catalogue") or (None, None))
        self.patch(paid, "opencode_verbose", lambda exec_prefix, provider, deadline: probed.append(provider) or {})
        gate = self.gate(job, phase="arm")
        self.assertEqual((gate["billing"], gate["pending"]), ("unknown", False))
        self.assertEqual(probed, ["opencode", "catalogue"])

    def test_check_job_notes_both_states(self):
        self.answer(["login", "status"], stderr="Logged in using ChatGPT\n")
        self.pi_ready("github-copilot")
        self.pi_ready("openai-codex")
        self.trust(self.work)
        self.write(".gemini/settings.json", {"security": {"auth": {"selectedType": "oauth-personal"}}})
        free_codex =S.usage_of(S.provider("codex", [], tier="Free"))
        fresh_cursor = S.usage_of(S.provider("cursor", [S.window("billing_total", 0.2, self.now + DAY, "Included")]))
        table = [
            ("claude", {}, None, False, ["subscription_only"]),
            ("claude", {}, None, True, ["paid_on", "budget_cap"]),
            ("codex", {}, free_codex, False, ["subscription_only", "codex_free_tier"]),
            ("codex", {}, None, False, ["subscription_only"]),
            ("codex", {}, free_codex, True, ["paid_on"]),
            ("gemini", {}, None, False, ["subscription_only"]),
            ("gemini", {}, None, True, ["paid_on"]),
            ("opencode", {"model": "openai/gpt-5"}, None, False, ["subscription_only", "opencode_provider"]),
            ("opencode", {"model": "openai/gpt-5"}, None, True, ["paid_on", "opencode_provider"]),
            ("pi", {"provider": "github-copilot", "model": "gpt-5"}, None, False,
             ["subscription_only", "pi_subscription"]),
            ("pi", {"provider": "github-copilot", "model": "gpt-5"}, None, True, ["paid_on"]),
            ("pi", {"provider": "openai-codex", "model": "gpt-5.5"}, None, False, ["subscription_only"]),
            ("cursor", {}, fresh_cursor, False, ["subscription_only"]),
            ("cursor", {}, None, False, ["cursor_on_demand"]),
            ("cursor", {}, fresh_cursor, True, ["paid_on", "cursor_on_demand"]),
        ]
        for harness_id, extra, usage, allow, notes in table:
            gate = self.gate(self.job(harness_id, allowPaid=allow, **extra), usage=usage)
            self.assertEqual((gate["ok"], gate["notes"]), (True, notes), (harness_id, extra, allow))
            self.assertTrue(set(gate["notes"]) <= set(paid.NOTES))
            self.assertEqual(gate["provider"], extra.get("provider"))
        self.assertTrue(set(paid.NOTES) >= {"zen_free", "go_plan"})


_REAL_PREFLIGHT = paid.cursor_preflight


if __name__ == "__main__":
    unittest.main()
