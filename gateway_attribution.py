"""Link OpenCode assistant messages to gateway usage rows and NewAPI settlements.

The gateway usage events carry no message key, so the link is an inference: an
OpenCode message and a gateway row describe the same request when their exact
(uncached input, cache read, output) token tuple is equal and the gateway row
timestamp falls inside the message's [started, completed] interval (+/- slack).
A message is attributed only when exactly one gateway row qualifies and no
gateway row is claimed twice. Anything else stays unattributed.

Settlement is then read from the NewAPI `logs` table (read-only SELECT through
`docker exec`; no credentials leave the container) so `quota` is observed per
request instead of sampled.

    python3 gateway_attribution.py            # writes report-gateway-attribution.json
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent
SLACK_SECONDS = 30


def epoch(stamp: str) -> float:
    return datetime.fromisoformat(stamp.replace("Z", "+00:00")).timestamp()


def token_key(tokens: dict[str, Any]) -> tuple[Any, Any, Any]:
    return (tokens.get("input_tokens"), tokens.get("cache_read_tokens"), tokens.get("output_tokens"))


def match_messages(messages: list[dict[str, Any]], gateway_rows: list[dict[str, Any]],
                   slack: float = SLACK_SECONDS) -> dict[str, Any]:
    """Return one-to-one matches plus the messages that could not be matched unambiguously."""
    by_key: dict[tuple, list[dict[str, Any]]] = defaultdict(list)
    for row in gateway_rows:
        if row.get("outcome") == "completed" and None not in token_key(row["tokens"]):
            by_key[token_key(row["tokens"])].append(row)
    matches, ambiguous, missing = [], [], []
    claimed: Counter[Any] = Counter()
    for msg in messages:
        start, end = epoch(msg["started_at"]), epoch(msg["time"])
        found = [r for r in by_key.get(token_key(msg["tokens"]), []) if start - slack <= epoch(r["time"]) <= end + slack]
        if len(found) == 1:
            claimed[found[0]["gateway_log_id"]] += 1
            matches.append({"message_id": msg["message_id"], "gateway_log_id": found[0]["gateway_log_id"],
                            "request_id": found[0]["request_id"], "account_id": found[0]["account_id"],
                            "billing_class": found[0]["billing_class"], "route": found[0]["route"],
                            "tokens": dict(zip(("input_tokens", "cache_read_tokens", "output_tokens"), token_key(msg["tokens"])))})
        elif found:
            ambiguous.append(msg["message_id"])
        else:
            missing.append(msg["message_id"])
    doubled = sorted(i for i, n in claimed.items() if n > 1)
    if doubled:  # a gateway row explaining two messages is not a clean attribution
        ambiguous += sorted(m["message_id"] for m in matches if m["gateway_log_id"] in doubled)
        matches = [m for m in matches if m["gateway_log_id"] not in doubled]
    return {"matches": matches, "ambiguous": ambiguous, "unmatched": missing, "doubly_claimed_gateway_rows": doubled}


def newapi_settlements(ids: list[int], container: str = "newapi-postgres") -> dict[int, dict[str, Any]]:
    """Read quota/tokens/model for the given NewAPI log ids. Read-only SELECT."""
    if not ids or not all(isinstance(i, int) for i in ids):
        raise ValueError("ids must be a non-empty list of integers")
    sql = ("select id,quota,prompt_tokens,completion_tokens,model_name from logs where id in (%s) order by id"
           % ",".join(str(i) for i in ids))
    script = 'psql -U "$POSTGRES_USER" -d "${POSTGRES_DB:-$POSTGRES_USER}" -At -F"|" -c "%s"' % sql
    out = subprocess.run(["docker", "exec", container, "sh", "-c", script], check=True,
                         capture_output=True, text=True, timeout=60).stdout
    rows = {}
    for line in out.splitlines():
        if not re.fullmatch(r"\d+\|-?\d+\|\d+\|\d+\|.*", line):
            continue
        i, quota, prompt, completion, model = line.split("|", 4)
        rows[int(i)] = {"quota": int(quota), "prompt_tokens": int(prompt), "completion_tokens": int(completion), "model": model}
    return rows


def build(ledger: dict[str, Any], settlements: dict[int, dict[str, Any]] | None = None) -> dict[str, Any]:
    core = [r for r in ledger["llm_usage"]["opencode"]["ledger"] if r["phase"] == "core_experiment"]
    gateway = ledger["llm_usage"]["gateway"]["ledger"]
    result = match_messages(core, gateway)
    ids = sorted(m["gateway_log_id"] for m in result["matches"])
    out: dict[str, Any] = {
        "method": ("Exact (uncached input, cache read, output) token tuple + gateway timestamp inside the message interval "
                   f"(+/-{SLACK_SECONDS}s), exactly one candidate, no gateway row claimed twice. Inference: the gateway usage events carry no message key."),
        "messages_considered": len(core), "matched": len(result["matches"]),
        "ambiguous": result["ambiguous"], "unmatched": result["unmatched"],
        "doubly_claimed_gateway_rows": result["doubly_claimed_gateway_rows"],
        "billing_classes": dict(Counter(m["billing_class"] for m in result["matches"])),
        "accounts": dict(Counter(m["account_id"] for m in result["matches"])),
        "routes": dict(Counter(m["route"] for m in result["matches"])),
        "matches": result["matches"],
    }
    if settlements is not None:
        sel = [settlements.get(i) for i in ids]
        out["settlement"] = {
            "source": "NewAPI logs table (read-only SELECT)", "rows_found": sum(1 for s in sel if s), "rows_expected": len(ids),
            "quota_sum": sum(s["quota"] for s in sel if s), "quota_nonzero_rows": sum(1 for s in sel if s and s["quota"] != 0),
            "prompt_tokens_sum": sum(s["prompt_tokens"] for s in sel if s),
            "completion_tokens_sum": sum(s["completion_tokens"] for s in sel if s),
            "models": sorted({s["model"] for s in sel if s}),
        }
    matched = set(ids)
    other = Counter((r["provider"], r["model"]) for r in gateway if r["gateway_log_id"] not in matched)
    out["other_gateway_rows"] = [{"provider": p, "model": m, "rows": n} for (p, m), n in sorted(other.items())]
    return out


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--ledger", type=Path, default=ROOT / "report-ledger.json")
    parser.add_argument("--out", type=Path, default=ROOT / "report-gateway-attribution.json")
    parser.add_argument("--no-settlement", action="store_true", help="skip the NewAPI database read")
    args = parser.parse_args(argv)
    ledger = json.loads(args.ledger.read_text())
    first = build(ledger)
    settlements = None if args.no_settlement or not first["matches"] else newapi_settlements(
        sorted(m["gateway_log_id"] for m in first["matches"]))
    result = build(ledger, settlements)
    args.out.write_text(json.dumps(result, indent=1, allow_nan=False) + "\n")
    print(json.dumps({k: result[k] for k in ("messages_considered", "matched")} | {"settlement": result.get("settlement")}), file=sys.stdout)


if __name__ == "__main__":
    main()
