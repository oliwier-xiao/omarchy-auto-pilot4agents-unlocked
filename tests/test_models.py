"""Tests for the model lists (bin/autopilot/models.py) and the `models` verb (cli_models.py).

Parsers get fixtures shaped like the verified outputs (FEATURE-MODELS and R7): OpenCode lines
and verbose blocks, Codex debug JSON with hidden models, the Gemini bundle constants, Cursor
failure texts and bracket ids, the Pi table and its empty text. Detection runs against
tests/stubs/fake-cli in a temporary HOME (tests/h5_support.py). No real CLI, no network.
"""

import json
import os
import sys
import unittest

sys.dont_write_bytecode = True
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import h5_support as S  # noqa: E402
from h5_support import H5Case  # noqa: E402
from autopilot import agents, cli_models, edition, h5_v2, models  # noqa: E402
from autopilot.errors import ApError  # noqa: E402

PI_LIST = ["--offline", "--no-extensions", "--no-approve", "--list-models"]
ENTRY_KEYS = ["billing", "default", "group", "id", "label", "modelId", "note", "provider"]
RESULT_KEYS = ["cached", "cliVersion", "fetchedAt", "harness", "models", "ok", "reason", "source", "truncated"]


class ModelsCase(H5Case):
    def setUp(self):
        super().setUp()
        self.versions = {"opencode": "1.18.31", "codex": "0.154.0", "claude": "2.1.270", "gemini": "0.50.0",
                         "cursor": "2026.09.10-fd3934a", "pi": "0.85.1"}
        self.patch(agents, "version_of", lambda harness_id, discovery, deadline: self.versions.get(harness_id))
        self.patch(agents, "cached_version", lambda harness_id: self.versions.get(harness_id))

    def detect(self, harness_id, refresh=True, now=None):
        result = models.detect(harness_id, refresh=refresh, now=self.now if now is None else now, deadline_s=20)
        for entry in result["models"]:
            self.assertEqual(sorted(entry), ENTRY_KEYS)
        return result

    @staticmethod
    def ids(result):
        return [entry["id"] for entry in (result["models"] if isinstance(result, dict) else result)]


class ModelsTests(ModelsCase):
    def test_models_opencode_lines_caps_groups(self):
        text = ("\x1b[1mopencode/big-pickle\x1b[0m\nanthropic/claude-fable-5\nopencode/big-pickle\n\n"
                "not a model line\nbad provider/x\nopenrouter/anthropic/claude-3.7\nUPPER/x\n")
        entries = models.parse_opencode_lines(text)
        self.assertEqual(self.ids(entries), ["opencode/big-pickle", "anthropic/claude-fable-5",
                                             "openrouter/anthropic/claude-3.7"])
        self.assertEqual(entries[0], {"id": "opencode/big-pickle", "label": "big-pickle", "group": "opencode",
                                      "default": False, "billing": None, "provider": None, "modelId": None,
                                      "note": None})
        self.assertEqual(models.parse_opencode_lines(None), [])

        lines = "\n".join("prov%d/model-%04d" % (n % 7, n) for n in range(1100))
        self.answer(["models"], stdout=lines)
        result = self.detect("opencode")
        self.assertEqual((len(result["models"]), result["truncated"], result["reason"], result["source"]),
                         (1000, True, None, "cli"))
        self.assertEqual(sorted({e["group"] for e in result["models"]}), ["prov%d" % n for n in range(7)])
        self.assertEqual([c[1] for c in self.calls()], [["models"]])

        self.answer(["models"], stderr="boom", rc=1)
        self.assertEqual(self.detect("opencode")["reason"], "failed")

    def test_models_opencode_billing_badges_deprecated_hidden(self):
        listed = ["opencode/big-pickle", "opencode/paid-model-01", "opencode/hy3-free",
                  "opencode-go/muse-spark-1.3-contributor", "anthropic/claude-fable-5", "openai/gpt-5"]
        go_cost = {"input": 0.10, "output": 0.20}
        self.answer(["models"], stdout="\n".join(listed) + "\n")
        self.answer(["models", "opencode", "--verbose"], stdout=S.verbose_text(S.seven_free_blocks() + [
            ("opencode", "paid-model-01", S.verbose_model("opencode", "paid-model-01", S.paid_cost_verbose(1))),
            ("opencode", "hy3-free", S.verbose_model("opencode", "hy3-free", S.zero_cost_verbose(), status="deprecated"))]))
        self.answer(["models", "opencode-go", "--verbose"], stdout=S.verbose_text([
            ("opencode-go", "muse-spark-1.3-contributor",
             S.verbose_model("opencode-go", "muse-spark-1.3-contributor", go_cost))]))
        self.write(".cache/opencode/models.json", S.free_catalogue(
            extra=[S.cat_model("paid-model-01", {"input": 1.5, "output": 2}),
                   S.cat_model("hy3-free", S.zero_cost_catalogue(), status="deprecated")],
            go_models=[S.cat_model("muse-spark-1.3-contributor", go_cost)]))
        self.write(".config/opencode/opencode.json", {"model": "opencode/big-pickle"})

        result = self.detect("opencode", refresh=False)
        by_id = {e["id"]: e for e in result["models"]}
        self.assertNotIn("opencode/hy3-free", by_id)
        self.assertEqual({k: v["billing"] for k, v in by_id.items()},
                         {"opencode/big-pickle": "zen_free", "opencode/paid-model-01": "zen_paid",
                          "opencode-go/muse-spark-1.3-contributor": "go", "anthropic/claude-fable-5": "anthropic",
                          "openai/gpt-5": "other"})
        self.assertEqual([k for k, v in by_id.items() if v["default"]], ["opencode/big-pickle"])
        self.assertEqual(by_id["opencode/big-pickle"]["label"], "Big Pickle")
        self.assertEqual([c[1] for c in self.calls()],
                         [["models"], ["models", "opencode", "--verbose"], ["models", "opencode-go", "--verbose"]])
        self.assertEqual(models.cached_billing("opencode/big-pickle", self.now), "zen_free")
        self.assertEqual(models.cached_billing("opencode/paid-model-01", self.now), "zen_paid")
        self.assertIsNone(models.cached_billing("opencode/not-listed", self.now))
        # An upgraded OpenCode, or a version that is not cached, never answers from the old list.
        self.versions["opencode"] = "1.19.0"
        self.assertIsNone(models.cached_billing("opencode/big-pickle", self.now))
        self.assertIsNone(models.cached("opencode", self.now))
        self.versions["opencode"] = None
        self.assertIsNone(models.cached_billing("opencode/big-pickle", self.now))
        self.versions["opencode"] = "1.18.31"
        self.present["opencode"] = False
        self.assertIsNone(models.cached("opencode", self.now))
        self.present["opencode"] = True
        self.assertEqual(models.cached_billing("opencode/big-pickle", self.now), "zen_free")

        # A failed verbose probe leaves billing unknown and is not cached.
        os.remove(os.path.join(self.home, ".local/state/omarchy", edition.STATE_DIR_NAME, "models-opencode.json"))
        self.answer(["models", "opencode", "--verbose"], stderr="network", rc=1)
        result = self.detect("opencode")
        self.assertEqual({e["id"]: e["billing"] for e in result["models"]}["opencode/big-pickle"], "unknown")
        self.assertIsNone(models.cached_billing("opencode/big-pickle", self.now))

    def test_models_codex_debug_visibility_priority(self):
        document = {"models": [
            {"slug": "gpt-5.5-codex", "display_name": "GPT-5.5 Codex", "visibility": "list", "priority": 2,
             "supported_in_api": True, "default_reasoning_level": "medium"},
            {"slug": "gpt-5.5", "display_name": "GPT-5.5", "visibility": "list", "priority": 1},
            {"slug": "codex-internal", "display_name": "Internal", "visibility": "hide", "priority": 0},
            {"slug": "gpt-5-mini", "display_name": "GPT-5 mini", "visibility": "list"},
            {"slug": "bad slug!", "visibility": "list", "priority": 0},
            "junk",
            {"slug": "gpt-5.5", "display_name": "again", "visibility": "list", "priority": 0},
        ]}
        entries = models.parse_codex_debug(json.dumps(document).encode())
        self.assertEqual(self.ids(entries), ["gpt-5.5", "gpt-5.5-codex", "gpt-5-mini"])
        self.assertEqual(entries[0]["label"], "GPT-5.5")
        self.assertEqual(models.parse_codex_debug(b"not json"), [])
        self.assertEqual(models.parse_codex_debug(b'{"models": 3}'), [])

        document["models"][0]["description"] = "x" * 200000
        self.answer(["debug", "models"], stdout=json.dumps(document))
        result = self.detect("codex")
        self.assertEqual((self.ids(result), result["source"], result["reason"], result["cliVersion"]),
                         (["gpt-5.5", "gpt-5.5-codex", "gpt-5-mini"], "cli", None, "0.154.0"))
        document["models"][0]["description"] = "x" * 600000
        self.answer(["debug", "models"], stdout=json.dumps(document))
        result = self.detect("codex")
        self.assertEqual((result["models"], result["reason"]), ([], "too_large"))
        self.answer(["debug", "models"], stdout="{truncated")
        self.assertEqual(self.detect("codex")["reason"], "failed")

    CLAUDE_CATALOGUE = {"anthropic": {"models": {
        "claude-opus-5": {"name": "Claude Opus 5"},
        "claude-sonnet-5": {"name": "Claude Sonnet 5"},
        "claude-fable-5-1": {"name": "Claude Fable 5.1"},
        "claude-fable-5": {"name": "Claude Fable 5"},
        "claude-opus-4-8": {"name": "Claude Opus 4.8"},
        "claude-haiku-4-5": {"name": "Claude Haiku 4.5 (latest)"},
        "claude-haiku-4-5-20251001": {"name": "Claude Haiku 4.5"},
        "claude-sonnet-4-20250514": {"name": "Claude Sonnet 4"},
        "claude-3-opus": {"name": "Claude Opus 3", "status": "deprecated"},
        "claude-opus-4-1": {"name": "Claude Opus 4.1", "status": "deprecated"}}},
        "openai": {"models": {"gpt-5": {"name": "GPT-5"}}}}

    def test_models_claude_aliases_versions_and_settings(self):
        # Without a catalogue: the four aliases, no version to name, and no modelSettings keys.
        bare = models.claude_static({"modelSettings": {"ox/alpha": {}, "claude-opus-5": {}}})
        self.assertEqual(self.ids(bare), ["fable", "opus", "sonnet", "haiku"])
        self.assertEqual([(e["label"], e["group"], e["note"]) for e in bare][1], ("Opus (newest)", "claude-latest", None))

        catalogue = {"anthropic/" + k: v for k, v in self.CLAUDE_CATALOGUE["anthropic"]["models"].items()}
        entries = models.claude_static({"model": "opus[1m]", "modelSettings": {"ox/alpha": {}}}, catalogue)
        self.assertEqual(self.ids(entries), ["fable", "opus", "sonnet", "haiku", "opus[1m]",
                                             "claude-fable-5-1", "claude-fable-5", "claude-opus-5", "claude-sonnet-5",
                                             "claude-opus-4-8", "claude-haiku-4-5", "claude-sonnet-4-20250514"])
        by_id = {e["id"]: e for e in entries}
        self.assertEqual(by_id["opus"]["note"], "newest: Claude Opus 5")
        self.assertEqual(by_id["fable"]["note"], "newest: Claude Fable 5.1")
        self.assertEqual((by_id["opus[1m]"]["label"], by_id["opus[1m]"]["note"], by_id["opus[1m]"]["default"]),
                         ("Opus 1M (newest)", "newest: Claude Opus 5, 1M context", True))
        self.assertEqual((by_id["claude-haiku-4-5"]["label"], by_id["claude-haiku-4-5"]["group"]),
                         ("Claude Haiku 4.5", "claude-pinned"))
        self.assertNotIn("ox/alpha", by_id)
        self.assertEqual([e["id"] for e in entries if e["default"]], ["opus[1m]"])

        # A pinned version as the settings model is marked on its own row; with [1m] it gets one.
        pinned = models.claude_static({"model": "claude-opus-5"}, catalogue)
        self.assertEqual([e["id"] for e in pinned if e["default"]], ["claude-opus-5"])
        self.assertEqual(len(pinned), len(entries) - 1)
        wide = models.claude_static({"model": "claude-opus-5[1m]"}, catalogue)
        self.assertEqual([(e["id"], e["label"]) for e in wide if e["default"]], [("claude-opus-5[1m]", "Claude Opus 5 1M")])
        custom = models.claude_static({"model": "my-proxy-model"}, catalogue)
        self.assertEqual([(e["id"], e["note"]) for e in custom if e["default"]], [("my-proxy-model", "from your Claude settings")])
        pin = models.claude_static({"env": {"ANTHROPIC_DEFAULT_OPUS_MODEL": "claude-opus-4-8", "ANTHROPIC_API_KEY": "sk-x"}},
                                   catalogue)
        self.assertEqual({e["id"]: e["note"] for e in pin}["opus"], "set to claude-opus-4-8 in your Claude settings")

        self.assertEqual(self.detect("claude")["source"], "static")
        self.write(".claude/settings.json", {"model": "opus[1m]", "modelSettings": {"ox/alpha": {}}})
        self.write(".cache/opencode/models.json", self.CLAUDE_CATALOGUE)
        result = self.detect("claude")
        self.assertEqual((result["source"], result["reason"], self.ids(result)[4:6]), ("mixed", None, ["opus[1m]", "claude-fable-5-1"]))
        self.assertNotIn("ox/alpha", self.ids(result))
        self.assertEqual(self.calls(), [])

    def test_models_gemini_bundle_scan_and_fallback(self):
        bundle = self.mkdir("gemini-cli/bundle")
        self.write("gemini-cli/bundle/gemini.js", "// entry\n")
        self.write("gemini-cli/bundle/chunk-AAA.js", "var GEMINI_UNRELATED = 1;\n")
        constants = ('const PREVIEW_GEMINI_MODEL = "gemini-3-pro-preview";\n'
                     'var DEFAULT_GEMINI_EMBEDDING_MODEL = "gemini-embedding-001";\n'
                     'var GEMINI_MODEL_ALIAS_PRO = "pro", GEMINI_MODEL_ALIAS_AUTO = "auto";\n'
                     'var DEFAULT_GEMINI_MODEL = "gemini-2.5-pro";\n'
                     'var PREVIEW_GEMINI_FLASH_LITE_MODEL = "none";\n'
                     "var DEFAULT_GEMINI_MODEL_AUTO = 'auto-gemini-2.5';\n"
                     'var PREVIEW_GEMINI_3_1_CUSTOM_TOOLS_MODEL = "gemini-3.1-pro-preview-customtools";\n'
                     'var GEMINI_MODEL_ALIAS_FLASH = "flash";\nvar GEMINI_MODEL_ALIAS_FLASH_LITE = "flash-lite";\n'
                     'var SOMETHING_MODEL = "not-a-gemini-constant";\n')
        chunk = self.write("gemini-cli/bundle/chunk-YUI.js", constants)
        expected = ["auto", "pro", "flash", "flash-lite", "gemini-3-pro-preview", "gemini-2.5-pro", "auto-gemini-2.5"]
        self.assertEqual(self.ids(models.parse_gemini_bundle(constants)), expected)
        reordered = "\n".join(reversed(constants.splitlines()))
        self.assertEqual(sorted(self.ids(models.parse_gemini_bundle(reordered))), sorted(expected))
        self.assertEqual(models.parse_gemini_bundle(""), [])

        self.real["gemini"] = os.path.join(bundle, "gemini.js")
        self.write(".gemini/settings.json", {"model": {"name": "auto-gemini-2.5"}})
        result = self.detect("gemini")
        self.assertEqual((self.ids(result), result["source"]), (expected, "static"))
        self.assertEqual([e["id"] for e in result["models"] if e["default"]], ["auto-gemini-2.5"])
        self.assertEqual(self.calls(), [])

        os.remove(chunk)
        result = self.detect("gemini")
        self.assertEqual((self.ids(result), result["source"]),
                         (["auto", "pro", "flash", "flash-lite", "auto-gemini-2.5"], "mixed"))
        os.remove(self.path(".gemini/settings.json"))
        result = self.detect("gemini")
        self.assertEqual((self.ids(result), result["source"]), (["auto", "pro", "flash", "flash-lite"], "static"))
        self.assertFalse(any(e["default"] for e in result["models"]))
        self.write(".gemini/settings.json", {"model": {"name": "gemini-3.5-flash"}})
        result = self.detect("gemini")
        self.assertEqual((self.ids(result)[-1], result["source"], result["models"][-1]["default"]),
                         ("gemini-3.5-flash", "mixed", True))

    def test_models_cursor_auth_failure_reason(self):
        self.env_extra["cursor"] = {"XDG_CONFIG_HOME": self.home + "/.config"}
        self.answer(["models"], stderr="Error: Authentication required. Run 'agent login' first.\n", rc=1)
        result = self.detect("cursor", refresh=False)
        self.assertEqual((result["models"], result["reason"], result["cached"]), ([], "cursor_no_list", False))
        self.answer(["models"], stdout="Error: Authentication required. Please run 'agent login'\n", rc=0)
        self.assertEqual(self.detect("cursor")["reason"], "cursor_no_list")
        self.answer(["models"], stdout="\n\n")
        self.assertEqual(self.detect("cursor")["reason"], "cursor_no_list")
        calls = self.calls()
        self.assertTrue(all(c[0] == "cursor" and c[1] == ["models"] and "XDG_CONFIG_HOME" in c[2] for c in calls))
        self.assertFalse(os.path.exists(os.path.join(self.home, ".local/state/omarchy", edition.STATE_DIR_NAME,
                                                     "models-cursor.json")))

        self.patch(models, "_PROBE_DEADLINE_S", 1.0)
        self.answer(["models"], stdout="composer-1\n", sleep=5)
        self.assertEqual(self.detect("cursor")["reason"], "timeout")

    def test_models_cursor_bracket_ids_no_auto(self):
        bracket = "claude-opus-4-8[context=1m,effort=high,fast=false]"
        output = ("Available models:\n  auto - Auto\n  composer-1 - Composer 1 (current)\n"
                  "  %s - Claude Opus 4.8 1M\n  gpt-5.5-high  GPT-5.5 High\n  grok-code-fast\n  Auto\n" % bracket)
        entries = models.parse_cursor_models(output)
        self.assertEqual(self.ids(entries), ["composer-1", bracket, "gpt-5.5-high", "grok-code-fast"])
        self.assertEqual((entries[0]["label"], entries[0]["default"]), ("Composer 1", True))
        self.assertEqual(entries[2]["label"], "GPT-5.5 High")
        self.assertTrue(h5_v2.model_ok(bracket))
        self.assertFalse(h5_v2.model_ok("x" * 129))

        self.answer(["models"], stdout=output)
        result = self.detect("cursor")
        self.assertEqual((self.ids(result), result["source"], result["reason"]),
                         (["composer-1", bracket, "gpt-5.5-high", "grok-code-fast"], "cli", None))
        self.assertFalse(any(e["id"].lower() == "auto" for e in result["models"]))

    def test_models_pi_table_fakeok(self):
        text = ("provider   model       context  max-out  thinking  images\n"
                "fakeok     fake-model  128K     16.4K    no        no\n"
                "openai-codex gpt-5.5   400K     128K     yes       yes\n"
                "Bad        row\n")
        entries = models.parse_pi_table("Loading...\n" + text)
        self.assertEqual(entries[0], {"id": "fakeok/fake-model", "label": "fake-model", "group": "fakeok",
                                      "default": False, "billing": None, "provider": "fakeok", "modelId": "fake-model",
                                      "note": None})
        self.assertEqual(self.ids(entries), ["fakeok/fake-model", "openai-codex/gpt-5.5"])
        self.assertEqual(models.parse_pi_table("fakeok fake-model 128K 16.4K no no\n"), [])

        self.answer(PI_LIST, stdout=text)
        result = self.detect("pi")
        self.assertEqual((self.ids(result), result["source"], result["reason"]),
                         (["fakeok/fake-model", "openai-codex/gpt-5.5"], "cli", None))
        self.assertEqual([e["modelId"] for e in result["models"]], ["fake-model", "gpt-5.5"])

    def test_models_pi_no_models_available_no_default_row(self):
        empty = ("No models available. Use /login to log into a provider via OAuth or API key. "
                 "See: https://example.invalid/providers.md https://example.invalid/models.md\n")
        self.answer(PI_LIST, stdout=empty)
        result = self.detect("pi")
        self.assertEqual((result["models"], result["reason"]), ([], "pi_no_provider"))
        self.answer(PI_LIST, stdout="provider model context max-out thinking images\n")
        self.assertEqual(self.detect("pi")["reason"], "pi_no_provider")
        self.answer(PI_LIST, stdout="something else\n")
        self.assertEqual(self.detect("pi")["reason"], "failed")
        self.answer(PI_LIST, stdout="provider model context max-out thinking images\n"
                                    "openai-codex gpt-5.5 400K 128K yes yes\n")
        result = self.detect("pi")
        self.assertFalse(any(e["default"] for e in result["models"]))
        self.assertTrue(all("/" in e["id"] and e["provider"] for e in result["models"]))

    def test_models_pi_listing_argv_isolation_flags(self):
        self.answer(PI_LIST, stdout="provider model context max-out thinking images\nxai grok-4 256K 32K yes yes\n")
        self.detect("pi")
        calls = self.calls()
        self.assertEqual([(c[0], c[1]) for c in calls], [("pi", PI_LIST)])
        for word in ("-p", "--mode", "rpc", "-a", "-e", "--extension", "--api-key", "--" + "approve"):
            self.assertNotIn(word, calls[0][1])
        self.assertTrue(set(calls[0][2]) <= set(self.fake_env("pi", "plan")) | {"LC_CTYPE"})

    def test_models_cache_ttl_refresh_version_key(self):
        document = {"models": [{"slug": "gpt-5.5", "display_name": "GPT-5.5", "visibility": "list", "priority": 1}]}
        self.answer(["debug", "models"], stdout=json.dumps(document))
        first = self.detect("codex", refresh=False)
        self.assertEqual((first["cached"], len(self.calls())), (False, 1))
        path = os.path.join(self.home, ".local/state/omarchy", edition.STATE_DIR_NAME, "models-codex.json")
        self.assertEqual(os.stat(path).st_mode & 0o777, 0o600)
        second = self.detect("codex", refresh=False, now=self.now + 60)
        self.assertEqual((second["cached"], second["models"], second["fetchedAt"], len(self.calls())),
                         (True, first["models"], first["fetchedAt"], 1))
        self.assertEqual(self.detect("codex", refresh=True)["cached"], False)
        self.assertEqual(len(self.calls()), 2)
        self.versions["codex"] = "0.155.0"
        self.assertEqual(self.detect("codex", refresh=False)["cached"], False)
        self.assertEqual(len(self.calls()), 3)
        self.assertEqual(self.detect("codex", refresh=False, now=self.now + 21601)["cached"], False)
        self.assertEqual(len(self.calls()), 4)

        self.assertIsNotNone(models.cached("codex", self.now + 21601))
        self.assertIsNone(models.cached("codex", self.now + 3 * 21601))
        with open(path) as handle:
            stored = json.load(handle)
        stored["result"]["models"][0]["id"] = "bad id!"
        with open(path, "w") as handle:
            json.dump(stored, handle)
        self.assertIsNone(models.cached("codex", self.now + 21601))
        self.assertEqual(self.detect("codex", refresh=False, now=self.now + 21601)["cached"], False)
        self.assertIsNone(models.cached_billing("gpt-5.5", self.now))
        self.assertIsNone(models.cached("nope", self.now))

    def test_models_verb_shape_and_caps(self):
        for argv in ([], ["--harness"], ["--harness", "vim"], ["--harness", "codex", "--refresh", "x"],
                     ["--refresh", "--harness", "codex"], ["--harness", "codex", "refresh"], ["codex"]):
            with self.assertRaises(ApError) as caught:
                cli_models.cmd_models(argv, None)
            self.assertEqual(caught.exception.code, "bad_args", argv)
        lines = "\n".join("longprovider%02d/%s%04d" % (n % 30, "m" * 100, n) for n in range(1000))
        self.answer(["models"], stdout=lines)
        result = cli_models.cmd_models(["--harness", "opencode", "--refresh"], None)
        self.assertEqual(list(result)[0], "ok")
        self.assertEqual(sorted(result), RESULT_KEYS)
        payload = json.dumps(result, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        self.assertLessEqual(len(payload), 262144)
        self.assertTrue(result["truncated"])
        self.assertTrue(all(len(e["label"]) <= models.LABEL_MAX and len(e["id"]) <= 128 for e in result["models"]))

        self.present["codex"] = False
        missing = cli_models.cmd_models(["--harness", "codex"], None)
        self.assertEqual((missing["ok"], missing["models"], missing["reason"]), (True, [], "cli_missing"))
        with self.assertRaises(ApError) as caught:
            models.detect("vim", refresh=False, now=self.now, deadline_s=5)
        self.assertEqual(caught.exception.code, "invalid_harness")


if __name__ == "__main__":
    unittest.main()
