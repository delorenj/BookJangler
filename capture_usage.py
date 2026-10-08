import argparse
import datetime
import errno
import json
import os
import pty
import select
import sqlite3
import subprocess
import time
from pathlib import Path
from typing import Any


DB_PATH = Path.home() / ".local/share/opencode/opencode.db"


def export_session_from_db(session_id: str, db_path: Path = DB_PATH) -> dict[str, Any]:
    """Rebuild the legacy `opencode export` shape from the read-only sqlite store.

    OpenCode v2 removed the `export` subcommand; message/part rows carry the same JSON.
    """
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        session = conn.execute(
            "select id, parent_id, directory, title, time_created from session where id = ?",
            (session_id,),
        ).fetchone()
        if session is None:
            raise RuntimeError(f"Session not found: {session_id}")
        parts: dict[str, list[dict[str, Any]]] = {}
        for part_id, message_id, data in conn.execute(
            "select id, message_id, data from part where session_id = ? order by message_id, id",
            (session_id,),
        ):
            parts.setdefault(message_id, []).append({"id": part_id, **json.loads(data)})
        messages = [
            {"info": {"id": message_id, **json.loads(data)}, "parts": parts.get(message_id, [])}
            for message_id, data in conn.execute(
                "select id, data from message where session_id = ? order by time_created, id",
                (session_id,),
            )
        ]
    finally:
        conn.close()
    return {
        "info": {
            "id": session[0],
            "parentID": session[1],
            "directory": session[2],
            "title": session[3],
            "time": {"created": session[4]},
        },
        "messages": messages,
    }


def export_session(session_id: str, timeout: float = 30) -> dict[str, Any]:
    try:
        return export_session_from_cli(session_id, timeout)
    except (OSError, ValueError, RuntimeError, TimeoutError):
        return export_session_from_db(session_id)


def export_session_from_cli(session_id: str, timeout: float = 30) -> dict[str, Any]:
    master, slave = pty.openpty()
    process = subprocess.Popen(
        ["opencode", "--pure", "export", session_id],
        stdout=slave,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    os.close(slave)
    deadline = time.monotonic() + timeout
    data = bytearray()
    try:
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError(f"Session export timed out: {session_id}")
            ready, _, _ = select.select([master], [], [], remaining)
            if not ready:
                raise TimeoutError(f"Session export timed out: {session_id}")
            try:
                chunk = os.read(master, 65536)
            except OSError as error:
                if error.errno == errno.EIO:
                    break
                raise
            if not chunk:
                break
            data.extend(chunk)
        if process.wait(timeout=max(0.1, deadline - time.monotonic())) != 0:
            raise RuntimeError(f"Session export failed: {session_id}")
        return json.loads(data)
    finally:
        os.close(master)
        if process.poll() is None:
            process.kill()
        process.wait()


def summarize_session(data: dict[str, Any]) -> dict[str, Any]:
    totals = {
        "input_tokens": 0,
        "output_tokens": 0,
        "reasoning_tokens": 0,
        "cache_read_tokens": 0,
        "cache_write_tokens": 0,
        "reported_total_tokens": 0,
        "opencode_reported_cost_usd": 0.0,
        "completed_assistant_messages": 0,
        "incomplete_assistant_messages_excluded": 0,
        "messages_missing_total_tokens": 0,
        "messages_missing_usage": 0,
        "messages_missing_cost": 0,
    }
    models: set[str] = set()
    providers: set[str] = set()
    for message in data.get("messages", []):
        info = message.get("info", {})
        if info.get("role") != "assistant":
            continue
        if not info.get("time", {}).get("completed"):
            totals["incomplete_assistant_messages_excluded"] += 1
            continue
        totals["completed_assistant_messages"] += 1
        tokens = info.get("tokens", {}) or {}
        if not tokens:
            totals["messages_missing_usage"] += 1
        cache = tokens.get("cache", {}) or {}
        for target, source in (
            ("input_tokens", "input"),
            ("output_tokens", "output"),
            ("reasoning_tokens", "reasoning"),
        ):
            totals[target] += tokens.get(source, 0) or 0
        totals["cache_read_tokens"] += cache.get("read", 0) or 0
        totals["cache_write_tokens"] += cache.get("write", 0) or 0
        if tokens.get("total") is None:
            totals["messages_missing_total_tokens"] += 1
        else:
            totals["reported_total_tokens"] += tokens["total"]
        if info.get("cost") is None:
            totals["messages_missing_cost"] += 1
        else:
            totals["opencode_reported_cost_usd"] += info["cost"]
        models.add(info.get("modelID", "unknown"))
        providers.add(info.get("providerID", "unknown"))
    return {
        "session_id": data["info"]["id"],
        "parent_session_id": data["info"].get("parentID"),
        "models": sorted(models),
        "providers": sorted(providers),
        **totals,
    }


def capture(session_ids: list[str]) -> dict[str, Any]:
    sessions = [summarize_session(export_session(sid)) for sid in dict.fromkeys(session_ids)]
    totals = {
        key: sum(session[key] for session in sessions)
        for key, value in sessions[0].items()
        if isinstance(value, (int, float))
    }
    return {
        "captured_at_utc": datetime.datetime.now(datetime.UTC).isoformat(),
        "source": "OpenCode completed assistant-message usage fields",
        "scope": "Explicit main session and delegated sessions; cumulative experiment setup, research, code, and rendering orchestration",
        "totals": totals,
        "sessions": sessions,
        "actual_llm_charge_usd": None,
        "caveats": [
            "Snapshot excludes the current unfinished assistant message, later messages, and final response.",
            "Repeated context/cache reads count on every request; totals are consumption, not unique chapter words.",
            "Reasoning and cache fields are reported separately; do not add them to reported_total_tokens again.",
            "A zero OpenCode-reported cost is not proof of zero provider cost or subscription consumption.",
            "VoxCPM2 text tokens use another tokenizer and are recorded separately in metrics.json.",
            "Search API charges, OCR CPU work, GPU electricity, and other infrastructure costs are not reported.",
        ],
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("session_ids", nargs="+")
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    if not args.out.parent.is_dir():
        parser.error(f"Output parent must already exist: {args.out.parent}")
    report = capture(args.session_ids)
    args.out.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report["totals"], indent=2))


if __name__ == "__main__":
    main()
