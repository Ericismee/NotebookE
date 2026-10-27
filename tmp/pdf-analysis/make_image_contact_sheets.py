import json
from collections import defaultdict
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont


ROOT = Path(__file__).resolve().parent
records = json.loads((ROOT / "image-index.json").read_text(encoding="utf-8"))
by_doc = defaultdict(list)
for record in records:
    if record.get("error") or record.get("area", 0) < 40000:
        continue
    doc = record["file"].split("\\")[0].split("/")[0]
    by_doc[doc].append(record)

font = ImageFont.load_default()
for doc, items in by_doc.items():
    seen = set()
    unique = []
    for item in sorted(items, key=lambda row: row["file"]):
        if item["sha256"] in seen:
            continue
        seen.add(item["sha256"])
        unique.append(item)
    cols = 3
    cell_w, cell_h = 420, 300
    rows = (len(unique) + cols - 1) // cols
    sheet = Image.new("RGB", (cols * cell_w, rows * cell_h), "white")
    draw = ImageDraw.Draw(sheet)
    for index, item in enumerate(unique):
        x = (index % cols) * cell_w
        y = (index // cols) * cell_h
        path = ROOT / item["file"]
        with Image.open(path) as source:
            thumb = source.convert("RGB")
            thumb.thumbnail((cell_w - 20, cell_h - 42), Image.Resampling.LANCZOS)
        px = x + (cell_w - thumb.width) // 2
        py = y + 24 + (cell_h - 38 - thumb.height) // 2
        sheet.paste(thumb, (px, py))
        label = Path(item["file"]).name
        draw.text((x + 8, y + 6), label, fill="black", font=font)
        draw.rectangle((x, y, x + cell_w - 1, y + cell_h - 1), outline="#B0B0B0")
    sheet.save(ROOT / f"{doc}-contact-sheet.jpg", quality=90)
    print(doc, len(unique))
