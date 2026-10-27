import fs from "node:fs/promises";
import path from "node:path";
import { FileBlob, PresentationFile } from "@oai/artifact-tool";

const workspaceDir = "D:/DLU/HocKy7/Do An Chuyen Nghanh/open-notebook";
const sourcePath = path.join(workspaceDir, "pdf", "Xây dựng NotebookLM đơn giản.pptx");
const outDir = path.join(workspaceDir, "tmp", "slides-build", "template-inspect", "output");
await fs.mkdir(outDir, { recursive: true });

const presentation = await PresentationFile.importPptx(await FileBlob.load(sourcePath));
const snapshot = await presentation.inspect({
  kind: "deck,slide,textbox,shape,image,table,chart,notes,layout",
  include: "id,slide,name,title,text,textPreview,textChars,textLines,bbox,bboxUnit,alt,isPlaceholder,placeholders",
  maxChars: 100000,
});
await fs.writeFile(path.join(outDir, "inspect.ndjson"), snapshot.ndjson, "utf8");

const deckInfo = {
  slideCount: presentation.slides.items.length,
  masterCount: presentation.masters.items.length,
  layoutCount: presentation.layouts.items.length,
  masters: presentation.masters.items.map((master) => ({ id: master.id, name: master.name })),
  layouts: presentation.layouts.items.map((layout) => ({
    id: layout.id,
    name: layout.name,
    placeholders: layout.placeholders.summary(),
  })),
};
await fs.writeFile(path.join(outDir, "deck-info.json"), JSON.stringify(deckInfo, null, 2), "utf8");

const montage = await presentation.export({ format: "png", montage: true, scale: 0.5 });
await fs.writeFile(path.join(outDir, "montage.png"), new Uint8Array(await montage.arrayBuffer()));

for (let index = 0; index < presentation.slides.items.length; index += 1) {
  const slide = presentation.slides.getItem(index);
  const preview = await slide.export({ format: "png", scale: 1 });
  await fs.writeFile(
    path.join(outDir, `slide-${String(index + 1).padStart(2, "0")}.png`),
    new Uint8Array(await preview.arrayBuffer()),
  );
  const layout = await slide.export({ format: "layout" });
  await fs.writeFile(
    path.join(outDir, `slide-${String(index + 1).padStart(2, "0")}.layout.json`),
    await layout.text(),
    "utf8",
  );
}

console.log(JSON.stringify(deckInfo, null, 2));
