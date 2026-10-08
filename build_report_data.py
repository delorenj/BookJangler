"""Adapt the CandyStore ledger, pricing assumptions, snapshot and TTS/ASR receipts
into the canonical input of render_usage_report.py.

Rules enforced here:
  * unknowns stay None (rendered "Unknown"), never 0;
  * LLM tokens and VoxCPM2 text tokens are never added together;
  * "subscription, $0 marginal charge" and "API-equivalent estimate" are separate cost lines;
  * LLM totals must reconcile to usage-final-snapshot.json or the build fails.
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from report_costs import api_equivalent, electricity_scenario
from report_runtime import runtime_evidence

ROOT = Path(__file__).resolve().parent
TOKEN_FIELDS = ("input_tokens", "cache_read_tokens", "cache_write_tokens", "output_tokens")

# Chronological order of the report rows. kind: llm sessions vs local engines.
PHASES = [
    ("research", "Research", "llm", "Cast and character research: CLI exploration, two research sessions, and the web searches and page fetches they ran."),
    ("source_preparation", "Source preparation", "llm", "PDF page selection, OCR checks and Chapter 1 text extraction."),
    ("pipeline_engineering", "Pipeline engineering", "llm", "Writing and testing the renderer, plus the usage-capture tooling."),
    ("voice_design", "Voice design", "tts", "Eleven VoxCPM2 voice-design requests, one per character, run locally on the RTX 3090."),
    ("chapter_synthesis", "Chapter synthesis", "tts", "296 VoxCPM2 chunk renders for the full chapter, run locally on the RTX 3090."),
    ("rendering_orchestration", "Rendering orchestration", "llm", "The agent that launched synthesis and assembly, inspected receipts and played back the result."),
    ("audio_quality_engineering", "Audio QA engineering", "llm", "Writing the ASR check and analysing audio quality."),
    ("audio_qa", "Audio QA transcription", "asr", "Local faster-whisper-large-v3-turbo transcription of 35 selected samples, CPU int8."),
]
LLM_GROUPS = [p[0] for p in PHASES if p[2] == "llm"]
EVENT_PHASE = {"unassigned_main_event": "orchestration", "post_snapshot_confirmation": "post_experiment"}
TTS_PHASE = {"voice_design": "voice_design", "narration": "chapter_synthesis", "audio_qa": "audio_qa"}


def short(sid: str | None, roles: dict[str, str]) -> str | None:
    if not sid:
        return None
    return f"{roles.get(sid, 'session')} …{sid[-6:]}"


def tool_details(tools: Any) -> str | None:
    if not isinstance(tools, list) or not tools:
        return None
    return "; ".join(f"tool {t.get('name')} {t.get('status') or ''}".strip() for t in tools[:3] if isinstance(t, dict)) or None


def event_phase(e: dict[str, Any]) -> str:
    """Ledger phase of a CandyStore event: by time first (before/after the final snapshot), then by group."""
    if e["phase"] != "core_experiment":
        return "post_experiment"
    group = e["group"]
    # A pre-snapshot tool call whose owning message was counted after the snapshot is still core by time.
    return "orchestration" if group == "post_snapshot_confirmation" else EVENT_PHASE.get(group, group)


def native_phase(row: dict[str, Any]) -> str:
    return "post_experiment" if row["phase"] != "core_experiment" else row["group"]


def sum_tokens(rows: list[dict[str, Any]]) -> dict[str, int]:
    out = {field: 0 for field in (*TOKEN_FIELDS, "reasoning_tokens", "reported_total_tokens")}
    for row in rows:
        for field in out:
            value = row["tokens"].get(field)
            if value is None:
                raise ValueError(f"Native message {row['message_id']} lacks {field}; refusing to sum")
            out[field] += value
    return out


def llm_block(rows: list[dict[str, Any]]) -> dict[str, Any]:
    t = sum_tokens(rows)
    return {"input_tokens": t["input_tokens"], "cache_read_tokens": t["cache_read_tokens"],
            "cache_write_tokens": t["cache_write_tokens"], "output_tokens": t["output_tokens"],
            "total_tokens": t["reported_total_tokens"], "calls": len(rows)}


def equivalent(usage: dict[str, Any], rates: dict[str, float]) -> dict[str, Any]:
    est = api_equivalent(usage, rates)
    if est["usd"] is None:
        return {"uncached_input_usd": None, "cache_read_usd": None, "cache_write_usd": None, "output_usd": None, "total_usd": None}
    b = est["breakdown_usd"]
    return {"uncached_input_usd": b["input_tokens"], "cache_read_usd": b["cache_read_tokens"],
            "cache_write_usd": b["cache_write_tokens"], "output_usd": b["output_tokens"], "total_usd": est["usd"]}


def build(ledger: dict[str, Any], pricing: dict[str, Any], snapshot: dict[str, Any], runtime: dict[str, Any],
          audio_src: str = "chapter-01.mp3", audio_seconds: float | None = None,
          attribution: dict[str, Any] | None = None) -> dict[str, Any]:
    eq = pricing["llm_api_equivalent"]
    rates = {"input_tokens": eq["uncached_input_usd_per_million"], "cache_read_tokens": eq["cached_input_usd_per_million"],
             "cache_write_tokens": eq["cache_write_usd_per_million"], "output_tokens": eq["output_usd_per_million"]}
    roles = {s["session_id"]: s["role"] for s in ledger["sessions"]}
    native = ledger["llm_usage"]["opencode"]["ledger"]
    core = [r for r in native if r["phase"] == "core_experiment"]
    post = [r for r in native if r["phase"] != "core_experiment"]
    by_group: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in core:
        by_group[row["group"]].append(row)
    unexpected = set(by_group) - set(LLM_GROUPS)
    if unexpected:
        raise ValueError(f"Unmapped LLM groups: {sorted(unexpected)}")

    # Hard reconciliation against the independently captured snapshot.
    core_totals = llm_block(core)
    snap = snapshot["totals"]
    expected = {"input_tokens": snap["input_tokens"], "cache_read_tokens": snap["cache_read_tokens"],
                "cache_write_tokens": snap["cache_write_tokens"], "output_tokens": snap["output_tokens"],
                "total_tokens": snap["reported_total_tokens"], "calls": snap["completed_assistant_messages"]}
    if core_totals != expected:
        raise ValueError(f"LLM ledger does not reconcile to snapshot: {core_totals} != {expected}")
    tts = runtime["tts"]
    if tts["totals"]["input_text_tokens"] != ledger["tts"]["totals"]["input_text_tokens"]:
        raise ValueError("TTS text token totals disagree between metrics and ledger")

    tts_phase = {"voice_design": tts["by_phase"]["design"], "chapter_synthesis": tts["by_phase"]["chapter"]}
    qa_wall = sum(q["wall_seconds"] for q in runtime["qa"])
    qa_samples = sum(q["summary"]["transcribed"] for q in runtime["qa"])
    event_rows = defaultdict(int)

    ledger_rows: list[dict[str, Any]] = []
    for e in ledger["events"]["ledger"]:
        phase = event_phase(e)
        ledger_rows.append({"id": e["event_id"], "time": e["time"], "phase": phase, "source": "CandyStore event",
                            "kind": e["type"].removeprefix("bloodbank."), "session": short(e["session_id"], roles),
                            "provider": e.get("provider"), "model": e.get("model"), "input_tokens": None,
                            "cache_read_tokens": None, "output_tokens": None, "total_tokens": None, "cost_usd": None,
                            "details": tool_details(e.get("tool")) or "Lifecycle metadata only; no prompts or outputs stored"})
    for r in native:
        phase = native_phase(r)
        t = r["tokens"]
        flag = "; phase by time fallback (uncertain)" if r["attribution"]["uncertain"] else ""
        ledger_rows.append({"id": r["message_id"], "time": r["time"], "phase": phase, "source": "OpenCode assistant message",
                            "kind": "llm.message.completed", "session": short(r["session_id"], roles), "provider": r["provider"],
                            "model": r["model"], "input_tokens": t["input_tokens"], "cache_read_tokens": t["cache_read_tokens"],
                            "output_tokens": t["output_tokens"], "total_tokens": t["reported_total_tokens"], "cost_usd": None,
                            "details": f"OpenCode-reported cost {r['cost']['opencode_reported_usd']} is client metadata, not a charge; "
                                       f"{len(r['tools'])} tool calls; attribution {r['attribution']['basis']}{flag}"})
    for r in runtime["ledger"]:
        phase = TTS_PHASE[r["phase"]]
        if phase == "audio_qa":
            detail = r["details"]
        else:
            detail = (f"{r['details']}; {r['tts_text_tokens']} VoxCPM2 text tokens (not LLM tokens); "
                      f"{r['audio_seconds']}s audio in {r['wall_seconds']:.2f}s wall on the local RTX 3090")
        ledger_rows.append({"id": r["id"], "time": r["time"], "phase": phase, "source": "Local engine receipt",
                            "kind": r["kind"], "session": r["session"], "provider": r["provider"], "model": r["model"],
                            "input_tokens": None, "cache_read_tokens": None, "output_tokens": None, "total_tokens": None,
                            "cost_usd": 0, "details": detail})
    for row in ledger_rows:
        event_rows[row["phase"]] += 1

    phases = []
    for pid, label, kind, desc in PHASES:
        entry: dict[str, Any] = {"id": pid, "label": label, "event_count": event_rows[pid]}
        if kind == "llm":
            block = llm_block(by_group[pid])
            uncertain = sum(1 for r in by_group[pid] if r["attribution"]["uncertain"])
            extra = f" {uncertain} of {block['calls']} messages were placed by time fallback." if uncertain else ""
            if pid == "research":
                rt = ledger["events"]["research_tools"]
                provs = Counter()
                for item in rt["ledger"]:
                    provs.update(item.get("wrapper_provider_attempts_by_provider") or {})
                extra += (f" Tools: {rt['invocations_by_operation'].get('search', 0)} searches "
                          f"({', '.join(f'{k} {v}' for k, v in sorted(provs.items()))} provider attempts) and "
                          f"{rt['invocations_by_operation'].get('fetch', 0)} page fetches.")
            entry.update(description=desc + extra, llm=block, tts={"applicable": False},
                         api_equivalent=equivalent(block, rates))
        elif kind == "tts":
            m = tts_phase[pid]
            entry.update(description=desc + " No paid TTS provider; the LLM tokens that designed and launched these runs are counted in the LLM rows.",
                         llm={"applicable": False},
                         tts={"input_text_tokens": m["input_text_tokens"], "calls": m["requests"],
                              "wall_seconds": m["wall_seconds"], "audio_seconds": m["successful_audio_seconds"]})
        else:
            wers = ", ".join(f"{q['summary']['micro_wer']:.2%}" for q in runtime["qa"])
            entry.update(description=f"{desc} Wall {qa_wall:.1f}s, {qa_samples} samples, diagnostic micro WER {wers}.",
                         llm={"applicable": False}, tts={"applicable": False})
        phases.append(entry)

    total_eq = equivalent(core_totals, rates)
    if abs(total_eq["total_usd"] - eq["original_snapshot_standard_usd"]) > 1e-6:
        raise ValueError("API-equivalent total disagrees with report-pricing.json")
    phase_sum = sum(p["api_equivalent"]["total_usd"] for p in phases if "api_equivalent" in p)
    if abs(phase_sum - total_eq["total_usd"]) > 1e-9:
        raise ValueError("Per-phase API-equivalent costs do not sum to the total")
    if max(r["tokens"]["normalized_input_tokens"] for r in core) > eq["long_context_threshold_input_tokens"]:
        raise ValueError("A request exceeded the long-context threshold; standard pricing would understate")

    assump = pricing["assumptions"]
    gpu = electricity_scenario(assump["synthesis_seconds"], assump["gpu_watts_low"], assump["gpu_watts_high"], assump["electricity_usd_per_kwh"])
    research = ledger["events"]["research_tools"]
    provider_attempts: Counter[str] = Counter()
    for item in research["ledger"]:
        provider_attempts.update(item.get("wrapper_provider_attempts_by_provider") or {})
    search_requests = research["wrapper_recorded_upstream_attempts"]
    search_list_high = sum(n * (0.005 if k == "brave" else 0.008) for k, n in provider_attempts.items())

    gw = ledger["llm_usage"]["gateway"]
    diag = gw["unmatched_window_diagnostics_NOT_experiment_totals"]
    diag_text = "; ".join(f"{d['provider']}/{d['model']} {d['rows']}" for d in diag)
    cov = ledger["coverage"]
    post_totals = llm_block(post)
    overhead = ledger["phases"]["excluded_native_messages"].get("report_overhead_messages")
    n_post_events = len(ledger["events"]["ledger"]) - ledger["events"]["core_count"]
    att = attribution if attribution and attribution.get("matched") == core_totals["calls"] and not attribution.get("ambiguous") \
        and not attribution.get("unmatched") else None
    settle = (att or {}).get("settlement")
    settled_zero = bool(settle and settle["rows_found"] == settle["rows_expected"] == att["matched"] and settle["quota_nonzero_rows"] == 0)
    if settled_zero:
        expected_prompt = core_totals["input_tokens"] + core_totals["cache_read_tokens"] + core_totals["cache_write_tokens"]
        if settle["prompt_tokens_sum"] != expected_prompt or settle["completion_tokens_sum"] != core_totals["output_tokens"]:
            raise ValueError("NewAPI settlement tokens disagree with the OpenCode snapshot")
    msg_session = {r["message_id"]: r["session_id"] for r in core}
    matched_by_session = Counter(msg_session[m["message_id"]] for m in att["matches"]) if att else Counter()
    n_gateway_rows = gw["core_occurrence_rows"]
    n_other_rows = n_gateway_rows - (att["matched"] if att else 0)
    other_text = "; ".join(f"{d['provider']}/{d['model']} {d['rows']}" for d in att["other_gateway_rows"]) if att else diag_text
    audio_s = audio_seconds if audio_seconds is not None else runtime["tts"]["assembly"]["raw"]["wav"]["duration_seconds"]

    data = {
        "title": "The Good Earth, Chapter 1: usage and cost report",
        "generated_at": ledger["collected_at_utc"],
        "scope": {
            "start": cov["experiment_start_utc"], "end": cov["core_cutoff_utc"],
            "cutoff": f"Final usage snapshot {snapshot['captured_at_utc']}",
            "description": ("A public-domain chapter turned into a 50-minute multi-voice audiobook. Character research and per-character voice "
                            "design ran through OpenCode on a subscription-backed LLM route; every second of speech was synthesized locally by "
                            "VoxCPM2 on an RTX 3090. Usage is grouped by phase and backed by a CandyStore event ledger."),
        },
        "summary": {
            "llm_total_tokens": core_totals["total_tokens"], "llm_calls": core_totals["calls"],
            "tts_text_tokens": tts["totals"]["input_text_tokens"], "tts_calls": tts["totals"]["requests"],
            "audio_seconds": audio_s, "synth_wall_seconds": tts["totals"]["wall_seconds"], "actual_total_usd": None,
            "highlights": [
                {"label": "LLM API-equivalent (hypothetical)", "value": f"${total_eq['total_usd']:.2f}"},
                {"label": "LLM marginal charge (subscription)", "value": "$0.00"},
                {"label": "Paid TTS", "value": "$0.00 · 100% local"},
                {"label": "Assumed GPU energy", "value": f"${gpu['usd_low']:.3f} – ${gpu['usd_high']:.3f}"},
            ],
        },
        "callouts": [
            {"title": "Every spoken word was synthesized on the local RTX 3090", "tone": "good",
             "body": (f"{tts['totals']['requests']} requests ({tts_phase['voice_design']['requests']} voice-design, {tts_phase['chapter_synthesis']['requests']} chapter chunks) "
                      f"went to the self-hosted VoxCPM2 service ({tts['model']}, package {tts['package_version']}) on big-chungus's {tts['device']}. "
                      f"{tts['totals']['wall_seconds']:.0f} s of synthesis produced the {int(audio_s // 60)}:{round(audio_s % 60):02d} chapter. "
                      "No paid TTS provider and no cloud fallback were called. Quality checks used a local faster-whisper model on CPU.")},
            {"title": "The LLM was a subscription route: $0 marginal gateway charge", "tone": "info",
             "body": (f"OpenCode's sol-6.1 maps to gateway route {eq['gateway_route']}, native {eq['native_model']} on account {eq['gateway_account']}, billed as {eq['actual_billing_path']}. "
                      + (f"All {att['matched']} requests were matched to gateway rows (exact token tuple and time) and every one settled at 0 quota in the NewAPI ledger. " if settled_zero else "Sampled gateway settlements for that route were $0. ")
                      + "The fixed subscription fee is not allocated to this experiment"
                      + ("; the message-to-row link is an inference because the gateway rows carry no message key (see Coverage)." if att else ", and per-request settlement could not be attributed to these sessions (see Coverage)."))},
            {"title": f"${total_eq['total_usd']:.2f} is an API-equivalent estimate, not a bill", "tone": "warn",
             "body": (f"If the same {core_totals['total_tokens']:,} tokens had been bought at published pay-as-you-go GPT-6.1 Sol rates "
                      f"(${rates['input_tokens']:.2f} uncached input, ${rates['cache_read_tokens']:.2f} cache read, ${rates['output_tokens']:.2f} output per million) it would cost about ${total_eq['total_usd']:.2f}. Nobody was charged this.")},
        ],
        "audio": {"src": audio_src, "label": "Chapter 1 audiobook", "duration_seconds": audio_s,
                  "note": "VoxCPM2 multi-voice render, 128 kbps MP3."},
        "phases": phases,
        "separate_work": [
            {"label": "Post-experiment confirmation", "description": "Main-session exchange after the final snapshot and before the HTML report request. Kept out of every total.",
             "llm": post_totals},
            {"label": "Report-request work", "description": f"Messages from the HTML-report request onward ({overhead} completed assistant messages seen when the ledger was collected). Excluded and not measured; this work is still growing.",
             "llm": {"input_tokens": None, "cache_read_tokens": None, "output_tokens": None, "total_tokens": None, "calls": overhead}},
        ],
        "models": [
            {"provider": "OpenAI (via AutomaticAI gateway)", "route": f"{eq['gateway_route']} (OpenCode alias automaticai/sol-6.1)", "native_model": eq["native_model"],
             "activity": f"{core_totals['calls']} completed assistant messages across 7 sessions: research, extraction, engineering, rendering, QA",
             "billing": f"Subscription ({eq['gateway_account']}); " + (f"$0 marginal gateway charge on all {att['matched']} matched requests" if settled_zero else "$0 marginal gateway charge on sampled rows"),
             "evidence": f"Route {eq['route_evidence']}; NewAPI ledger samples {', '.join(map(str, eq['historical_route_samples']['newapi_ledger_ids']))} returned {eq['historical_route_samples']['returned_model']}. " + (f"All {att['matched']} messages matched one-to-one to gateway rows (account {', '.join(att['accounts'])}, billing class {', '.join(att['billing_classes'])})." if att else "Per-message provider receipts were not linkable.")},
            {"provider": "Self-hosted (vox service)", "route": "voxxy CLI to vox.delo.sh, VoxCPM2 engine", "native_model": tts["model"],
             "activity": f"{tts['totals']['requests']} TTS requests, {tts['totals']['input_text_tokens']:,} text tokens, {tts['totals']['wall_seconds']:.0f} s wall on {tts['device']}",
             "billing": "No paid provider; electricity is an assumption",
             "evidence": f"requests.jsonl receipts and metrics.json; revision {tts['revision']}"},
            {"provider": "Self-hosted (CPU, int8)", "route": "local faster-whisper", "native_model": runtime["qa"][0]["model"]["id"],
             "activity": f"{qa_samples} audio samples transcribed in {qa_wall:.0f} s for quality checks",
             "billing": "No paid provider", "evidence": "qa-refs.json and qa-chunks.json receipts"},
            {"provider": "Tavily and Brave (web-search wrapper)", "route": "web-search search and fetch", "native_model": None,
             "activity": f"{research['invocations_by_operation'].get('search', 0)} searches ({', '.join(f'{k} {v}' for k, v in sorted(provider_attempts.items()))} provider attempts), {research['invocations_by_operation'].get('fetch', 0)} page fetches",
             "billing": "Unknown plan allowance; no receipts for charges",
             "evidence": "Wrapper attempt receipts in OpenCode tool output; fetches had no attempt receipts"},
        ],
        "costs": {
            "actual": {"llm_usd": 0, "tts_provider_usd": 0, "search_usd": None, "energy_usd": None},
            "actual_basis": {
                "llm_usd": ("Marginal gateway charge on a subscription route: NewAPI quota was 0 for each of the " + str(att["matched"]) + " requests matched to these messages. Not the subscription fee."
                            if settled_zero else "Marginal gateway charge on a subscription route; sampled settlements were $0. Not a per-request attribution and not the subscription fee."),
                "tts_provider_usd": "Only the self-hosted VoxCPM2 endpoint was called; no paid provider or fallback.",
                "search_usd": "Plan allowance and billing for the Tavily and Brave calls were not obtained.",
                "energy_usd": "Power was not metered. See the assumed range in the cost views.",
            },
            "views": [
                {"label": "LLM marginal gateway charge", "usd": 0, "kind": "actual", "basis": (f"Subscription route; NewAPI quota 0 on all {att['matched']} matched requests. " if settled_zero else "Subscription route; sampled NewAPI settlements at $0. ") + "Fixed monthly subscription is not allocated."},
                {"label": "Paid TTS and ASR providers", "usd": 0, "kind": "actual", "basis": "VoxCPM2 and faster-whisper ran locally; no paid endpoint was invoked."},
                {"label": "LLM API-equivalent (research, engineering, rendering, QA)", "usd": total_eq["total_usd"], "kind": "estimate",
                 "basis": f"{core_totals['input_tokens']:,} uncached + {core_totals['cache_read_tokens']:,} cache read + {core_totals['output_tokens']:,} output tokens at Standard API rates. Counterfactual."},
                {"label": "GPU electricity (assumed)", "usd": gpu["usd_low"], "usd_high": gpu["usd_high"], "kind": "estimate",
                 "basis": f"{assump['gpu_watts_low']}-{assump['gpu_watts_high']} W GPU only x {assump['synthesis_seconds']:.1f} s x ${assump['electricity_usd_per_kwh']}/kWh. Not metered."},
                {"label": "Web search at list price (upper bound)", "usd": 0, "usd_high": search_list_high, "kind": "estimate",
                 "basis": "Zero if inside free allowances; upper bound uses $0.005 per Brave and $0.008 per Tavily Basic request. Fetches assumed free."},
                {"label": "Everything at list price (ceiling)", "usd": total_eq["total_usd"] + gpu["usd_low"],
                 "usd_high": total_eq["total_usd"] + gpu["usd_high"] + search_list_high, "kind": "estimate",
                 "basis": "API-equivalent LLM plus assumed electricity plus search at list price. A ceiling for comparison only."},
            ],
            "assumptions": {
                "llm_uncached_per_million": rates["input_tokens"], "llm_output_per_million": rates["output_tokens"],
                "llm_cache_read_per_million": rates["cache_read_tokens"], "search_per_request": None, "search_requests": search_requests,
                "gpu_watts_low": assump["gpu_watts_low"], "gpu_watts_high": assump["gpu_watts_high"],
                "electricity_usd_per_kwh": assump["electricity_usd_per_kwh"], "synthesis_seconds": assump["synthesis_seconds"],
            },
            "explanation": pricing["explanation"],
            "sources": pricing["sources"],
        },
        "coverage": {
            "status": ("LLM tokens fully reconciled; gateway settlement observed per request (message link inferred)" if settled_zero
                       else "LLM tokens fully reconciled; gateway settlement not attributable"),
            "description": (f"OpenCode assistant-message usage for all 7 sessions matches usage-final-snapshot.json exactly ({core_totals['calls']} messages, {core_totals['total_tokens']:,} tokens). "
                            f"All {cov['query_count']} CandyStore queries were exhausted. " + (
                                f"The gateway usage events carry no message key, so each OpenCode message was linked to its gateway row by exact token tuple and timestamp: "
                                f"{att['matched']} of {att['messages_considered']} matched uniquely and one-to-one"
                                + (f", all with quota 0 in the NewAPI ledger (summed prompt {settle['prompt_tokens_sum']:,} = uncached + cache read, completion {settle['completion_tokens_sum']:,})." if settled_zero else ".")
                                if att else "but the gateway usage events carry no message key linking them to these sessions, so none were charged to the experiment.")),
            "queried_events": cov["candidate_unique_event_count"] + n_post_events,
            "usage_events": gw["core_occurrence_rows"] + len(gw["outside_core_occurrence_rows_not_charged"]),
            "counts": [
                {"label": "CandyStore events matched to the experiment", "value": f"{ledger['events']['core_count']} core + {n_post_events} post-snapshot"},
                {"label": "OpenCode messages", "value": f"{core_totals['calls']} core + {post_totals['calls']} post-snapshot"},
                {"label": "Local receipts", "value": f"{tts['totals']['requests']} TTS + {len(runtime['qa'])} ASR batches"},
                {"label": "Gateway usage rows attributed", "value": (f"{att['matched']} of {n_gateway_rows} by token+time inference (no message key); {n_other_rows} belong to other traffic"
                                                                 if att else f"{ledger['reconciliation']['deterministically_attributed_gateway_rows']} of {n_gateway_rows}")},
                {"label": "Embedded ledger rows", "value": str(len(ledger_rows))},
            ],
            "session_coverage": [
                {"id": s["session_id"], "label": s["role"].replace("_", " "), "events": s["candystore_events"], "usage_events": matched_by_session.get(s["session_id"], 0),
                 "llm_calls": s["completed_core_messages"]} for s in ledger["sessions"]],
            "notes": [
                (f"Gateway rows in the window: {n_gateway_rows}. {att['matched']} are this experiment's requests (matched as described above, all {', '.join(att['routes'])} on {', '.join(att['accounts'])}, subscription class). "
                 f"The other {n_other_rows} are concurrent traffic from other agents and are not charged here: {other_text}."
                 if att else f"Unattributed gateway traffic in the window ({n_gateway_rows} rows, not linkable to these sessions, may include this experiment's own requests): {diag_text}."),
                "Phase attribution of main-session messages follows actual tool calls first, then the latest child phase start; uncertain placements are flagged in the ledger.",
                "Lifecycle events with no tool call (session and turn markers) appear as phase 'orchestration'; their tokens sit inside the other phases.",
                *[c for c in ledger["caveats"] if not (att and c.startswith("Unknown gateway session/message/request linkage"))],
                *(["Gateway rows are linked to messages by an inferred match (the rows have no message key); NewAPI quota was read per matched request from the gateway database, "
                   "while the CandyStore usage schema itself has no monetary amount."] if att else []),
            ],
        },
        "ledger": sorted(ledger_rows, key=lambda r: (r["time"] or "", r["id"] or "")),
        "notes": [
            "LLM tokens (OpenCode usage) and VoxCPM2 text tokens use different tokenizers and are never added together.",
            "Cache reads count on every request; totals are consumption, not unique chapter words.",
            "Reasoning tokens were reported as 0 for every message.",
            "Voice design and chapter synthesis rows show n/a for LLM tokens because the tokens that drove them are inside the LLM rows.",
            f"Audio seconds in the phase table are raw synthesized chunk audio (voice-design samples {tts_phase['voice_design']['successful_audio_seconds']:.2f} s; chapter chunks "
            f"{tts_phase['chapter_synthesis']['successful_audio_seconds']:.2f} s). The {audio_s:.2f} s chapter adds {runtime['tts']['assembly']['raw']['pause_seconds']:.2f} s of assembled pauses.",
            "Events column counts ledger rows per phase. Session and turn lifecycle events with no phase appear as 'orchestration' and events after the final snapshot as 'post_experiment'; both are outside the phase table.",
            f"Collected {ledger['collected_at_utc']} from CandyStore ({cov['http_request_count']} GET requests), OpenCode's local session store, and local TTS/ASR receipts.",
        ],
        "sources": pricing["sources"],
    }
    return data


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Build report-data.json for render_usage_report.py")
    parser.add_argument("--ledger", type=Path, default=ROOT / "report-ledger.json")
    parser.add_argument("--pricing", type=Path, default=ROOT / "report-pricing.json")
    parser.add_argument("--snapshot", type=Path, default=ROOT / "usage-final-snapshot.json")
    parser.add_argument("--attribution", type=Path, default=ROOT / "report-gateway-attribution.json")
    parser.add_argument("--out", type=Path, default=ROOT / "report-data.json")
    parser.add_argument("--audio-src", default="chapter-01.mp3")
    parser.add_argument("--audio-seconds", type=float, default=None, help="Final MP3 duration; defaults to the assembly record")
    args = parser.parse_args(argv)
    try:
        data = build(json.loads(args.ledger.read_text()), json.loads(args.pricing.read_text()),
                     json.loads(args.snapshot.read_text()), runtime_evidence(), args.audio_src, args.audio_seconds,
                     json.loads(args.attribution.read_text()) if args.attribution.exists() else None)
    except (OSError, ValueError, KeyError) as error:
        parser.error(f"{type(error).__name__}: {error}")
    args.out.write_text(json.dumps(data, indent=1, allow_nan=False) + "\n")
    print(json.dumps({"ledger_rows": len(data["ledger"]), "phases": len(data["phases"]),
                      "llm_total_tokens": data["summary"]["llm_total_tokens"]}), file=sys.stdout)


if __name__ == "__main__":
    main()
