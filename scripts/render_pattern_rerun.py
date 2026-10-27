"""Render the matched benchmark results to publication SVG and slide PNG."""

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
DATA = ROOT / "artifacts/design-pattern-benchmark/rerun-2026-10-16"
OUT = DATA / "figures"
COLORS = ["#687584", "#127B8B", "#AD7833"]
INK = "#193348"


def tx(x, y, s, attrs=""):
    return f'<text x="{x}" y="{y}" {attrs}>{escape(str(s))}</text>'


def ln(x, y, xx, yy, color="#E1E7EB", width=1):
    return f'<line x1="{x}" y1="{y}" x2="{xx}" y2="{yy}" stroke="{color}" stroke-width="{width}"/>'


def rect(x, y, w, h, color):
    return f'<rect x="{x}" y="{y}" width="{max(w, 1.5)}" height="{h}" rx="2" fill="{color}"/>'


def number(n, dec=3):
    if dec == 0:
        return f"{n:,.0f}".replace(",", ".")
    return f"{n:.{dec}f}".replace(".", ",")


def save(name, title, sub, body, notes, height=820):
    foot = []
    for note in notes:
        foot.extend(textwrap.wrap(note, width=132))
    yy = height - 30 - len(foot) * 24
    body += ln(40, yy - 18, 1160, yy - 18)
    body += "".join(tx(40, yy + i * 24, n) for i, n in enumerate(foot))
    heading = tx(40, 38, title, 'font-weight="bold"')
    svg = f'<svg xmlns="http://www.w3.org/2000/svg" width="1200" height="{height}" viewBox="0 0 1200 {height}"><rect width="1200" height="{height}" fill="white"/><style>text{{font-family:"Times New Roman",Times,serif;font-size:13pt;fill:{INK}}}</style>{heading}{tx(40, 70, sub)}{body}</svg>'
    (OUT / name).write_text(svg, encoding="utf-8")


def legend(a, b):
    return (
        rect(40, 102, 18, 14, COLORS[0])
        + tx(68, 115, a)
        + rect(620, 102, 18, 14, COLORS[1])
        + tx(648, 115, b)
    )


def grouped_horizontal(labels, pairs, maxval, unit, dec=3, p95=None):
    left, width = 400, 590
    step = 95 if len(labels) == 4 else 115
    top = 190
    bottom = top + (len(labels) - 1) * step + 64
    s = ""
    for k in range(6):
        value = maxval * k / 5
        xx = left + width * k / 5
        s += ln(xx, 155, xx, bottom) + tx(
            xx,
            bottom + 29,
            number(value, 0 if maxval >= 10 else 1),
            'text-anchor="middle"',
        )
    for i, (label, pair) in enumerate(zip(labels, pairs)):
        y = top + i * step
        for j, row in enumerate(textwrap.wrap(label, 33)):
            s += tx(40, y + 13 + j * 22, row)
        for j, val in enumerate(pair):
            yy = y + j * 35
            xx = left + width * val / maxval
            s += rect(left, yy - 14, xx - left, 23, COLORS[j])
            labelx = 1000
            if p95:
                pp = left + width * p95[i][j] / maxval
                s += ln(xx, yy - 3, pp, yy - 3, COLORS[j], 2) + ln(
                    pp, yy - 9, pp, yy + 3, COLORS[j], 2
                )
                s += tx(
                    labelx,
                    yy + 3,
                    number(val, dec) + " / " + number(p95[i][j], dec),
                    'font-weight="bold"',
                )
            else:
                s += tx(labelx, yy + 3, number(val, dec), 'font-weight="bold"')
    s += tx(left + width / 2, bottom + 65, unit, 'text-anchor="middle"')
    return s


def validate(result, live):
    # Independently recompute all plotted medians / p95 from raw files.
    for filename, summary, key, metrics in [
        (
            "middleware_raw.csv",
            result["middleware"],
            "workload",
            ["mean_us_per_operation", "bytes_read"],
        ),
        (
            "command_live_raw.csv",
            live["summary"],
            "scenario",
            ["submit_ms", "completion_ms", "succeeded", "retries"],
        ),
    ]:
        with (DATA / filename).open(encoding="utf-8", newline="") as f:
            rows = list(csv.DictReader(f))
        if filename == "command_live_raw.csv":
            assert len(rows) == 180
            assert all(int(r["peak_active"]) <= 4 for r in rows)
            for rep in range(1, 31):
                for scenario in {r["scenario"] for r in rows}:
                    pair = [
                        r
                        for r in rows
                        if int(r["repetition"]) == rep and r["scenario"] == scenario
                    ]
                    assert len(pair) == 2
                    assert pair[0]["output_sha256"] == pair[1]["output_sha256"]
                    assert pair[0]["attempts"] == pair[1]["attempts"]
        for record in summary:
            group = [
                r
                for r in rows
                if r["variant"] == record["variant"] and r[key] == record[key]
            ]
            assert len(group) == 30
            for metric in metrics:
                values = sorted(float(r[metric]) for r in group)
                assert math.isclose(
                    statistics.median(values), record[metric + "_median"], abs_tol=1e-9
                )
                pos = 29 * 0.95
                lo = math.floor(pos)
                hi = math.ceil(pos)
                p95 = values[lo] * (hi - pos) + values[hi] * (pos - lo)
                assert math.isclose(p95, record[metric + "_p95"], abs_tol=1e-9)
    return {
        "checked": True,
        "recalculated_from_raw": True,
        "groups": 14,
        "repetitions": 30,
        "output_equality": "Measured pairs verified in benchmark scripts using digests; no duplicate persisted outputs.",
        "note": "The 27 September unmatched Command results are superseded for causal comparisons.",
    }


def main():
    OUT.mkdir(exist_ok=True)
    data = json.loads((DATA / "results.json").read_text(encoding="utf-8"))
    live = json.loads((DATA / "command_live_results.json").read_text(encoding="utf-8"))
    audit = validate(data, live)
    audit["runtime_runs"] = {
        "middleware": 240,
        "command_live": 180,
        "command_model": 180,
    }
    measured_source = DATA / "benchmark_command_live.py"
    if not measured_source.exists():
        measured_source = ROOT / "scripts/benchmark_command_live.py"
    audit["command_live_source_matches"] = (
        hashlib.sha256(measured_source.read_bytes()).hexdigest()
        == live["metadata"]["source_sha256"]
    )
    assert audit["command_live_source_matches"]
    (DATA / "verification.json").write_text(
        json.dumps(audit, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    s = ""
    left, bottom, width, ph = 140, 500, 900, 280
    for v in [0, 5, 10, 15, 20]:
        yy = bottom - v / 20 * ph
        s += ln(left, yy, left + width, yy) + tx(
            left - 20, yy + 5, str(v), 'text-anchor="end"'
        )
    counts = [19, 0, len(data["current_factory"]["direct_provider_classes"])]
    labels = ["Trước chuyển đổi", "Sau chuyển đổi", "Mã nguồn khảo sát hiện tại"]
    for i, (v, label) in enumerate(zip(counts, labels)):
        x = 260 + i * 340
        s += (
            rect(x - 50, bottom - v / 20 * ph, 100, max(v / 20 * ph, 1.5), COLORS[i])
            + tx(
                x,
                bottom - v / 20 * ph - 16,
                str(v),
                'text-anchor="middle" font-weight="bold"',
            )
            + tx(x, bottom + 35, label, 'text-anchor="middle"')
        )
    s += tx(
        590,
        585,
        "Số lớp provider import trực tiếp tại thành phần tạo mô hình",
        'text-anchor="middle"',
    )
    save(
        "01-factory.svg",
        "Hình 7.1. Factory — So sánh mức phụ thuộc trực tiếp",
        "Đếm lại bằng AST; phân biệt rõ ba trạng thái để tránh hiểu sai số 0.",
        s,
        [
            "Số module provider nội bộ tại lần chuyển đổi: 4 → 0. Mã hiện tại có 4 điểm gọi tạo qua AIFactory.",
            "Ngoại lệ hiện tại: EdgeTTSProvider. Phân tích tĩnh không phải phép đo thời gian thực thi.",
        ],
    )
    scenarios = ["clean", "fault20_no_retry", "fault20_retry5"]
    labs = [
        "Không tiêm lỗi, 1 lần thử",
        "20% lỗi lần đầu, không retry",
        "20% lỗi lần đầu, tối đa 5 lần thử",
    ]
    cs = {(r["variant"], r["scenario"]): r for r in live["summary"]}
    pairs = [
        [100 * cs[(v, sc)]["succeeded_median"] / 20 for v in ["direct", "worker"]]
        for sc in scenarios
    ]
    s = legend(
        "Gọi trực tiếp — tối đa 4 tác vụ", "Command + worker thật — tối đa 4 tác vụ"
    )
    s += grouped_horizontal(labs, pairs, 100, "Tỷ lệ bản ghi thành công (%)", 0)
    save(
        "02-command-success-live.svg",
        "Hình 7.2. Command — Tỷ lệ thành công với cùng chính sách retry",
        "SurrealDB và worker thật; 20 công việc × 30 lần/kịch bản; cùng payload 1 KB.",
        s,
        [
            "Hai phía có cùng lịch lỗi và giới hạn đồng thời. Retry giúp cả hai phía đạt cùng tỷ lệ thành công.",
            "Handler benchmark dùng hàm repo_create của NotebookE; chưa phải luồng insight/embedding hoặc API đầy đủ.",
        ],
    )
    for metric, title, axis, name in [
        (
            "completion_ms",
            "Thời gian hoàn thành toàn bộ lô",
            "Thời gian hoàn thành (ms)",
            "03-command-completion-live.svg",
        ),
        (
            "submit_ms",
            "Thời gian chuyển giao công việc",
            "Thời gian chuyển giao (ms)",
            "04-command-submission-live.svg",
        ),
    ]:
        pairs = [
            [cs[(v, sc)][metric + "_median"] for v in ["direct", "worker"]]
            for sc in scenarios
        ]
        high = max(max(p) for p in pairs)
        ceiling = (
            math.ceil(high / 100) * 100 if high >= 100 else math.ceil(high / 10) * 10
        )
        s = legend(
            "Gọi trực tiếp — tối đa 4 tác vụ", "Command + worker thật — tối đa 4 tác vụ"
        ) + grouped_horizontal(labs, pairs, ceiling, axis, 3)
        notes = [
            "Trung vị 30 lần chạy; cùng dữ liệu, lịch lỗi, giới hạn đồng thời và số lần thử. Số liệu mới, không dùng số cũ.",
            "Worker chịu thêm chi phí lưu command, nhận qua LIVE query và cập nhật trạng thái; không mặc định nhanh hơn.",
        ]
        if metric == "submit_ms":
            notes[1] = (
                "Trực tiếp đo lập lịch task trong RAM; worker đo lưu command bền vững. Hai phía khác bảo đảm lưu trữ."
            )
        save(
            name,
            "Hình 7."
            + ("3" if metric == "completion_ms" else "4")
            + ". Command — "
            + title,
            "Benchmark tích hợp thành phần: SurrealDB + surreal-commands + hàm lưu trữ NotebookE.",
            s,
            notes,
        )
    workloads = [
        "valid_512_cl",
        "large_2048_cl",
        "large_2048_one_chunk",
        "large_65536_chunks256",
    ]
    labels = [
        "512 B hợp lệ, có Content-Length",
        "2.048 B quá lớn, có Content-Length",
        "2.048 B, không header, 1 chunk",
        "65.536 B, không header, chunk 256 B",
    ]
    ms = {(r["variant"], r["workload"]): r for r in data["middleware"]}
    pairs = [
        [ms[(v, w)]["mean_us_per_operation_median"] for v in ["inline", "middleware"]]
        for w in workloads
    ]
    tails = [
        [ms[(v, w)]["mean_us_per_operation_p95"] for v in ["inline", "middleware"]]
        for w in workloads
    ]
    high = math.ceil(max(max(p) for p in tails) / 5) * 5
    s = legend(
        "Kiểm tra trực tiếp trong endpoint", "Chain of Responsibility — middleware"
    )
    s += grouped_horizontal(labels, pairs, high, "Thời gian xử lý (µs/lượt)", 3, tails)
    save(
        "05-chain-time-matched.svg",
        "Hình 7.5. Chain of Responsibility — Chi phí với cùng quy tắc kiểm tra",
        "Nhãn: trung vị / p95; 30 lần × 10.000 lượt; giới hạn body 1.024 byte.",
        s,
        [
            "Hai phía trả cùng mã HTTP, cùng nội dung phản hồi và đọc cùng lượng dữ liệu; không có đối chứng bỏ kiểm tra.",
            "p95 của 30 thời gian trung bình/lượt theo batch; không phải p95 từng request. Benchmark ASGI cô lập.",
        ],
    )
    pairs = [
        [ms[(v, w)]["bytes_read_median"] for v in ["inline", "middleware"]]
        for w in workloads
    ]
    s = legend(
        "Kiểm tra trực tiếp trong endpoint", "Chain of Responsibility — middleware"
    )
    s += grouped_horizontal(labels, pairs, 2560, "Body đã đọc (byte/request)", 0)
    save(
        "06-chain-bytes-matched.svg",
        "Hình 7.6. Chain of Responsibility — Dữ liệu đọc với cùng mức bảo vệ",
        "Cùng giới hạn 1.024 byte; trường hợp hợp lệ trả 200, ba trường hợp quá lớn trả 413.",
        s,
        [
            "Request 65.536 B chia chunk 256 B: cả hai dừng sau 1.280 B (5 chunk), không đọc toàn bộ body.",
            "Lượng đọc bằng nhau khi cùng chính sách. Lợi ích của pattern nằm ở phân chia và tái sử dụng trách nhiệm.",
        ],
    )
    node = Path(
        r"C:\Users\Mera\.cache\codex-runtimes\codex-primary-runtime\dependencies\node\bin\node.exe"
    )
    sharp = Path(
        r"C:\Users\Mera\.cache\codex-runtimes\codex-primary-runtime\dependencies\node\node_modules\sharp"
    )
    js = "const sharp=require(process.argv[1]);const fs=require('fs');const path=require('path');(async()=>{for(const n of fs.readdirSync(process.argv[2]).filter(n=>n.endsWith('.svg')))await sharp(path.join(process.argv[2],n),{density:72}).resize({width:3600}).png().toFile(path.join(process.argv[2],n.replace(/\\.svg$/,'.png')));})().catch(e=>{console.error(e);process.exit(1)});"
    subprocess.run([str(node), "-e", js, str(sharp), str(OUT)], check=True)
    for name in [
        "benchmark_patterns_fair.py",
        "benchmark_command_live.py",
        "benchmark_design_patterns.py",
        "render_pattern_rerun.py",
    ]:
        target = DATA / name
        if not target.exists() or name == "render_pattern_rerun.py":
            shutil.copy2(ROOT / "scripts" / name, target)
    report = [
        "# Kết quả đo lại và bộ hình thay thế",
        "",
        "Bộ số liệu ngày 16/10/2026 thay thế các so sánh không tương đương trong bộ 27/09/2026.",
        "",
        "## Phạm vi đo",
        "- Factory: đếm lại mã nguồn, không benchmark AI.",
        "- Command: SurrealDB 2.7.0 trong Docker memory riêng; surreal-commands listener và executor thật; handler tổng hợp dùng repo_create của NotebookE. Worker cùng tiến trình/event loop với bên gửi; chưa phải benchmark API hoặc insight/embedding end-to-end.",
        "- Middleware: chạy lớp MaxBodySizeMiddleware thật; đối chứng giả định kiểm tra inline có cùng policy, không phải phiên bản cũ có bằng chứng.",
        "",
        "## Môi trường",
        f"- {data['metadata']['platform']}; Python {data['metadata']['python'].split()[0]}; CPU {data['metadata']['cpu']}.",
        f"- surreal-commands {live['metadata']['surreal_commands_version']}; SurrealDB Python client {live['metadata']['surrealdb_client_version']}.",
        "- Thứ tự cấu hình được hoán đổi theo seed 20261016; 30 lần lặp. Middleware có 200 lượt warmup/case; Command có một lô warmup/case.",
        "- Command: 20 công việc/lô, payload 1.024 byte, 4 tác vụ đồng thời; các kịch bản cùng lịch lỗi lần đầu và cùng retry cố định 0 giây, tối đa 1 hoặc 5 lần. Không cần AI key.",
        "- Middleware: 10.000 lượt/batch; body 512 B, 2.048 B, hoặc 65.536 B; giới hạn 1.024 B; logger tắt.",
        "",
        "## Command — kết quả tích hợp thật",
    ]
    for sc, label in zip(scenarios, labs):
        direct, worker = cs[("direct", sc)], cs[("worker", sc)]
        report.append(
            f"- {label}: trực tiếp {direct['completion_ms_median']:.3f} ms, worker {worker['completion_ms_median']:.3f} ms; thành công {direct['succeeded_median']:.0f}/20 và {worker['succeeded_median']:.0f}/20. Retry: {direct['retries_median']:.0f} và {worker['retries_median']:.0f}."
        )
    report += [
        "",
        "Lập lịch task trực tiếp chỉ tạo công việc trong RAM; submit worker thực sự ghi command vào DB. Không dùng chênh lệch submit để tuyên bố API nhanh/chậm hơn cùng mức bảo đảm.",
        "Với tác vụ ghi nhỏ, worker chậm hơn do ghi command, nhận LIVE query, cập nhật running/completed và kết nối. Kết quả không chứng minh Command tự tăng tỷ lệ thành công; lợi ích là chuyển giao và quản lý vòng đời công việc.",
        "",
        "## Middleware — đối chứng cùng chức năng",
    ]
    for w, label in zip(workloads, labels):
        control, treatment = ms[("inline", w)], ms[("middleware", w)]
        report.append(
            f"- {label}: inline {control['mean_us_per_operation_median']:.3f} µs/lượt, middleware {treatment['mean_us_per_operation_median']:.3f} µs/lượt; body đọc {control['bytes_read_median']:.0f} và {treatment['bytes_read_median']:.0f} byte; HTTP {treatment['status_median']:.0f}."
        )
    report += [
        "",
        "Hai phía trả cùng status, response digest và lượng đọc. Không còn quy lợi ích chặn body cho riêng pattern khi đối chứng cũng có cùng policy. Pattern hỗ trợ tách trách nhiệm và áp dụng kiểm tra tập trung.",
        "p95 middleware là p95 của 30 giá trị thời gian trung bình/lượt theo batch, không phải p95 latency từng request. paired_differences.csv chứa chênh lệch ghép cặp của middleware và mô hình; chênh lệch worker thật nằm trong command_live_results.json. Không gọi vạch p95 là CI.",
        "",
        "## Cập nhật tiểu luận",
        "- Chương 3: giữ số đếm 19 → 0, 4 → 0 tại lần chuyển đổi; bổ sung mã hiện tại còn 1 lớp EdgeTTSProvider và 4 điểm gọi Factory.",
        "- Chương 4 và 7: bỏ lập luận 4% → 100% là hiệu quả của Command. Cùng chính sách: 100%/100% khi không lỗi; 80%/80% khi lỗi không retry; 100%/100% khi retry.",
        "- Chương 5 và 7: dùng đối chứng cùng giới hạn thay cho cấu hình bỏ kiểm tra. Hai cách đọc cùng lượng body; phân tích overhead và phân chia trách nhiệm.",
        "",
        "## Cách tái hiện",
        "Giữ dữ liệu cũ nguyên vẹn. Các script được sao lưu ngay trong thư mục này và mã băm nằm trong sha256.json.",
        "Chạy benchmark_patterns_fair.py với --repetitions 30 --operations 10000 để đo ASGI và mô hình có đối chứng.",
        "Để chạy benchmark_command_live.py --repetitions 30, cần container SurrealDB benchmark ở 127.0.0.1:18080 với tài khoản thử benchmark/benchmark. Script tạo database riêng mỗi lần, không dùng .env của người dùng. Chạy bằng Python trong .venv của dự án.",
        "render_pattern_rerun.py tính lại trung vị/p95 từ raw CSV, đối chiếu số liệu trước khi xuất SVG và PNG.",
        "",
        "## Bộ hình sử dụng về sau",
        "figures/ gồm 6 hình SVG và PNG 3.600 px, Times New Roman 13 pt ở kích thước vector gốc. Thu phóng toàn hình thay đổi kích thước chữ hiển thị.",
        "01 Factory; 02 Command tỷ lệ thành công; 03 Command hoàn tất; 04 Command chuyển giao; 05 Chain thời gian; 06 Chain lượng body.",
        "Bộ cũ giữ để truy vết, không dùng các kết luận so sánh cũ. Khi làm slide có thể tăng cỡ chữ và rút chú thích nhưng phải giữ nhãn phạm vi đo.",
    ]
    (DATA / "README.md").write_text("\n".join(report) + "\n", encoding="utf-8")
    manifest = {
        str(p.relative_to(DATA)): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(DATA.rglob("*"))
        if p.is_file() and p.name != "sha256.json"
    }
    (DATA / "sha256.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print(
        json.dumps(
            {"figures": 6, "verification": audit, "output": str(OUT)},
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
