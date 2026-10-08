from __future__ import annotations

import copy
import json
import shutil
import subprocess
import unittest
from html.parser import HTMLParser
from pathlib import Path
from unittest.mock import patch

import render_usage_report as report


class ParsedReport(HTMLParser):
    def __init__(self, document):
        super().__init__()
        self.sections, self.scripts, self.rows, self.inputs = [], {}, {}, {}
        self.body = self.script = None
        self.cells = None
        self.feed(document)

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "section":
            self.sections.append(attrs.get("id"))
        if tag == "input":
            self.inputs[attrs["id"]] = attrs
        if tag == "script":
            self.script = attrs.get("id", "executable")
            self.scripts[self.script] = ""
        if tag == "tbody":
            self.body = attrs["id"]
            self.rows[self.body] = []
        if tag == "tr" and self.body:
            self.cells = []
            self.rows[self.body].append(self.cells)
        if tag == "td" and self.cells is not None:
            self.cells.append("")

    def handle_data(self, data):
        if self.script:
            self.scripts[self.script] += data
        elif self.cells:
            self.cells[-1] += data

    def handle_endtag(self, tag):
        if tag == "script":
            self.script = None
        if tag == "tbody":
            self.body = self.cells = None
        if tag == "tr":
            self.cells = None


def fixture():
    return {
        "title": "Chapter 1 · usage workbook", "generated_at": "2026-10-07T12:00:00Z",
        "scope": {"start": "2026-10-06", "end": "2026-10-07", "description": "A chapter experiment", "cutoff": "Final capture"},
        "summary": {"llm_total_tokens": 175, "llm_calls": 2, "tts_text_tokens": 19, "tts_calls": 1, "audio_seconds": 3020, "synth_wall_seconds": 1460.767, "actual_total_usd": None},
        "phases": [{"id": "build", "label": "Build", "description": "Verified events", "llm": {"input_tokens": 100, "output_tokens": 20, "cache_read_tokens": 50, "cache_write_tokens": 5, "total_tokens": 175, "calls": 2}, "tts": {"input_text_tokens": 19, "calls": 1, "wall_seconds": 1460.767, "audio_seconds": 3020}, "event_count": 2}],
        "models": [{"provider": "Supplied provider", "route": "route-alias", "native_model": None, "activity": "LLM", "billing": "Unknown", "evidence": "Client route only"}],
        "costs": {"actual": {"llm_usd": None, "tts_provider_usd": 0, "search_usd": None, "energy_usd": None}, "assumptions": {"llm_uncached_per_million": None, "llm_output_per_million": None, "llm_cache_read_per_million": None, "search_per_request": None, "search_requests": None, "gpu_watts_low": 150, "gpu_watts_high": 350, "electricity_usd_per_kwh": 0.15, "synthesis_seconds": 1460.767}, "explanation": "Provider charges are not established.", "sources": []},
        "coverage": {"status": "Partial", "description": "Captured sessions only", "queried_events": 4, "matched_events": 2, "usage_events": 2, "session_coverage": [{"id": "session-1", "label": "Build", "events": 2, "usage_events": 2, "llm_calls": 2}], "notes": ["Uncaptured work remains unknown."]},
        "ledger": [{"id": "second", "time": "2026-10-07T12:00:00Z", "phase": "build", "source": "OpenCode", "kind": "llm", "session": "session-1", "provider": "Supplied provider", "model": "", "input_tokens": 100, "cache_read_tokens": 50, "output_tokens": 20, "total_tokens": 175, "cost_usd": 0, "details": "Client reported cost only", "evidence_url": "https://example.test/event?id=2&view=raw"}, {"id": "first", "time": "2026-10-07T11:00:00Z", "phase": "build", "source": "Local tokenizer", "kind": "tts", "session": "session-1", "input_tokens": 19, "cost_usd": None, "details": "Text tokens, not token billing"}],
        "notes": ["No settled invoice available."], "sources": [{"label": "Capture", "url": "https://example.test/capture"}],
    }


class ReportTests(unittest.TestCase):
    def test_sections_and_self_contained_snapshot(self):
        document = report.render_report(fixture())
        parsed = ParsedReport(document)
        self.assertEqual(parsed.sections, ["phases", "coverage", "models", "economics", "events", "sources"])
        self.assertEqual(set(parsed.scripts), {"report-data", "ledger-columns", "executable"})
        for asset in ('src=', '<link', '<img', '@import', 'fetch(', 'XMLHttpRequest', 'innerHTML', '<!--'):
            self.assertNotIn(asset, document)
        for phrase in ("Coverage &amp; reconciliation", "OpenCode reported zero cost", "never a combined token total", "TTS text tokens never infer token billing", "not measured power", "CPU/OCR/build/QA", 'aria-live="polite"', '@media print', 'prefers-reduced-motion', '<noscript>', 'Reset filters'):
            self.assertIn(phrase, document)
        self.assertIn("50:20", document)
        self.assertEqual(parsed.rows["scenario-body"][0], ["Build"] + ["Unknown"] * 5)

    def test_unknown_and_zero_distinction_preserved_without_mutation(self):
        data = fixture()
        before = copy.deepcopy(data)
        parsed = ParsedReport(report.render_report(data))
        self.assertEqual(data, before)
        self.assertEqual(json.loads(parsed.scripts["report-data"]), data)
        self.assertEqual(parsed.rows["actual-body"], [["LLM", "Unknown"], ["TTS provider", "$0.0000"], ["Search", "Unknown"], ["Energy", "Unknown"]])
        self.assertEqual(parsed.rows["ledger-body"][1][7], "—")
        self.assertEqual(parsed.rows["ledger-body"][1][12], "$0.0000")
        self.assertEqual(parsed.rows["ledger-body"][0][12], "Unknown")
        self.assertEqual(parsed.rows["model-body"][0][2], "Unknown")
        for identity in ("uncached", "cached", "output", "search-price"):
            self.assertEqual(parsed.inputs[identity]["value"], "")
        self.assertEqual(parsed.inputs["watts-low"]["value"], "150")
        self.assertEqual(parsed.rows["phase-body"][0], ["Build", "Verified events", "100", "50", "5", "20", "175", "2", "19", "1", "1,460.767", "3,020", "2"])

    def test_missing_fields_do_not_invent_metrics_or_providers(self):
        document = report.render_report({})
        parsed = ParsedReport(document)
        self.assertIn("Unknown", document)
        self.assertNotIn("50:20", document)
        self.assertNotIn("$0.0000", document)
        for identity, attrs in parsed.inputs.items():
            if identity != "query":
                self.assertEqual(attrs["value"], "")
        for name in ("OpenAI", "Anthropic", "VoxCPM", "RTX", "Local provider"):
            self.assertNotIn(name, document)
        self.assertIsNone(report.number(True))
        for value in (-1, float("inf"), float("nan"), "12", 10**400):
            self.assertIsNone(report.number(value))
        for value in (0, 10, 100):
            self.assertEqual(report.metric(value), str(value))

    def test_malicious_content_is_text_and_json_cannot_close_script(self):
        data = fixture()
        attack = '</script><script>alert("x")</script><img src=x onerror="oops">&\u2028'
        data["title"] = data["ledger"][0]["details"] = attack
        data["phases"][0]["description"] = attack
        data["sources"] = [{"label": attack, "url": "javascript:alert(1)"}, {"label": 'Quoted " link', "url": 'https://example.test/?q="&x=<bad>'}]
        parsed = ParsedReport(report.render_report(data))
        self.assertEqual(set(parsed.scripts), {"report-data", "ledger-columns", "executable"})
        self.assertNotIn("</script", parsed.scripts["report-data"])
        self.assertNotIn("<", parsed.scripts["report-data"])
        self.assertEqual(json.loads(parsed.scripts["report-data"]), data)
        self.assertIn('&lt;img src=x onerror=&quot;oops&quot;&gt;', report.render_report(data))
        self.assertNotIn('href="javascript:', report.render_report(data))
        self.assertIn('q=&quot;&amp;x=&lt;bad&gt;', report.render_report(data))
        for url in ("javascript:alert(1)", "data:text/html,x", "//example.test", "file:///tmp/x", "https://user:password@example.test", "https://example.test/\nattack", "http://[bad"):
            self.assertIsNone(report.safe_url(url))

    def test_stable_chronology_pagination_fallback_and_coverage_warning(self):
        data = fixture()
        data["ledger"] = [{"id": str(i), "time": "2026-10-07T12:00:00Z", "details": "event"} for i in range(60)]
        data["ledger"].insert(2, {"id": "earlier", "time": "2026-10-07T13:00:00+02:00"})
        data["ledger"].append({"id": "missing-time"})
        data["summary"]["llm_total_tokens"] = 999
        parsed = ParsedReport(report.render_report(data))
        self.assertEqual(len(parsed.rows["ledger-body"]), 50)
        self.assertEqual([row[0] for row in parsed.rows["ledger-body"][:4]], ["earlier", "0", "1", "2"])
        self.assertEqual(len(json.loads(parsed.scripts["report-data"])["ledger"]), 62)
        self.assertIn("embedded ledger has 62 rows", report.render_report(data))
        self.assertIn("do not reconcile", report.render_report(data))
        self.assertIn("filtered.map(r=>", report.JS)
        self.assertIn("JSON.stringify(data,null,2)", report.JS)
        self.assertIn("setTimeout(()=>URL.revokeObjectURL(url)", report.JS)

    def test_optional_sections_render_escaped_and_leave_base_report_unchanged(self):
        base = report.render_report(fixture())
        for marker in ('class="callouts"', "<audio", "costview-body", "apieq-body", "separate-body"):
            self.assertNotIn(marker, base)
        data = fixture()
        attack = '<b onclick="x">&'
        data["callouts"] = [{"title": attack, "body": attack, "tone": "good"}, {"title": "t", "body": "b", "tone": "evil"}, "bad"]
        data["audio"] = {"src": "chapter-01.mp3", "label": attack, "duration_seconds": 3020}
        data["costs"]["views"] = [{"label": "Hypothetical", "usd": 1.5, "kind": "estimate", "basis": attack}, {"label": "Range", "usd": 0, "usd_high": 2, "kind": "actual"}, {"label": "Open", "usd": None, "kind": "mystery"}]
        data["costs"]["actual_basis"] = {"llm_usd": "Subscription"}
        data["phases"][0]["api_equivalent"] = {"uncached_input_usd": 1, "cache_read_usd": 0.5, "output_usd": 0.25, "total_usd": 1.75}
        data["phases"].append({"id": "local", "label": "Local", "llm": {"applicable": False}, "tts": {"applicable": False}})
        data["separate_work"] = [{"label": "After", "description": "d", "llm": {"input_tokens": 3, "total_tokens": 4}}]
        data["coverage"]["counts"] = [{"label": "Receipts", "value": "7 + 2"}]
        data["summary"]["highlights"] = [{"label": "Paid TTS", "value": "$0.00"}]
        document = report.render_report(data)
        parsed = ParsedReport(document)
        self.assertNotIn(attack, document)
        self.assertEqual(document.count('class="callout '), 2)
        self.assertIn('class="callout info"', document)
        self.assertIn('<audio controls preload="none" src="chapter-01.mp3">', document)
        self.assertEqual(parsed.rows["costview-body"][0][1:3], ["$1.5000", "Estimate · not charged"])
        self.assertEqual(parsed.rows["costview-body"][1][1], "$0.0000 – $2.0000")
        self.assertEqual(parsed.rows["costview-body"][2][1:3], ["Unknown", "Unknown"])
        self.assertEqual(parsed.rows["actual-body"][0], ["LLM", "Unknown", "Subscription"])
        self.assertEqual(parsed.rows["apieq-body"][0], ["Build", "$1.0000", "$0.5000", "$0.2500", "$1.7500", "100.0%"])
        self.assertEqual(parsed.rows["apieq-body"][1], ["Local", "n/a", "n/a", "n/a", "n/a", "n/a"])
        self.assertEqual(parsed.rows["phase-body"][1][2:], ["n/a"] * 10 + ["Unknown"])
        self.assertEqual(parsed.rows["scenario-body"][1], ["Local"] + ["n/a"] * 5)
        self.assertEqual(parsed.rows["separate-body"][0], ["After", "d", "3", "Unknown", "Unknown", "4", "Unknown"])
        self.assertIn("Receipts: 7 + 2", document)
        self.assertIn("Paid TTS<strong>$0.00</strong>", document)
        self.assertEqual(parsed.sections, ["phases", "coverage", "models", "economics", "events", "sources"])

    def test_media_paths_are_restricted_to_relative_files(self):
        for value in ("chapter-01.mp3", "good-earth/a.mp3"):
            self.assertEqual(report.safe_media_path(value), value)
        for value in ("../a.mp3", "/etc/passwd", "https://x.test/a.mp3", "a b.mp3", 'a".mp3', None, "javascript:x"):
            self.assertIsNone(report.safe_media_path(value))
        self.assertNotIn("<audio", report.render_report({"audio": {"src": "../x.mp3"}}))

    @unittest.skipUnless(shutil.which("node"), "Node not available for embedded JavaScript checks")
    def test_javascript_costs_csv_and_sorting(self):
        checks = r'''
const assert=require('node:assert/strict');
const usage={input_tokens:100,cache_write_tokens:5,cache_read_tokens:50,output_tokens:20};
assert.deepEqual(llmCosts(usage,[1,2,3]),[0.000105,0.0001,0.00006,0.000265]);
assert.deepEqual(llmCosts(usage,[0,0,0]),[0,0,0,0]);
for(const rates of [[1,null,3],[1,-1,3],[1,Infinity,3],[1,NaN,3],[1,true,3],[1,2]])assert.equal(llmCosts(usage,rates),null);
assert.equal(llmCosts({...usage,cache_read_tokens:null},[1,2,3]),null);
assert.equal(llmCosts({...usage,input_tokens:1e308},[1e308,2,3]),null);
assert.deepEqual(energy(3600,150,350,0.15),[0.0225,0.0525]);
assert.equal(energy(null,150,350,0.15),null);
assert.equal(energy(3600,350,150,0.15),null);
assert.equal(energy(1e308,150,350,0.15),null);
assert.equal(csvCell('a,"b"\nc'),'"a,""b""\nc"');
for(const v of ['=SUM(A1)',' +1','\t@cmd','\r-2','\uFEFF=cmd'])assert.equal(csvCell(v)[1],"'");
assert.equal(csvCell(null),'""');assert.equal(csvCell(0),'"0"');
assert.deepEqual(sortLedger([{id:1,time:'2026-10-07T12:00:00Z'},{id:2,time:'2026-10-07T11:00:00Z'},{id:3,time:'2026-10-07T12:00:00Z'},{id:4}]).map(r=>r.id),[2,1,3,4]);
'''
        result = subprocess.run(["node", "-e", report.JS.split("const data=", 1)[0] + checks], capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_cli_and_input_protection_without_writing_report(self):
        with patch.object(Path, "read_text", return_value=json.dumps(fixture())) as read, patch.object(Path, "write_text") as write:
            report.main(["--data", "report-data.json", "--out", "usage-report.html"])
            read.assert_called_once_with(encoding="utf-8")
            self.assertIn("<!doctype html>", write.call_args.args[0])
            self.assertEqual(write.call_args.kwargs, {"encoding": "utf-8"})
        with patch("sys.stderr"), self.assertRaises(SystemExit):
            report.main(["--data", "report-data.json", "--out", "report-data.json"])
        for invalid in ([], {"ledger": "bad"}, {"phases": [None]}, {"costs": None}, {"summary": {"llm_total_tokens": float("nan")}}):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                report.render_report(invalid)


if __name__ == "__main__":
    unittest.main()
