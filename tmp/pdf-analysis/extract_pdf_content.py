import json
import re
from pathlib import Path

from pypdf import PdfReader


ROOT = Path(__file__).resolve().parents[2]
PDF_DIR = ROOT / "pdf"
OUT_DIR = Path(__file__).resolve().parent


def safe_name(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", value).strip("_")


summary = []
for pdf_path in sorted(PDF_DIR.glob("*.pdf")):
    reader = PdfReader(pdf_path)
    doc_dir = OUT_DIR / safe_name(pdf_path.stem)
    image_dir = doc_dir / "images"
    image_dir.mkdir(parents=True, exist_ok=True)
    pages = []
    image_records = []
    for page_number, page in enumerate(reader.pages, start=1):
        try:
            text = page.extract_text(extraction_mode="layout") or ""
        except TypeError:
            text = page.extract_text() or ""
        pages.append(f"\n===== PAGE {page_number} =====\n{text.rstrip()}\n")
        try:
            images = list(page.images)
        except Exception:
            images = []
        for image_number, image in enumerate(images, start=1):
            raw_name = getattr(image, "name", f"image-{image_number}.bin")
            suffix = Path(raw_name).suffix or ".bin"
            output_name = f"p{page_number:03d}-i{image_number:02d}{suffix}"
            output_path = image_dir / output_name
            try:
                output_path.write_bytes(image.data)
                image_records.append({
                    "page": page_number,
                    "file": str(output_path.relative_to(OUT_DIR)),
                    "bytes": output_path.stat().st_size,
                    "source_name": raw_name,
                })
            except Exception as exc:
                image_records.append({
                    "page": page_number,
                    "source_name": raw_name,
                    "error": str(exc),
                })
    text_path = doc_dir / "text.txt"
    text_path.write_text("".join(pages), encoding="utf-8")
    meta = getattr(reader, "metadata", None)
    record = {
        "file": pdf_path.name,
        "pages": len(reader.pages),
        "title": getattr(meta, "title", None) if meta else None,
        "author": getattr(meta, "author", None) if meta else None,
        "text_file": str(text_path.relative_to(OUT_DIR)),
        "text_chars": sum(len(p) for p in pages),
        "images": image_records,
    }
    (doc_dir / "summary.json").write_text(
        json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    summary.append(record)

(OUT_DIR / "summary.json").write_text(
    json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
)
print(json.dumps(summary, ensure_ascii=False, indent=2))
