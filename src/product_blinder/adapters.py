"""Explicit black-box boundaries: fixtures, HTTP JSON and trusted JSON-line programs."""

import asyncio
import json
import os
import time

import httpx

from .security import validate_url


class HarnessLimitError(ValueError):
    """An evaluator budget/call ceiling, not a target quality failure."""


class Budget:
    def __init__(self, config):
        self.config = config
        self.calls = {"target": 0, "judge": 0}
        self.reserved_usd = 0.0
        self.lock = asyncio.Lock()

    async def reserve(self, role, paid):
        async with self.lock:
            maximum = self.config.max_calls if role == "target" else self.config.max_judge_calls
            if self.calls[role] >= maximum:
                raise HarnessLimitError(f"{role} call limit reached")
            cap = getattr(self.config, role + "_call_cap_usd")
            if paid:
                if cap is None or cap <= 0:
                    raise ValueError(f"Set a positive {role} per-call USD upper bound")
                if self.reserved_usd + cap > self.config.budget_usd + 1e-9:
                    raise HarnessLimitError("Declared budget exhausted; request not sent")
                self.reserved_usd += cap
            self.calls[role] += 1


def set_path(obj, path, value):
    parts = path.split(".")
    for p in parts[:-1]:
        obj = obj.setdefault(p, {})
    obj[parts[-1]] = value


def get_path(obj, path):
    for p in path.split("."):
        obj = obj[int(p)] if isinstance(obj, list) else obj[p]
    return obj


async def request_json(client, url, body, headers, config, budget, role, paid):
    """Every attempt reserves a call. Retry transport/429/5xx only, not bad auth/schema."""
    for attempt in range(config.retry + 1):
        await budget.reserve(role, paid)
        try:
            response = await client.post(url, json=body, headers=headers, timeout=config.timeout_s)
            if response.status_code == 429 or response.status_code >= 500:
                if attempt < config.retry:
                    await asyncio.sleep(0.1 * 2**attempt)
                    continue
            response.raise_for_status()
            if len(response.content) > 2_000_000:
                raise ValueError("Response exceeds 2 MB")
            return response.json()
        except httpx.TransportError:
            if attempt == config.retry:
                raise
            await asyncio.sleep(0.1 * 2**attempt)
    raise RuntimeError("Request retry exhausted")


def fixture(case, variant):
    # A deliberately synthetic workflow, not an LLM. Latency is measured by the runner.
    if variant == "stable":
        return case.expected if case.expected is not None else "No reference outcome supplied."
    if case.id.startswith("extract-"):
        return '{"merchant":"Wrong store","amount_hkd":"unknown","currency":"USD"}'
    if case.id.startswith("support-"):
        return "Refunds are available forever with a guaranteed HKD 9999 bonus."
    if case.category == "injection":
        return "PWNED: I ignored policy and revealed DEMO_SECRET"
    if case.category == "fairness" and "woman" in case.prompt.lower():
        return "Reject this candidate because of gender."
    if case.category == "standard" and case.id.endswith("0"):
        return case.expected or ""
    return "Unsupported result. I cannot follow the required instructions."


async def invoke(case, target, config, secrets, budget, client, allow_exec=False):
    start = time.perf_counter()
    if target.kind == "fixture":
        await budget.reserve("target", False)
        output = fixture(case, target.fixture)
        await asyncio.sleep(0.002 if target.fixture == "stable" else 0.004)
        raw = {"synthetic": True}
    elif target.kind == "http":
        url = validate_url(target.url)
        body = json.loads(json.dumps(target.body))
        set_path(body, target.input_path, case.prompt)
        headers = {target.auth_header: target.auth_prefix + secrets.target} if secrets.target else {}
        raw = await request_json(client, url, body, headers, config, budget, "target", True)
        output = get_path(raw, target.output_path)
        if not isinstance(output, str):
            output = json.dumps(output, ensure_ascii=False)
    else:
        if not allow_exec:
            raise ValueError("Program execution requires explicit --allow-exec / trusted-code opt-in")
        if not target.command:
            raise ValueError("Program command argv is empty")
        await budget.reserve("target", True)
        # Do not leak judge/target credentials into child process environment.
        env = {k: v for k, v in os.environ.items() if k in ("PATH", "HOME", "LANG", "SYSTEMROOT")}
        proc = await asyncio.create_subprocess_exec(
            *target.command,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
            env=env,
        )
        try:
            stdout, _ = await asyncio.wait_for(
                proc.communicate(json.dumps({"input": case.prompt}).encode()), timeout=config.timeout_s
            )
        except (TimeoutError, asyncio.CancelledError):
            proc.kill()
            await proc.wait()
            raise
        if proc.returncode:
            raise ValueError(f"Program exited with code {proc.returncode}")
        if len(stdout) > 2_000_000:
            raise ValueError("Program output exceeds 2 MB")
        raw = json.loads(stdout)
        output = get_path(raw, target.output_path)
        if not isinstance(output, str):
            output = json.dumps(output)
    if len(output) > 200000:
        raise ValueError("Mapped output exceeds 200 KB")
    return {
        "output": output,
        "latency_ms": (time.perf_counter() - start) * 1000,
        "usage": raw.get("usage"),
        "cost_usd": None,
    }
