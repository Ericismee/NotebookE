"""External RSS/Working Set sampling and Docker cgroup accounting for patterns."""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import csv
import gc
import hashlib
import importlib.metadata
import json
import os
import platform
import random
import statistics
import subprocess
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
OUT = ROOT / "artifacts/design-pattern-benchmark/ram-2026-10-17"
CONTAINER = "notebooke-pattern-bench-20261016"


def emit(event, **data):
    print(json.dumps({"event": event, "pid": os.getpid(), **data}), flush=True)


def boundary(event):
    emit(event)
    assert sys.stdin.readline().strip() == "GO"


async def command_child(variant):
    import benchmark_command_live as live

    db = await live.connection()
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
    listener = None
    if variant == "worker":
        listener = asyncio.create_task(live.worker.listen_for_commands(4))
        await asyncio.sleep(0.3)
        if listener.done():
            await listener
    try:
        await live.run_batch(variant, "clean", "warmup", items=20)
        live.attempts.clear()
        live.outputs.clear()
        live.finished.clear()
        gc.collect()
        result = await live.run_batch(
            variant, "clean", "measured", items=20, execution_observer=boundary
        )
        assert result["succeeded"] == 20 and result["peak_active"] <= 4
        emit("result", result=result)
    finally:
        if listener:
            listener.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await listener


async def middleware_child(variant):
    import benchmark_patterns_fair as bench
    from loguru import logger

    from api.middleware import MaxBodySizeMiddleware

    logger.disable("api.middleware")
    app = (
        bench.inline_checked_endpoint(1024)
        if variant == "inline"
        else MaxBodySizeMiddleware(bench.business, 1024)
    )
    workloads = [
        ("valid_512_cl", (b"x" * 512,), [(b"content-length", b"512")], 200, 512),
        ("large_2048_cl", (b"x" * 2048,), [(b"content-length", b"2048")], 413, 0),
        ("large_2048_one_chunk", (b"x" * 2048,), [], 413, 2048),
        ("large_65536_chunks256", tuple([b"x" * 256] * 256), [], 413, 1280),
    ]
    for _, chunks, headers, status, read in workloads:
        await bench.batch(app, chunks, headers, 200)
    gc.collect()
    boundary("ready")
    blocks = 0
    hashes = {}
    start = time.perf_counter()
    for _ in range(100):
        for name, chunks, headers, status, read in workloads:
            result = await bench.batch(app, chunks, headers, 1000)
            assert result["status"] == status and result["bytes_read"] == read
            hashes[name] = result["output_sha256"]
            blocks += 1
    duration = time.perf_counter() - start
    boundary("end")
    emit(
        "result",
        result={"duration_s": duration, "operations": blocks * 1000, "outputs": hashes},
    )


def calibration_child():
    boundary("ready")
    memory = bytearray(64 * 1024 * 1024)
    for offset in range(0, len(memory), 4096):
        memory[offset] = 1
    time.sleep(0.4)
    boundary("end")
    emit("result", result={"allocated_bytes": len(memory)})


class Monitor:
    def __init__(self, pid, container=None):
        import psutil

        self.process = psutil.Process(pid)
        self.container = container
        self.stop = threading.Event()
        self.phase = "baseline"
        self.rows = []
        self.errors = []
        self.origin = time.perf_counter()

    def python_loop(self):
        while not self.stop.is_set():
            try:
                info = self.process.memory_info()
                self.rows.append(
                    {
                        "source": "python",
                        "phase": self.phase,
                        "elapsed_s": time.perf_counter() - self.origin,
                        "rss_bytes": info.rss,
                        "private_bytes": getattr(info, "private", None),
                        "container_usage_bytes": None,
                        "container_working_set_bytes": None,
                    }
                )
            except Exception as exc:
                self.errors.append(type(exc).__name__)
                return
            self.stop.wait(0.005)

    def docker_loop(self):
        import docker

        client = docker.from_env(timeout=5)
        try:
            while not self.stop.is_set():
                try:
                    stats = client.api.stats(
                        self.container, stream=False, one_shot=True
                    )
                    mem = stats["memory_stats"]
                    inactive = mem.get("stats", {}).get(
                        "inactive_file",
                        mem.get("stats", {}).get("total_inactive_file", 0),
                    )
                    self.rows.append(
                        {
                            "source": "docker",
                            "phase": self.phase,
                            "elapsed_s": time.perf_counter() - self.origin,
                            "rss_bytes": None,
                            "private_bytes": None,
                            "container_usage_bytes": mem["usage"],
                            "container_working_set_bytes": max(
                                0, mem["usage"] - inactive
                            ),
                        }
                    )
                except Exception as exc:
                    self.errors.append(type(exc).__name__)
                    return
                self.stop.wait(0.05)
        finally:
            client.close()

    def start(self):
        self.threads = [threading.Thread(target=self.python_loop, daemon=True)]
        if self.container:
            self.threads.append(threading.Thread(target=self.docker_loop, daemon=True))
        for thread in self.threads:
            thread.start()

    def end(self):
        self.stop.set()
        for thread in self.threads:
            thread.join(6)
        assert not self.errors, self.errors


def next_event(process):
    for line in process.stdout:
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        if "event" in value:
            return value
    raise RuntimeError(f"Child ended before event: {process.stderr.read()[-2000:]}")


def trial(kind, variant, rep, client):
    if kind == "command":
        target = client.containers.get(CONTAINER)
        assert (
            target.name == CONTAINER
            and target.image.tags
            and "surrealdb/surrealdb:v2" in target.image.tags
        )
        target.restart(timeout=5)
    process = subprocess.Popen(
        [sys.executable, __file__, "--child", kind, "--variant", variant],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        cwd=ROOT,
    )
    monitor = None
    try:
        ready = next_event(process)
        assert ready["event"] == "ready"
        monitor = Monitor(ready["pid"], CONTAINER if kind == "command" else None)
        monitor.start()
        time.sleep(0.65)
        monitor.phase = "workload"
        process.stdin.write("GO\n")
        process.stdin.flush()
        assert next_event(process)["event"] == "end"
        monitor.end()
        process.stdin.write("GO\n")
        process.stdin.flush()
        result = next_event(process)
        assert result["event"] == "result"
        process.wait(timeout=30)
        assert process.returncode == 0, process.stderr.read()[-2000:]
        for row in monitor.rows:
            row.update(kind=kind, variant=variant, repetition=rep)
        measured = {
            "kind": kind,
            "variant": variant,
            "repetition": rep,
            "launcher_pid": process.pid,
            "measured_pid": ready["pid"],
        }
        for source, field, prefix in [
            ("python", "rss_bytes", "python_rss"),
            ("python", "private_bytes", "python_private"),
            ("docker", "container_usage_bytes", "docker_usage"),
            ("docker", "container_working_set_bytes", "docker_working_set"),
        ]:
            available = [
                r
                for r in monitor.rows
                if r["source"] == source and r[field] is not None
            ]
            if not available:
                continue
            base = [r[field] for r in available if r["phase"] == "baseline"]
            work = [r[field] for r in available if r["phase"] == "workload"]
            assert len(base) >= 3 and len(work) >= 3, (
                kind,
                variant,
                source,
                len(base),
                len(work),
            )
            measured[prefix + "_baseline_bytes"] = statistics.median(base)
            measured[prefix + "_sampled_peak_bytes"] = max(work)
            measured[prefix + "_delta_bytes"] = max(
                0, max(work) - statistics.median(base)
            )
            measured[prefix + "_workload_samples"] = len(work)
            times = [r["elapsed_s"] for r in available if r["phase"] == "workload"]
            measured[prefix + "_max_gap_ms"] = max(
                (b - a) * 1000 for a, b in zip(times, times[1:])
            )
        return measured, monitor.rows, result["result"]
    finally:
        if monitor and not monitor.stop.is_set():
            monitor.end()
        if process.poll() is None:
            process.kill()
            process.wait()


def save_csv(name, rows):
    keys = list(dict.fromkeys(k for row in rows for k in row))
    with (OUT / name).open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=keys)
        writer.writeheader()
        writer.writerows(rows)


def parent(repetitions):
    import docker

    OUT.mkdir(parents=True, exist_ok=True)
    client = docker.from_env(timeout=30)
    rng = random.Random(20261017)
    records = []
    samples = []
    outputs = []
    try:
        calibration, calibration_raw, expected = trial(
            "calibration", "allocation", 0, client
        )
        assert (
            calibration["python_rss_delta_bytes"] >= expected["allocated_bytes"] * 0.9
        )
        assert (
            calibration["python_private_delta_bytes"]
            >= expected["allocated_bytes"] * 0.9
        )
        (OUT / "calibration.json").write_text(
            json.dumps(
                {"passed": True, "expected": expected, "measurement": calibration},
                indent=2,
            ),
            encoding="utf-8",
        )
        save_csv("calibration_samples.csv", calibration_raw)
        emit(
            "calibration_passed",
            delta_mib=calibration["python_rss_delta_bytes"] / 1048576,
        )
        for rep in range(1, repetitions + 1):
            order = [
                ("command", "direct"),
                ("command", "worker"),
                ("middleware", "inline"),
                ("middleware", "middleware"),
            ]
            rng.shuffle(order)
            for kind, variant in order:
                record, raw, result = trial(kind, variant, rep, client)
                records.append(record)
                samples.extend(raw)
                outputs.append(
                    {
                        "kind": kind,
                        "variant": variant,
                        "repetition": rep,
                        "result": result,
                    }
                )
                save_csv("trials.csv", records)
                emit(
                    "progress",
                    repetition=rep,
                    total=repetitions,
                    kind=kind,
                    variant=variant,
                    rss_peak_mib=round(
                        record["python_rss_sampled_peak_bytes"] / 1048576, 3
                    ),
                )
            for kind in ["command", "middleware"]:
                pair = [
                    r for r in outputs if r["kind"] == kind and r["repetition"] == rep
                ]
                if kind == "command":
                    assert (
                        pair[0]["result"]["output_sha256"]
                        == pair[1]["result"]["output_sha256"]
                    )
                else:
                    assert pair[0]["result"]["outputs"] == pair[1]["result"]["outputs"]
        save_csv("samples.csv", samples)
        summary = []
        for kind, variant in [
            ("command", "direct"),
            ("command", "worker"),
            ("middleware", "inline"),
            ("middleware", "middleware"),
        ]:
            group = [
                r for r in records if r["kind"] == kind and r["variant"] == variant
            ]
            item = {"kind": kind, "variant": variant, "runs": len(group)}
            for key in group[0]:
                if key.endswith(("_bytes", "_samples", "_gap_ms")):
                    values = [r[key] for r in group]
                    item[key + "_median"] = statistics.median(values)
                    item[key + "_min"] = min(values)
                    item[key + "_max"] = max(values)
            summary.append(item)
        result = {
            "metadata": {
                "date": "2026-10-17",
                "platform": platform.platform(),
                "python": sys.version,
                "psutil_version": importlib.metadata.version("psutil"),
                "docker_sdk_version": importlib.metadata.version("docker"),
                "repetitions": repetitions,
                "seed": 20261017,
                "rss_requested_interval_ms": 5,
                "docker_requested_interval_ms": 50,
                "command_workload": "20 real DB writes x 1024 bytes, max concurrency 4, no injected errors, no retry; actual surreal-commands worker",
                "middleware_workload": "same four cases as matched benchmark; 100000 operations per case (400000 total), same count for both variants",
                "isolation": "fresh Python per trial; restart dedicated in-memory DB per Command trial; setup+20-work-item warmup then baseline; no validation inside measured phase",
                "metrics": "sampled Python Working Set/RSS and Private Bytes; Docker cgroup usage and working set = usage - inactive_file; MiB = bytes / 1048576",
                "limitations": [
                    "Sampled peak is a lower bound; see observed gaps.",
                    "Python and Docker measured separately; not host-wide RAM or Docker Desktop VM footprint.",
                    "Same-process Python worker; not separate production worker deployment.",
                    "Database stores commands and results: delta includes retention, allocator and cache behavior, not just pattern objects.",
                    "Memory sampling introduces overhead; use previous unprofiled runs for timing comparisons.",
                ],
                "source_sha256": hashlib.sha256(
                    Path(__file__).read_bytes()
                ).hexdigest(),
            },
            "summary": summary,
            "outputs": outputs,
        }
        (OUT / "results.json").write_text(
            json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        emit("finished", output=str(OUT), summary=summary)
    finally:
        client.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--repetitions", type=int, default=30)
    parser.add_argument("--child", choices=["command", "middleware", "calibration"])
    parser.add_argument("--variant")
    args = parser.parse_args()
    if args.child == "command":
        asyncio.run(command_child(args.variant))
    elif args.child == "middleware":
        asyncio.run(middleware_child(args.variant))
    elif args.child == "calibration":
        calibration_child()
    else:
        parent(args.repetitions)
