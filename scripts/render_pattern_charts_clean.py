"""Redraw the verified essay figures without modifying benchmark data."""

import hashlib
import json
import math
import shutil
import statistics
import subprocess
import textwrap
import zipfile
from pathlib import Path
from xml.sax.saxutils import escape

from render_pattern_memory import validate as validate_memory
from render_pattern_rerun import validate as validate_time

ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / "artifacts/design-pattern-benchmark"
OUT = BASE / "charts-clean-2026-10-17"
COLORS = ["#2463A8", "#C73737", "#7254A3"]


def text(x, y, value, attrs=""):
    return f'<text x="{x}" y="{y}" {attrs}>{escape(str(value))}</text>'


def number(value, digits=3):
    return f"{value:.{digits}f}".replace(".", ",")


def chart(name, title, subtitle, labels, pairs, series, unit, notes, digits=3):
    """Horizontal grouped bars, zero-based scale and a fixed value column."""
    single = len(series) == 1
    top, step, left, width = 115, (45 if single else 76), 272, 350
    bottom = top + (len(labels) - 1) * step + (24 if single else 53)
    height = bottom + 65
    high = max(v for pair in pairs for v in pair)
    magnitude = 10 ** math.floor(math.log10(high)) if high else 1
    maximum = math.ceil(high / magnitude) * magnitude if high else 1
    title_lines = textwrap.wrap(title.replace(" — hình bổ trợ", ""), width=77)
    body = "".join(text(18, 24 + k * 20, row, 'font-weight="bold"') for k, row in enumerate(title_lines))
    for j, label in enumerate(series):
        x = 18 + j * 300
        body += f'<rect x="{x}" y="54" width="14" height="12" fill="{COLORS[j]}"/>'
        body += text(x + 22, 66, label)
    vertical_label = ("Trạng thái mã nguồn" if name == "01-factory" else
                      "Chỉ số RAM" if "ram" in name else "Tình huống thử nghiệm")
    body += text(18, 94, vertical_label)
    body += text(671, 94, unit, 'text-anchor="middle" font-weight="bold"')
    for k in range(5):
        x = left + width * k / 4
        body += f'<line x1="{x}" y1="105" x2="{x}" y2="{bottom}" stroke="#CED4DB"/>'
        body += text(x, bottom + 23, number(maximum * k / 4, 0 if maximum >= 10 else 2), 'text-anchor="middle"')
    for i, (label, pair) in enumerate(zip(labels, pairs)):
        y = top + step * i
        label_lines = [part for row in label.split("\n") for part in textwrap.wrap(row, width=28)]
        for k, line in enumerate(label_lines):
            body += text(18, y + 16 + k * 19, line)
        for j, value in enumerate(pair):
            yy = y + j * 28
            bar_width = width * value / maximum
            if value:
                body += f'<rect x="{left}" y="{yy}" width="{bar_width}" height="19" rx="1" fill="{COLORS[j]}"/>'
            else:
                body += f'<circle cx="{left}" cy="{yy + 9.5}" r="3" fill="{COLORS[j]}"/>'
            body += text(671, yy + 16, number(value, digits), 'text-anchor="middle" font-weight="bold"')
    metric_label = ("Số lớp provider import trực tiếp" if name == "01-factory" else
                    "Dung lượng RAM (MiB)" if "ram" in name else
                    "Thời gian hoàn thành (ms/lô)" if name == "02-command-time" else
                    "Thời gian kiểm tra (µs/lượt)" if name == "03-chain-time" else
                    "Tỷ lệ thành công (%)" if name == "07-command-success" else
                    "Thời gian chuyển giao (ms/lô)" if name == "08-command-submit" else
                    "Lượng body đã đọc (byte/request)")
    body += text(left + width / 2, bottom + 52, metric_label, 'text-anchor="middle"')
    svg = f'<svg xmlns="http://www.w3.org/2000/svg" width="720" height="{height}" viewBox="0 0 720 {height}"><rect width="720" height="{height}" fill="white"/><style>text{{font-family:"Times New Roman",Times,serif;font-size:13pt;fill:#162232}}</style>{body}</svg>'
    (OUT / f"{name}.svg").write_text(svg, encoding="utf-8")
    (OUT / f"{name}-caption.txt").write_text(
        "\n".join([title, subtitle, *notes]) + "\n", encoding="utf-8")


def main():
    OUT.mkdir(exist_ok=True)
    td = BASE / "rerun-2026-10-16"
    md = BASE / "ram-2026-10-17"
    data = json.loads((td / "results.json").read_text(encoding="utf-8"))
    live = json.loads((td / "command_live_results.json").read_text(encoding="utf-8"))
    memory = json.loads((md / "results.json").read_text(encoding="utf-8"))
    audit_time = validate_time(data, live)
    trials, audit_memory = validate_memory(memory)
    chart("01-factory", "Factory — Mức phụ thuộc vào lớp nhà cung cấp AI",
          "Phân tích tĩnh mã nguồn; không phải phép đo tốc độ hoặc RAM.",
          ["Trước chuyển đổi", "Ngay sau chuyển đổi", "Mã nguồn hiện tại"],
          [[19], [0], [len(data["current_factory"]["direct_provider_classes"])]],
          ["Số lớp provider được import trực tiếp"], "Số lớp",
          ["Ba trạng thái mã nguồn được tách riêng; mã hiện tại còn ngoại lệ EdgeTTSProvider.",
           "Nguồn: phân tích AST đã lưu trong bộ đo 16/10/2026."], 0)
    scenarios = ["clean", "fault20_no_retry", "fault20_retry5"]
    labels = ["Không tiêm lỗi", "20% lỗi lần đầu\nKhông thử lại", "20% lỗi lần đầu\nTối đa 5 lần thử"]
    cs = {(r["variant"], r["scenario"]): r for r in live["summary"]}
    command_series = ["Trực tiếp", "Command + worker"]
    for metric, name, title, unit, digits, notes in [
        ("completion_ms", "02-command-time", "Thời gian hoàn thành 20 công việc", "ms / lô", 3,
         ["Cùng tải 20 × 1 KB, tối đa 4 tác vụ; cùng lịch lỗi và chính sách thử lại.",
          "Worker thật + SurrealDB; chưa phải benchmark toàn bộ API hoặc AI."]),
        ("succeeded", "07-command-success", "Tỷ lệ công việc thành công", "%", 0,
         ["Thử lại giúp cả hai phía phục hồi; không quy mức tăng này cho riêng Command.",
          "Cùng dữ liệu, lịch lỗi và số lần thử; 20 công việc trong mỗi lô."]),
        ("submit_ms", "08-command-submit", "Thời gian chuyển giao công việc — hình bổ trợ", "ms / lô", 3,
         ["Trực tiếp: lập lịch task trong RAM. Worker: ghi command vào cơ sở dữ liệu.",
          "Khác bảo đảm lưu trữ; không dùng hình này để kết luận bên nào nhanh hơn tương đương."]),
    ]:
        pairs = [[cs[(v, s)][metric + "_median"] * (5 if metric == "succeeded" else 1)
                  for v in ["direct", "worker"]] for s in scenarios]
        chart(name, "Command — " + title, "Trung vị của 30 lần chạy / phương án / tình huống · Đo ngày 16/10/2026.",
              labels, pairs, command_series, unit, notes, digits)
    workloads = ["valid_512_cl", "large_2048_cl", "large_2048_one_chunk", "large_65536_chunks256"]
    labels = ["512 B hợp lệ\nCó Content-Length", "2.048 B quá giới hạn\nCó Content-Length",
              "2.048 B, không Content-Length\nGửi một khối", "65.536 B, không Content-Length\nChia khối 256 B"]
    ms = {(r["variant"], r["workload"]): r for r in data["middleware"]}
    chain_series = ["Inline", "Middleware"]
    for metric, name, title, unit, digits in [
        ("mean_us_per_operation", "03-chain-time", "Thời gian kiểm tra request", "µs / lượt", 3),
        ("bytes_read", "09-chain-body", "Lượng body đã đọc — hình bổ trợ", "byte / request", 0),
    ]:
        pairs = [[ms[(v, w)][metric + "_median"] for v in ["inline", "middleware"]] for w in workloads]
        chart(name, "Chain of Responsibility — " + title,
              "Trung vị 30 lần × 10.000 lượt / tình huống · Giới hạn 1.024 B · Đo 16/10/2026.",
              labels, pairs, chain_series, unit,
              ["Đối chứng được dựng với cùng quy tắc kiểm tra; không phải phiên bản cũ của NotebookE.",
               "Hai phương án trả cùng kết quả và đọc cùng lượng dữ liệu; không bao gồm mạng hoặc AI."], digits)
    for kind, prefix, name, title, series in [
        ("command", "python_rss", "04-command-python-ram", "Command — RAM tiến trình Python", command_series),
        ("command", "docker_working_set", "05-command-db-ram", "Command — RAM container SurrealDB", command_series),
        ("middleware", "python_rss", "06-chain-ram", "Chain of Responsibility — RAM tiến trình Python", chain_series),
    ]:
        variants = ["direct", "worker"] if kind == "command" else ["inline", "middleware"]
        pairs = [[statistics.median(r[prefix + field] for r in trials
                                    if r["kind"] == kind and r["variant"] == variant) / 1048576
                  for variant in variants]
                 for field in ["_sampled_peak_bytes", "_delta_bytes"]]
        chart(name, title, "Trung vị 30 lượt / phương án; tiến trình mới mỗi lượt · Đo ngày 17/10/2026.",
              ["RAM cực đại quan sát được", "RAM tăng thêm so với nền"], pairs, series, "MiB",
              [("Docker Engine: working set = usage − inactive_file; không phải RAM toàn bộ Docker Desktop."
                if prefix.startswith("docker") else "psutil RSS/Working Set; không phải RAM toàn máy. 1 MiB = 1.048.576 byte."),
               "Đỉnh theo mẫu có thể bỏ sót đỉnh ngắn; tăng thêm bằng 0 không có nghĩa không cấp phát."])
    nodebase = Path(r"C:\Users\Mera\.cache\codex-runtimes\codex-primary-runtime\dependencies\node")
    js = "const s=require(process.argv[1]),f=require('fs'),p=require('path');(async()=>{for(const n of f.readdirSync(process.argv[2]).filter(x=>x.endsWith('.svg')))await s(p.join(process.argv[2],n),{density:72}).resize({width:3600}).png().toFile(p.join(process.argv[2],n.replace(/\\.svg$/,'.png')));})()"
    subprocess.run([str(nodebase / "bin/node.exe"), "-e", js, str(nodebase / "node_modules/sharp"), str(OUT)], check=True)
    (OUT / "verification.json").write_text(json.dumps({"time": audit_time, "memory": audit_memory}, indent=2), encoding="utf-8")
    report = """# Bộ biểu đồ sạch — 17/10/2026

Đây là bản vẽ lại, không phải lượt benchmark mới. Số liệu thời gian lấy từ rerun-2026-10-16; RAM lấy từ ram-2026-10-17. Các trung vị được kiểm tra lại với CSV gốc trước khi vẽ.

## Hình chính
01 Factory: phụ thuộc lớp provider (ba trạng thái có bằng chứng, không gộp hiện tại với ngay sau chuyển đổi).
02 Command: thời gian hoàn thành toàn bộ lô.
03 Chain of Responsibility: thời gian kiểm tra với cùng quy tắc.
04 Command: RAM Python.
05 Command: RAM container cơ sở dữ liệu.
06 Chain of Responsibility: RAM Python.

## Hình bổ trợ
07 Command: tỷ lệ thành công khi giữ cùng chính sách thử lại.
08 Command: thời gian chuyển giao, KHÁC bảo đảm lưu trữ; không dùng để kết luận hiệu năng tương đương.
09 Chain of Responsibility: lượng body đọc, dùng xác nhận hai bên kiểm tra tương đương.

## Quy ước trình bày
Xanh dương: đối chứng trực tiếp. Đỏ: phương án áp dụng pattern. Factory là một đại lượng đếm ở ba trạng thái, dùng cùng màu.
Hình chỉ giữ tiêu đề, chú giải ngắn và tên trục. Thông tin phép đo được lưu riêng trong các tệp -caption.txt để đặt dưới hình trong tiểu luận.
Thanh ngang bắt đầu từ 0; cột số bên phải dùng dấu phẩy thập phân. RAM cực đại và RAM tăng thêm dùng chung thang MiB để không phóng đại chênh lệch; xem số nhãn để đọc các mức tăng nhỏ.
Times New Roman 13 pt tại SVG gốc; PNG rộng 3.600 px. Chèn hình vào Word sẽ thay đổi cỡ chữ hiển thị theo mức thu phóng.

## Phạm vi và cách đọc
Các đối chứng runtime trực tiếp/inline là phương án thực nghiệm, không phải bằng chứng về phiên bản cũ của NotebookE. Không đặt nhãn “trước” cho chúng.
Command: 20 bản ghi × 1.024 byte, tối đa 4 tác vụ; handler dùng repo_create và worker thật, bên gửi và worker chung tiến trình; không có API/AI end-to-end.
Thời gian: 30 lượt mỗi tình huống; RAM: 30 lượt mỗi phương án, đo riêng có profiler. Middleware RAM: 100.000 lượt × 4 workload mỗi lượt.
Môi trường Windows 11, Python 3.12.13, SurrealDB 2.7.0. Chi tiết CPU, phiên bản thư viện, seed, warmup, raw samples và giới hạn lấy mẫu trong README của hai bộ nguồn.
Factory chứng minh giảm phụ thuộc trực tiếp; không suy ra tăng tốc. Worker có overhead trong workload nhỏ này. Chain không thể hiện tiết kiệm RAM đáng kể.
Không cộng RSS Python với working set container thành RAM toàn máy; RAM DB gồm dữ liệu giữ lại và cache.

## Tái hiện hình
Chạy scripts/render_pattern_charts_clean.py; script tính lại số liệu từ CSV, xuất SVG/PNG, verification.json và sha256.json. Không sửa bộ dữ liệu gốc.
"""
    (OUT / "README.md").write_text(report, encoding="utf-8")
    shutil.copy2(__file__, OUT / Path(__file__).name)
    manifest = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(OUT.iterdir()) if p.is_file() and p.name != "sha256.json"}
    (OUT / "sha256.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    with zipfile.ZipFile(BASE / "charts-final-2026-10-17.zip", "w", zipfile.ZIP_DEFLATED) as bundle:
        for directory in [OUT, BASE / "charts-lines-2026-10-17"]:
            for p in sorted(directory.iterdir()):
                if p.is_file():
                    bundle.write(p, str(p.relative_to(BASE)))
    chapter_figures = [
        (OUT, "01-factory", "Hinh-7.1-Factory"),
        (BASE / "charts-lines-2026-10-17", "01-command-time", "Hinh-7.2-Command-thoi-gian"),
        (OUT, "07-command-success", "Hinh-7.3-Command-thanh-cong"),
        (OUT, "04-command-python-ram", "Hinh-7.4-Command-RAM-Python"),
        (OUT, "05-command-db-ram", "Hinh-7.5-Command-RAM-SurrealDB"),
        (BASE / "charts-lines-2026-10-17", "02-chain-time", "Hinh-7.6-Chain-thoi-gian"),
        (OUT, "06-chain-ram", "Hinh-7.7-Chain-RAM"),
    ]
    with zipfile.ZipFile(BASE / "chuong-7-hinh-cap-nhat.zip", "w", zipfile.ZIP_DEFLATED) as bundle:
        for directory, source_name, figure_name in chapter_figures:
            for ext in [".png", ".svg", "-caption.txt"]:
                bundle.write(directory / (source_name + ext), figure_name + ext)
    print(json.dumps({"output": str(OUT), "figures": 9, "verified": True}))


if __name__ == "__main__":
    main()
