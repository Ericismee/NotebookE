"""Audit saved measurements and freeze the approved figures for later slides."""
from __future__ import annotations

import csv
import hashlib
import importlib.util
import json
import math
import shutil
import statistics
import subprocess
from pathlib import Path
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / 'artifacts/design-pattern-benchmark'
DEST = SOURCE / 'slide-assets-2026-10-16'
NODE = Path(r'C:\Users\Mera\.cache\codex-runtimes\codex-primary-runtime\dependencies\node\bin\node.exe')
PACKAGES = Path(r'C:\Users\Mera\.cache\codex-runtimes\codex-primary-runtime\dependencies\node\node_modules')


def read_csv(name):
    with (SOURCE / name).open(encoding='utf-8', newline='') as handle:
        return list(csv.DictReader(handle))


def close(a, b):
    assert math.isclose(float(a), float(b), rel_tol=1e-10, abs_tol=1e-10), (a, b)


def main():
    data = json.loads((SOURCE / 'results.json').read_text(encoding='utf-8'))
    raw = read_csv('middleware_raw.csv')
    checks = []
    for summary in data['middleware_summary']:
        group = [r for r in raw if r['variant'] == summary['variant'] and r['workload'] == summary['workload']]
        assert len(group) == summary['runs'] == 30
        assert all(int(r['operations']) == 10000 for r in group)
        values = sorted(float(r['mean_us_per_operation']) for r in group)
        pos = (len(values)-1)*.95
        low, high = math.floor(pos), math.ceil(pos)
        p95 = values[low]*(1-(pos-low))+values[high]*(pos-low)
        close(statistics.median(values), summary['median_us_per_operation'])
        close(p95, summary['p95_us_per_operation'])
        close(statistics.median(float(r['bytes_read_per_operation']) for r in group), summary['median_bytes_read'])
        assert all(int(r['status']) == summary['status'] for r in group)
        for row in group:
            close(float(row['elapsed_ms'])*1000/int(row['operations']), row['mean_us_per_operation'])
        checks.append({'kind':'middleware','variant':summary['variant'],'workload':summary['workload'],'verified':True})
    raw_cmd = read_csv('command_simulation_raw.csv')
    for summary in data['command_simulation_summary']:
        group = [r for r in raw_cmd if r['variant'] == summary['variant']]
        assert len(group) == summary['runs'] == 30
        assert all(int(r['submitted']) == 100 and int(r['succeeded'])+int(r['failed']) == 100 for r in group)
        close(statistics.median(100*int(r['succeeded'])/int(r['submitted']) for r in group), summary['median_success_rate_percent'])
        for raw_key, summary_key in [('retries','median_retries'),('submit_elapsed_ms','median_submit_elapsed_ms'),('completion_elapsed_ms','median_completion_elapsed_ms')]:
            close(statistics.median(float(r[raw_key]) for r in group), summary[summary_key])
        checks.append({'kind':'command_simulation','variant':summary['variant'],'verified':True})
    spec = importlib.util.spec_from_file_location('benchmark_design_patterns', ROOT / 'scripts/benchmark_design_patterns.py')
    import sys
    benchmark = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = benchmark
    spec.loader.exec_module(benchmark)
    static = benchmark.factory_static_metrics() + benchmark.command_static_metrics()
    assert static == data['static_metrics'], 'Static metrics differ from original source revisions'
    checks.append({'kind':'static_metrics','rows':len(static),'verified':True})
    DEST.mkdir(parents=True, exist_ok=True)
    names = [
        ('factory-tnr13.svg','01-factory.svg','Hình 7.1. Factory — So sánh mức phụ thuộc trước và sau chuyển đổi'),
        ('command-tnr13.svg','02-command-simulation.svg','Hình 7.2. Command — So sánh thời gian gửi và tỷ lệ thành công'),
        ('middleware-time-tnr13.svg','03-chain-of-responsibility-time.svg','Hình 7.3. Chain of Responsibility — Thời gian xử lý request'),
        ('middleware-bytes-tnr13.svg','04-chain-of-responsibility-bytes.svg','Hình 7.4. Chain of Responsibility — Lượng body đã đọc'),
    ]
    for source, target, title in names:
        tree = ET.parse(SOURCE / 'figures' / source)
        ns = {'s':'http://www.w3.org/2000/svg'}
        tree.getroot().find('s:text',ns).text = title
        ET.register_namespace('', 'http://www.w3.org/2000/svg')
        tree.write(DEST / target, encoding='utf-8', xml_declaration=True)
        style = tree.getroot().find('s:style',ns).text
        assert 'Times New Roman' in style and '13pt' in style
    for name in ['results.json','metadata.json','static_metrics.csv','middleware_raw.csv','middleware_summary.csv','command_simulation_raw.csv','command_simulation_summary.csv']:
        shutil.copy2(SOURCE / name, DEST / name)
    shutil.copy2(ROOT / 'scripts/benchmark_design_patterns.py', DEST / 'benchmark_design_patterns.py')
    audit = {'checked_on':'2026-10-16','measurement_date':'2026-09-27','runtime_benchmarks_rerun':False,'checks':checks,'interpretation':{
        'factory':'Verified source revision counts; not an assertion of zero concrete imports in current code. Edge TTS is an exception.',
        'command':'Recomputed values match raw simulation data. Not a fair isolation of Command: admission control and retry differ; transient failure is injected only into queue variant.',
        'middleware':'Recomputed median and p95 match 30 batch means. Measures added body-size enforcement, not a same-functionality comparison of pattern vs no pattern.',
        'timing':'Windows scheduler affects asyncio.sleep(2ms); requested sleep is not guaranteed service time. Original run timing is not a portable performance prediction.'}}
    (DEST / 'verification.json').write_text(json.dumps(audit,ensure_ascii=False,indent=2),encoding='utf-8')
    render_js = "const sharp=require(process.argv[1]); const fs=require('fs'); const path=require('path'); (async()=>{for(const name of fs.readdirSync(process.argv[2]).filter(n=>n.endsWith('.svg'))){await sharp(path.join(process.argv[2],name),{density:72}).resize({width:3300}).png().toFile(path.join(process.argv[2],name.replace(/\\.svg$/,'.png')))}})().catch(e=>{console.error(e);process.exit(1)});"
    result = subprocess.run([str(NODE),'-e',render_js,str(PACKAGES / 'sharp'),str(DEST)],capture_output=True,text=True)
    print('PNG_RENDER',result.returncode,result.stderr[:700])
    manifest = {p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(DEST.iterdir()) if p.is_file() and p.name != 'sha256.json'}
    (DEST / 'sha256.json').write_text(json.dumps(manifest,indent=2),encoding='utf-8')
    print(json.dumps({'verified_groups':len(checks),'snapshot':str(DEST),'files':len(manifest),'png_rendered':result.returncode==0},ensure_ascii=False))


if __name__ == '__main__':
    main()
