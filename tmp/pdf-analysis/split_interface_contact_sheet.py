from pathlib import Path

from PIL import Image


root = Path(__file__).resolve().parent
source_path = root / "ThietKeGiaoDien-contact-sheet.jpg"
with Image.open(source_path) as source:
    chunk_height = 1200
    for index, top in enumerate(range(0, source.height, chunk_height), start=1):
        bottom = min(source.height, top + chunk_height)
        chunk = source.crop((0, top, source.width, bottom))
        chunk.save(root / f"ThietKeGiaoDien-contact-{index:02d}.jpg", quality=92)
print("done")
