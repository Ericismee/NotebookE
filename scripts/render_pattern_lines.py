"""Simple line figures showing measured variation across repetitions."""

import csv
import math
import subprocess
import textwrap
from pathlib import Path
from xml.sax.saxutils import escape

ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / "artifacts/design-pattern-benchmark"
OUT = BASE / "charts-lines-2026-10-17"


def text(x, y, s, attrs=""):
    return f'<text x="{x}" y="{y}" {attrs}>{escape(str(s))}</text>'


def read(path):
    with path.open(encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def draw(name, title, subtitle, groups, labels, unit, note):
    left, top, width, height = 90, 95, 600, 205
    maximum = max(float(v) for g in groups for v in g)
    magnitude = 10 ** math.floor(math.log10(maximum))
    ceiling = math.ceil(maximum * 1.08 / (magnitude / 2)) * magnitude / 2
    title_lines = textwrap.wrap(title, width=77)
    body = "".join(text(18, 24 + k * 20, row, 'font-weight="bold"') for k, row in enumerate(title_lines))
    styles = [("#2463A8", "", "circle"), ("#C73737", 'stroke-dasharray="10 6"', "square")]
    for j, (color, dash, marker) in enumerate(styles):
        x = 90 + j * 300
        body += f'<line x1="{x}" y1="65" x2="{x + 34}" y2="65" stroke="{color}" stroke-width="2.3" {dash}/>'
        body += text(x + 44, 71, labels[j].split(" — ")[0])
    for k in range(6):
        y = top + height - height * k / 5
        body += f'<line x1="{left}" y1="{y}" x2="{left + width}" y2="{y}" stroke="#CED4DB"/>'
        value = ceiling * k / 5
        body += text(left - 10, y + 5, f"{value:.0f}".replace(".", ",") if ceiling >= 100 else f"{value:.1f}".replace(".", ","), 'text-anchor="end"')
    axis_label = ("Thời gian hoàn thành (ms/lô)" if unit == "ms / lô" else
                  "Thời gian kiểm tra (µs/lượt)" if unit == "µs / lượt" else
                  "RAM cực đại quan sát (MiB)")
    body += text(20, top + height / 2, axis_label,
                 f'text-anchor="middle" transform="rotate(-90 20 {top + height / 2})"')
    for rep in [1, 5, 10, 15, 20, 25, 30]:
        x = left + (rep - 1) / 29 * width
        body += text(x, top + height + 28, rep, 'text-anchor="middle"')
    body += text(left + width / 2, top + height + 52, "Lượt đo", 'text-anchor="middle"')
    for values, (color, dash, marker) in zip(groups, styles):
        points = [(left + i / 29 * width, top + height - float(v) / ceiling * height) for i, v in enumerate(values)]
        coords = " ".join(f"{x:.2f},{y:.2f}" for x, y in points)
        body += f'<polyline points="{coords}" fill="none" stroke="{color}" stroke-width="2.3" {dash}/>'
        for x, y in points:
            if marker == "circle":
                body += f'<circle cx="{x}" cy="{y}" r="3" fill="white" stroke="{color}" stroke-width="1.8"/>'
            else:
                body += f'<rect x="{x - 3}" y="{y - 3}" width="6" height="6" fill="white" stroke="{color}" stroke-width="1.8"/>'
    svg = f'<svg xmlns="http://www.w3.org/2000/svg" width="720" height="365"><rect width="720" height="365" fill="white"/><style>text{{font-family:"Times New Roman",Times,serif;font-size:13pt;fill:#162232}}</style>{body}</svg>'
    (OUT / (name + ".svg")).write_text(svg, encoding="utf-8")
    (OUT / (name + "-caption.txt")).write_text(
        f"{title}\n{subtitle}\n{note}\nMỗi điểm ứng với một lượt đo. Đường nối thể hiện dao động giữa các lượt, không phải xu hướng theo thời gian.\n",
        encoding="utf-8")


def main():
    OUT.mkdir(exist_ok=True)
    command = read(BASE / "rerun-2026-10-16/command_live_raw.csv")
    chain = read(BASE / "rerun-2026-10-16/middleware_raw.csv")
    ram = read(BASE / "ram-2026-10-17/trials.csv")
    specifications = [
        ("01-command-time", "Command — Thời gian hoàn thành qua 30 lượt đo", "Không tiêm lỗi · 20 công việc × 1 KB · Tối đa 4 tác vụ · Đo 16/10/2026",
         command, "scenario", "clean", ["direct", "worker"], "completion_ms", 1,
         ["Trực tiếp — xanh, nét liền, điểm tròn", "Command + worker — đỏ, nét đứt, điểm vuông"], "ms / lô",
         "Worker thật + SurrealDB; handler dùng repo_create. Không đo toàn bộ API hoặc AI."),
        ("02-chain-time", "Chain of Responsibility — Thời gian kiểm tra qua 30 lượt đo", "Body hợp lệ 512 B, có Content-Length · Giới hạn 1.024 B · Đo 16/10/2026",
         chain, "workload", "valid_512_cl", ["inline", "middleware"], "mean_us_per_operation", 1,
         ["Inline — xanh, nét liền, điểm tròn", "Middleware — đỏ, nét đứt, điểm vuông"], "µs / lượt",
         "Mỗi điểm là thời gian trung bình của 10.000 lượt kiểm tra; hai phía cùng quy tắc và kết quả."),
    ]
    for kind, metric, name, title, variants in [
        ("command", "python_rss_sampled_peak_bytes", "03-command-python-ram", "Command — RAM Python qua 30 lượt đo", ["direct", "worker"]),
        ("command", "docker_working_set_sampled_peak_bytes", "04-command-db-ram", "Command — RAM SurrealDB qua 30 lượt đo", ["direct", "worker"]),
        ("middleware", "python_rss_sampled_peak_bytes", "05-chain-ram", "Chain of Responsibility — RAM Python qua 30 lượt đo", ["inline", "middleware"]),
    ]:
        labels = (["Trực tiếp — xanh, nét liền, điểm tròn", "Command + worker — đỏ, nét đứt, điểm vuông"] if kind == "command" else
                  ["Inline — xanh, nét liền, điểm tròn", "Middleware — đỏ, nét đứt, điểm vuông"])
        specifications.append((name, title, "30 lượt / phương án · Tiến trình mới mỗi lượt · Đo RAM riêng ngày 17/10/2026",
                               ram, "kind", kind, variants, metric, 1048576, labels, "MiB",
                               "Cực đại quan sát theo mẫu mỗi lượt, có thể bỏ sót đỉnh ngắn. Không phải RAM toàn máy."))
    for name, title, subtitle, rows, key, match, variants, metric, divisor, labels, unit, note in specifications:
        groups = []
        for variant in variants:
            selected = sorted([r for r in rows if r[key] == match and r["variant"] == variant], key=lambda r: int(r["repetition"]))
            assert len(selected) == 30
            groups.append([float(r[metric]) / divisor for r in selected])
        draw(name, title, subtitle, groups, labels, unit, note)
    nodebase = Path(r"C:\Users\Mera\.cache\codex-runtimes\codex-primary-runtime\dependencies\node")
    js = "const s=require(process.argv[1]),f=require('fs'),p=require('path');(async()=>{for(const n of f.readdirSync(process.argv[2]).filter(x=>x.endsWith('.svg')))await s(p.join(process.argv[2],n),{density:72}).resize({width:3600}).png().toFile(p.join(process.argv[2],n.replace(/\\.svg$/,'.png')));})()"
    subprocess.run([str(nodebase / "bin/node.exe"), "-e", js, str(nodebase / "node_modules/sharp"), str(OUT)], check=True)
    print(OUT)


if __name__ == "__main__":
    main()
