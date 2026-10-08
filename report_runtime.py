import json
from pathlib import Path
from typing import Any

from render_chapter import request_records


ROOT = Path(__file__).resolve().parent
AUDIO = ROOT / "output" / "voxcpm2-chapter-01"


def runtime_evidence() -> dict[str, Any]:
    metrics = json.loads((AUDIO / "metrics.json").read_text())
    requests = request_records(AUDIO)
    records = []
    for row in requests:
        records.append({
            "id": row["request_id"],
            "time": row["timestamp_utc"],
            "completed_at": row.get("completed_utc"),
            "phase": "voice_design" if row["phase"] == "design" else "narration",
            "source": "Local TTS receipt",
            "kind": "tts.synthesis." + row["status"],
            "session": "local-engine",
            "provider": "Self-hosted",
            "model": "openbmb/VoxCPM2",
            "input_tokens": None,
            "cache_read_tokens": None,
            "output_tokens": None,
            "total_tokens": None,
            "tts_text_tokens": row["input_text_tokens"],
            "cost_usd": 0,
            "wall_seconds": row["wall_seconds"],
            "audio_seconds": (row.get("wav") or {}).get("duration_seconds"),
            "details": f"{row['speaker']}; {row['phase']}; item {row['item_id']}; {row['status']}",
        })
    qa = []
    for filename in ("qa-refs.json", "qa-chunks.json"):
        data = json.loads((AUDIO / filename).read_text())
        qa.append({
            "source": filename,
            "time": data["timestamp_utc"],
            "model": data["model"],
            "wall_seconds": data["wall_seconds"],
            "summary": data["summary"],
            "selection": data["selection"],
        })
        records.append({
            "id": filename,
            "time": data["timestamp_utc"],
            "phase": "audio_qa",
            "source": "Local ASR receipt",
            "kind": "asr.batch." + data["status"],
            "session": "local-engine",
            "provider": "Self-hosted",
            "model": data["model"]["id"],
            "input_tokens": None,
            "cache_read_tokens": None,
            "output_tokens": None,
            "total_tokens": None,
            "cost_usd": 0,
            "wall_seconds": data["wall_seconds"],
            "details": f"CPU int8 offline; {data['summary']['transcribed']} samples; diagnostic WER {data['summary']['micro_wer']:.2%}",
        })
    assert len(requests) == metrics["totals"]["requests"] == 307
    assert sum(row["input_text_tokens"] for row in requests) == 9906
    assert all(row["engine"] == "voxcpm" for row in requests)
    return {
        "tts": {
            "model": "openbmb/VoxCPM2",
            "revision": requests[0]["model_revision"],
            "package_version": "2.0.3",
            "device": "NVIDIA RTX 3090",
            "token_label": metrics["token_label"],
            "totals": metrics["totals"],
            "by_phase": metrics["by_phase"],
            "by_speaker": metrics["by_speaker"],
            "assembly": metrics["assembly"],
        },
        "qa": qa,
        "ledger": records,
        "caveats": [
            "Local TTS and ASR receipts are not claimed to be CandyStore events.",
            "VoxCPM2 text tokens are a separate tokenizer; generated acoustic tokens and reference features were not exposed.",
            "ASR was offline CPU int8; its internal token usage was not exposed.",
            "No paid TTS or ASR provider endpoint was invoked; local electricity and hardware costs are separate.",
        ],
    }


if __name__ == "__main__":
    evidence = runtime_evidence()
    print(json.dumps({"tts_requests": evidence["tts"]["totals"]["requests"], "tts_text_tokens": evidence["tts"]["totals"]["input_text_tokens"], "qa_batches": len(evidence["qa"]), "ledger_rows": len(evidence["ledger"])}))
