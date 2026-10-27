"""Reproducible measurements for the Design Patterns essay.

This script deliberately separates three evidence classes:

1. ``historical_static`` reads real Git revisions and counts source-level
   dependencies/operations without executing the old environments.
2. ``isolated_runtime`` executes NotebookE's current MaxBodySizeMiddleware
   against a minimal ASGI receiver and a faithful pre-middleware control.
3. ``controlled_simulation`` models direct concurrent writes versus a bounded
   command worker. It demonstrates the mechanism only; it is not presented as
   a production NotebookE benchmark.

Outputs are written under artifacts/design-pattern-benchmark/.
"""

from __future__ import annotations

import argparse
import ast
import asyncio
import csv
import json
import math
import platform
import random
import statistics
import subprocess
import sys
import time
from collections import Counter
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Awaitable, Callable

ROOT = Path(__file__).resolve().parents[1]
OUTPUT_DIR = ROOT / "artifacts" / "design-pattern-benchmark"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

FACTORY_BEFORE = "10049342cb4ef8f098fedd1989d2de5a19f6aa71"
FACTORY_AFTER = "bea43f3ce7de3c492e1a9f478713e7ce00cfc297"
COMMAND_BEFORE = "303ffacfa5cb8505a53150f2a7051c447c3fe003"
COMMAND_AFTER = "301dd4e20ae050c41d04e790a83148954a49b427"
MIDDLEWARE_BEFORE = "fd7dee7cedeb99fab28ab0c2e26c6a33cfce17ca"
MIDDLEWARE_AFTER = "5316b1a8f389588589acebf6b20b51bccaaf5c7f"


def git_show(revision: str, path: str) -> str:
    result = subprocess.run(
        ["git", "show", f"{revision}:{path}"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    return result.stdout


def find_async_method(source: str, class_name: str, method_name: str) -> ast.AsyncFunctionDef:
    tree = ast.parse(source)
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == class_name:
            for child in node.body:
                if isinstance(child, ast.AsyncFunctionDef) and child.name == method_name:
                    return child
    raise ValueError(f"{class_name}.{method_name} not found")


def source_segment(source: str, node: ast.AST) -> str:
    segment = ast.get_source_segment(source, node)
    if segment is None:
        raise ValueError("Unable to recover source segment")
    return segment


def count_calls(node: ast.AST) -> Counter[str]:
    counts: Counter[str] = Counter()
    for child in ast.walk(node):
        if not isinstance(child, ast.Call):
            continue
        func = child.func
        if isinstance(func, ast.Name):
            counts[func.id] += 1
        elif isinstance(func, ast.Attribute):
            counts[func.attr] += 1
    return counts


def factory_static_metrics() -> list[dict[str, Any]]:
    before = git_show(FACTORY_BEFORE, "open_notebook/models/__init__.py")
    after = git_show(FACTORY_AFTER, "open_notebook/domain/models.py")

    def imported_concrete_models(source: str) -> tuple[int, int, list[str]]:
        tree = ast.parse(source)
        base_names = {
            "EmbeddingModel",
            "LanguageModel",
            "SpeechToTextModel",
            "TextToSpeechModel",
            "ModelType",
        }
        modules: set[str] = set()
        names: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                if node.module.startswith("open_notebook.models."):
                    modules.add(node.module)
                    for alias in node.names:
                        if alias.name.endswith("Model") and alias.name not in base_names:
                            names.add(alias.name)
        return len(names), len(modules), sorted(names)

    before_classes, before_modules, before_names = imported_concrete_models(before)
    after_classes, after_modules, after_names = imported_concrete_models(after)
    after_tree = ast.parse(after)
    factory_calls = 0
    for node in ast.walk(after_tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            if (
                isinstance(node.func.value, ast.Name)
                and node.func.value.id == "AIFactory"
                and node.func.attr.startswith("create_")
            ):
                factory_calls += 1

    return [
        {
            "pattern": "Factory",
            "evidence": "historical_static",
            "variant": "Before Esperanto factory",
            "commit": FACTORY_BEFORE,
            "metric": "provider_specific_classes_imported",
            "value": before_classes,
            "unit": "classes",
            "details": ", ".join(before_names),
        },
        {
            "pattern": "Factory",
            "evidence": "historical_static",
            "variant": "After Esperanto factory",
            "commit": FACTORY_AFTER,
            "metric": "provider_specific_classes_imported",
            "value": after_classes,
            "unit": "classes",
            "details": ", ".join(after_names),
        },
        {
            "pattern": "Factory",
            "evidence": "historical_static",
            "variant": "Before Esperanto factory",
            "commit": FACTORY_BEFORE,
            "metric": "local_provider_modules_imported",
            "value": before_modules,
            "unit": "modules",
            "details": "Imports under open_notebook.models.*",
        },
        {
            "pattern": "Factory",
            "evidence": "historical_static",
            "variant": "After Esperanto factory",
            "commit": FACTORY_AFTER,
            "metric": "local_provider_modules_imported",
            "value": after_modules,
            "unit": "modules",
            "details": "Imports under open_notebook.models.*",
        },
        {
            "pattern": "Factory",
            "evidence": "historical_static",
            "variant": "After Esperanto factory",
            "commit": FACTORY_AFTER,
            "metric": "central_aifactory_creation_paths",
            "value": factory_calls,
            "unit": "call_sites",
            "details": "AIFactory.create_* calls in ModelManager",
        },
    ]


def command_static_metrics() -> list[dict[str, Any]]:
    before_source = git_show(COMMAND_BEFORE, "open_notebook/domain/notebook.py")
    after_source = git_show(COMMAND_AFTER, "open_notebook/domain/notebook.py")
    handler_source = git_show(COMMAND_AFTER, "commands/embedding_commands.py")
    before_node = find_async_method(before_source, "Source", "add_insight")
    after_node = find_async_method(after_source, "Source", "add_insight")
    before_calls = count_calls(before_node)
    after_calls = count_calls(after_node)
    before_segment = source_segment(before_source, before_node)
    after_segment = source_segment(after_source, after_node)

    retry_match = None
    handler_tree = ast.parse(handler_source)
    for node in ast.walk(handler_tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == "create_insight_command":
            for decorator in node.decorator_list:
                text = ast.get_source_segment(handler_source, decorator) or ""
                if "max_attempts" in text:
                    retry_match = text
                    break
    max_attempts = 0
    if retry_match:
        import re

        match = re.search(r'["\']max_attempts["\']\s*:\s*(\d+)', retry_match)
        max_attempts = int(match.group(1)) if match else 0

    rows: list[dict[str, Any]] = []
    for variant, commit, calls, segment in (
        ("Direct insight write", COMMAND_BEFORE, before_calls, before_segment),
        ("Queued create_insight command", COMMAND_AFTER, after_calls, after_segment),
    ):
        rows.extend(
            [
                {
                    "pattern": "Command",
                    "evidence": "historical_static",
                    "variant": variant,
                    "commit": commit,
                    "metric": "caller_database_writes",
                    "value": calls["repo_query"],
                    "unit": "calls",
                    "details": "Calls in Source.add_insight",
                },
                {
                    "pattern": "Command",
                    "evidence": "historical_static",
                    "variant": variant,
                    "commit": commit,
                    "metric": "caller_command_submissions",
                    "value": calls["submit_command"],
                    "unit": "calls",
                    "details": "Calls in Source.add_insight",
                },
                {
                    "pattern": "Command",
                    "evidence": "historical_static",
                    "variant": variant,
                    "commit": commit,
                    "metric": "add_insight_source_lines",
                    "value": len(segment.splitlines()),
                    "unit": "lines",
                    "details": "Function source including docstring",
                },
            ]
        )
    rows.append(
        {
            "pattern": "Command",
            "evidence": "historical_static",
            "variant": "Queued create_insight command",
            "commit": COMMAND_AFTER,
            "metric": "configured_max_attempts",
            "value": max_attempts,
            "unit": "attempts",
            "details": "Decorator configuration on create_insight_command",
        }
    )
    return rows


class ReceiveOnce:
    def __init__(self, body: bytes) -> None:
        self.body = body
        self.sent = False
        self.bytes_read = 0

    async def __call__(self) -> dict[str, Any]:
        if self.sent:
            return {"type": "http.request", "body": b"", "more_body": False}
        self.sent = True
        self.bytes_read += len(self.body)
        return {"type": "http.request", "body": self.body, "more_body": False}


class NullSend:
    def __init__(self) -> None:
        self.status = 0

    async def __call__(self, message: dict[str, Any]) -> None:
        if message["type"] == "http.response.start":
            self.status = int(message["status"])


async def body_reader_app(scope: dict[str, Any], receive: Callable[[], Awaitable[dict[str, Any]]], send: NullSend) -> None:
    while True:
        message = await receive()
        if not message.get("more_body", False):
            break
    await send({"type": "http.response.start", "status": 200, "headers": []})
    await send({"type": "http.response.body", "body": b"ok"})


@dataclass
class BatchMeasurement:
    pattern: str
    evidence: str
    variant: str
    commit: str
    workload: str
    repetition: int
    operations: int
    elapsed_ms: float
    mean_us_per_operation: float
    bytes_read_per_operation: float
    status: int


async def middleware_batch(app: Any, body: bytes, operations: int, content_length: bool) -> tuple[float, float, int]:
    headers = []
    if content_length:
        headers.append((b"content-length", str(len(body)).encode()))
    scope = {"type": "http", "method": "POST", "path": "/upload", "headers": headers}
    bytes_read = 0
    status = 0
    start = time.perf_counter_ns()
    for _ in range(operations):
        receive = ReceiveOnce(body)
        send = NullSend()
        await app(scope, receive, send)
        bytes_read += receive.bytes_read
        status = send.status
    elapsed_ms = (time.perf_counter_ns() - start) / 1_000_000
    return elapsed_ms, bytes_read / operations, status


async def middleware_runtime_metrics(repetitions: int, operations: int) -> list[BatchMeasurement]:
    from loguru import logger

    # Isolate middleware control-flow cost from terminal/log sink throughput.
    logger.disable("api.middleware")
    from api.middleware import MaxBodySizeMiddleware

    limit = 1024
    before_app = body_reader_app
    after_app = MaxBodySizeMiddleware(body_reader_app, max_body_size=limit)
    workloads = [
        ("valid_512B_content_length", b"x" * 512, True),
        ("oversized_2048B_content_length", b"x" * 2048, True),
        ("oversized_2048B_no_content_length", b"x" * 2048, False),
    ]

    for app, body, has_length in (
        (before_app, b"x" * 512, True),
        (after_app, b"x" * 512, True),
        (before_app, b"x" * 2048, True),
        (after_app, b"x" * 2048, True),
        (before_app, b"x" * 2048, False),
        (after_app, b"x" * 2048, False),
    ):
        await middleware_batch(app, body, 200, has_length)

    rows: list[BatchMeasurement] = []
    for repetition in range(1, repetitions + 1):
        order = [
            ("Before size middleware", MIDDLEWARE_BEFORE, before_app),
            ("After size middleware", MIDDLEWARE_AFTER, after_app),
        ]
        if repetition % 2 == 0:
            order.reverse()
        for variant, commit, app in order:
            for workload, body, has_length in workloads:
                elapsed_ms, bytes_read, status = await middleware_batch(
                    app, body, operations, has_length
                )
                rows.append(
                    BatchMeasurement(
                        pattern="Chain of Responsibility",
                        evidence="isolated_runtime",
                        variant=variant,
                        commit=commit,
                        workload=workload,
                        repetition=repetition,
                        operations=operations,
                        elapsed_ms=elapsed_ms,
                        mean_us_per_operation=elapsed_ms * 1000 / operations,
                        bytes_read_per_operation=bytes_read,
                        status=status,
                    )
                )
    return rows


@dataclass
class SimulationBatch:
    pattern: str
    evidence: str
    variant: str
    repetition: int
    submitted: int
    succeeded: int
    failed: int
    retries: int
    submit_elapsed_ms: float
    completion_elapsed_ms: float


async def simulate_direct_writes(items: int, capacity: int, work_ms: float) -> tuple[int, int, int, float, float]:
    active = 0
    lock = asyncio.Lock()

    async def one() -> bool:
        nonlocal active
        async with lock:
            active += 1
            admitted = active <= capacity
        if not admitted:
            async with lock:
                active -= 1
            return False
        await asyncio.sleep(work_ms / 1000)
        async with lock:
            active -= 1
        return True

    start = time.perf_counter_ns()
    tasks = [asyncio.create_task(one()) for _ in range(items)]
    submit_elapsed = (time.perf_counter_ns() - start) / 1_000_000
    outcomes = await asyncio.gather(*tasks)
    completion = (time.perf_counter_ns() - start) / 1_000_000
    succeeded = sum(outcomes)
    return succeeded, items - succeeded, 0, submit_elapsed, completion


async def simulate_command_queue(items: int, capacity: int, work_ms: float, max_attempts: int) -> tuple[int, int, int, float, float]:
    queue: asyncio.Queue[tuple[int, int]] = asyncio.Queue()
    completed = 0
    failed = 0
    retries = 0

    async def worker() -> None:
        nonlocal completed, failed, retries
        while True:
            item, attempt = await queue.get()
            if item < 0:
                queue.task_done()
                return
            # Deterministic transient failure for 20% of items on first try.
            transient = item % 5 == 0 and attempt == 1
            await asyncio.sleep(work_ms / 1000)
            if transient and attempt < max_attempts:
                retries += 1
                await queue.put((item, attempt + 1))
            elif transient:
                failed += 1
            else:
                completed += 1
            queue.task_done()

    workers = [asyncio.create_task(worker()) for _ in range(capacity)]
    start = time.perf_counter_ns()
    for item in range(items):
        queue.put_nowait((item, 1))
    submit_elapsed = (time.perf_counter_ns() - start) / 1_000_000
    await queue.join()
    completion = (time.perf_counter_ns() - start) / 1_000_000
    for _ in workers:
        queue.put_nowait((-1, 1))
    await queue.join()
    await asyncio.gather(*workers)
    return completed, failed, retries, submit_elapsed, completion


async def command_simulation_metrics(repetitions: int) -> list[SimulationBatch]:
    rows: list[SimulationBatch] = []
    items = 100
    capacity = 4
    work_ms = 2.0
    for repetition in range(1, repetitions + 1):
        variants = [
            ("Direct concurrent writes, no retry", simulate_direct_writes),
            ("Bounded command workers, retry", simulate_command_queue),
        ]
        if repetition % 2 == 0:
            variants.reverse()
        for name, func in variants:
            if func is simulate_direct_writes:
                result = await func(items, capacity, work_ms)
            else:
                result = await func(items, capacity, work_ms, 5)
            succeeded, failed, retries, submit_elapsed, completion = result
            rows.append(
                SimulationBatch(
                    pattern="Command",
                    evidence="controlled_simulation",
                    variant=name,
                    repetition=repetition,
                    submitted=items,
                    succeeded=succeeded,
                    failed=failed,
                    retries=retries,
                    submit_elapsed_ms=submit_elapsed,
                    completion_elapsed_ms=completion,
                )
            )
    return rows


def percentile(values: list[float], quantile: float) -> float:
    ordered = sorted(values)
    if not ordered:
        return math.nan
    position = (len(ordered) - 1) * quantile
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    fraction = position - lower
    return ordered[lower] * (1 - fraction) + ordered[upper] * fraction


def summarize_runtime(rows: list[BatchMeasurement]) -> list[dict[str, Any]]:
    groups: dict[tuple[str, str], list[BatchMeasurement]] = {}
    for row in rows:
        groups.setdefault((row.variant, row.workload), []).append(row)
    summary = []
    for (variant, workload), group in sorted(groups.items()):
        latencies = [row.mean_us_per_operation for row in group]
        summary.append(
            {
                "pattern": "Chain of Responsibility",
                "evidence": "isolated_runtime",
                "variant": variant,
                "workload": workload,
                "runs": len(group),
                "operations_per_run": group[0].operations,
                "median_us_per_operation": statistics.median(latencies),
                "p95_us_per_operation": percentile(latencies, 0.95),
                "stdev_us_per_operation": statistics.stdev(latencies) if len(latencies) > 1 else 0,
                "median_bytes_read": statistics.median(row.bytes_read_per_operation for row in group),
                "status": group[0].status,
            }
        )
    return summary


def summarize_simulation(rows: list[SimulationBatch]) -> list[dict[str, Any]]:
    groups: dict[str, list[SimulationBatch]] = {}
    for row in rows:
        groups.setdefault(row.variant, []).append(row)
    summary = []
    for variant, group in sorted(groups.items()):
        summary.append(
            {
                "pattern": "Command",
                "evidence": "controlled_simulation",
                "variant": variant,
                "runs": len(group),
                "items_per_run": group[0].submitted,
                "median_success_rate_percent": statistics.median(
                    100 * row.succeeded / row.submitted for row in group
                ),
                "median_retries": statistics.median(row.retries for row in group),
                "median_submit_elapsed_ms": statistics.median(row.submit_elapsed_ms for row in group),
                "median_completion_elapsed_ms": statistics.median(
                    row.completion_elapsed_ms for row in group
                ),
            }
        )
    return summary


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repetitions", type=int, default=30)
    parser.add_argument("--operations", type=int, default=1000)
    args = parser.parse_args()
    random.seed(20260927)

    static_rows = factory_static_metrics() + command_static_metrics()
    runtime_rows = await middleware_runtime_metrics(args.repetitions, args.operations)
    simulation_rows = await command_simulation_metrics(args.repetitions)
    runtime_summary = summarize_runtime(runtime_rows)
    simulation_summary = summarize_simulation(simulation_rows)
    metadata = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "git_head": subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
        ).strip(),
        "python": sys.version,
        "platform": platform.platform(),
        "processor": platform.processor(),
        "repetitions": args.repetitions,
        "middleware_operations_per_run": args.operations,
        "random_seed": 20260927,
        "notes": [
            "Factory and Command structural values are extracted from real Git revisions.",
            "Middleware timings use the current MaxBodySizeMiddleware in a minimal ASGI harness.",
            "api.middleware logging is disabled during timing to avoid measuring terminal sink throughput.",
            "Command runtime values are a deterministic controlled simulation, not a live SurrealDB/worker benchmark.",
        ],
    }

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    write_csv(OUTPUT_DIR / "static_metrics.csv", static_rows)
    write_csv(OUTPUT_DIR / "middleware_raw.csv", [asdict(row) for row in runtime_rows])
    write_csv(OUTPUT_DIR / "middleware_summary.csv", runtime_summary)
    write_csv(OUTPUT_DIR / "command_simulation_raw.csv", [asdict(row) for row in simulation_rows])
    write_csv(OUTPUT_DIR / "command_simulation_summary.csv", simulation_summary)
    (OUTPUT_DIR / "metadata.json").write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (OUTPUT_DIR / "results.json").write_text(
        json.dumps(
            {
                "metadata": metadata,
                "static_metrics": static_rows,
                "middleware_summary": runtime_summary,
                "command_simulation_summary": simulation_summary,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    print(json.dumps({"output_dir": str(OUTPUT_DIR), "metadata": metadata}, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
