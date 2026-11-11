import asyncio
import json

import httpx
import pytest
import respx

from product_blinder.agents import AgentPlan, plan_testers
from product_blinder.analysis import attach_test_cases, heuristic_agents
from product_blinder.adapters import Budget
from product_blinder.batches import run_batch
from product_blinder.models import Case, RunConfig, Suite, TargetConfig
from product_blinder.security import Secrets
from product_blinder.store import Store


def suite():
    return Suite(
        name="tiny",
        cases=[
            Case(id="ok", prompt="say A", expected="A"),
            Case(id="policy-1-typo-1", prompt="typo", expected="A", must_include=["A"], category="typo"),
        ],
    )


def run(coro):
    return asyncio.run(coro)


def test_heuristic_testers_and_test_case_context():
    agents = heuristic_agents(8)
    assert len(agents) == 8 and all(a["origin"] == "local-heuristic" for a in agents)
    assert len(heuristic_agents(5)) == 5
    records = attach_test_cases(suite(), agents)
    typo = next(r for r in records if r["id"] == "policy-1-typo-1")
    assert typo["label"] == "Test Case"
    assert typo["source"] == "authored"
    assert typo["expected"] == "A"
    assert "Robustness tester" in typo["agents"]
    assert "typo" in typo["check"].lower() or "Robustness" in typo["check"]


def test_find_problems_empty_on_stable_and_filled_on_degraded(tmp_path):
    store = Store(tmp_path / "p.sqlite")
    stable = run(run_batch(suite(), TargetConfig(), RunConfig(max_calls=1000), Secrets(), store))
    assert stable["agents"]["mode"] == "local-heuristic"
    assert "no paid model ran" in stable["agents"]["label"].lower()
    assert stable["problems"] == []
    assert stable["analysis"]["problem_count"] == 0
    assert all(r["label"] == "Test Case" for r in stable["test_cases"])
    store.set_batch_baseline(stable["id"])
    candidate = run(
        run_batch(suite(), TargetConfig(fixture="degraded"), RunConfig(max_calls=1000), Secrets(), store)
    )
    assert candidate["problems"]
    typo = next(p for p in candidate["problems"] if "policy-1-typo-1" in p["test_cases"])
    assert typo["repetitions_failed"] == 5
    assert typo["failed_of_five"] == "5/5"
    assert typo["severity"] in {"high", "medium"}
    assert typo["evidence"] and typo["evidence"] != "N/A"
    assert "robustness" in typo["failed_checks"] or "accuracy" in typo["failed_checks"]
    report = candidate["analysis"]
    assert report["quality"]["display"].startswith("100%")
    assert report["completions"]["display"] == "5/5"
    assert report["missing"]["tokens"] == "N/A"
    assert report["missing"]["cost_usd"] == "N/A"
    assert report["top_failing_test_cases"]
    assert report["mode"] == "local-heuristic"


def test_no_key_planner_falls_back_without_pretending(tmp_path):
    store = Store(tmp_path / "nokey.sqlite")
    config = RunConfig(max_calls=1000, use_agent_planner=True, judge_model="x", judge_call_cap_usd=0.01)
    with pytest.raises(ValueError, match="Missing judge API key"):
        run(run_batch(suite(), TargetConfig(), config, Secrets(), store, consent=True))
    config = RunConfig(max_calls=1000)  # planner off: works without keys
    batch = run(run_batch(suite(), TargetConfig(), config, Secrets(), store))
    assert batch["agents"]["mode"] == "local-heuristic"
    assert batch["agents"]["planner_error"] is None


def test_llm_agent_plan_mocked_http_validates_and_marks_generated(tmp_path):
    plan = {
        "agents": [
            {"id": f"{role}-t", "name": role.title() + " agent", "role": role, "focus": role}
            for role in [
                "accuracy",
                "groundedness",
                "instructions",
                "validity",
                "robustness",
                "fairness",
                "safety",
                "reliability",
            ]
        ],
        "test_cases": [
            {
                "id": "generated-check-1",
                "prompt": "Return the word OK",
                "expected": "OK",
                "category": "standard",
                "check": "Simple expected-outcome check",
                "agent_id": "accuracy-t",
            }
        ],
    }
    config = RunConfig(
        max_calls=1000,
        use_agent_planner=True,
        judge_model="mock-planner",
        judge_call_cap_usd=0.01,
        max_generated_cases=4,
        use_judge=False,
    )

    async def scenario():
        with respx.mock:
            respx.post("https://api.openai.com/v1/chat/completions").mock(
                return_value=httpx.Response(
                    200, json={"choices": [{"message": {"content": json.dumps(plan)}}]}
                )
            )
            async with httpx.AsyncClient() as client:
                return await run_batch(
                    suite(),
                    TargetConfig(),
                    config,
                    Secrets(judge="test-judge-key"),
                    Store(tmp_path / "llm.sqlite"),
                    consent=True,
                    client=client,
                )

    batch = run(scenario())
    assert batch["agents"]["mode"] == "llm"
    assert batch["agents"]["planner_error"] is None
    assert "generated-check-1" in batch["agents"]["generated_case_ids"]
    generated = next(t for t in batch["test_cases"] if t["id"] == "generated-check-1")
    assert generated["source"] == "agent-generated" and generated["generated"] is True
    assert any(t["case_id"] == "generated-check-1" for t in batch["runs"][0]["traces"])


@pytest.mark.parametrize(
    "content", ["not json", '{"agents":[]}', '{"agents":[{"id":"a","name":"A","role":"nope","focus":"x"}]}']
)
def test_invalid_llm_plan_does_not_invent_findings(content, tmp_path):
    config = RunConfig(
        max_calls=1000,
        use_agent_planner=True,
        judge_model="mock-planner",
        judge_call_cap_usd=0.01,
    )

    async def scenario():
        with respx.mock:
            respx.post("https://api.openai.com/v1/chat/completions").mock(
                return_value=httpx.Response(200, json={"choices": [{"message": {"content": content}}]})
            )
            async with httpx.AsyncClient() as client:
                return await plan_testers(
                    suite(),
                    config,
                    Secrets(judge="test-judge-key"),
                    Budget(config),
                    client,
                    consent=True,
                )

    planned_suite, payload, records = run(scenario())
    assert payload["mode"] == "local-heuristic"
    assert payload["planner_error"]
    assert "local testers used" in payload["planner_error"]
    assert all(r["source"] == "authored" for r in records)
    assert planned_suite.cases[0].id == "ok"
    AgentPlan  # imported for visibility of the contract
