"""Audit memory samples and render RAM comparisons for the essay."""

from __future__ import annotations

import csv
import hashlib
import json
import math
import shutil
import statistics
import subprocess
import textwrap
from pathlib import Path
from xml.sax.saxutils import escape

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "artifacts/design-pattern-benchmark/ram-2026-10-17"
FIG = DATA / "figures"
MIB = 1048576
COLORS = ["#687584", "#127B8B"]


def tx(x, y, text, extra=""):
    return f'<text x="{x}" y="{y}" {extra}>{escape(str(text))}</text>'


def line(x, y, xx, yy, c="#DFE5E9", w=1):
    return (
        f'<line x1="{x}" y1="{y}" x2="{xx}" y2="{yy}" stroke="{c}" stroke-width="{w}"/>'
    )


def fmt(v):
    return f"{v:.3f}".replace(".", ",")


def panel(x, y, w, title, groups, field, labels):
    all_values = [r[field] / MIB for group in groups for r in group]
    high = max(all_values)
    if high < 0.01:
        maxval = 0.01
    elif high < 1:
        maxval = math.ceil(high * 10) / 10
    else:
        maxval = math.ceil(high * 1.1)
    left = x + 90
    plotw = w - 120
    result = tx(x, y, title, 'font-weight="bold"')
    for k in range(6):
        xx = left + plotw * k / 5
        result += line(xx, y + 60, xx, y + 245) + tx(
            xx, y + 277, fmt(maxval * k / 5), 'text-anchor="middle"'
        )
    for i, (group, label) in enumerate(zip(groups, labels)):
        values = [r[field] / MIB for r in group]
        yy = y + 105 + i * 100
        result += tx(x, yy + 5, label)
        for j, val in enumerate(values):
            offset = ((j % 7) - 3) * 4
            result += f'<circle cx="{left + val / maxval * plotw}" cy="{yy + offset}" r="3" fill="{COLORS[i]}" fill-opacity="0.5"/>'
        median = statistics.median(values)
        xx = left + median / maxval * plotw
        result += line(xx, yy - 19, xx, yy + 19, COLORS[i], 4)
        result += tx(
            left,
            yy + 42,
            f"Trung vị {fmt(median)}; min–max {fmt(min(values))}–{fmt(max(values))} MiB",
        )
    result += tx(
        left + plotw / 2,
        y + 315,
        "MiB (1 MiB = 1.048.576 byte)",
        'text-anchor="middle"',
    )
    return result


def save(name, title, body, notes):
    y = 690
    foot = ""
    for note in notes:
        for row in textwrap.wrap(note, width=132):
            foot += tx(40, y, row)
            y += 25
    height = max(800, y + 30)
    heading = tx(40, 38, title, 'font-weight="bold"')
    svg = f'<svg xmlns="http://www.w3.org/2000/svg" width="1200" height="{height}" viewBox="0 0 1200 {height}"><rect width="1200" height="{height}" fill="white"/><style>text{{font-family:"Times New Roman",Times,serif;font-size:13pt;fill:#193348}}</style>{heading}{tx(40, 72, "Mỗi điểm là một lượt đo; vạch đậm là trung vị; 30 tiến trình mới cho mỗi phương án.")}{body}{line(40, 660, 1160, 660)}{foot}</svg>'
    (FIG / name).write_text(svg, encoding="utf-8")


def validate(result):
    with (DATA / "samples.csv").open(encoding="utf-8", newline="") as f:
        samples = list(csv.DictReader(f))
    with (DATA / "trials.csv").open(encoding="utf-8", newline="") as f:
        trials = list(csv.DictReader(f))
    assert len(trials) == 120
    mappings = [
        ("python", "rss_bytes", "python_rss"),
        ("python", "private_bytes", "python_private"),
        ("docker", "container_usage_bytes", "docker_usage"),
        ("docker", "container_working_set_bytes", "docker_working_set"),
    ]
    for trial in trials:
        rows = [
            r
            for r in samples
            if r["kind"] == trial["kind"]
            and r["variant"] == trial["variant"]
            and r["repetition"] == trial["repetition"]
        ]
        for source, field, prefix in mappings:
            if trial["kind"] != "command" and source == "docker":
                continue
            baseline = [
                float(r[field])
                for r in rows
                if r["source"] == source and r["phase"] == "baseline"
            ]
            work = [
                float(r[field])
                for r in rows
                if r["source"] == source and r["phase"] == "workload"
            ]
            assert len(work) >= 3 and len(baseline) >= 3
            expected = {
                "baseline_bytes": statistics.median(baseline),
                "sampled_peak_bytes": max(work),
                "delta_bytes": max(0, max(work) - statistics.median(baseline)),
            }
            for suffix, value in expected.items():
                assert math.isclose(
                    float(trial[prefix + "_" + suffix]), value, abs_tol=1e-6
                )
    converted = []
    for row in trials:
        converted.append(
            {
                k: float(v)
                if v and k.endswith(("_bytes", "_samples", "_gap_ms"))
                else v
                for k, v in row.items()
            }
        )
    for summary in result["summary"]:
        group = [
            r
            for r in converted
            if r["kind"] == summary["kind"] and r["variant"] == summary["variant"]
        ]
        assert len(group) == 30
        for key, value in summary.items():
            if key.endswith("_median"):
                metric = key.removesuffix("_median")
                assert math.isclose(
                    statistics.median(r[metric] for r in group), value, abs_tol=1e-6
                )
    calibration = json.loads((DATA / "calibration.json").read_text(encoding="utf-8"))
    assert calibration["passed"]
    assert calibration["measurement"]["python_rss_delta_bytes"] >= 64 * MIB * 0.9
    return converted, {
        "passed": True,
        "trials": 120,
        "sample_count": len(samples),
        "calibration": "64 MiB touched allocation confirmed",
        "method": "independent recalculation of baseline, sampled maximum and delta from raw samples; medians over 30 trials",
    }


def main():
    FIG.mkdir(exist_ok=True)
    result = json.loads((DATA / "results.json").read_text(encoding="utf-8"))
    trials, audit = validate(result)

    def groups(kind):
        return [
            [r for r in trials if r["kind"] == kind and r["variant"] == variant]
            for variant in (
                ["direct", "worker"] if kind == "command" else ["inline", "middleware"]
            )
        ]

    for kind, prefix, name, title, labels in [
        (
            "command",
            "python_rss",
            "01-command-python-ram.svg",
            "Command — RAM vật lý của tiến trình Python",
            ["Trực tiếp", "Worker"],
        ),
        (
            "command",
            "docker_working_set",
            "02-command-docker-ram.svg",
            "Command — RAM container SurrealDB",
            ["Trực tiếp", "Worker"],
        ),
        (
            "middleware",
            "python_rss",
            "03-chain-python-ram.svg",
            "Chain of Responsibility — RAM tiến trình Python",
            ["Inline", "Middleware"],
        ),
    ]:
        body = panel(
            40,
            175,
            540,
            "A. RAM cực đại quan sát được",
            groups(kind),
            prefix + "_sampled_peak_bytes",
            labels,
        )
        body += panel(
            620,
            175,
            540,
            "B. RAM tăng thêm so với nền",
            groups(kind),
            prefix + "_delta_bytes",
            labels,
        )
        body += tx(
            40,
            580,
            "Khoảng min–max thể hiện biến thiên giữa các lượt, không phải khoảng tin cậy.",
        )
        notes = [
            "Cực đại lấy theo mẫu, có thể bỏ sót đỉnh ngắn. Mức tăng = max(0, cực đại trong tải − trung vị RAM nền)."
        ]
        if prefix == "docker_working_set":
            notes += [
                "Docker Engine: working set = cgroup usage − inactive_file. Container RAM riêng, không phải toàn bộ máy hoặc VM."
            ]
        else:
            notes += [
                "psutil Working Set/RSS trên Windows; đo đúng PID Python, không cộng launcher hoặc bộ nhớ container."
            ]
        notes += [
            "Command: 20 bản ghi × 1 KB, tối đa 4 tác vụ, worker thật; middleware: 100.000 lượt/case × 4 case."
            if kind == "command"
            else "Hai phía cùng bốn loại request, cùng giới hạn body và cùng số lượt; không bao gồm cơ sở dữ liệu hoặc AI."
        ]
        save(name, title, body, notes)
    report = [
        "# Kết quả đo RAM cho Design Patterns trong NotebookE",
        "",
        "Ngày đo: 17/10/2026. Không thay thế benchmark thời gian ngày 16/10/2026; đây là lượt riêng có profiler.",
        "",
        "## Công cụ và định nghĩa",
        f"- psutil {result['metadata']['psutil_version']}: Working Set/RSS và Private Bytes của đúng PID Python.",
        f"- Docker SDK {result['metadata']['docker_sdk_version']}: memory_stats.usage và working set = usage - inactive_file từ Docker Engine.",
        "- RSS/Working Set là bộ nhớ vật lý cư trú; Private Bytes là bộ nhớ riêng đã commit, không được gọi là RAM vật lý.",
        "- MiB = 1.048.576 byte. Cực đại là cực đại quan sát theo mẫu, không phải một bảo đảm bắt được mọi đỉnh.",
        "- RAM tăng thêm là max(0, đỉnh trong tải - trung vị nền). Giá trị 0 không có nghĩa chương trình không cấp phát.",
        "",
        "## Đối chứng và kiểm soát",
        "- 30 lượt/phương án, 120 lượt chính thức. Tiến trình Python mới mỗi lượt; container DB benchmark khởi động lại trước mỗi lượt Command.",
        "- Command: cùng 20 thao tác ghi, payload 1.024 byte, tối đa 4 tác vụ, không tiêm lỗi và không retry. Worker dùng thư viện thật, handler dùng repo_create của NotebookE; không gọi API hoặc AI.",
        "- Middleware: cùng 4 workload và cùng giới hạn 1.024 byte; 100.000 lượt/workload, tổng 400.000 lượt/phương án/lần.",
        "- Warmup trước khi đo; RAM nền lấy trong 650 ms chờ. Kiểm tra output nằm ngoài pha đo.",
        "- Profiler nằm ở tiến trình cha; mục tiêu là PID thực do tiến trình con báo, không phải PID launcher môi trường ảo.",
        "- Hiệu chuẩn bằng vùng nhớ 64 MiB đã chạm từng trang; phải quan sát được ít nhất 90% phần tăng mới cho chạy chính thức.",
        "",
        "## Kết quả (trung vị của 30 cực đại theo lượt)",
    ]
    for s in result["summary"]:
        report.append(
            f"- {s['kind']} / {s['variant']}: Python RSS đỉnh {s['python_rss_sampled_peak_bytes_median'] / MIB:.3f} MiB; tăng thêm {s['python_rss_delta_bytes_median'] / MIB:.3f} MiB."
        )
        report.append(
            f"  Khoảng lấy mẫu RSS lớn nhất từng lượt: trung vị {s['python_rss_max_gap_ms_median']:.3f} ms; giá trị lớn nhất {s['python_rss_max_gap_ms_max']:.3f} ms."
        )
        if s["kind"] == "command":
            report.append(
                f"  Docker working set đỉnh {s['docker_working_set_sampled_peak_bytes_median'] / MIB:.3f} MiB; tăng thêm {s['docker_working_set_delta_bytes_median'] / MIB:.3f} MiB."
            )
            report.append(
                f"  Khoảng lấy mẫu Docker lớn nhất từng lượt: trung vị {s['docker_working_set_max_gap_ms_median']:.3f} ms; giá trị lớn nhất {s['docker_working_set_max_gap_ms_max']:.3f} ms."
            )
    report += [
        "",
        "## Giới hạn khi kết luận",
        "- Không đo RAM toàn máy hoặc phần nền Docker Desktop VM. Không cộng Python RSS với container working set thành con số tổng RAM máy.",
        "- Worker và bên gửi cùng tiến trình Python trong benchmark; chưa đo cách triển khai worker riêng của production.",
        "- DB lưu command và trạng thái ngoài dữ liệu nghiệp vụ. Phần tăng gồm cache/allocator và dữ liệu giữ lại, không thể quy toàn bộ cho đối tượng Command.",
        "- Middleware tạo các đối tượng ngắn hạn; độ phân giải lấy mẫu và cách Python tái sử dụng heap có thể khiến delta RSS gần 0.",
        "- Không suy ra tốc độ từ thời gian các lượt có profiler; dùng bộ thời gian riêng.",
        "- Factory không được benchmark RAM vì nội dung đang đánh giá là giảm phụ thuộc mã, chưa có đối chứng khởi tạo tương đương.",
        "",
        "## Tái hiện",
        "Dùng Python .venv, cài psutil và Docker SDK. Bật Docker, dùng đúng container thử nghiệm notebooke-pattern-bench-20261016; không dùng DB NotebookE hiện có.",
        "Chạy: .venv/Scripts/python.exe scripts/profile_pattern_memory.py --repetitions 30",
        "Để vẽ: chạy scripts/render_pattern_memory.py bằng runtime có thư viện sharp ở đường dẫn đã ghi trong script.",
        "samples.csv chứa mẫu theo thời gian; trials.csv chứa từng lượt; results.json chứa tổng hợp; calibration.json và verification.json chứa kiểm tra.",
        "Các hình SVG giữ Times New Roman 13 pt; PNG rộng 3.600 px. Các nguồn đã dùng đo được sao lưu cùng số liệu.",
    ]
    (DATA / "README.md").write_text("\n".join(report) + "\n", encoding="utf-8")
    (DATA / "verification.json").write_text(
        json.dumps(audit, indent=2), encoding="utf-8"
    )
    for file in [
        "profile_pattern_memory.py",
        "benchmark_command_live.py",
        "benchmark_patterns_fair.py",
        "render_pattern_memory.py",
    ]:
        shutil.copy2(ROOT / "scripts" / file, DATA / file)
    node = Path(
        r"C:\Users\Mera\.cache\codex-runtimes\codex-primary-runtime\dependencies\node\bin\node.exe"
    )
    sharp = Path(
        r"C:\Users\Mera\.cache\codex-runtimes\codex-primary-runtime\dependencies\node\node_modules\sharp"
    )
    js = "const sharp=require(process.argv[1]);const fs=require('fs');const path=require('path');(async()=>{for(const n of fs.readdirSync(process.argv[2]).filter(n=>n.endsWith('.svg')))await sharp(path.join(process.argv[2],n),{density:72}).resize({width:3600}).png().toFile(path.join(process.argv[2],n.replace(/\\.svg$/,'.png')));})().catch(e=>{console.error(e);process.exit(1)});"
    subprocess.run([str(node), "-e", js, str(sharp), str(FIG)], check=True)
    manifest = {
        str(p.relative_to(DATA)): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(DATA.rglob("*"))
        if p.is_file() and p.name != "sha256.json"
    }
    (DATA / "sha256.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print(json.dumps({"verification": audit, "figures": str(FIG)}))


if __name__ == "__main__":
    main()
