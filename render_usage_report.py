from __future__ import annotations

import argparse
import html
import json
import math
import re
from datetime import datetime, timezone
from pathlib import Path
from string import Template
from typing import Any
from urllib.parse import urlsplit


LEDGER_COLUMNS = (
    "id", "time", "phase", "source", "kind", "session", "provider", "model",
    "input_tokens", "cache_read_tokens", "output_tokens", "total_tokens", "cost_usd", "details",
)


def text(value: Any, missing: str = "Unknown") -> str:
    if value is None or value == "":
        return missing
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False, allow_nan=False)
    return str(value)


def object_fields(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def escape(value: Any, missing: str = "Unknown") -> str:
    return html.escape(text(value, missing), quote=True)


def number(value: Any) -> int | float | None:
    if type(value) not in (int, float):
        return None
    try:
        return value if math.isfinite(value) and value >= 0 else None
    except OverflowError:
        return None


def metric(value: Any, money: bool = False) -> str:
    numeric = number(value)
    if numeric is None:
        return "Unknown"
    return f"${numeric:,.4f}" if money else f"{numeric:,.3f}".rstrip("0").rstrip(".")


def duration(value: Any) -> str:
    seconds = number(value)
    if seconds is None:
        return "Unknown"
    minutes, remainder = divmod(round(seconds), 60)
    return f"{minutes}:{remainder:02d}"


def safe_url(value: Any) -> str | None:
    if not isinstance(value, str) or "\\" in value or any(c.isspace() or ord(c) < 32 for c in value):
        return None
    try:
        parsed = urlsplit(value)
        if parsed.scheme.lower() in ("http", "https") and parsed.hostname and not parsed.username and not parsed.password:
            return value
    except ValueError:
        pass
    return None


def link(label: Any, url: Any) -> str:
    href = safe_url(url)
    return f'<a href="{escape(href)}" target="_blank" rel="noopener noreferrer">{escape(label)}</a>' if href else escape(label)


def table(headers: list[str], rows: list[list[str]], identity: str = "") -> str:
    heading = "".join(f'<th scope="col">{escape(h)}</th>' for h in headers)
    body = "".join("<tr>" + "".join(f"<td>{cell}</td>" for cell in row) + "</tr>" for row in rows)
    body = body or f'<tr><td colspan="{len(headers)}">Unknown — no rows supplied.</td></tr>'
    return f'<div class="table-wrap" tabindex="0" role="region" aria-label="{escape(identity or headers[0])}"><table><thead><tr>{heading}</tr></thead><tbody id="{escape(identity)}">{body}</tbody></table></div>'


def bullets(values: list[Any]) -> str:
    return "<ul>" + "".join(f"<li>{escape(v)}</li>" for v in values or ["Unknown — no notes supplied."]) + "</ul>"


def chronological(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    def key(row: dict[str, Any]) -> float:
        try:
            stamp = datetime.fromisoformat(str(row.get("time")).replace("Z", "+00:00"))
            return stamp.replace(tzinfo=stamp.tzinfo or timezone.utc).timestamp()
        except (ValueError, OverflowError, OSError):
            return math.inf
    return sorted(rows, key=key)


def ledger_row(row: dict[str, Any]) -> list[str]:
    cells = [metric(row.get(k), k == "cost_usd") if k.endswith("tokens") or k == "cost_usd" else escape(row.get(k), "—" if k == "model" else "Unknown") for k in LEDGER_COLUMNS]
    cells.append(link("Evidence", row.get("evidence_url")) if safe_url(row.get("evidence_url")) else "Unknown")
    return cells


CSS = """
:root{color-scheme:light;--ink:#172b45;--muted:#455971;--blue:#1453a0;--line:#cbd3dd;--paper:#f5f4ef}
*{box-sizing:border-box}body{margin:0;background:var(--paper);color:var(--ink);font:16px/1.6 system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif}
main{max-width:1280px;margin:auto;padding:36px 32px 72px}h1{font-size:2.1rem;line-height:1.2;margin:12px 0 20px}h2{font-size:1.4rem;margin:0 0 12px}h3{font-size:1.05rem;margin:24px 0 8px}
p{max-width:74ch;margin:8px 0 16px}a{color:var(--blue);text-underline-offset:3px}a:hover{color:#0a3470}header{padding-bottom:24px}section{margin-top:40px;scroll-margin-top:90px}
.meta,.muted{color:var(--muted)}.summary{display:flex;flex-wrap:wrap;gap:12px 28px;padding:16px 0;border-block:1px solid var(--line)}.summary span{font-size:.94rem}.summary strong{display:block;font-size:1.2rem}
nav{position:sticky;top:0;z-index:2;background:#fff;border-block:1px solid var(--line);display:flex;gap:24px;overflow-x:auto;padding:12px 0}nav a{white-space:nowrap;font-weight:600}nav a:first-child{margin-left:12px}
.table-wrap{overflow-x:auto;background:#fff;border:1px solid var(--line);margin:12px 0}table{border-collapse:collapse;width:100%;font-size:.86rem;font-variant-numeric:tabular-nums}th,td{padding:12px 14px;border-bottom:1px solid var(--line);text-align:left;vertical-align:top}th{background:#edf1f6;white-space:nowrap;font-weight:650}td{min-width:90px;max-width:420px;overflow-wrap:anywhere}tbody tr:last-child td{border-bottom:0}tbody tr:nth-child(even){background:#fafbfc}td:first-child{font-weight:600}#ledger-body td{min-width:110px}#ledger-body td:last-child{min-width:100px}
.notice{padding:18px 22px;background:#eef2f8;border:1px solid var(--line)}.notice p:last-child{margin-bottom:0}li{max-width:90ch;margin:6px 0}label{font-size:.9rem;font-weight:600;display:flex;flex-direction:column;gap:6px}input,select,button{font:inherit;color:var(--ink);min-height:44px;padding:8px 12px;border:1px solid #8495aa;border-radius:3px;background:#fff}input{min-width:0;width:100%}input:invalid{border-color:#a4262c}button{cursor:pointer;font-weight:600}button:hover{background:#e7edf5}button:active{background:#d9e4f1}button:disabled{color:#536172;background:#eee;cursor:default}button.primary{background:var(--blue);color:#fff}button.primary:hover{background:#0a3470}:focus-visible{outline:3px solid #1453a0;outline-offset:3px}
.controls,.rates{display:grid;grid-template-columns:repeat(auto-fit,minmax(190px,1fr));gap:16px;margin:20px 0}.actions{display:flex;flex-wrap:wrap;gap:12px;align-items:center;margin:16px 0}.status{font-weight:600}footer{margin-top:40px;padding-top:20px;border-top:1px solid var(--line)}[hidden]{display:none!important}
.callouts{display:grid;grid-template-columns:repeat(auto-fit,minmax(280px,1fr));gap:14px;margin:20px 0}.callout{padding:16px 20px;border:1px solid var(--line);border-left:6px solid var(--blue);background:#fff}.callout h2{font-size:1.05rem;margin:0 0 6px}.callout p{margin:0;max-width:none}.callout.good{border-left-color:#1d6b3a;background:#eef7f0}.callout.warn{border-left-color:#9a5b00;background:#fff6e8}
.player{padding:16px 20px;border:1px solid var(--line);background:#fff;margin:20px 0}.player audio{width:100%;margin-top:8px}.badge{display:inline-block;padding:2px 8px;border-radius:3px;font-size:.78rem;font-weight:650;background:#edf1f6;white-space:nowrap}.badge.actual{background:#dff1e4;color:#14502b}.badge.estimate{background:#fff0d6;color:#6e4200}.badge.unknown{background:#eee;color:#444}.bar{display:block;height:8px;background:var(--blue);min-width:1px}
@media(max-width:600px){main{padding:24px 16px 48px}h1{font-size:1.65rem}nav{gap:18px}section{margin-top:32px}.controls,.rates{grid-template-columns:1fr}.notice{padding:16px}}
@media(prefers-reduced-motion:reduce){*{scroll-behavior:auto!important;animation:none!important;transition:none!important}}
@media print{body{background:#fff;color:#000}main{max-width:none;padding:0}nav,.actions,.controls,.rates{display:none!important}section{break-inside:auto;scroll-margin:0}.table-wrap{overflow:visible;border:0}table{font-size:8pt}th,td{padding:4px;min-width:0!important}thead{display:table-header-group}tr{break-inside:avoid}a{color:inherit}#events table{table-layout:fixed}#events td{word-break:break-word}h2{break-after:avoid}}
"""

JS = r"""
function numeric(v){return typeof v==='number'&&Number.isFinite(v)&&v>=0?v:null;}
function llmCosts(llm,rates){
  const v=['input_tokens','cache_write_tokens','cache_read_tokens','output_tokens'].map(k=>numeric(llm?.[k]));
  if(v.includes(null)||rates.length!==3||rates.some(r=>numeric(r)===null))return null;
  const parts=[(v[0]+v[1])*rates[0]/1e6,v[2]*rates[1]/1e6,v[3]*rates[2]/1e6];
  const total=parts.reduce((a,b)=>a+b,0);
  return [...parts,total].every(x=>numeric(x)!==null)?[...parts,total]:null;
}
function energy(seconds,low,high,rate){
  if([seconds,low,high,rate].some(v=>numeric(v)===null)||low>high)return null;
  const values=[seconds*low/3600000*rate,seconds*high/3600000*rate];
  return values.every(v=>numeric(v)!==null)?values:null;
}
function csvCell(value){
  let s=value==null?'':typeof value==='object'?JSON.stringify(value):String(value);
  if(typeof value!=='number'&&/^[\s\uFEFF]*[=+@-]/u.test(s))s="'"+s;
  return '"'+s.replaceAll('"','""')+'"';
}
function sortLedger(rows){
  const stamp=v=>{const s=String(v??'');const t=Date.parse(/^\d{4}-\d\d-\d\dT\d\d:\d\d(?::\d\d(?:\.\d+)?)?$/.test(s)?s+'Z':s);return Number.isFinite(t)?t:Infinity;};
  return rows.map((row,index)=>({row,index})).sort((a,b)=>(stamp(a.row.time)-stamp(b.row.time))||a.index-b.index).map(x=>x.row);
}
const data=JSON.parse(document.getElementById('report-data').textContent);
const el=id=>document.getElementById(id);
const show=v=>v==null||v===''?'Unknown':typeof v==='object'?JSON.stringify(v):String(v);
const money=v=>numeric(v)===null?'Unknown':'$'+v.toFixed(4);
const finiteInput=id=>{const input=el(id);return input.value.trim()!==''&&input.validity.valid?numeric(input.valueAsNumber):null;};
const safeURL=v=>{try{if(typeof v!=='string'||/[\s\x00-\x1f\\]/u.test(v))return null;const u=new URL(v);return ['http:','https:'].includes(u.protocol)&&u.hostname&&!u.username&&!u.password?v:null;}catch{return null;}};
function replaceRows(body,rows){
  body.replaceChildren();
  for(const cells of rows){const tr=document.createElement('tr');for(const value of cells){const td=document.createElement('td');td.textContent=show(value);tr.append(td);}body.append(tr);}
}
const assumptions=data.costs?.assumptions??{};
function calculate(){
  const rates=['uncached','cached','output'].map(finiteInput);
  const [low,high,rate]=['watts-low','watts-high','electricity'].map(finiteInput);
  const rows=(data.phases??[]).map(p=>{
    if(p.llm?.applicable===false)return [show(p.label??p.id),'n/a','n/a','n/a','n/a',p.tts?.applicable===false?'n/a':(energy(numeric(p.tts?.wall_seconds),low,high,rate)?.map(money).join(' – ')??'Unknown')];
    const c=llmCosts(p.llm,rates), e=energy(numeric(p.tts?.wall_seconds),low,high,rate);
    const part=i=>c?money(c[i])+' · '+(c[3]>0?(c[i]/c[3]*100).toFixed(1)+'%':'— share'): 'Unknown';
    return [show(p.label??p.id),part(0),part(1),part(2),c?money(c[3]):'Unknown',e?e.map(money).join(' – '):'Unknown'];
  });
  replaceRows(el('scenario-body'),rows.length?rows:[['Unknown — no phases supplied.']]);
  const costs=(data.phases??[]).filter(p=>p.llm?.applicable!==false).map(p=>llmCosts(p.llm,rates));
  const total=costs.length&&costs.every(Boolean)?costs.reduce((sum,c)=>sum+c[3],0):null;
  const gpu=energy(numeric(assumptions.synthesis_seconds),low,high,rate);
  const requests=numeric(assumptions.search_requests), price=finiteInput('search-price');
  const search=requests!==null&&Number.isInteger(requests)&&price!==null?numeric(requests*price):null;
  el('scenario-status').textContent='What-if LLM subtotal: '+money(numeric(total))+'. Assumed GPU energy: '+(gpu?gpu.map(money).join(' – '):'Unknown')+'. What-if search: '+money(search)+'. Not actual charges.';
}
el('calculator').hidden=false;
el('calculator').addEventListener('input',calculate);
calculate();
const ledger=sortLedger(data.ledger??[]);
const columns=JSON.parse(el('ledger-columns').textContent);
let page=0, filtered=ledger;
for(const key of ['phase','source']){
  for(const value of [...new Set(ledger.map(r=>show(r[key])))].sort()){
    const option=document.createElement('option');option.value=value;option.textContent=value;el(key+'-filter').append(option);
  }
}
function renderLedger(){
  const query=el('query').value.toLocaleLowerCase(), phase=el('phase-filter').value, source=el('source-filter').value;
  filtered=ledger.filter(r=>(!phase||show(r.phase)===phase)&&(!source||show(r.source)===source)&&JSON.stringify(r).toLocaleLowerCase().includes(query));
  const pages=Math.max(1,Math.ceil(filtered.length/50));page=Math.min(page,pages-1);
  replaceRows(el('ledger-body'),filtered.slice(page*50,page*50+50).map(r=>columns.map(k=>k==='model'&&(r[k]==null||r[k]==='')?'—':k==='cost_usd'?money(r[k]):k.endsWith('tokens')?show(numeric(r[k])):show(r[k]))));
  const trs=el('ledger-body').querySelectorAll('tr');
  filtered.slice(page*50,page*50+50).forEach((r,i)=>{
    const td=document.createElement('td'), url=safeURL(r.evidence_url);
    if(url){const a=document.createElement('a');a.href=url;a.textContent='Evidence';a.target='_blank';a.rel='noopener noreferrer';td.append(a);}else td.textContent='Unknown';
    trs[i].append(td);
  });
  if(!filtered.length)replaceRows(el('ledger-body'),[['No matching events. Clear the filters to see the ledger.']]);
  el('ledger-status').textContent=`${filtered.length} matched / ${ledger.length} total · Page ${page+1} of ${pages} · Showing ${filtered.length?page*50+1:0}–${Math.min((page+1)*50,filtered.length)}`;
  el('previous').disabled=page===0;el('next').disabled=page+1>=pages;
}
for(const id of ['query','phase-filter','source-filter'])el(id).addEventListener('input',()=>{page=0;renderLedger();});
el('reset').addEventListener('click',()=>{for(const id of ['query','phase-filter','source-filter'])el(id).value='';page=0;renderLedger();el('query').focus();});
el('previous').addEventListener('click',()=>{page--;renderLedger();});
el('next').addEventListener('click',()=>{page++;renderLedger();});
function download(content,type,name){const url=URL.createObjectURL(new Blob([content],{type}));const a=document.createElement('a');a.href=url;a.download=name;a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);}
el('download-json').addEventListener('click',()=>download(JSON.stringify(data,null,2),'application/json','report-data.json'));
el('download-csv').addEventListener('click',()=>{const keys=[...columns,'evidence_url'];download('\uFEFF'+[keys.map(csvCell).join(','),...filtered.map(r=>keys.map(k=>csvCell(r[k])).join(','))].join('\r\n'),'text/csv;charset=utf-8','usage-ledger-filtered.csv');});
el('ledger-controls').hidden=false;renderLedger();
"""

PAGE = Template("""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>$title</title><style>$css</style></head>
<body><main><header><div class="meta">Usage &amp; economics workbook · Generated $generated</div><h1>$title</h1><p>$description</p><p class="meta">Window: $start → $end · Cutoff: $cutoff</p>
<div class="summary">$summary</div>$callouts$audio<p class="muted">LLM tokens and TTS text tokens are separate measures, never a combined token total. Audio duration is not synthesis wall time.</p></header>
<nav aria-label="Report sections"><a href="#phases">Phase ledger</a><a href="#coverage">Coverage</a><a href="#models">Models</a><a href="#economics">Economics</a><a href="#events">Events</a><a href="#sources">Sources</a></nav>
<section id="phases"><h2>Phase ledger</h2><p>Reported usage by phase. Uncached input, cache reads, cache writes and output remain separate; missing values are unknown, not zero.</p>$phases$separate</section>
<section id="coverage"><h2>Coverage &amp; reconciliation</h2><div class="notice"><strong>Status: $coverage_status</strong><p>$coverage_description</p><p>$coverage_counts</p></div>$sessions<h3>Reconciliation notes</h3>$coverage_notes</section>
<section id="models"><h2>Models &amp; providers</h2><p>Routes are not proof of a native model. Provider and billing labels below come only from supplied evidence.</p>$models</section>
<section id="economics"><h2>Economics</h2><p>$explanation</p>$costviews$actuals$apieq<div class="notice"><strong>Reported cost is not a settled charge.</strong><p>OpenCode reported zero cost, when present in the event ledger, is client metadata — not evidence of a free request or a real provider charge. Actual charges remain unknown unless supplied.</p></div>
<h3>What-if inputs · USD</h3><p>Blank pricing means unknown: no estimated LLM cost by default. All three token rates must be entered independently. Uncached input includes cache writes at the same assumed rate; no separate cache-write rate is supplied. TTS text tokens never infer token billing.</p>
<div id="calculator" hidden><div class="rates">$inputs</div><p>Verified search request count: $search_requests. A price alone cannot establish search cost.</p></div>
<p id="scenario-status" class="status" role="status" aria-live="polite">What-if estimates unavailable until valid inputs are supplied and JavaScript is enabled.</p>
<p>Assumed GPU energy = synthesis seconds × watts ÷ 3,600,000 × USD/kWh. Synthesis seconds: $synthesis_seconds. The 150–350 W range is an assumption, not measured power; use the supplied or revised inputs. Phase energy uses each phase’s TTS wall seconds.</p>
$scenario<p class="muted">LLM component shares are fractions of each phase’s what-if LLM subtotal, not actual charges. Excludes fixed subscriptions, CPU/OCR/build/QA energy, taxes and depreciation. No all-in actual cost is inferred.</p><noscript><p>Enable JavaScript to adjust what-if inputs. Reported usage and actual-charge fields remain visible.</p></noscript>$cost_sources</section>
<section id="events"><h2>Event ledger</h2><p>Chronological, stable ordering. Blank models display —. Token fields here are source-reported; consult kind and details before interpreting them.</p>
<div id="ledger-controls" hidden><div class="controls"><label for="query">Search events<input id="query" type="search" placeholder="Search any event field"></label><label for="phase-filter">Phase<select id="phase-filter"><option value="">All phases</option></select></label><label for="source-filter">Source<select id="source-filter"><option value="">All sources</option></select></label></div><div class="actions"><button id="reset" type="button">Reset filters</button><button id="download-json" type="button">Download full JSON</button><button id="download-csv" type="button">Export filtered CSV</button></div></div>
<p id="ledger-status" class="status" role="status" aria-live="polite">$ledger_status</p>$ledger<noscript><p>JavaScript is disabled. The first 50 chronological events are shown; the complete ledger is embedded in this file.</p></noscript>
<div class="actions"><button id="previous" type="button" disabled>Previous page</button><button id="next" type="button" disabled>Next page</button></div></section>
<section id="sources"><h2>Notes &amp; sources</h2>$notes$sources</section><footer class="muted">Local report · Embedded data · No external assets or network requests</footer></main>
<script id="report-data" type="application/json">$data</script><script id="ledger-columns" type="application/json">$columns</script><script>$js</script></body></html>
""")


def na_or(obj: dict[str, Any], keys: tuple[str, ...]) -> list[str]:
    return ["n/a"] * len(keys) if obj.get("applicable") is False else [metric(obj.get(k)) for k in keys]


def safe_media_path(value: Any) -> str | None:
    if isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._/-]*", value) and ".." not in value.split("/"):
        return value
    return None


def callout_block(items: Any) -> str:
    rows = [c for c in items if isinstance(c, dict)] if isinstance(items, list) else []
    if not rows:
        return ""

    def tone(c: dict[str, Any]) -> str:
        return c["tone"] if c.get("tone") in ("good", "warn") else "info"

    return '<div class="callouts">' + "".join(f'<div class="callout {tone(c)}"><h2>{escape(c.get("title"))}</h2><p>{escape(c.get("body"))}</p></div>' for c in rows) + "</div>"


def audio_block(audio: Any) -> str:
    src = safe_media_path(object_fields(audio).get("src"))
    if not src:
        return ""
    return (f'<div class="player"><strong>{escape(audio.get("label"))}</strong> · {duration(audio.get("duration_seconds"))}'
            f'<audio controls preload="none" src="{escape(src)}"></audio><p class="muted">{escape(audio.get("note"), "")} '
            f'<a href="{escape(src)}" download>Download the MP3</a></p></div>')


def badge(kind: Any) -> str:
    label = {"actual": "Actual / observed", "estimate": "Estimate · not charged"}.get(kind, "Unknown")
    return f'<span class="badge {kind if kind in ("actual", "estimate") else "unknown"}">{label}</span>'


def cost_view_table(rows: Any) -> str:
    rows = [r for r in rows if isinstance(r, dict)] if isinstance(rows, list) else []
    if not rows:
        return ""
    return "<h3>Cost views · what is real and what is hypothetical</h3>" + table(
        ["Cost line", "USD", "Kind", "Basis"],
        [[escape(r.get("label")), metric(r.get("usd"), True) if r.get("usd_high") is None else f'{metric(r.get("usd"), True)} – {metric(r.get("usd_high"), True)}', badge(r.get("kind")), escape(r.get("basis"))] for r in rows],
        "costview-body")


def api_equivalent_table(phases: list[dict[str, Any]]) -> str:
    rows = []
    totals = [number(object_fields(p.get("api_equivalent")).get("total_usd")) for p in phases]
    grand = sum(t for t in totals if t is not None)
    if not any(object_fields(p.get("api_equivalent")) for p in phases):
        return ""
    for phase, total in zip(phases, totals):
        eq = object_fields(phase.get("api_equivalent"))
        label = escape(phase.get("label", phase.get("id")))
        if object_fields(phase.get("llm")).get("applicable") is False:
            rows.append([label, "n/a", "n/a", "n/a", "n/a", "n/a"])
            continue
        share = f"{total / grand * 100:.1f}%" if total is not None and grand > 0 else "Unknown"
        bar = f'<span class="bar" style="width:{total / grand * 100:.1f}%"></span>' if total is not None and grand > 0 else ""
        rows.append([label, *(metric(eq.get(k), True) for k in ("uncached_input_usd", "cache_read_usd", "output_usd", "total_usd")), f"{share}{bar}"])
    rows.append(["All phases", "", "", "", metric(grand, True), "100%"])
    return "<h3>API-equivalent estimate by phase · counterfactual, not a bill</h3>" + table(
        ["Phase", "Uncached input", "Cache read", "Output", "Total", "Share"], rows, "apieq-body")


def actual_table(actual: dict[str, Any], basis: dict[str, Any]) -> str:
    keys = (("LLM", "llm_usd"), ("TTS provider", "tts_provider_usd"), ("Search", "search_usd"), ("Energy", "energy_usd"))
    if not basis:
        return table(["Actual-charge category", "USD"], [[label, metric(actual.get(key), True)] for label, key in keys], "actual-body")
    return table(["Actual-charge category", "USD", "Basis"], [[label, metric(actual.get(key), True), escape(basis.get(key))] for label, key in keys], "actual-body")


def separate_table(rows: Any) -> str:
    rows = [r for r in rows if isinstance(r, dict)] if isinstance(rows, list) else []
    if not rows:
        return ""
    return "<h3>Kept separate · excluded from every total above</h3>" + table(
        ["Work", "Description", "LLM uncached input", "LLM cache read", "LLM output", "LLM total", "LLM calls"],
        [[escape(r.get("label")), escape(r.get("description")), *(metric(object_fields(r.get("llm")).get(k)) for k in ("input_tokens", "cache_read_tokens", "output_tokens", "total_tokens", "calls"))] for r in rows],
        "separate-body")


def source_list(rows: list[dict[str, Any]]) -> str:
    if not rows:
        return "<p>Unknown — no sources supplied.</p>"
    return "<ul>" + "".join(f'<li>{link(s.get("label"), s.get("url"))} · {escape(s.get("url"))}</li>' for s in rows) + "</ul>"


def render_report(data: Any) -> str:
    if not isinstance(data, dict):
        raise ValueError("Report data must be a JSON object")
    for key in ("scope", "summary", "costs", "coverage"):
        if key in data and not isinstance(data[key], dict):
            raise ValueError(f"{key} must be an object when supplied")
    for key in ("phases", "models", "ledger", "sources"):
        if key in data and (not isinstance(data[key], list) or any(not isinstance(row, dict) for row in data[key])):
            raise ValueError(f"{key} must be a list of objects when supplied")
    payload = json.dumps(data, ensure_ascii=True, allow_nan=False).replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")
    scope, summary, coverage, costs = (data.get(k, {}) for k in ("scope", "summary", "coverage", "costs"))
    phases, ledger = data.get("phases", []), chronological(data.get("ledger", []))
    assumptions, actual = (object_fields(costs.get(key)) for key in ("assumptions", "actual"))
    phase_rows = []
    for phase in phases:
        llm, tts = (object_fields(phase.get(key)) for key in ("llm", "tts"))
        phase_rows.append([escape(phase.get("label", phase.get("id"))), escape(phase.get("description")),
            *na_or(llm, ("input_tokens", "cache_read_tokens", "cache_write_tokens", "output_tokens", "total_tokens", "calls")),
            *na_or(tts, ("input_text_tokens", "calls", "wall_seconds", "audio_seconds")), metric(phase.get("event_count"))])
    for owner, key in ((data, "notes"), (coverage, "notes"), (costs, "sources"), (coverage, "session_coverage")):
        value = owner.get(key, [])
        if not isinstance(value, list) or key in ("sources", "session_coverage") and any(not isinstance(row, dict) for row in value):
            raise ValueError(f"{key} must be a list with the expected row type")
    warnings = list(coverage.get("notes", []))
    if number(coverage.get("matched_events")) is not None and coverage["matched_events"] != len(ledger):
        warnings.append(f"Mismatch: coverage reports {coverage['matched_events']} matched events; embedded ledger has {len(ledger)} rows.")
    for field, component in (("llm_total_tokens", "total_tokens"), ("llm_calls", "calls")):
        values = [number(object_fields(p.get("llm")).get(component)) for p in phases]
        if phases and all(v is not None for v in values) and number(summary.get(field)) is not None and sum(v for v in values if v is not None) != summary[field]:
            warnings.append(f"Mismatch: phase {component} do not reconcile to summary {field}.")
    warnings.append("Missing values mean unknown. Event matching alone does not prove complete session coverage or settled billing.")
    input_specs = [("uncached", "Uncached input USD / million", "llm_uncached_per_million"), ("cached", "Cache read USD / million", "llm_cache_read_per_million"), ("output", "Output USD / million", "llm_output_per_million"), ("search-price", "Search USD / request", "search_per_request"), ("watts-low", "Assumed GPU watts · low", "gpu_watts_low"), ("watts-high", "Assumed GPU watts · high", "gpu_watts_high"), ("electricity", "Electricity USD / kWh", "electricity_usd_per_kwh")]
    inputs = "".join(f'<label for="{identity}">{label}<input id="{identity}" type="number" min="0" step="any" placeholder="Unknown" value="{escape(number(assumptions.get(key)), "")}"></label>' for identity, label, key in input_specs)
    extra_counts = [c for c in coverage.get("counts", []) if isinstance(c, dict)] if isinstance(coverage.get("counts", []), list) else []
    summary_items = [("LLM tokens", metric(summary.get("llm_total_tokens"))), ("LLM calls", metric(summary.get("llm_calls"))), ("TTS text tokens", metric(summary.get("tts_text_tokens"))), ("TTS calls", metric(summary.get("tts_calls"))), ("Audio duration", duration(summary.get("audio_seconds"))), ("Synthesis wall seconds", metric(summary.get("synth_wall_seconds"))), ("Actual total USD", metric(summary.get("actual_total_usd"), True))]
    summary_items += [(escape(h.get("label")), escape(h.get("value"))) for h in summary.get("highlights", []) if isinstance(h, dict)] if isinstance(summary.get("highlights", []), list) else []
    return PAGE.substitute(
        title=escape(data.get("title")), generated=escape(data.get("generated_at")), description=escape(scope.get("description")),
        start=escape(scope.get("start")), end=escape(scope.get("end")), cutoff=escape(scope.get("cutoff")), css=CSS, js=JS,
        summary="".join(f"<span>{label}<strong>{value}</strong></span>" for label, value in summary_items),
        phases=table(["Phase", "Description", "LLM uncached input", "LLM cache read", "LLM cache write", "LLM output", "LLM total", "LLM calls", "TTS text tokens", "TTS calls", "TTS wall seconds", "Audio seconds", "Events"], phase_rows, "phase-body"),
        coverage_status=escape(coverage.get("status")), coverage_description=escape(coverage.get("description")),
        coverage_counts=" · ".join(f"{label}: {metric(coverage.get(key))}" for label, key in (("Queried events", "queried_events"), ("Matched events", "matched_events"), ("Usage events", "usage_events")) if not extra_counts or coverage.get(key) is not None) + "".join(f" · {escape(c.get('label'))}: {escape(c.get('value'))}" for c in extra_counts),
        sessions=table(["Session ID", "Session", "Events", "Usage events", "LLM calls"], [[escape(s.get("id")), escape(s.get("label")), *(metric(s.get(k)) for k in ("events", "usage_events", "llm_calls"))] for s in coverage.get("session_coverage", [])], "session-body"), coverage_notes=bullets(warnings),
        models=table(["Provider", "Route", "Native model", "Activity", "Billing", "Evidence"], [[escape(m.get(k)) for k in ("provider", "route", "native_model", "activity", "billing", "evidence")] for m in data.get("models", [])], "model-body"),
        callouts=callout_block(data.get("callouts")), audio=audio_block(data.get("audio")), separate=separate_table(data.get("separate_work")),
        costviews=cost_view_table(costs.get("views")), apieq=api_equivalent_table(phases),
        explanation=escape(costs.get("explanation")), actuals=actual_table(actual, object_fields(costs.get("actual_basis"))), inputs=inputs,
        search_requests=metric(assumptions.get("search_requests")), synthesis_seconds=metric(assumptions.get("synthesis_seconds")),
        scenario=table(["Phase · what-if", "Uncached + writes USD / share", "Cache read USD / share", "Output USD / share", "LLM subtotal USD", "Assumed GPU energy USD"], [[escape(p.get("label", p.get("id"))), *(["n/a" if object_fields(p.get("llm")).get("applicable") is False else "Unknown"] * 5)] for p in phases], "scenario-body"), cost_sources=source_list(costs.get("sources", [])),
        ledger=table([k.replace("_", " ").title() for k in LEDGER_COLUMNS] + ["Evidence"], [ledger_row(r) for r in ledger[:50]], "ledger-body"), ledger_status=f"{len(ledger)} embedded events · Showing first {min(50, len(ledger))} chronologically",
        notes=bullets(data.get("notes", [])), sources=source_list(data.get("sources", [])), data=payload, columns=json.dumps(LEDGER_COLUMNS),
    )


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Render a self-contained usage and economics workbook.")
    parser.add_argument("--data", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    if args.data.resolve() == args.out.resolve():
        parser.error("--out must not overwrite --data")
    try:
        rendered = render_report(json.loads(args.data.read_text(encoding="utf-8")))
        args.out.write_text(rendered, encoding="utf-8")
    except (OSError, ValueError, TypeError) as error:
        parser.error(str(error))


if __name__ == "__main__":
    main()
