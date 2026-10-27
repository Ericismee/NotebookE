import hashlib
import json
from pathlib import Path

from PIL import Image


ROOT = Path(__file__).resolve().parent
records = []
for image_path in ROOT.glob("*/images/*"):
    try:
        with Image.open(image_path) as image:
            width, height = image.size
            mode = image.mode
            image_format = image.format
    except Exception as exc:
        records.append({"file": str(image_path.relative_to(ROOT)), "error": str(exc)})
        continue
    data = image_path.read_bytes()
    records.append({
        "file": str(image_path.relative_to(ROOT)),
        "width": width,
        "height": height,
        "area": width * height,
        "bytes": len(data),
        "mode": mode,
        "format": image_format,
        "sha256": hashlib.sha256(data).hexdigest(),
    })

(ROOT / "image-index.json").write_text(
    json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8"
)
print(f"indexed={len(records)}")
