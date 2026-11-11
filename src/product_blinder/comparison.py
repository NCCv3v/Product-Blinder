"""Paired, cluster-bootstrap uncertainty. No attribution of provider causes."""

import random
import statistics

from .evaluators import ROLES


def summarize(traces):
    means, counts = {}, {}
    for role in ROLES:
        values = [t["metrics"][role]["score"] for t in traces if t["metrics"][role]["score"] is not None]
        means[role] = statistics.mean(values) if values else None
        counts[role] = len(values)
    case_scores = [t["score"] for t in traces if t["score"] is not None]
    return {
        "score": statistics.mean(case_scores) if case_scores else None,
        "per_evaluator": means,
        "sample_sizes": counts,
        "case_count": len(traces),
        "errors": sum(bool(t.get("error")) for t in traces),
        "latency_ms_mean": statistics.mean(t["latency_ms"] for t in traces) if traces else None,
        "cost_usd": None,
        "tokens": None,
    }


def compare(baseline, candidate, threshold=0.05, min_pairs=10):
    out = {
        "verdict": "insufficient-data",
        "baseline_id": baseline["id"] if baseline else None,
        "candidate_id": candidate["id"],
        "paired_cases": 0,
        "independent_units": 0,
        "delta": None,
        "ci95": None,
        "threshold": threshold,
        "per_case": [],
        "warning": "Small samples and synthetic cases do not establish real-world model quality or its causes.",
    }
    if not baseline:
        out["reason"] = "No baseline: explicitly select a completed run as baseline"
        return out
    if (
        baseline["suite_hash"] != candidate["suite_hash"]
        or baseline["scoring_hash"] != candidate["scoring_hash"]
    ):
        out["reason"] = "Frozen suite or evaluator/judge configuration changed; comparison rejected"
        return out
    before = {t["case_id"]: t for t in baseline["traces"]}
    clusters = {}
    for t in candidate["traces"]:
        b = before.get(t["case_id"])
        if not b or t["score"] is None or b["score"] is None:
            continue
        a_roles = {r for r, m in b["metrics"].items() if m["score"] is not None}
        c_roles = {r for r, m in t["metrics"].items() if m["score"] is not None}
        if a_roles != c_roles or any(
            m["method"] == "judge-error" for m in list(t["metrics"].values()) + list(b["metrics"].values())
        ):
            out["reason"] = "Evaluator coverage changed or judge failed; comparison rejected"
            return out
        delta = t["score"] - b["score"]
        out["per_case"].append(
            {"case_id": t["case_id"], "baseline": b["score"], "candidate": t["score"], "delta": delta}
        )
        clusters.setdefault(t["family"] or t["case_id"], []).append(delta)
    out["paired_cases"] = len(out["per_case"])
    out["independent_units"] = len(clusters)
    if out["paired_cases"] != len(candidate["traces"]) or len(clusters) < min_pairs:
        out["reason"] = f"Need complete paired scores and at least {min_pairs} independent case families"
        return out
    units = list(clusters.values())
    values = [v for group in units for v in group]
    delta = statistics.mean(values)
    rng = random.Random(42)
    boot = []
    for _ in range(2000):
        sample = [v for g in rng.choices(units, k=len(units)) for v in g]
        boot.append(statistics.mean(sample))
    boot.sort()
    low, high = boot[49], boot[1949]
    out.update(delta=delta, ci95=[low, high])
    if high < -threshold:
        verdict = "regression"
    elif low > threshold:
        verdict = "improved"
    elif low >= -threshold and high <= threshold:
        verdict = "stable"
    else:
        verdict = "insufficient-data"
    out.update(
        verdict=verdict,
        reason="Paired case delta with deterministic 2,000-resample family-cluster bootstrap; 95% percentile interval",
    )
    return out


def percentile(values, p):
    if not values:
        return None
    a = sorted(values)
    index = (len(a) - 1) * p
    low = int(index)
    high = min(low + 1, len(a) - 1)
    return a[low] + (a[high] - a[low]) * (index - low)


def performance(traces, duration_s):
    """Rates use observed final case outcomes, excluding harness limits and unobserved cancellation; judge time is in wall duration."""
    attempted = [t for t in traces if not t.get("harness_error")]
    latencies = [t["latency_ms"] for t in attempted]
    n = len(attempted)
    errors = sum(bool(t.get("error")) for t in attempted)
    timeouts = sum("Timeout" in (t.get("error") or "") for t in attempted)
    return {
        "duration_s": duration_s,
        "attempted_cases": n,
        "completed_cases": len(traces),
        "target_success_rate": (n - errors) / n if n else None,
        "target_error_rate": errors / n if n else None,
        "timeout_rate": timeouts / n if n else None,
        "latency_ms_mean": statistics.mean(latencies) if latencies else None,
        "latency_ms_p50": percentile(latencies, 0.5),
        "latency_ms_p95": percentile(latencies, 0.95),
        "throughput_cases_s": n / duration_s if duration_s > 0 and n else None,
        "ttft_ms": None,
    }
