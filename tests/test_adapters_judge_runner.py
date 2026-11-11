import asyncio
import json
import sys

import httpx
import pytest
import respx

from product_blinder.adapters import Budget, invoke
from product_blinder.judge import judge, propose_rubric
from product_blinder.models import Case, RunConfig, Suite, TargetConfig
from product_blinder.runner import run_suite
from product_blinder.security import Secrets
from product_blinder.store import Store


def conf(**kw):
    return RunConfig(
        target_call_cap_usd=0.01, judge_call_cap_usd=0.01, judge_model="mock-judge", retry=0, **kw
    )


def single():
    return Suite(name="Mock API suite", cases=[Case(id="one", prompt="Q", expected="A", evidence=["A"])])


def test_http_nested_mapping_auth_and_redaction(tmp_path):
    async def scenario():
        config = conf()
        target = TargetConfig(
            kind="http",
            url="http://127.0.0.1:9000/test",
            input_path="request.prompt",
            output_path="result.answer",
            model="mock-target",
        )
        with respx.mock as mock:
            route = mock.post(target.url).mock(
                return_value=httpx.Response(
                    200, json={"result": {"answer": "A target-private-key"}, "usage": {"total_tokens": 12}}
                )
            )
            run = await run_suite(
                single(), target, config, Secrets("target-private-key"), Store(tmp_path / "db"), consent=True
            )
            assert json.loads(route.calls[0].request.content)["request"]["prompt"] == "Q"
            assert route.calls[0].request.headers["Authorization"] == "Bearer target-private-key"
            assert "target-private-key" not in json.dumps(run)
            assert b"target-private-key" not in (tmp_path / "db").read_bytes()
            assert run["summary"]["tokens"] == 12

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "status,body", [(401, {"error": "bad auth"}), (200, {"wrong": "mapping"}), (200, "not json")]
)
def test_http_errors_are_recorded_not_crashes(tmp_path, status, body):
    async def scenario():
        with respx.mock as mock:
            response = (
                httpx.Response(status, json=body)
                if not isinstance(body, str)
                else httpx.Response(status, text=body)
            )
            mock.post("http://localhost:9000/test").mock(return_value=response)
            run = await run_suite(
                single(),
                TargetConfig(kind="http", url="http://localhost:9000/test"),
                conf(),
                Secrets(),
                Store(tmp_path / "db"),
                consent=True,
            )
            assert run["summary"]["errors"] == 1
            assert run["traces"][0]["metrics"]["reliability"]["score"] == 0

    asyncio.run(scenario())


def test_http_timeout_retries_and_counts(tmp_path):
    async def scenario():
        with respx.mock as mock:
            route = mock.post("http://localhost:9000/test").mock(
                side_effect=httpx.ReadTimeout("private response")
            )
            run = await run_suite(
                single(),
                TargetConfig(kind="http", url="http://localhost:9000/test"),
                RunConfig(retry=1, target_call_cap_usd=0.01),
                Secrets(),
                Store(tmp_path / "db"),
                consent=True,
            )
            assert route.call_count == 2
            assert run["calls"]["target"] == 2
            assert run["reserved_budget_usd"] == 0.02
            assert run["traces"][0]["error"] == "ReadTimeout"

    asyncio.run(scenario())


def test_429_retry_then_success(tmp_path):
    async def scenario():
        with respx.mock as mock:
            route = mock.post("http://localhost:9000/test").mock(
                side_effect=[httpx.Response(429), httpx.Response(200, json={"output": "A"})]
            )
            run = await run_suite(
                single(),
                TargetConfig(kind="http", url="http://localhost:9000/test"),
                RunConfig(retry=1, target_call_cap_usd=0.01),
                Secrets(),
                Store(tmp_path / "db"),
                consent=True,
            )
            assert route.call_count == 2
            assert run["traces"][0]["metrics"]["accuracy"]["score"] == 1

    asyncio.run(scenario())


def test_concurrent_budget_reservation_is_atomic():
    async def scenario():
        b = Budget(conf(budget_usd=0.025))
        results = await asyncio.gather(*(b.reserve("target", True) for _ in range(8)), return_exceptions=True)
        assert sum(not isinstance(x, Exception) for x in results) == 2
        assert b.reserved_usd == 0.02

    asyncio.run(scenario())


def test_attempt_limit_counts_every_request():
    async def scenario():
        b = Budget(conf(max_calls=1))
        await b.reserve("target", True)
        with pytest.raises(ValueError):
            await b.reserve("target", True)

    asyncio.run(scenario())


def test_missing_key_and_paid_consent_fail_before_request(tmp_path):
    async def scenario():
        with pytest.raises(ValueError, match="consent"):
            await run_suite(
                single(),
                TargetConfig(kind="http", url="http://localhost:9000/test"),
                conf(),
                Secrets(),
                Store(tmp_path / "db"),
            )
        with pytest.raises(ValueError, match="Missing judge"):
            await run_suite(
                single(),
                TargetConfig(),
                conf(use_judge=True),
                Secrets(),
                Store(tmp_path / "db"),
                consent=True,
            )

    asyncio.run(scenario())


def test_program_requires_trusted_opt_in(tmp_path):
    async def scenario():
        with pytest.raises(ValueError, match="opt-in"):
            await run_suite(
                single(),
                TargetConfig(kind="program", command=[sys.executable, "-c", "print('x')"]),
                conf(),
                Secrets(),
                Store(tmp_path / "db"),
                consent=True,
            )

    asyncio.run(scenario())


def test_trusted_program_contract_and_timeout():
    async def scenario():
        async with httpx.AsyncClient() as client:
            config = conf()
            t = TargetConfig(
                kind="program",
                command=[
                    sys.executable,
                    "-c",
                    "import json,sys; r=json.load(sys.stdin); print(json.dumps({'output':r['input']}))",
                ],
            )
            trace = await invoke(
                Case(id="a", prompt="hello"), t, config, Secrets(), Budget(config), client, True
            )
            assert trace["output"] == "hello"
            t.command = [sys.executable, "-c", "import time; time.sleep(2)"]
            with pytest.raises(asyncio.TimeoutError):
                await invoke(
                    Case(id="a", prompt="hello"),
                    t,
                    conf(timeout_s=0.1),
                    Secrets(),
                    Budget(config),
                    client,
                    True,
                )

    asyncio.run(scenario())


def test_openai_compatible_judge_http_validation():
    async def scenario():
        config = conf()
        with respx.mock as mock:
            route = mock.post("https://api.openai.com/v1/chat/completions").mock(
                return_value=httpx.Response(
                    200,
                    json={
                        "choices": [
                            {
                                "message": {
                                    "content": json.dumps(
                                        {"score": 0.75, "reason": "Reference mostly fulfilled"}
                                    )
                                }
                            }
                        ],
                        "usage": {"total_tokens": 55},
                    },
                )
            )
            async with httpx.AsyncClient() as client:
                result, usage = await judge(
                    client,
                    config,
                    Secrets(judge="private-judge-key"),
                    Budget(config),
                    single().cases[0],
                    "A",
                    "accuracy",
                )
            assert result.score == 0.75
            assert usage["total_tokens"] == 55
            sent = json.loads(route.calls[0].request.content)
            assert sent["response_format"] == {"type": "json_object"}
            assert sent["temperature"] == 0
            assert route.calls[0].request.headers["Authorization"] == "Bearer private-judge-key"

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "content", ['{"score":2,"reason":"bad"}', '{"score":0.5}', "not JSON", '{"score":"NaN","reason":"bad"}']
)
def test_judge_invalid_json_or_scores_rejected(content):
    async def scenario():
        with respx.mock as mock:
            mock.post("https://api.openai.com/v1/chat/completions").mock(
                return_value=httpx.Response(200, json={"choices": [{"message": {"content": content}}]})
            )
            async with httpx.AsyncClient() as client:
                with pytest.raises((ValueError, KeyError)):
                    await judge(
                        client,
                        conf(),
                        Secrets(judge="test-key"),
                        Budget(conf()),
                        single().cases[0],
                        "A",
                        "accuracy",
                    )

    asyncio.run(scenario())


def test_real_rubric_code_path_with_mock_http():
    async def scenario():
        with respx.mock as mock:
            mock.post("https://api.openai.com/v1/chat/completions").mock(
                return_value=httpx.Response(
                    200,
                    json={
                        "choices": [
                            {
                                "message": {
                                    "content": '{"rubric":"Check if the requested task is completed using verified evidence."}'
                                }
                            }
                        ]
                    },
                )
            )
            async with httpx.AsyncClient() as client:
                result = await propose_rubric(
                    client, conf(), Secrets(judge="test-key"), Budget(conf()), "Summarize a report"
                )
            assert result.startswith("Check")

    asyncio.run(scenario())


def test_judge_errors_are_na_not_product_failure(tmp_path):
    async def scenario():
        with respx.mock as mock:
            mock.post("http://localhost:9000/test").mock(
                return_value=httpx.Response(200, json={"output": "A"})
            )
            mock.post("https://api.openai.com/v1/chat/completions").mock(
                return_value=httpx.Response(401, json={"error": "bad key"})
            )
            run = await run_suite(
                single(),
                TargetConfig(kind="http", url="http://localhost:9000/test"),
                conf(use_judge=True),
                Secrets(judge="key"),
                Store(tmp_path / "db"),
                consent=True,
            )
            assert run["summary"]["errors"] == 0
            assert run["traces"][0]["metrics"]["accuracy"]["method"] == "judge-error"
            assert run["traces"][0]["metrics"]["accuracy"]["score"] is None
            assert run["comparison"]["verdict"] == "insufficient-data"

    asyncio.run(scenario())


def test_budget_exhaustion_is_harness_missing_data_not_quality_decline(tmp_path):
    async def scenario():
        with respx.mock as mock:
            route = mock.post("http://localhost:9000/test").mock(
                return_value=httpx.Response(200, json={"output": "A"})
            )
            run = await run_suite(
                single(),
                TargetConfig(kind="http", url="http://localhost:9000/test"),
                conf(budget_usd=0),
                Secrets(),
                Store(tmp_path / "db"),
                consent=True,
            )
            assert not route.called
            assert run["traces"][0]["harness_error"]
            assert run["summary"]["score"] is None
            assert run["comparison"]["verdict"] == "insufficient-data"

    asyncio.run(scenario())
