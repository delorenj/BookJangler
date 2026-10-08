import math
from collections import defaultdict
from typing import Any, cast


RATES = {"input_tokens": 2.0, "cache_read_tokens": 0.1, "cache_write_tokens": 2.5, "output_tokens": 10.0}


def api_equivalent(usage: dict[str, Any], rates: dict[str, float] | None = None) -> dict[str, Any]:
    prices = rates if rates is not None else RATES
    counts = {field: usage.get(field) for field in RATES}
    for field, value in counts.items():
        if value is not None and (type(value) is not int or value < 0):
            raise ValueError(f"Invalid token count: {field}")
    for field, value in prices.items():
        if field not in RATES or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
            raise ValueError(f"Invalid token price: {field}")
    if any(counts[field] is None or prices.get(field) is None for field in RATES):
        return {"usd": None, "breakdown_usd": None, "basis": "Incomplete usage or prices; not estimated"}
    breakdown = {field: cast(int, counts[field]) * prices[field] / 1_000_000 for field in RATES}
    return {"usd": sum(breakdown.values()), "breakdown_usd": breakdown, "basis": "Counterfactual Standard GPT-6.1 Sol API pricing, not a subscription charge"}


def request_tier_equivalent(usage: dict[str, Any]) -> dict[str, Any]:
    counts = [usage.get(field) for field in ("input_tokens", "cache_read_tokens", "cache_write_tokens")]
    if any(value is None for value in counts):
        return {"usd": None, "tier": "unknown"}
    api_equivalent(usage)
    effective_input = sum(cast(int, value) for value in counts)
    long_context = effective_input > 272_000
    prices = {
        field: rate * (1.5 if field == "output_tokens" else 2.0)
        for field, rate in RATES.items()
    } if long_context else RATES
    return {**api_equivalent(usage, prices), "tier": "long_context" if long_context else "standard", "normalized_input_tokens": effective_input}


def grouped_snapshot_costs(snapshot: dict[str, Any]) -> dict[str, Any]:
    groups = defaultdict(lambda: {field: 0 for field in RATES})
    for session in snapshot["sessions"]:
        group = session["session_id"]
        for field in RATES:
            groups[group][field] += session[field]
    return {group: api_equivalent(usage) for group, usage in groups.items()}


def electricity_scenario(seconds: float, low_watts: float, high_watts: float, price: float) -> dict[str, Any]:
    values = [seconds, low_watts, high_watts, price]
    if any(not math.isfinite(value) or value < 0 for value in values) or high_watts < low_watts:
        raise ValueError("Energy assumptions must be finite, nonnegative, and ordered")
    low, high = (seconds * watts / 3_600_000 for watts in (low_watts, high_watts))
    return {"kwh_low": low, "kwh_high": high, "usd_low": low * price, "usd_high": high * price, "measured": False}
