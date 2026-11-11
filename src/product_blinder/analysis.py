"""Named testers, Test Case context, problem list and compact analysis.

Findings come only from completed observations. Nothing is invented, and a
local heuristic tester is never labelled as a paid model.
"""

from .scores import score_percent, score_transition
from .evaluators import DESCRIPTIONS, ROLES

CHECKS = {
    "standard": "Expected-outcome / standard behaviour",
    "typo": "Robustness to typo perturbation",
    "paraphrase": "Robustness to paraphrase",
    "negation": "Correct behaviour under negation",
    "fairness": "Demographic counterfactual parity",
    "injection": "Prompt-injection / safety probe",
}

HEURISTIC_AGENTS = [
    {
        "id": "accuracy-tester",
        "name": "Accuracy tester",
        "role": "accuracy",
        "focus": "Expected-outcome match",
    },
    {
        "id": "groundedness-tester",
        "name": "Groundedness tester",
        "role": "groundedness",
        "focus": "Evidence-supported answers",
    },
    {
        "id": "instruction-tester",
        "name": "Instruction tester",
        "role": "instructions",
        "focus": "Required and forbidden phrases",
    },
    {
        "id": "validity-tester",
        "name": "Validity tester",
        "role": "validity",
        "focus": "Schema or nonempty output",
    },
    {
        "id": "robustness-tester",
        "name": "Robustness tester",
        "role": "robustness",
        "focus": "Typo, paraphrase and negation",
    },
    {
        "id": "fairness-tester",
        "name": "Fairness tester",
        "role": "fairness",
        "focus": "Authored demographic pairs",
    },
    {
        "id": "safety-tester",
        "name": "Safety tester",
        "role": "safety",
        "focus": "Authored injection probes",
    },
    {
        "id": "reliability-tester",
        "name": "Reliability tester",
        "role": "reliability",
        "focus": "Completion within latency SLO",
    },
]


def as_case(case):
    return case if isinstance(case, dict) else case.model_dump(by_alias=True)


def heuristic_agents(count=8):
    count = max(5, min(8, int(count or 8)))
    return [
        {**agent, "origin": "local-heuristic", "mode": "local-heuristic"}
        for agent in HEURISTIC_AGENTS[:count]
    ]


def role_applies(role, case):
    case = as_case(case)
    if role == "accuracy":
        return case.get("expected") is not None
    if role == "groundedness":
        return bool(case.get("evidence"))
    if role == "instructions":
        return bool(case.get("must_include") or case.get("must_not_include"))
    if role == "validity":
        return True
    if role == "robustness":
        return case.get("category") in {"typo", "paraphrase", "negation"}
    if role == "fairness":
        return case.get("category") == "fairness"
    if role == "safety":
        return case.get("category") == "injection"
    if role == "reliability":
        return True
    return False


def select_agents(case, agents):
    selected = [agent for agent in agents if role_applies(agent["role"], case)]
    return selected or list(agents[:1])


def test_case_record(case, agents):
    case = as_case(case)
    selected = select_agents(case, agents)
    source = case.get("source") or "authored"
    check = case.get("check") or CHECKS.get(case.get("category") or "standard", "Specified evaluation checks")
    return {
        "id": case["id"],
        "label": "Test Case",
        "family": case.get("pair") or case["id"],
        "check": check,
        "expected": case.get("expected"),
        "category": case.get("category") or "standard",
        "source": source,
        "agents": [agent["name"] for agent in selected],
        "agent_ids": [agent["id"] for agent in selected],
        "generated": source == "agent-generated",
    }


def attach_test_cases(suite, agents):
    cases = suite.cases if hasattr(suite, "cases") else suite["cases"]
    return [test_case_record(case, agents) for case in cases]


def context_line(record):
    expected = record.get("expected")
    expected_text = "expected outcome set" if expected not in (None, "") else "no expected outcome"
    origin = "agent-generated" if record.get("generated") else "authored"
    agents = ", ".join(record.get("agents") or ["unassigned"])
    return (
        f"Test Case {record['id']} · family {record['family']} · {record['check']} · "
        f"{expected_text} · {origin} · testers: {agents}"
    )


def _failed_roles(trace):
    failed = [
        role
        for role, metric in (trace.get("metrics") or {}).items()
        if metric.get("score") is not None and metric["score"] < 1
    ]
    if trace.get("error"):
        failed.append("target-error")
    return failed


def find_problems(batch):
    """List only problems evidenced by completed repetitions. Never fabricate rows."""
    problems = []
    expected = batch.get("repetitions") or 0
    contexts = {item["id"]: item for item in batch.get("test_cases") or []}
    complete = [run for run in batch.get("runs") or [] if run.get("status") == "complete"]
    case_ids = []
    if complete:
        case_ids = [trace["case_id"] for trace in complete[0].get("traces") or []]
    elif batch.get("suite"):
        case_ids = [case["id"] for case in batch["suite"]["cases"]]
    for case_id in case_ids:
        observations = []
        for run in complete:
            trace = next((item for item in run["traces"] if item["case_id"] == case_id), None)
            if trace is not None:
                observations.append((run["repetition"], trace))
        if not observations:
            continue
        failed_reps = []
        checks = set()
        evidence = []
        for repetition, trace in observations:
            roles = _failed_roles(trace)
            troubled = (
                bool(roles) or trace.get("error") or (trace.get("score") is not None and trace["score"] < 1)
            )
            if not troubled:
                continue
            failed_reps.append(repetition)
            checks.update(roles)
            snippet = (trace.get("output") or trace.get("error") or "").strip().replace("\n", " ")
            if snippet:
                evidence.append(snippet[:160])
        if not failed_reps:
            continue
        observed = len(observations)
        if len(failed_reps) == observed and len(checks) >= 2:
            severity = "high"
        elif len(failed_reps) == observed or len(checks) >= 2:
            severity = "medium"
        else:
            severity = "low"
        record = contexts.get(case_id) or {
            "id": case_id,
            "agents": [],
            "check": "Specified evaluation checks",
        }
        checks_list = sorted(checks) or ["quality"]
        problems.append(
            {
                "problem": f"Test Case {case_id}: {', '.join(checks_list)}",
                "severity": severity,
                "test_cases": [case_id],
                "failed_checks": checks_list,
                "repetitions_failed": len(failed_reps),
                "repetitions_observed": observed,
                "repetitions_expected": expected,
                "failed_of_five": f"{len(failed_reps)}/{expected or observed}",
                "evidence": evidence[0] if evidence else "N/A",
                "agents": record.get("agents") or [],
                "check": record.get("check"),
            }
        )
    rank = {"high": 0, "medium": 1, "low": 2}
    problems.sort(
        key=lambda item: (rank[item["severity"]], -item["repetitions_failed"], item["test_cases"][0])
    )
    return problems


def _missing(value):
    return "N/A" if value is None else value


def suggested_next(batch, problems):
    tips = []
    if batch.get("status") != "complete":
        tips.append("Complete all planned repetitions before treating this as a release gate.")
    if batch.get("agents", {}).get("planner_error"):
        tips.append(
            "Planner output was invalid or unavailable; review the recorded error. No paid findings were invented."
        )
    gaps = {
        item["role"] for item in evaluator_gaps(batch) if item.get("score") is not None and item["score"] < 1
    }
    mapping = {
        "accuracy": "Review expected-outcome Test Cases and the actual responses.",
        "groundedness": "Check evidence-bearing Test Cases for unsupported claims.",
        "instructions": "Inspect required/forbidden phrase Test Cases.",
        "validity": "Inspect schema-bearing Test Cases and malformed output.",
        "robustness": "Revisit typo, paraphrase and negation Test Cases.",
        "fairness": "Review authored demographic pair Test Cases.",
        "safety": "Review authored injection/safety Test Cases.",
        "reliability": "Inspect timeouts, errors and latency SLO misses.",
    }
    for role in ROLES:
        if role in gaps:
            tips.append(mapping[role])
    if any(item.get("generated") for item in batch.get("test_cases") or []):
        tips.append("Human-review agent-generated Test Cases before using them as a new baseline.")
    if not problems and batch.get("status") == "complete":
        tips.append(
            "No failed Test Cases in completed observations. Keep this frozen suite if you want a baseline."
        )
    tokens = batch.get("aggregate", {}).get("tokens")
    if tokens is None:
        tips.append("Tokens, cost and TTFT stay N/A until a target actually reports them.")
    return tips[:6]


def evaluator_gaps(batch):
    summary = (batch.get("aggregate") or {}).get("summary") or {}
    scores = summary.get("per_evaluator") or {}
    counts = summary.get("sample_sizes") or {}
    rows = []
    for role in ROLES:
        score = scores.get(role)
        rows.append(
            {
                "role": role,
                "score": score,
                "sample_size": counts.get(role),
                "note": "N/A" if score is None else DESCRIPTIONS[role],
            }
        )
    return rows


def analysis_report(batch, baseline=None):
    aggregate = batch.get("aggregate") or {}
    performance = aggregate.get("performance") or {}
    comparison = batch.get("comparison") or {}
    problems = batch.get("problems")
    if problems is None:
        problems = find_problems(batch)
    current = aggregate.get("score_mean")
    baseline_score = comparison.get("baseline_score")
    if baseline_score is None and baseline:
        baseline_score = baseline.get("aggregate", {}).get("score_mean")
    complete = aggregate.get("complete_repetitions") or 0
    expected = batch.get("repetitions") or aggregate.get("expected_repetitions") or 0
    mode = (batch.get("agents") or {}).get("mode") or "local-heuristic"
    mode_label = (
        "LLM-planned testers (validated structured plan)"
        if mode == "llm"
        else "Local heuristic testers — no paid model ran"
    )
    failing = [
        {
            "id": item["test_cases"][0],
            "failed_checks": item["failed_checks"],
            "repetitions_failed": item["failed_of_five"],
            "severity": item["severity"],
        }
        for item in problems[:6]
    ]
    recurrence = [
        item
        for item in problems
        if item["repetitions_observed"] and item["repetitions_failed"] == item["repetitions_observed"]
    ]
    report = {
        "title": "Analysis report",
        "mode": mode,
        "mode_label": mode_label,
        "planner_error": (batch.get("agents") or {}).get("planner_error"),
        "quality": {
            "baseline": baseline_score,
            "current": current,
            "display": score_transition(baseline_score, current)
            if baseline_score is not None or current is not None
            else "N/A",
        },
        "completions": {"complete": complete, "expected": expected, "display": f"{complete}/{expected}"},
        "consistency": {
            "value": aggregate.get("output_consistency_mean"),
            "display": score_percent(aggregate.get("output_consistency_mean")),
        },
        "latency": {
            "mean_ms": performance.get("latency_ms_mean"),
            "p95_ms": performance.get("latency_ms_p95"),
            "error_rate": performance.get("target_error_rate"),
            "timeout_rate": performance.get("timeout_rate"),
        },
        "top_failing_test_cases": failing,
        "evaluator_gaps": evaluator_gaps(batch),
        "recurrence": [
            {"test_case": item["test_cases"][0], "failed_of_observed": item["failed_of_five"]}
            for item in recurrence[:8]
        ],
        "missing": {
            "tokens": _missing(aggregate.get("tokens")),
            "cost_usd": "N/A",
            "ttft_ms": "N/A",
        },
        "problem_count": len(problems),
        "suggested_next": suggested_next(batch, problems),
        "synthetic": (batch.get("source") or {}).get("synthetic", False),
    }
    return report


def analysis_markdown(report, batch=None):
    latency = report["latency"]
    lines = [
        "# Analysis report",
        "",
        f"- Testers: {report['mode_label']}",
        f"- Quality: {report['quality']['display']}",
        f"- Completions: {report['completions']['display']}",
        f"- Consistency: {report['consistency']['display']}",
        f"- Mean latency: {_fmt_num(latency['mean_ms'], 'ms')} · P95: {_fmt_num(latency['p95_ms'], 'ms')}",
        f"- Error rate: {_fmt_pct(latency['error_rate'])} · Timeout rate: {_fmt_pct(latency['timeout_rate'])}",
        f"- Tokens / cost / TTFT: {report['missing']['tokens']} / {report['missing']['cost_usd']} / {report['missing']['ttft_ms']}",
        f"- Problems: {report['problem_count']}",
    ]
    if report.get("planner_error"):
        lines += ["", f"- Planner error: {report['planner_error']}"]
    if report["top_failing_test_cases"]:
        lines += ["", "## Top failing Test Cases", ""]
        for item in report["top_failing_test_cases"]:
            lines.append(
                f"- {item['id']} · {item['severity']} · {item['repetitions_failed']} · {', '.join(item['failed_checks'])}"
            )
    if report["recurrence"]:
        lines += ["", "## Recurrence across repetitions", ""]
        for item in report["recurrence"]:
            lines.append(f"- {item['test_case']} failed {item['failed_of_observed']} observed repetitions")
    lines += ["", "## Evaluator gaps", ""]
    for item in report["evaluator_gaps"]:
        lines.append(f"- {item['role']}: {score_percent(item['score'])} · n={item['sample_size'] or 0}")
    lines += ["", "## Suggested next checks", ""]
    for tip in report["suggested_next"]:
        lines.append(f"- {tip}")
    if batch and batch.get("problems"):
        lines += ["", "## Find out problem", ""]
        for item in batch["problems"]:
            lines.append(
                f"- {item['problem']} · {item['severity']} · {item['failed_of_five']} · {item['evidence']}"
            )
    return "\n".join(lines) + "\n"


def _fmt_num(value, suffix=""):
    if value is None:
        return "N/A"
    return f"{value:.2f}{suffix}"


def _fmt_pct(value):
    return score_percent(value)
