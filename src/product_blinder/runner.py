"""Generate/import -> freeze -> run -> compare -> gate -> suggest; never release/retrain."""

import asyncio
import importlib.metadata
import platform
import statistics
import time
import uuid
from datetime import datetime
from zoneinfo import ZoneInfo

import httpx

from .adapters import Budget, HarnessLimitError, invoke
from .comparison import compare, summarize, performance
from .evaluators import ROLES, apply_fairness, evaluate, metric
from .judge import JUDGE_PROMPT
from .models import fingerprint
from .security import check_no_embedded_secrets, validate_url


def preflight(suite, target, config, secrets, consent, allow_exec):
    check_no_embedded_secrets(suite.model_dump(by_alias=True), secrets)
    check_no_embedded_secrets(target.model_dump(), secrets)
    if target.kind == "http":
        validate_url(target.url)
    if target.kind == "program" and not allow_exec:
        raise ValueError("Program target requires explicit trusted-code execution opt-in")
    paid = target.kind != "fixture" or config.use_judge or config.use_agent_planner
    if paid and not consent:
        raise ValueError("Paid/external runs require explicit --consent-paid confirmation")
    if target.kind != "fixture" and not config.target_call_cap_usd:
        raise ValueError("Set a positive target per-call cost upper bound before running")
    if config.use_judge or config.use_agent_planner:
        if not secrets.judge:
            raise ValueError("Missing judge API key")
        if not config.judge_model:
            raise ValueError("Missing judge model")
        if not config.judge_call_cap_usd:
            raise ValueError("Set a positive judge per-call cost upper bound")
        validate_url(config.judge_base_url.rstrip("/") + "/chat/completions")
    if len(suite.cases) > config.max_calls:
        raise ValueError("Max target calls is smaller than the frozen suite")


async def run_suite(
    suite,
    target,
    config,
    secrets,
    store,
    *,
    consent=False,
    allow_exec=False,
    client=None,
    budget=None,
    cancel_event=None,
    batch_id=None,
    repetition=1,
    seed=42,
):
    preflight(suite, target, config, secrets, consent, allow_exec)
    wall_start = time.perf_counter()
    created = datetime.now(ZoneInfo("Asia/Hong_Kong")).isoformat()
    budget = budget or Budget(config)
    calls_before = dict(budget.calls)
    reserved_before = budget.reserved_usd
    completed = {}
    limiter = asyncio.Semaphore(config.concurrency)
    own_client = client is None
    if own_client:
        client = httpx.AsyncClient(follow_redirects=False, trust_env=False)

    async def one(case):
        async with limiter:
            started = time.perf_counter()
            try:
                trace = await invoke(case, target, config, secrets, budget, client, allow_exec)
                trace["error"] = None
            except Exception as exc:
                # Never persist arbitrary exception strings (could contain auth/body).
                trace = {
                    "output": "",
                    "latency_ms": (time.perf_counter() - started) * 1000,
                    "error": type(exc).__name__,
                    "usage": None,
                    "cost_usd": None,
                }
            if trace.get("error") == HarnessLimitError.__name__:
                metrics = {
                    r: metric(
                        None, "Harness call/budget ceiling reached; not a target failure", "harness-limit"
                    )
                    for r in ROLES
                }
                judge_usage = []
                trace["harness_error"] = True
            else:
                metrics, judge_usage = await evaluate(case, trace, client, config, secrets, budget)
            result = {
                **trace,
                "case_id": case.id,
                "prompt": case.prompt,
                "expected": case.expected,
                "category": case.category,
                "family": case.pair,
                "metrics": metrics,
                "judge_usage": judge_usage,
            }
            completed[case.id] = result
            return result

    tasks = [asyncio.create_task(one(c)) for c in suite.cases]

    async def watch_cancel():
        while not cancel_event.is_set():
            await asyncio.sleep(0.025)
        for task in tasks:
            task.cancel()

    watcher = asyncio.create_task(watch_cancel()) if cancel_event is not None else None
    try:
        await asyncio.gather(*tasks, return_exceptions=True)
        traces = [completed[c.id] for c in suite.cases if c.id in completed]
    finally:
        if watcher:
            watcher.cancel()
            await asyncio.gather(watcher, return_exceptions=True)
        if own_client:
            await client.aclose()
    apply_fairness([c for c in suite.cases if c.id in completed], traces)
    for t in traces:
        scores = [m["score"] for m in t["metrics"].values() if m["score"] is not None]
        t["score"] = statistics.mean(scores) if scores else None
    run = {
        "id": uuid.uuid4().hex[:12],
        "created": created,
        "finished": datetime.now(ZoneInfo("Asia/Hong_Kong")).isoformat(),
        "batch_id": batch_id,
        "repetition": repetition,
        "recorded_seed": seed,
        "target_seed": target.body.get("seed"),
        "provider_seed_applied": False,
        "status": "cancelled"
        if cancel_event is not None and cancel_event.is_set()
        else (
            "complete"
            if len(traces) == len(suite.cases)
            and not any(
                t.get("harness_error") or any(m["method"] == "judge-error" for m in t["metrics"].values())
                for t in traces
            )
            else "incomplete"
        ),
        "expected_cases": len(suite.cases),
        "missing_case_ids": [c.id for c in suite.cases if c.id not in completed],
        "suite_hash": suite.digest,
        "suite": suite.model_dump(by_alias=True),
        "target": target.model_dump(),
        "config": config.model_dump(),
        "scoring_hash": config.scoring_digest,
        "metadata": {
            "python": platform.python_version(),
            "engine": "0.7.0",
            "rules_version": "pb-rules-v1",
            "judge_system_prompt": JUDGE_PROMPT if config.use_judge else None,
            "dependencies": {k: importlib.metadata.version(k) for k in ("httpx", "pydantic", "jsonschema")},
            "synthetic": target.kind == "fixture",
            "paid_consent": consent,
        },
        "traces": traces,
        "summary": summarize(traces),
        "performance": performance(traces, time.perf_counter() - wall_start),
        "calls": {k: budget.calls[k] - calls_before[k] for k in budget.calls},
        "reserved_budget_usd": budget.reserved_usd - reserved_before,
        "suggestions": [
            "Review failed cases, add human-verified typo, paraphrase, negation, gender and ethnicity counterfactuals.",
            "Freeze a NEW suite for augmentation; establish a new baseline. No automatic retraining or release.",
        ],
    }
    # Known usage only: never pretend missing usage or price is zero.
    usage = [t["usage"] for t in traces if isinstance(t.get("usage"), dict)]
    if traces and len(usage) == len(traces) and all(isinstance(u.get("total_tokens"), int) for u in usage):
        run["summary"]["tokens"] = sum(u["total_tokens"] for u in usage)
    run = secrets.redact(run)
    baseline = store.baseline(suite.digest)
    run["comparison"] = compare(baseline, run, config.regression_threshold, config.min_pairs)
    if run["status"] != "complete":
        run["comparison"].update(
            verdict="insufficient-data", reason="Run is incomplete/cancelled; no quality verdict"
        )
    run["metadata"]["config_hash"] = fingerprint(run["config"])
    store.save(run)
    return run
