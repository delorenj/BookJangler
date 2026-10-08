import copy
import json
import unittest
from pathlib import Path

import build_report_data as build
import gateway_attribution as attribution
import render_usage_report as renderer

ROOT = Path(__file__).resolve().parent
LEDGER = ROOT / "report-ledger.json"
HAVE_INPUTS = LEDGER.exists() and (ROOT / "output/voxcpm2-chapter-01/metrics.json").exists()


def inputs():
    return (json.loads(LEDGER.read_text()), json.loads((ROOT / "report-pricing.json").read_text()),
            json.loads((ROOT / "usage-final-snapshot.json").read_text()), build.runtime_evidence())


class HelperTests(unittest.TestCase):
    RATES = {"input_tokens": 2.0, "cache_read_tokens": 0.1, "cache_write_tokens": 2.5, "output_tokens": 10.0}

    def test_equivalent_keeps_missing_usage_unknown(self):
        result = build.equivalent({"input_tokens": 5, "cache_read_tokens": None, "cache_write_tokens": 0, "output_tokens": 1}, self.RATES)
        self.assertTrue(all(value is None for value in result.values()))

    def test_equivalent_prices_each_token_class(self):
        usage = {"input_tokens": 1_000_000, "cache_read_tokens": 1_000_000, "cache_write_tokens": 0, "output_tokens": 100_000}
        result = build.equivalent(usage, self.RATES)
        self.assertAlmostEqual(result["total_usd"], 2.0 + 0.1 + 1.0)
        self.assertEqual(result["cache_write_usd"], 0)

    def test_sum_tokens_refuses_missing_fields(self):
        row = {"message_id": "m", "tokens": {"input_tokens": 1, "cache_read_tokens": 0, "cache_write_tokens": 0,
                                              "output_tokens": 1, "reasoning_tokens": 0, "reported_total_tokens": None}}
        with self.assertRaises(ValueError):
            build.sum_tokens([row])

    def test_tool_details_and_short(self):
        self.assertIsNone(build.tool_details([]))
        self.assertEqual(build.tool_details([{"name": "bash", "status": "completed"}]), "tool bash completed")
        self.assertEqual(build.short("ses_abcdef123456", {"ses_abcdef123456": "research"}), "research …123456")
        self.assertIsNone(build.short(None, {}))


class EventPhaseTests(unittest.TestCase):
    def test_pre_snapshot_event_is_never_labelled_post_experiment(self):
        self.assertEqual(build.event_phase({"phase": "core_experiment", "group": "post_snapshot_confirmation"}), "orchestration")
        self.assertEqual(build.event_phase({"phase": "core_experiment", "group": "research"}), "research")
        self.assertEqual(build.event_phase({"phase": "core_experiment", "group": "unassigned_main_event"}), "orchestration")
        self.assertEqual(build.event_phase({"phase": "post_snapshot_confirmation", "group": "post_snapshot_confirmation"}), "post_experiment")


class AttributionTests(unittest.TestCase):
    @staticmethod
    def msg(mid, started, done, i, c, o):
        return {"message_id": mid, "started_at": started, "time": done,
                "tokens": {"input_tokens": i, "cache_read_tokens": c, "output_tokens": o}}

    @staticmethod
    def row(log_id, when, i, c, o, outcome="completed"):
        return {"gateway_log_id": log_id, "time": when, "outcome": outcome, "request_id": f"r{log_id}", "account_id": "a",
                "billing_class": "subscription", "route": "x", "tokens": {"input_tokens": i, "cache_read_tokens": c, "output_tokens": o}}

    def test_unique_token_and_time_match(self):
        out = attribution.match_messages([self.msg("m1", "2026-01-01T00:00:00+00:00", "2026-01-01T00:00:10+00:00", 5, 1, 2)],
                                         [self.row(1, "2026-01-01T00:00:08+00:00", 5, 1, 2), self.row(2, "2026-01-01T00:00:08+00:00", 6, 1, 2)])
        self.assertEqual([m["gateway_log_id"] for m in out["matches"]], [1])

    def test_ambiguous_wrong_time_and_failed_rows_are_not_attributed(self):
        m = self.msg("m1", "2026-01-01T00:00:00+00:00", "2026-01-01T00:00:10+00:00", 5, 1, 2)
        two = attribution.match_messages([m], [self.row(1, "2026-01-01T00:00:05+00:00", 5, 1, 2), self.row(2, "2026-01-01T00:00:06+00:00", 5, 1, 2)])
        self.assertEqual((two["matches"], two["ambiguous"]), ([], ["m1"]))
        late = attribution.match_messages([m], [self.row(1, "2026-01-01T01:00:00+00:00", 5, 1, 2)])
        self.assertEqual(late["unmatched"], ["m1"])
        failed = attribution.match_messages([m], [self.row(1, "2026-01-01T00:00:05+00:00", 5, 1, 2, outcome="failed")])
        self.assertEqual(failed["unmatched"], ["m1"])

    def test_gateway_row_cannot_explain_two_messages(self):
        a = self.msg("m1", "2026-01-01T00:00:00+00:00", "2026-01-01T00:00:10+00:00", 5, 1, 2)
        b = self.msg("m2", "2026-01-01T00:00:05+00:00", "2026-01-01T00:00:15+00:00", 5, 1, 2)
        out = attribution.match_messages([a, b], [self.row(1, "2026-01-01T00:00:08+00:00", 5, 1, 2)])
        self.assertEqual(out["matches"], [])
        self.assertEqual(sorted(out["ambiguous"]), ["m1", "m2"])


@unittest.skipUnless(HAVE_INPUTS, "collected ledger and render receipts not present")
class BuildTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ledger, cls.pricing, cls.snapshot, cls.runtime = inputs()
        cls.data = build.build(cls.ledger, cls.pricing, cls.snapshot, cls.runtime)

    def test_llm_phases_reconcile_to_snapshot(self):
        llm = [p["llm"] for p in self.data["phases"] if p["llm"].get("applicable") is not False]
        totals = self.snapshot["totals"]
        self.assertEqual(sum(p["calls"] for p in llm), totals["completed_assistant_messages"])
        self.assertEqual(sum(p["total_tokens"] for p in llm), totals["reported_total_tokens"])
        self.assertEqual(sum(p["input_tokens"] for p in llm), totals["input_tokens"])
        self.assertEqual(sum(p["cache_read_tokens"] for p in llm), totals["cache_read_tokens"])
        self.assertEqual(sum(p["output_tokens"] for p in llm), totals["output_tokens"])
        self.assertEqual(self.data["summary"]["llm_total_tokens"], totals["reported_total_tokens"])

    def test_tts_reconciles_to_metrics_and_is_never_mixed_with_llm_tokens(self):
        metrics = json.loads((ROOT / "output/voxcpm2-chapter-01/metrics.json").read_text())["totals"]
        tts = [p["tts"] for p in self.data["phases"] if p["tts"].get("applicable") is not False]
        self.assertEqual(sum(p["calls"] for p in tts), metrics["requests"])
        self.assertEqual(sum(p["input_text_tokens"] for p in tts), metrics["input_text_tokens"])
        self.assertEqual(self.data["summary"]["tts_text_tokens"], 9906)
        for phase in self.data["phases"]:
            applicable = [k for k in ("llm", "tts") if phase[k].get("applicable") is not False]
            self.assertLessEqual(len(applicable), 1, phase["id"])
        for row in self.data["ledger"]:
            if row["source"] == "Local engine receipt":
                self.assertIsNone(row["total_tokens"])
                self.assertEqual(row["cost_usd"], 0)
            if row["source"] == "OpenCode assistant message":
                self.assertIsNone(row["cost_usd"])

    def test_cost_views_separate_subscription_from_api_equivalent(self):
        views = {v["label"]: v for v in self.data["costs"]["views"]}
        marginal = views["LLM marginal gateway charge"]
        self.assertEqual((marginal["usd"], marginal["kind"]), (0, "actual"))
        estimate = next(v for v in self.data["costs"]["views"] if v["label"].startswith("LLM API-equivalent"))
        self.assertEqual(estimate["kind"], "estimate")
        self.assertAlmostEqual(estimate["usd"], 29.404868, places=6)
        total = sum(p["api_equivalent"]["total_usd"] for p in self.data["phases"] if "api_equivalent" in p)
        self.assertAlmostEqual(total, 29.404868, places=6)
        self.assertIsNone(self.data["summary"]["actual_total_usd"])
        self.assertIsNone(self.data["costs"]["actual"]["search_usd"])
        self.assertIsNone(self.data["costs"]["actual"]["energy_usd"])

    def test_post_experiment_work_is_kept_out_of_totals(self):
        post = next(w for w in self.data["separate_work"] if w["label"].startswith("Post-experiment"))
        self.assertEqual(post["llm"]["total_tokens"], 778441)
        report_work = next(w for w in self.data["separate_work"] if w["label"].startswith("Report-request"))
        self.assertIsNone(report_work["llm"]["total_tokens"])
        self.assertIn("post_experiment", {r["phase"] for r in self.data["ledger"]})
        self.assertNotIn("post_experiment", {p["id"] for p in self.data["phases"]})

    def test_every_ledger_row_has_a_known_phase_and_ledger_is_chronological(self):
        known = {p["id"] for p in self.data["phases"]} | {"orchestration", "post_experiment"}
        self.assertLessEqual({r["phase"] for r in self.data["ledger"]}, known)
        times = [r["time"] for r in self.data["ledger"]]
        self.assertEqual(times, sorted(times))

    def test_core_events_are_not_reported_as_post_snapshot(self):
        core = sum(1 for r in self.data["ledger"] if r["source"] == "CandyStore event" and r["phase"] != "post_experiment")
        self.assertEqual(core, self.ledger["events"]["core_count"])

    def test_gateway_attribution_requires_complete_zero_quota_settlement(self):
        path = ROOT / "report-gateway-attribution.json"
        if not path.exists():
            self.skipTest("no gateway attribution file")
        att = json.loads(path.read_text())
        coverage = build.build(self.ledger, self.pricing, self.snapshot, self.runtime, attribution=att)["costs"]["actual_basis"]["llm_usd"]
        self.assertIn("NewAPI quota was 0", coverage)
        broken = copy.deepcopy(att)
        broken["settlement"]["quota_nonzero_rows"] = 1
        weaker = build.build(self.ledger, self.pricing, self.snapshot, self.runtime, attribution=broken)["costs"]["actual_basis"]["llm_usd"]
        self.assertNotIn("NewAPI quota was 0", weaker)
        bad = copy.deepcopy(att)
        bad["settlement"]["prompt_tokens_sum"] += 1
        with self.assertRaises(ValueError):
            build.build(self.ledger, self.pricing, self.snapshot, self.runtime, attribution=bad)

    def test_snapshot_mismatch_fails_closed(self):
        bad = copy.deepcopy(self.snapshot)
        bad["totals"]["reported_total_tokens"] += 1
        with self.assertRaises(ValueError):
            build.build(self.ledger, self.pricing, bad, self.runtime)

    def test_long_context_request_fails_closed(self):
        bad = copy.deepcopy(self.ledger)
        bad["llm_usage"]["opencode"]["ledger"][0]["tokens"]["normalized_input_tokens"] = 300_000
        with self.assertRaises(ValueError):
            build.build(bad, self.pricing, self.snapshot, self.runtime)

    def test_unmapped_group_fails_closed(self):
        bad = copy.deepcopy(self.ledger)
        bad["llm_usage"]["opencode"]["ledger"][0]["group"] = "mystery"
        with self.assertRaises(ValueError):
            build.build(bad, self.pricing, self.snapshot, self.runtime)

    def test_renders_with_audio_and_no_external_resources(self):
        document = renderer.render_report(self.data)
        self.assertIn('<audio controls preload="none" src="chapter-01.mp3">', document)
        self.assertIn("100% local", document)
        self.assertIn("Estimate · not charged", document)
        self.assertNotIn("<img", document)
        self.assertNotIn("http://127.0.0.1", document)
        self.assertEqual(json.loads(json.dumps(self.data, allow_nan=False)), self.data)


if __name__ == "__main__":
    unittest.main()
