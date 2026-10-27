"""Real SurrealDB + surreal-commands worker, isolated synthetic write workload."""

import argparse
import asyncio
import contextlib
import csv
import hashlib
import io
import json
import os
import platform
import random
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATABASE = "comparison_20261016_" + uuid.uuid4().hex[:8]
sys.path.insert(0, str(ROOT))
# Explicit ephemeral benchmark instance; never load user's deployment credentials.
os.environ.update(
    SURREAL_URL="ws://127.0.0.1:18080/rpc",
    SURREAL_USER="benchmark",
    SURREAL_PASSWORD="benchmark",
    SURREAL_NAMESPACE="pattern_benchmark",
    SURREAL_DATABASE=DATABASE,
    SURREAL_COMMANDS_RETRY_ENABLED="false",
)
from loguru import logger
from pydantic import BaseModel
from rich.console import Console
from surreal_commands import command
from surreal_commands.core import worker
from surreal_commands.core.retry import RetryConfig, build_async_retry_instance
from surreal_commands.core.service import CommandRequest, command_service
from surrealdb import AsyncSurreal, RecordID

from notebooke.database.repository import repo_create

OUT = ROOT / "artifacts/design-pattern-benchmark/rerun-2026-10-16"
attempts = {}
outputs = {}
finished = {}
active = 0
peak = 0
error_buffer = io.StringIO()
logger.remove()
logger.add(error_buffer, level="ERROR")
worker.console = Console(file=io.StringIO(), force_terminal=False)
original_execute = worker.execute_command_with_semaphore


async def observed_execute(cmd_id, *args, **kwargs):
    try:
        return await original_execute(cmd_id, *args, **kwargs)
    finally:
        finished[str(cmd_id)] = time.perf_counter_ns()


worker.execute_command_with_semaphore = observed_execute


class WriteInput(BaseModel):
    batch: str
    item: int
    faults: bool
    payload: str


async def write_one(data: WriteInput) -> dict:
    global active, peak
    key = (data.batch, data.item)
    attempts[key] = attempts.get(key, 0) + 1
    active += 1
    peak = max(peak, active)
    assert active <= 4, active
    try:
        if data.faults and data.item % 5 == 0 and attempts[key] == 1:
            raise RuntimeError("Injected transient failure before write")
        digest = hashlib.sha256(data.payload.encode()).hexdigest()
        result = await repo_create(
            "benchmark_output",
            {
                "batch": data.batch,
                "item": data.item,
                "payload": data.payload,
                "digest": digest,
            },
        )
        assert result
        outputs[key] = digest
        return {"item": data.item, "digest": digest, "success": True}
    finally:
        active -= 1


def config(tries):
    return RetryConfig(
        enabled=True,
        max_attempts=tries,
        wait_strategy="fixed",
        wait_time=0,
        stop_on=[ValueError],
        retry_log_level="none",
    )


command("write_one", app="pattern_bench", retry=config(1))(write_one)
command("write_retry", app="pattern_bench", retry=config(5))(write_one)


async def connection():
    db = AsyncSurreal(os.environ["SURREAL_URL"])
    await db.signin({"username": "benchmark", "password": "benchmark"})
    await db.use("pattern_benchmark", DATABASE)
    return db


async def run_batch(variant, scenario, rep, items=20, execution_observer=None):
    global peak
    assert active == 0
    peak = 0
    batch = f"{variant}_{scenario}_{rep}"
    faults = scenario != "clean"
    tries = 5 if scenario == "fault20_retry5" else 1
    inputs = [
        WriteInput(
            batch=batch,
            item=i,
            faults=faults,
            payload=(f"item{i:02d}:" + ("x" * 1017))[:1024],
        )
        for i in range(items)
    ]
    cmd_ids = []
    sem = asyncio.Semaphore(4)
    if execution_observer:
        execution_observer("ready")
    start = time.perf_counter_ns()
    if variant == "direct":

        async def bounded(data):
            async with sem:
                try:
                    async for attempt in build_async_retry_instance(config(tries)):
                        with attempt:
                            await write_one(data)
                except RuntimeError:
                    pass

        tasks = [asyncio.create_task(bounded(data)) for data in inputs]
        submit_ms = (time.perf_counter_ns() - start) / 1e6
        await asyncio.gather(*tasks)
    else:

        async def submit(data):
            async with sem:
                return await command_service.submit_command(
                    CommandRequest(
                        app="pattern_bench",
                        command="write_retry" if tries > 1 else "write_one",
                        args=data.model_dump(),
                    )
                )

        cmd_ids = await asyncio.gather(*(submit(data) for data in inputs))
        submit_ms = (time.perf_counter_ns() - start) / 1e6
        deadline = time.perf_counter() + 45
        while not all(str(cid) in finished for cid in cmd_ids):
            if time.perf_counter() > deadline:
                raise TimeoutError(
                    f"Worker did not finish {batch}: {error_buffer.getvalue()[-1200:]}"
                )
            await asyncio.sleep(0.002)
    completion_ms = (time.perf_counter_ns() - start) / 1e6
    if execution_observer:
        execution_observer("end")
    # Query outside timed section: verify persisted rows, distinct items and content.
    db = await connection()
    try:
        all_rows = await db.select("benchmark_output")
        rows = [r for r in all_rows if r["batch"] == batch]
        success = items if not faults or tries > 1 else items - items // 5
        assert len(rows) == success, (
            batch,
            len(rows),
            success,
            error_buffer.getvalue()[-1800:],
        )
        assert len({r["item"] for r in rows}) == success, "Duplicate output"
        assert all(
            r["digest"] == hashlib.sha256(r["payload"].encode()).hexdigest()
            for r in rows
        )
        if variant == "worker":
            for cid in cmd_ids:
                status = await db.select(RecordID.parse(str(cid)))
                if isinstance(status, list):
                    assert len(status) == 1, status
                    status = status[0]
                assert status["status"] in ("completed", "failed"), status
            # Persisted output count and observed attempts enforce the injected fault schedule.
    finally:
        await db.close()
    used = sum(attempts[(batch, i)] for i in range(items))
    assert used == items + (items // 5 if faults and tries > 1 else 0), (batch, used)
    digest = hashlib.sha256(
        json.dumps(sorted((r["item"], r["digest"]) for r in rows)).encode()
    ).hexdigest()
    return {
        "variant": variant,
        "scenario": scenario,
        "repetition": rep,
        "items": items,
        "capacity": 4,
        "max_attempts": tries,
        "submit_ms": submit_ms,
        "completion_ms": completion_ms,
        "succeeded": success,
        "attempts": used,
        "retries": used - items,
        "peak_active": peak,
        "output_sha256": digest,
    }


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--repetitions", type=int, default=30)
    parser.add_argument("--smoke", action="store_true")
    args = parser.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    db = await connection()
    try:
        await db.create(
            "benchmark_output",
            {
                "batch": "setup",
                "item": -1,
                "payload": "",
                "digest": hashlib.sha256(b"").hexdigest(),
            },
        )
        await db.create(
            "command",
            {
                "status": "completed",
                "app": "pattern_bench",
                "name": "write_one",
                "args": {},
            },
        )
    finally:
        await db.close()
    listener = asyncio.create_task(worker.listen_for_commands(4))
    await asyncio.sleep(0.5)
    if listener.done():
        await listener
    rows = []
    rng = random.Random(20261016)
    scenarios = ["clean", "fault20_no_retry", "fault20_retry5"]
    try:
        for scenario in scenarios:
            for variant in ["direct", "worker"]:
                result = await run_batch(
                    variant, scenario, "warmup" + str(time.perf_counter_ns())
                )
                print(
                    "Warmup",
                    variant,
                    scenario,
                    round(result["completion_ms"], 3),
                    flush=True,
                )
        if not args.smoke:
            for rep in range(1, args.repetitions + 1):
                jobs = [(v, s) for v in ["direct", "worker"] for s in scenarios]
                rng.shuffle(jobs)
                for variant, scenario in jobs:
                    rows.append(await run_batch(variant, scenario, rep))
                for scenario in scenarios:
                    pair = [
                        r
                        for r in rows
                        if r["repetition"] == rep and r["scenario"] == scenario
                    ]
                    assert pair[0]["output_sha256"] == pair[1]["output_sha256"]
                print(f"Live worker {rep}/{args.repetitions}", flush=True)
            with (OUT / "command_live_raw.csv").open(
                "w", encoding="utf-8", newline=""
            ) as f:
                writer = csv.DictWriter(f, fieldnames=list(rows[0]))
                writer.writeheader()
                writer.writerows(rows)
            from benchmark_patterns_fair import paired, summarize

            summary = summarize(
                rows,
                "scenario",
                ["submit_ms", "completion_ms", "succeeded", "attempts", "retries"],
            )
            # paired helper expects direct/queue names; map worker only within bootstrap calculation.
            differences = paired(
                [
                    {**r, "variant": "queue" if r["variant"] == "worker" else "direct"}
                    for r in rows
                ],
                "scenario",
                "completion_ms",
                rng,
            )
            import importlib.metadata

            meta = {
                "generated_utc": datetime.now(timezone.utc).isoformat(),
                "python": sys.version,
                "platform": platform.platform(),
                "repetitions": args.repetitions,
                "items": 20,
                "capacity": 4,
                "payload_bytes": 1024,
                "seed": 20261016,
                "surreal_commands_version": importlib.metadata.version(
                    "surreal-commands"
                ),
                "surrealdb_client_version": importlib.metadata.version("surrealdb"),
                "database_engine": "surrealdb/surrealdb:v2; separate Docker memory instance",
                "database_name": DATABASE,
                "worker": "surreal_commands.core.worker.listen_for_commands, in same Python event loop, max_tasks=4",
                "scope": "Real DB writes and real command service/listener; synthetic registered handler using NotebookE repo_create; no API, AI, insight or embedding end-to-end.",
                "fairness": "Same payload, same repo_create, max concurrency 4, same first-attempt fault schedule, same retry attempts and zero fixed retry wait. Worker adds durable command and status writes.",
                "submission": "Direct measures scheduling local tasks; worker measures persisted command admission. Not equal durability guarantees.",
                "source_sha256": hashlib.sha256(
                    Path(__file__).read_bytes()
                ).hexdigest(),
            }
            (OUT / "command_live_results.json").write_text(
                json.dumps(
                    {
                        "metadata": meta,
                        "summary": summary,
                        "paired_differences": differences,
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )
            print(json.dumps(summary), flush=True)
        print("Live verification passed", flush=True)
    finally:
        listener.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await listener


if __name__ == "__main__":
    asyncio.run(main())
