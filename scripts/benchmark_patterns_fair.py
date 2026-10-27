"""Matched controls for the NotebookE essay; runtime Command remains a model."""

from __future__ import annotations

import argparse
import ast
import asyncio
import csv
import hashlib
import json
import platform
import random
import statistics
import subprocess
import sys
import time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import benchmark_design_patterns as original

OUT = ROOT / "artifacts/design-pattern-benchmark/rerun-2026-10-16"


def save_csv(name, rows):
    with (OUT / name).open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(
            f, fieldnames=list(dict.fromkeys(k for row in rows for k in row))
        )
        writer.writeheader()
        writer.writerows(rows)


def percentile(v, p):
    return original.percentile(list(v), p)


class Receiver:
    def __init__(self, chunks):
        self.chunks = chunks
        self.index = 0
        self.bytes_read = 0

    async def __call__(self):
        if self.index >= len(self.chunks):
            return {"type": "http.request", "body": b"", "more_body": False}
        body = self.chunks[self.index]
        self.index += 1
        self.bytes_read += len(body)
        return {
            "type": "http.request",
            "body": body,
            "more_body": self.index < len(self.chunks),
        }


class Sender:
    def __init__(self):
        self.status = None
        self.body = b""

    async def __call__(self, message):
        if message["type"] == "http.response.start":
            self.status = message["status"]
        elif message["type"] == "http.response.body":
            self.body += message.get("body", b"")


async def response(body, send):
    await send({"type": "http.response.start", "status": 200, "headers": []})
    await send({"type": "http.response.body", "body": hashlib.sha256(body).digest()})


async def business(scope, receive, send):
    parts = []
    while True:
        message = await receive()
        parts.append(message.get("body", b""))
        if not message.get("more_body", False):
            break
    await response(b"".join(parts), send)


def inline_checked_endpoint(limit):
    from starlette.datastructures import Headers

    from api.middleware import _send_413

    async def app(scope, receive, send):
        if scope["type"] != "http":
            return await business(scope, receive, send)
        declared = Headers(scope=scope).get("content-length")
        if declared is not None:
            try:
                if int(declared) > limit:
                    await _send_413(send)
                    return
            except ValueError:
                pass
        parts = []
        size = 0
        while True:
            message = await receive()
            body = message.get("body") or b""
            size += len(body)
            if size > limit:
                await _send_413(send)
                return
            parts.append(body)
            if not message.get("more_body", False):
                break
        await response(b"".join(parts), send)

    return app


async def batch(app, chunks, headers, n):
    scope = {"type": "http", "method": "POST", "path": "/benchmark", "headers": headers}
    reads = 0
    start = time.perf_counter_ns()
    for _ in range(n):
        recv, send = Receiver(chunks), Sender()
        await app(scope, recv, send)
        reads += recv.bytes_read
    elapsed = (time.perf_counter_ns() - start) / 1e6
    return {
        "elapsed_ms": elapsed,
        "mean_us_per_operation": elapsed * 1000 / n,
        "bytes_read": reads / n,
        "status": send.status,
        "output_sha256": hashlib.sha256(send.body).hexdigest(),
    }


async def middleware(repetitions, operations, rng):
    from loguru import logger

    from api.middleware import MaxBodySizeMiddleware

    logger.disable("api.middleware")
    variants = {
        "inline": inline_checked_endpoint(1024),
        "middleware": MaxBodySizeMiddleware(business, 1024),
    }
    workloads = [
        ("valid_512_cl", (b"x" * 512,), [(b"content-length", b"512")], 200, 512),
        ("large_2048_cl", (b"x" * 2048,), [(b"content-length", b"2048")], 413, 0),
        ("large_2048_one_chunk", (b"x" * 2048,), [], 413, 2048),
        ("large_65536_chunks256", tuple([b"x" * 256] * 256), [], 413, 1280),
    ]
    # Verify equal status, bytes read, and response contents before timing.
    for name, chunks, headers, status, read in workloads:
        checks = [await batch(app, chunks, headers, 1) for app in variants.values()]
        assert all(v["status"] == status and v["bytes_read"] == read for v in checks), (
            name,
            checks,
        )
        assert checks[0]["output_sha256"] == checks[1]["output_sha256"], name
        for app in variants.values():
            await batch(app, chunks, headers, 200)
    extra = [
        ((b"x" * 512,), [(b"content-length", b"invalid")], 200),
        ((b"x" * 2048,), [(b"content-length", b"512")], 413),
    ]
    for chunks, headers, status in extra:
        results = [await batch(app, chunks, headers, 1) for app in variants.values()]
        assert results[0]["status"] == results[1]["status"] == status
        assert results[0]["bytes_read"] == results[1]["bytes_read"]
        assert results[0]["output_sha256"] == results[1]["output_sha256"]
    rows = []
    for rep in range(1, repetitions + 1):
        order = [(v, w) for v in variants for w in workloads]
        rng.shuffle(order)
        for variant, (name, chunks, headers, status, read) in order:
            row = await batch(variants[variant], chunks, headers, operations)
            assert row["status"] == status and row["bytes_read"] == read
            rows.append(
                {
                    "variant": variant,
                    "workload": name,
                    "repetition": rep,
                    "operations": operations,
                    **row,
                }
            )
        if rep % 5 == 0:
            print(f"Middleware {rep}/{repetitions}", flush=True)
    return rows


async def command_model(variant, items, capacity, delay_ms, faults, max_attempts):
    attempts = 0
    outcomes = {}
    active = 0
    peak_active = 0
    durations = []

    async def handle(item):
        nonlocal attempts, active, peak_active
        for attempt in range(1, max_attempts + 1):
            attempts += 1
            active += 1
            peak_active = max(peak_active, active)
            assert active <= capacity
            service_start = time.perf_counter_ns()
            deadline = service_start + int(delay_ms * 1_000_000)
            while time.perf_counter_ns() < deadline:
                await asyncio.sleep(max((deadline - time.perf_counter_ns()) / 1e9, 0))
            durations.append((time.perf_counter_ns() - service_start) / 1e6)
            active -= 1
            transient = faults and item % 5 == 0 and attempt == 1
            if not transient:
                outcomes[item] = hashlib.sha256(f"item:{item}".encode()).hexdigest()
                return
        outcomes[item] = None

    start = time.perf_counter_ns()
    if variant == "direct":
        sem = asyncio.Semaphore(capacity)

        async def bounded(item):
            async with sem:
                await handle(item)

        tasks = [asyncio.create_task(bounded(i)) for i in range(items)]
        submitted = (time.perf_counter_ns() - start) / 1e6
        await asyncio.gather(*tasks)
    else:
        queue = asyncio.Queue()

        async def worker():
            while True:
                item = await queue.get()
                try:
                    if item is None:
                        return
                    await handle(item)
                finally:
                    queue.task_done()

        workers = [asyncio.create_task(worker()) for _ in range(capacity)]
        for i in range(items):
            queue.put_nowait(i)
        submitted = (time.perf_counter_ns() - start) / 1e6
        await queue.join()
    completed = (time.perf_counter_ns() - start) / 1e6
    if variant == "queue":
        for _ in workers:
            queue.put_nowait(None)
        await asyncio.gather(*workers)
    assert len(outcomes) == items
    successes = sum(v is not None for v in outcomes.values())
    expected = items if not faults or max_attempts > 1 else items - items // 5
    assert successes == expected
    assert min(durations) >= delay_ms * 0.99
    assert completed >= attempts * delay_ms / capacity * 0.99, (
        variant,
        completed,
        attempts,
    )
    assert all(
        value == hashlib.sha256(f"item:{i}".encode()).hexdigest()
        for i, value in outcomes.items()
        if value
    )
    digest = hashlib.sha256(json.dumps(outcomes, sort_keys=True).encode()).hexdigest()
    return {
        "submitted": items,
        "succeeded": successes,
        "failed": items - successes,
        "attempts": attempts,
        "retries": attempts - items,
        "submit_ms": submitted,
        "completion_ms": completed,
        "output_sha256": digest,
        "peak_active": peak_active,
        "service_min_ms": min(durations),
        "service_median_ms": statistics.median(durations),
    }


async def commands(repetitions, rng):
    scenarios = [
        ("clean", False, 1),
        ("fault20_no_retry", True, 1),
        ("fault20_retry5", True, 5),
    ]
    for _, faults, tries in scenarios:
        for v in ["direct", "queue"]:
            await command_model(v, 100, 4, 10, faults, tries)
    rows = []
    for rep in range(1, repetitions + 1):
        jobs = [(v, s) for v in ["direct", "queue"] for s in scenarios]
        rng.shuffle(jobs)
        for variant, (name, faults, tries) in jobs:
            result = await command_model(variant, 100, 4, 10, faults, tries)
            rows.append(
                {
                    "variant": variant,
                    "scenario": name,
                    "repetition": rep,
                    "capacity": 4,
                    "requested_delay_ms": 10,
                    "max_attempts": tries,
                    **result,
                }
            )
        for scenario, _, _ in scenarios:
            pair = [
                r for r in rows if r["repetition"] == rep and r["scenario"] == scenario
            ]
            assert pair[0]["output_sha256"] == pair[1]["output_sha256"]
            assert pair[0]["attempts"] == pair[1]["attempts"]
        if rep % 5 == 0:
            print(f"Command model {rep}/{repetitions}", flush=True)
    return rows


def summarize(rows, key, metrics):
    groups = defaultdict(list)
    for row in rows:
        groups[(row["variant"], row[key])].append(row)
    result = []
    for (variant, name), group in sorted(groups.items()):
        record = {"variant": variant, key: name, "runs": len(group)}
        for metric in metrics:
            values = [r[metric] for r in group]
            record[metric + "_median"] = statistics.median(values)
            record[metric + "_p95"] = percentile(values, 0.95)
        result.append(record)
    return result


def paired(rows, key, metric, rng):
    results = []
    names = sorted({r[key] for r in rows})
    for name in names:
        groups = defaultdict(dict)
        for row in rows:
            if row[key] == name:
                groups[row["repetition"]][row["variant"]] = row[metric]
        control, treatment = (
            ("inline", "middleware") if key == "workload" else ("direct", "queue")
        )
        diffs = [p[treatment] - p[control] for p in groups.values()]
        means = [statistics.mean(rng.choices(diffs, k=len(diffs))) for _ in range(2000)]
        results.append(
            {
                key: name,
                "metric": metric,
                "treatment": treatment,
                "control": control,
                "paired_mean_delta": statistics.mean(diffs),
                "bootstrap_ci95_low": percentile(means, 0.025),
                "bootstrap_ci95_high": percentile(means, 0.975),
            }
        )
    return results


async def main():
    p = argparse.ArgumentParser()
    p.add_argument("--repetitions", type=int, default=30)
    p.add_argument("--operations", type=int, default=10000)
    args = p.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    rng = random.Random(20261016)
    static = original.factory_static_metrics() + original.command_static_metrics()
    source = (ROOT / "notebooke/ai/models.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    current = {
        "direct_provider_classes": sorted(
            {
                a.name
                for n in ast.walk(tree)
                if isinstance(n, ast.ImportFrom)
                and n.module
                and n.module.startswith("notebooke.ai.")
                and "provider" in n.module
                for a in n.names
                if a.name.endswith(("Provider", "Model"))
            }
        ),
        "factory_creation_calls": sum(
            isinstance(n, ast.Call)
            and isinstance(n.func, ast.Attribute)
            and isinstance(n.func.value, ast.Name)
            and n.func.value.id == "AIFactory"
            and n.func.attr.startswith("create_")
            for n in ast.walk(tree)
        ),
    }
    print("Static source recount completed", flush=True)
    mw = await middleware(args.repetitions, args.operations, rng)
    save_csv("middleware_raw.csv", mw)
    cmd = await commands(args.repetitions, rng)
    save_csv("command_model_raw.csv", cmd)
    ms = summarize(mw, "workload", ["mean_us_per_operation", "bytes_read", "status"])
    cs = summarize(
        cmd,
        "scenario",
        ["submit_ms", "completion_ms", "succeeded", "retries", "attempts"],
    )
    differences = paired(mw, "workload", "mean_us_per_operation", rng) + paired(
        cmd, "scenario", "completion_ms", rng
    )
    save_csv("middleware_summary.csv", ms)
    save_csv("command_model_summary.csv", cs)
    save_csv("paired_differences.csv", differences)
    save_csv("static_metrics.csv", static)
    metadata = {
        "generated_utc": datetime.now(timezone.utc).isoformat(),
        "python": sys.version,
        "platform": platform.platform(),
        "cpu": platform.processor(),
        "head": subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
        ).strip(),
        "repetitions": args.repetitions,
        "operations_per_batch": args.operations,
        "seed": 20261016,
        "middleware_warmup_per_case": 200,
        "command_warmup_per_case": 1,
        "docker_used_by_this_harness": False,
        "command_model": {
            "items": 100,
            "capacity": 4,
            "requested_delay_ms": 10,
            "failure_schedule": "item % 5 == 0 fails first attempt for BOTH variants in fault scenarios",
        },
        "limitations": [
            "Command is a matched in-memory model, not surreal-commands runtime.",
            "Middleware inline baseline is a constructed same-policy control, not a historical release.",
            "Middleware p95 is over batch means, not individual request latency.",
            "Async sleep is a requested delay; actual timer scheduling affects completion time.",
        ],
        "source_hashes": {
            n: hashlib.sha256((ROOT / n).read_bytes()).hexdigest()
            for n in [
                "scripts/benchmark_patterns_fair.py",
                "scripts/benchmark_design_patterns.py",
                "api/middleware.py",
                "notebooke/ai/models.py",
            ]
        },
    }
    result = {
        "metadata": metadata,
        "static_metrics": static,
        "current_factory": current,
        "middleware": ms,
        "command_model": cs,
        "paired_differences": differences,
    }
    (OUT / "results.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(result, ensure_ascii=True), flush=True)


if __name__ == "__main__":
    asyncio.run(main())
