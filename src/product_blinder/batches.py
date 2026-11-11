"""Five full repetitions per session, one frozen suite/rubric and one global budget."""

import copy
import httpx
from collections import Counter
from datetime import datetime
import statistics
import time
import uuid
from zoneinfo import ZoneInfo
from .adapters import Budget
from .comparison import compare, performance, summarize
from .evaluators import ROLES, norm
from .agents import plan_testers
from .analysis import analysis_report, find_problems
from .judge import JUDGE_PROMPT
from .models import fingerprint
from .runner import preflight, run_suite


def now():
    return datetime.now(ZoneInfo("Asia/Hong_Kong")).isoformat()


def estimate(suite, target, config, repetitions=5):
    target_n = len(suite.cases) * repetitions
    judge_n = (
        repetitions
        * sum(int(c.expected is not None or bool(c.rubric)) + int(bool(c.evidence)) for c in suite.cases)
        if config.use_judge
        else 0
    )
    if config.use_agent_planner:
        judge_n += 1
    reserve = (target_n * (config.target_call_cap_usd or 0) if target.kind != "fixture" else 0) + judge_n * (
        config.judge_call_cap_usd or 0
    )
    return {
        "repetitions": repetitions,
        "cases_per_repetition": len(suite.cases),
        "minimum_target_attempts": target_n,
        "minimum_judge_attempts": judge_n,
        "planned_initial_reservation_usd": reserve,
        "attempts_with_max_retries": (target_n + judge_n) * (1 + config.retry),
        "warning": "Declared per-attempt reservations, NOT actual/verified provider cost. Retries also consume the shared ceilings. Calls already sent may be billed after cancellation.",
    }


def batch_scoring_hash(config):
    return fingerprint(
        {
            "scoring": config.scoring_digest,
            "judge_prompt": JUDGE_PROMPT if config.use_judge else None,
            "timeout": config.timeout_s,
            "retry": config.retry,
            "concurrency": config.concurrency,
            "batch_engine": "pb-batch-v2",
        }
    )


def aggregate(runs, expected_repetitions):
    complete = [r for r in runs if r["status"] == "complete"]
    case_ids = [t["case_id"] for t in complete[0]["traces"]] if complete else []
    traces = []
    consistencies = []
    for cid in case_ids:
        values = [next(t for t in r["traces"] if t["case_id"] == cid) for r in complete]
        first = values[0]
        score_values = [t["score"] for t in values if t["score"] is not None]
        output_counts = Counter(norm(t["output"]) for t in values)
        consistency = max(output_counts.values()) / len(values)
        consistencies.append(consistency)
        metrics = {}
        for role in ROLES:
            scores = [t["metrics"][role]["score"] for t in values if t["metrics"][role]["score"] is not None]
            metrics[role] = {
                "score": statistics.mean(scores) if len(scores) == len(values) else None,
                "reason": "Mean over complete repetitions only; N/A if coverage is missing",
                "method": first["metrics"][role]["method"],
            }
        traces.append(
            {
                "case_id": cid,
                "family": first["family"],
                "metrics": metrics,
                "score": statistics.mean(score_values) if len(score_values) == len(values) else None,
                "score_sd": statistics.stdev(score_values) if len(score_values) > 1 else None,
                "output_consistency": consistency,
                "repetitions_observed": len(values),
                "latency_ms": statistics.mean(t["latency_ms"] for t in values),
                "error": None,
            }
        )
    scores = [r["summary"]["score"] for r in complete if r["summary"]["score"] is not None]
    all_traces = [t for r in runs for t in r["traces"]]
    duration = sum(r["performance"]["duration_s"] for r in runs)
    return {
        "complete_repetitions": len(complete),
        "expected_repetitions": expected_repetitions,
        "score_mean": statistics.mean(scores) if scores else None,
        "score_min": min(scores) if scores else None,
        "score_max": max(scores) if scores else None,
        "score_sd": statistics.stdev(scores) if len(scores) > 1 else None,
        "score_sample_count": len(scores),
        "tokens": sum(r["summary"]["tokens"] for r in runs)
        if len(runs) == expected_repetitions and all(r["summary"]["tokens"] is not None for r in runs)
        else None,
        "output_consistency_mean": statistics.mean(consistencies)
        if len(complete) > 1 and consistencies
        else None,
        "summary": summarize(traces),
        "performance": performance(all_traces, duration),
        "traces": traces,
        "note": "Quality aggregation uses complete repetitions only. Partial sessions cannot pass a gate. Repeat variance is descriptive; repeated responses are NOT independent case families.",
    }


def compare_batches(baseline, candidate):
    config = candidate["config"]
    view = {
        "id": candidate["id"],
        "suite_hash": candidate["suite_hash"],
        "scoring_hash": candidate["batch_scoring_hash"],
        "traces": candidate["aggregate"]["traces"],
    }
    before = (
        {
            "id": baseline["id"],
            "suite_hash": baseline["suite_hash"],
            "scoring_hash": baseline["batch_scoring_hash"],
            "traces": baseline["aggregate"]["traces"],
        }
        if baseline
        else None
    )
    result = compare(before, view, config["regression_threshold"], config["min_pairs"])
    if candidate["status"] != "complete" or (
        baseline and (baseline["status"] != "complete" or baseline["repetitions"] != candidate["repetitions"])
    ):
        result.update(
            verdict="insufficient-data",
            reason="Need two complete batches with matching repetition count and frozen scoring/operational configuration",
        )
    result["baseline_score"] = baseline["aggregate"]["score_mean"] if baseline else None
    result["candidate_score"] = candidate["aggregate"]["score_mean"]
    result["repeat_noise"] = {
        "baseline_score_sd": baseline["aggregate"]["score_sd"] if baseline else None,
        "candidate_score_sd": candidate["aggregate"]["score_sd"],
    }
    result["method_note"] = (
        "Pair case means over all repetitions, then family-cluster bootstrap. The 5 repeats are not treated as 5x independent test cases; temporal/production noise needs more representative sessions."
    )
    return result


async def run_batch(
    suite,
    target,
    config,
    secrets,
    store,
    *,
    repetitions=5,
    consent=False,
    allow_exec=False,
    cancel_event=None,
    on_progress=None,
    client=None,
):
    if not 1 <= repetitions <= 20:
        raise ValueError("Repetitions must be 1–20")
    # Freeze once; GUI edits cannot change a running batch or regenerate its rubric.
    suite = suite.model_copy(deep=True)
    target = target.model_copy(deep=True)
    config = config.model_copy(deep=True)
    if config.use_agent_planner:
        preflight(suite, target, config, secrets, consent, allow_exec)
    own_client = client is None
    if own_client:
        client = httpx.AsyncClient(follow_redirects=False, trust_env=False)
    budget = Budget(config)
    try:
        suite, agents_payload, test_cases = await plan_testers(
            suite, config, secrets, budget, client, consent=consent, cancel_event=cancel_event
        )
        suite = suite.model_copy(deep=True)
        preflight(suite, target, config, secrets, consent, allow_exec)
        quote = estimate(suite, target, config, repetitions)
        if quote["minimum_target_attempts"] > config.max_calls:
            raise ValueError("Target attempt ceiling is smaller than the full repeated suite")
        if quote["minimum_judge_attempts"] > config.max_judge_calls:
            raise ValueError("Judge attempt ceiling is smaller than the full repeated suite")
        start = time.perf_counter()
        batch = {
            "id": uuid.uuid4().hex[:12],
            "created": now(),
            "finished": None,
            "status": "running",
            "repetitions": repetitions,
            "suite_hash": suite.digest,
            "suite": suite.model_dump(by_alias=True),
            "target": target.model_dump(),
            "config": config.model_dump(),
            "batch_scoring_hash": batch_scoring_hash(config),
            "estimate": quote,
            "source": {
                "adapter": target.kind,
                "endpoint": target.url or None,
                "model": target.model,
                "revision": target.revision,
                "prompt_version": target.prompt_version,
                "synthetic": target.kind == "fixture",
                "engine": "0.7.0",
            },
            "agents": agents_payload,
            "test_cases": test_cases,
            "problems": [],
            "analysis": {},
            "repetition_records": [
                {
                    "index": i + 1,
                    "recorded_seed": 42 + i,
                    "provider_seed_applied": False,
                    "run_id": None,
                    "status": "pending",
                    "started": None,
                    "finished": None,
                    "error_type": None,
                }
                for i in range(repetitions)
            ],
            "runs": [],
            "aggregate": aggregate([], repetitions),
            "calls": {"target": 0, "judge": 0},
            "reserved_budget_usd": 0.0,
            "comparison": {"verdict": "insufficient-data", "reason": "Batch running"},
        }

        def publish():
            safe = secrets.redact(batch)
            store.save_batch(safe)
            if on_progress:
                on_progress(copy.deepcopy(safe))

        publish()
        for entry in batch["repetition_records"]:
            if cancel_event is not None and cancel_event.is_set():
                break
            entry.update(status="running", started=now())
            publish()
            try:
                result = await run_suite(
                    suite,
                    target,
                    config,
                    secrets,
                    store,
                    consent=consent,
                    allow_exec=allow_exec,
                    client=client,
                    budget=budget,
                    cancel_event=cancel_event,
                    batch_id=batch["id"],
                    repetition=entry["index"],
                    seed=entry["recorded_seed"],
                )
                batch["runs"].append(result)
                entry.update(run_id=result["id"], status=result["status"], finished=result["finished"])
                if result["status"] != "complete":
                    break
            except Exception as exc:
                entry.update(status="failed", error_type=type(exc).__name__, finished=now())
                break
            batch["aggregate"] = aggregate(batch["runs"], repetitions)
            batch["calls"] = dict(budget.calls)
            batch["reserved_budget_usd"] = budget.reserved_usd
            publish()
        cancelled = cancel_event is not None and cancel_event.is_set()
        for entry in batch["repetition_records"]:
            if entry["status"] == "pending":
                entry["status"] = "not-run-cancelled" if cancelled else "not-run-incomplete"
        batch.update(
            status="cancelled"
            if cancelled
            else (
                "complete"
                if len(batch["runs"]) == repetitions and all(r["status"] == "complete" for r in batch["runs"])
                else "incomplete"
            ),
            finished=now(),
            duration_s=time.perf_counter() - start,
        )
        batch["aggregate"] = aggregate(batch["runs"], repetitions)
        batch["calls"] = dict(budget.calls)
        batch["reserved_budget_usd"] = budget.reserved_usd
        batch["comparison"] = compare_batches(store.batch_baseline(suite.digest), batch)
        batch["problems"] = find_problems(batch)
        batch["analysis"] = analysis_report(batch)
        publish()
        return secrets.redact(batch)
    finally:
        if own_client:
            await client.aclose()
