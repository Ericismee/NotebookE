import fs from 'node:fs/promises';
import {pathToFileURL} from 'node:url';
const S='C:/Users/Mera/.codex/plugins/cache/openai-primary-runtime/presentations/26.909.12148/skills/presentations';
const {importRuntimeModule}=await import(pathToFileURL(S+'/container_tools/runtime_helpers.mjs'));
const {FileBlob,PresentationFile}=await importRuntimeModule('@oai/artifact-tool');
const p=await PresentationFile.importPptx(await FileBlob.load('C:/Users/Mera/Downloads/White And Blue Modern Technology As The Core Solution Presentation.pptx'));
await fs.mkdir('tmp/slides-build/new',{recursive:true});
console.log(JSON.stringify(p.slides.items.map(s=>({id:s.id,shapes:s.shapes.items.map(a=>({id:a.id,text:String(a.text),position:a.position,geometry:a.geometry})),images:s.images.items.map(a=>({id:a.id,position:a.position}))})),null,2));
const b=await p.export({format:'png',montage:true,scale:0.2});await fs.writeFile('tmp/slides-build/new/template.png',new Uint8Array(await b.arrayBuffer()));
