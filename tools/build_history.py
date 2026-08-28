# -*- coding: utf-8 -*-
"""完整歷史儀表板 → docs/history.html

★兩段資料, 方法論不同, 必須明確區分(不可無縫接成一條假裝是同一件事):
   recon (2021-04-23 ~ 2026-07-16): 日線【重建】, 資料來自 data/hist_recon.json (靜態, 不變)
       production 用小時線+24h平滑, 這裡用日線近似 → 逐日抖動細節對不上, 只適合看
       趨勢/量級/區間統計, 不適合宣稱「這就是當時實際會拿到的績效」
   live  (2026-07-17 起): state/nav.jsonl 的【真實執行紀錄】, 每小時更新

★頁面功能: 使用者自選任意起訖日, 前端即時重算該區間所有指標 + 損益圖
"""
import json, os, sys
from datetime import datetime, timezone, timedelta

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import config as C

TW = timezone(timedelta(hours=8))
LEGS = ["premium", "dvol", "aleg", "tleg"]
NAMES = {"premium": "溢價腿", "dvol": "DVOL腿", "aleg": "A腿", "tleg": "T腿"}


def read_jsonl(path):
    if not os.path.exists(path):
        return []
    out = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            if line.strip():
                try:
                    out.append(json.loads(line))
                except Exception:
                    pass
    return out


def build_live_daily(navs):
    """把逐筆 nav.jsonl 聚合成每日: 當日各腿報酬(由nav相對變化)、當日收盤淨部位、BTC價"""
    by_day = {}
    for n in navs:
        d = n["ts"][:10]
        by_day.setdefault(d, []).append(n)
    days = sorted(by_day)
    rows, prev_nav = [], None
    for d in days:
        recs = by_day[d]
        last = recs[-1]
        legs = last.get("legs", {})
        cur_nav = {l: (legs.get(l) or {}).get("nav", 1.0) for l in LEGS}
        row = dict(d=d, src="live",
                   btc=round(float(last.get("prices", {}).get("BTC", 0) or 0), 2))
        for l in LEGS:
            # 當日報酬 = 該腿nav相對前一日收盤的變化
            if prev_nav and prev_nav.get(l):
                row[l] = round(cur_nav[l] / prev_nav[l] - 1, 6)
            else:
                row[l] = 0.0
            row[l + "_n"] = round(float(sum((legs.get(l) or {}).get("pos", {}).values())), 4)
        rows.append(row)
        prev_nav = cur_nav
    return rows


def main():
    # ---- 靜態重建段 ----
    hist_path = os.path.join(ROOT, "data", "hist_recon.json")
    recon = json.load(open(hist_path, encoding="utf-8")) if os.path.exists(hist_path) else []
    # ---- 真實執行段 ----
    navs = read_jsonl(os.path.join(ROOT, "state", "nav.jsonl"))
    live = build_live_daily(navs)
    live_start = C.START_DATE

    # 重建段只保留 live 開始之前(避免重疊)
    recon = [r for r in recon if r["d"] < live_start]
    series = recon + live

    payload = dict(
        series=series,
        live_start=live_start,
        recon_start=recon[0]["d"] if recon else live_start,
        legs=LEGS,
        names=NAMES,
        weights=C.LEG_WEIGHTS,
        honest=dict(sharpe=C.HONEST["honest_sharpe"], mdd=C.HONEST["expected_mdd"],
                    backtest=C.HONEST["backtest_sharpe"]),
    )
    data_js = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    now_tw = datetime.now(TW).strftime("%Y-%m-%d %H:%M")

    html = f"""<!DOCTYPE html>
<html lang="zh-Hant"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>四腿 forward — 完整歷史</title>
<style>
*{{margin:0;padding:0;box-sizing:border-box}}
body{{background:#0f1117;color:#e5e7eb;font-family:-apple-system,"PingFang TC","Microsoft JhengHei",sans-serif;padding:14px;max-width:900px;margin:0 auto}}
h1{{font-size:17px;color:#9ca3af;font-weight:600}}
h2{{font-size:14px;color:#9ca3af;margin:20px 0 8px;border-left:3px solid #3b4252;padding-left:8px}}
.sub{{color:#9ca3af;font-size:13px;margin:4px 0 12px;line-height:1.6}}
.warn{{background:#2a2119;border:1px solid #7c5a2a;border-radius:10px;padding:11px;font-size:12px;color:#fbbf24;line-height:1.7;margin:12px 0}}
.ctl{{background:#171a23;border-radius:10px;padding:12px;margin:12px 0}}
.ctlrow{{display:flex;gap:8px;flex-wrap:wrap;align-items:center;margin-bottom:8px}}
label{{font-size:12px;color:#6b7280}}
input[type=date]{{background:#0f1117;color:#e5e7eb;border:1px solid #2a2f3a;border-radius:8px;padding:6px 8px;font-size:13px}}
.pbtn{{background:#171a23;color:#9ca3af;border:1px solid #2a2f3a;border-radius:8px;padding:5px 11px;font-size:12px;cursor:pointer}}
.pbtn:hover{{border-color:#4ade80;color:#4ade80}}
.pbtn.on{{background:#4ade80;color:#0f1117;border-color:#4ade80;font-weight:700}}
/* ---- 時間軸區間選擇器 (像剪影片的 trim bar) ---- */
.brushwrap{{margin:14px 0 4px;user-select:none;-webkit-user-select:none}}
.btrack{{position:relative;height:64px;background:#0f1117;border:1px solid #2a2f3a;border-radius:8px;overflow:hidden;touch-action:none;cursor:crosshair}}
.bspark{{position:absolute;inset:0;width:100%;height:100%;pointer-events:none}}
.bdim{{position:absolute;top:0;bottom:0;background:rgba(15,17,23,.72);pointer-events:none}}
.bsel{{position:absolute;top:0;bottom:0;border-left:2px solid #4ade80;border-right:2px solid #4ade80;
  background:rgba(74,222,128,.10);pointer-events:auto;cursor:grab}}
.bsel:active{{cursor:grabbing}}
.bh{{position:absolute;top:0;bottom:0;width:22px;margin-left:-11px;pointer-events:auto;cursor:ew-resize;
  display:flex;align-items:center;justify-content:center;touch-action:none}}
.bh::after{{content:"";width:6px;height:34px;border-radius:3px;background:#4ade80;box-shadow:0 0 0 2px #0f1117}}
.bmark{{position:absolute;top:0;bottom:0;width:2px;background:#fbbf24;pointer-events:none;opacity:.9}}
.bmarklbl{{position:absolute;top:2px;font-size:9px;color:#fbbf24;pointer-events:none;white-space:nowrap}}
.blab{{display:flex;justify-content:space-between;font-size:11px;color:#6b7280;margin-top:4px}}
.bhint{{font-size:11px;color:#4b5563;margin-top:2px}}
.grid{{display:grid;grid-template-columns:repeat(auto-fill,minmax(112px,1fr));gap:8px;margin:12px 0}}
.card{{background:#171a23;border-radius:10px;padding:10px}}
.cl{{color:#6b7280;font-size:11px}}
.cv{{font-size:17px;font-weight:700;margin-top:2px}}
.cs{{color:#6b7280;font-size:10px;margin-top:1px}}
.card.pos .cv{{color:#4ade80}}.card.neg .cv{{color:#f87171}}
.chart{{width:100%;height:auto;background:#171a23;border-radius:10px}}
table{{width:100%;border-collapse:collapse;background:#171a23;border-radius:10px;overflow:hidden;font-size:13px}}
th{{color:#6b7280;font-size:11px;text-align:left;padding:8px;border-bottom:1px solid #2a2f3a}}
td{{padding:8px;border-bottom:1px solid #1f2430}}
tr:last-child td{{border:none}}
.pos{{color:#4ade80}}.neg{{color:#f87171}}.sm{{font-size:11px;color:#9ca3af}}
.foot{{color:#4b5563;font-size:11px;text-align:center;margin:18px 0 8px;line-height:1.7}}
a{{color:#60a5fa}}
</style></head><body>
<h1>四腿 forward — 完整歷史</h1>
<div class="sub">自選任意區間, 即時重算該期間所有指標 · 更新 {now_tw} 台北 ·
<a href="./index.html">← 回即時儀表板</a></div>

<div class="warn">
⚠️ <b>三件事必須先知道, 否則會誤讀這頁的數字</b><br>
<b>① 早期不是四腿</b>: 各腿誕生日不同(溢價 2017-09 · T腿 2017-10 · A腿 2018-02 需FNG · <b>DVOL 2021-04</b>)。
組合報酬 = <b>當天實際存在的腿等權平均</b>(只有2腿就各50%)。
<b>所以 2021-04 之前跟之後不是同一個策略組態</b>, 區間摘要會標示腿數組成。<br>
<b>② 重建段未扣成本</b>: 2026-07-16 以前是日線【重建】(production 用小時線+24h平滑),
<b>且完全未扣交易成本</b>(定案回測扣 maker 混合 2.45bp) → Sharpe 系統性偏高。
<b>看趨勢與相對關係可以, 別把絕對數字當真實績效。</b><br>
<b>③ 只有 2026-07-17 之後有判決效力</b>(綠色標記線右側), 來自 state/nav.jsonl 真實執行紀錄, 已含真實成本。
誠實預期 Sharpe {C.HONEST["honest_sharpe"]}(定案 2.10 打折後), 判決在 12 個月。
</div>

<div class="ctl">
  <div class="ctlrow">
    <label>起</label><input type="date" id="d0">
    <label>訖</label><input type="date" id="d1">
  </div>
  <div class="ctlrow">
    <button class="pbtn" onclick="preset('live')">正式執行後</button>
    <button class="pbtn" onclick="preset('all')">全部歷史</button>
    <button class="pbtn" onclick="preset('1y')">近1年</button>
    <button class="pbtn" onclick="preset('6m')">近6個月</button>
    <button class="pbtn" onclick="preset('3m')">近3個月</button>
    <button class="pbtn" onclick="preset('ytd')">今年以來</button>
  </div>
  <div class="brushwrap">
    <div class="btrack" id="btrack">
      <svg class="bspark" id="bspark" viewBox="0 0 1000 64" preserveAspectRatio="none"></svg>
      <div class="bdim" id="bdimL"></div>
      <div class="bdim" id="bdimR"></div>
      <div class="bmark" id="bmark"></div>
      <div class="bmarklbl" id="bmarklbl">7/17 正式執行</div>
      <div class="bsel" id="bsel"></div>
      <div class="bh" id="bh0"></div>
      <div class="bh" id="bh1"></div>
    </div>
    <div class="blab"><span id="blab0"></span><span id="blab1"></span></div>
    <div class="bhint">↔ 拖曳兩端把手選區間 · 拖中間整段平移 · 點軌道快速跳到該處</div>
  </div>
</div>

<div id="range" class="sub"></div>
<div class="grid" id="cards"></div>

<h2>累積損益</h2>
<div id="eqchart"></div>
<div class="sub" id="eqnote"></div>

<h2>回撤</h2>
<div id="ddchart"></div>

<h2>各腿表現</h2>
<div id="legtable"></div>

<h2>腿間相關性</h2>
<div id="corrtable"></div>

<h2>逐年/逐月</h2>
<div id="periodtable"></div>

<div class="foot">
四腿等權各25% · 誠實預期 Sharpe {C.HONEST["honest_sharpe"]} / MDD {C.HONEST["expected_mdd"]}%<br>
判決時鐘: 6個月只看機制 · 12個月正式判決 · 起算 {C.START_DATE}
</div>

<script>
const D = {data_js};
const S = D.series, LEGS = D.legs, NM = D.names, W = D.weights;
const LIVE0 = D.live_start;

/* ★組合報酬 = 【當天實際存在的腿】的等權平均(重新正規化)
   null = 該腿當天還沒誕生(DVOL 2021-04才有 / A腿需FNG 2018-02才有), 不是報酬0。
   若當成0會把「不存在」誤算成「持平的一條腿」, 稀釋掉其他腿的表現。 */
function combo(r) {{
  let s=0, n=0;
  for (const l of LEGS) {{ const v=r[l]; if (v!==null && v!==undefined) {{ s+=v; n++; }} }}
  return n ? s/n : 0;
}}
function nLegs(r) {{
  let n=0; for (const l of LEGS) if (r[l]!==null && r[l]!==undefined) n++; return n;
}}

function fmt(v,d=2){{ return (v>=0?"+":"") + v.toFixed(d); }}
function cls(v){{ return v>=0 ? "pos" : "neg"; }}

/* 以索引為唯一真實來源(時間軸拖曳/日期框/快捷鍵都先換算成索引) */
function sel() {{ return S.slice(I0, I1+1); }}

function stats(rows) {{
  const n = rows.length;
  if (n < 2) return null;
  const rets = rows.map(combo);
  let eq = 1, curve = [], peak = 1, mdd = 0;
  for (const r of rets) {{ eq *= (1+r); curve.push(eq); peak = Math.max(peak, eq); mdd = Math.min(mdd, eq/peak-1); }}
  const tot = eq - 1;
  const yrs = n/365;
  const cagr = yrs > 0 ? Math.pow(eq, 1/yrs)-1 : 0;
  const mean = rets.reduce((a,b)=>a+b,0)/n;
  const sd = Math.sqrt(rets.reduce((a,b)=>a+(b-mean)*(b-mean),0)/(n-1));
  const vol = sd*Math.sqrt(365);
  const sharpe = vol>0 ? cagr/vol : 0;
  const win = rets.filter(x=>x>0).length/n*100;
  const t = sd>0 ? mean/(sd/Math.sqrt(n)) : 0;
  const b0 = rows[0].btc, b1 = rows[n-1].btc;
  const bh = (b0>0 && b1>0) ? (b1/b0-1) : null;
  return {{n,tot,cagr,vol,sharpe,mdd,win,t,curve,rets,bh}};
}}

function svgLine(pts, opts) {{
  opts = opts||{{}};
  const w=880, h=opts.h||220, ml=44, mb=24, mt=10, mr=10;
  if (pts.length<2) return '<div class="sub">區間太短</div>';
  let ys = pts.map(p=>p.v);
  if (opts.extra) ys = ys.concat(opts.extra.map(p=>p.v));
  let lo=Math.min(...ys), hi=Math.max(...ys);
  if (opts.base!==undefined) {{ lo=Math.min(lo,opts.base); hi=Math.max(hi,opts.base); }}
  const pad=(hi-lo)*0.12||Math.abs(hi)*0.02||1; lo-=pad; hi+=pad;
  const X=i=>ml+i*(w-ml-mr)/(pts.length-1);
  const Y=v=>mt+(h-mb-mt)*(1-(v-lo)/(hi-lo));
  let s=`<svg viewBox="0 0 ${{w}} ${{h}}" preserveAspectRatio="none" class="chart">`;
  for (const f of [0,0.25,0.5,0.75,1]) {{
    const gy=mt+(h-mb-mt)*f, gv=hi-(hi-lo)*f;
    s+=`<line x1="${{ml}}" y1="${{gy}}" x2="${{w-mr}}" y2="${{gy}}" stroke="#2a2f3a" stroke-width="1"/>`;
    s+=`<text x="${{ml-5}}" y="${{gy+4}}" fill="#4b5563" font-size="10" text-anchor="end">${{opts.yfmt?opts.yfmt(gv):gv.toFixed(1)}}</text>`;
  }}
  if (opts.base!==undefined && lo<opts.base && opts.base<hi)
    s+=`<line x1="${{ml}}" y1="${{Y(opts.base)}}" x2="${{w-mr}}" y2="${{Y(opts.base)}}" stroke="#888" stroke-dasharray="4,4" stroke-width="1"/>`;
  // 7/17 正式執行標記線
  const li = pts.findIndex(p=>p.d>=LIVE0);
  if (li>0) {{
    s+=`<line x1="${{X(li)}}" y1="${{mt}}" x2="${{X(li)}}" y2="${{h-mb}}" stroke="#4ade80" stroke-width="2" stroke-dasharray="5,3" opacity="0.85"/>`;
    s+=`<text x="${{X(li)+4}}" y="${{mt+12}}" fill="#4ade80" font-size="11" font-weight="700">▶ 正式執行 ${{LIVE0}}</text>`;
  }}
  if (opts.extra) {{
    const e=opts.extra;
    const EX=i=>ml+i*(w-ml-mr)/(e.length-1);
    s+=`<path d="${{e.map((p,i)=>(i?'L':'M')+EX(i).toFixed(1)+','+Y(p.v).toFixed(1)).join(' ')}}" fill="none" stroke="#f97316" stroke-width="1.8" opacity="0.9"/>`;
  }}
  const col=opts.color||"#4ade80";
  s+=`<path d="${{pts.map((p,i)=>(i?'L':'M')+X(i).toFixed(1)+','+Y(p.v).toFixed(1)).join(' ')}}" fill="none" stroke="${{col}}" stroke-width="2.2"/>`;
  const n=pts.length;
  for (const i of [0, Math.floor(n/2), n-1]) {{
    const an = i===0?"start":(i===n-1?"end":"middle");
    s+=`<text x="${{X(i)}}" y="${{h-6}}" fill="#6b7280" font-size="11" text-anchor="${{an}}">${{pts[i].d.slice(2)}}</text>`;
  }}
  s+='</svg>';
  return s;
}}

/* 只用兩腿【同時都存在】的日子算相關; null不可當0(會製造假相關) */
function corr(a,b) {{
  const A=[],B=[];
  for(let i=0;i<a.length;i++){{
    if(a[i]!==null&&a[i]!==undefined&&b[i]!==null&&b[i]!==undefined){{ A.push(a[i]); B.push(b[i]); }}
  }}
  const n=A.length; if(n<10) return null;
  const ma=A.reduce((x,y)=>x+y,0)/n, mb=B.reduce((x,y)=>x+y,0)/n;
  let va=0,vb=0,cv=0;
  for(let i=0;i<n;i++){{ va+=(A[i]-ma)**2; vb+=(B[i]-mb)**2; cv+=(A[i]-ma)*(B[i]-mb); }}
  return (va>0&&vb>0) ? cv/Math.sqrt(va*vb) : null;
}}

function render() {{
  const rows = sel();
  const st = stats(rows);
  const R = document.getElementById('range');
  if (!st) {{ R.innerHTML='<span class="neg">區間內資料不足(至少需2天)</span>';
    ['cards','eqchart','ddchart','legtable','corrtable','periodtable'].forEach(i=>document.getElementById(i).innerHTML='');
    return; }}
  const nRecon = rows.filter(r=>r.src==='recon').length, nLive = rows.filter(r=>r.src==='live').length;
  const lc = {{}}; rows.forEach(r=>{{ const k=nLegs(r); lc[k]=(lc[k]||0)+1; }});
  const lcTxt = Object.keys(lc).sort().reverse().map(k=>`${{k}}腿 ${{lc[k]}}天`).join(' · ');
  const mixed = Object.keys(lc).length > 1;
  R.innerHTML = `<b>${{rows[0].d}} → ${{rows[rows.length-1].d}}</b> · 共 ${{st.n}} 天 ` +
    `(重建 ${{nRecon}} 天 / <span class="pos">真實執行 ${{nLive}} 天</span>)<br>` +
    `<span class="${{mixed?'neg':'sm'}}">組成: ${{lcTxt}}` +
    (mixed ? ' ← 此區間腿數不一致, 前後不是同一個策略組態, 比較時要留意' : '') + '</span>';

  const excess = st.bh!==null ? (st.tot-st.bh)*100 : null;
  document.getElementById('cards').innerHTML = [
    ['總報酬', fmt(st.tot*100)+'%', '', cls(st.tot)],
    ['年化 CAGR', fmt(st.cagr*100)+'%', '', cls(st.cagr)],
    ['年化波動', (st.vol*100).toFixed(2)+'%', '', ''],
    ['Sharpe', fmt(st.sharpe,2), '誠實預期 '+D.honest.sharpe, cls(st.sharpe)],
    ['最大回撤', (st.mdd*100).toFixed(2)+'%', '預期 '+D.honest.mdd+'%', 'neg'],
    ['日勝率', st.win.toFixed(1)+'%', '', ''],
    ['t 值', fmt(st.t,2), Math.abs(st.t)>2?'顯著':'未達顯著', Math.abs(st.t)>2?'pos':''],
    ['買進持有BTC', st.bh!==null?fmt(st.bh*100)+'%':'—', '同期間', st.bh!==null?cls(st.bh):''],
    ['超額報酬', excess!==null?fmt(excess)+'%':'—', 'vs 買進持有', excess!==null?cls(excess):''],
  ].map(([l,v,s,c])=>`<div class="card ${{c}}"><div class="cl">${{l}}</div><div class="cv">${{v}}</div><div class="cs">${{s}}</div></div>`).join('');

  // 損益圖(策略 vs 買進持有)
  const eqPts = rows.map((r,i)=>({{d:r.d, v:(st.curve[i]-1)*100}}));
  const b0 = rows[0].btc;
  const bhPts = b0>0 ? rows.map(r=>({{d:r.d, v:(r.btc/b0-1)*100}})) : null;
  document.getElementById('eqchart').innerHTML =
    svgLine(eqPts, {{base:0, color:'#4ade80', extra:bhPts, h:240, yfmt:v=>v.toFixed(0)+'%'}});
  document.getElementById('eqnote').innerHTML =
    `綠=四腿策略 · <span style="color:#f97316">橘=買進持有BTC</span> · 綠色虛線=正式執行起點(${{LIVE0}})`;

  // 回撤
  let peak=-1e9;
  const ddPts = rows.map((r,i)=>{{ peak=Math.max(peak,st.curve[i]); return {{d:r.d, v:(st.curve[i]/peak-1)*100}}; }});
  document.getElementById('ddchart').innerHTML =
    svgLine(ddPts, {{base:0, color:'#f87171', h:150, yfmt:v=>v.toFixed(0)+'%'}});

  // 各腿
  let lt = '<table><tr><th>腿</th><th>有效天數</th><th>總報酬</th><th>年化</th><th>波動</th><th>Sharpe</th><th>勝率</th><th>平均淨部位</th></tr>';
  for (const l of LEGS) {{
    /* ★只取該腿真的存在的日子, null 不能當0 */
    const rr = rows.map(r=>r[l]).filter(v=>v!==null&&v!==undefined);
    if (rr.length < 2) {{
      lt += `<tr><td><b>${{NM[l]}}</b></td><td class="sm">0</td><td colspan="6" class="sm">此區間尚未誕生</td></tr>`;
      continue;
    }}
    let e=1; for(const x of rr) e*=(1+x);
    const yrs=rr.length/365, cg=yrs>0?Math.pow(e,1/yrs)-1:0;
    const m=rr.reduce((a,b)=>a+b,0)/rr.length;
    const sd=Math.sqrt(rr.reduce((a,b)=>a+(b-m)**2,0)/(rr.length-1));
    const vol=sd*Math.sqrt(365), sh=vol>0?cg/vol:0;
    const wr=rr.filter(x=>x>0).length/rr.length*100;
    const nvs=rows.map(r=>r[l+'_n']).filter(v=>v!==null&&v!==undefined);
    const nn=nvs.length?nvs.reduce((a,b)=>a+b,0)/nvs.length:0;
    const partial = rr.length < rows.length;
    lt += `<tr><td><b>${{NM[l]}}</b></td>`+
          `<td class="sm">${{rr.length}}${{partial?' <span class="neg">(部分)</span>':''}}</td>`+
          `<td class="${{cls(e-1)}}">${{fmt((e-1)*100)}}%</td>`+
          `<td class="${{cls(cg)}}">${{fmt(cg*100)}}%</td><td>${{(vol*100).toFixed(1)}}%</td>`+
          `<td class="${{cls(sh)}}">${{fmt(sh,2)}}</td><td>${{wr.toFixed(0)}}%</td>`+
          `<td class="${{cls(nn)}}">${{fmt(nn,3)}}</td></tr>`;
  }}
  document.getElementById('legtable').innerHTML = lt+
    '</table><div class="sm" style="margin-top:6px">「部分」= 該腿在此區間並非全程存在(溢價腿2017-09起 · T腿2017-10起 · A腿2018-02起需FNG · DVOL腿2021-04起)</div>';

  // 相關矩陣 (只用兩腿【同時存在】的日子)
  const ser = {{}}; for (const l of LEGS) ser[l]=rows.map(r=>r[l]);
  let ct = '<table><tr><th></th>'+LEGS.map(l=>`<th>${{NM[l]}}</th>`).join('')+'</tr>';
  for (let i=0;i<LEGS.length;i++) {{
    ct += `<tr><td class="sm"><b>${{NM[LEGS[i]]}}</b></td>`;
    for (let j=0;j<LEGS.length;j++) {{
      if (j<=i) {{ ct+='<td></td>'; continue; }}
      const c = corr(ser[LEGS[i]], ser[LEGS[j]]);
      ct += c===null ? '<td class="sm">—</td>'
        : `<td class="${{Math.abs(c)>0.6?'neg':(Math.abs(c)<0.3?'sm':'')}}">${{fmt(c,2)}}</td>`;
    }}
    ct += '</tr>';
  }}
  document.getElementById('corrtable').innerHTML = ct+'</table><div class="sm" style="margin-top:6px">警戒 |corr|&gt;0.6 = 分散失效 · 回測基準 0.002~0.31</div>';

  // 逐年 or 逐月(區間<400天用逐月)
  const useMonth = rows.length < 400;
  const key = r => useMonth ? r.d.slice(0,7) : r.d.slice(0,4);
  const grp = {{}};
  rows.forEach((r,i)=>{{ (grp[key(r)] = grp[key(r)]||[]).push(r); }});
  let pt = `<table><tr><th>${{useMonth?'月份':'年份'}}</th><th>報酬</th><th>天數</th><th>BTC同期</th><th>超額</th></tr>`;
  for (const k of Object.keys(grp).sort()) {{
    const g = grp[k];
    let e=1; for(const r of g) e*=(1+combo(r));
    const gb = (g[0].btc>0&&g[g.length-1].btc>0) ? (g[g.length-1].btc/g[0].btc-1) : null;
    const ex = gb!==null ? (e-1-gb)*100 : null;
    pt += `<tr><td>${{k}}</td><td class="${{cls(e-1)}}">${{fmt((e-1)*100)}}%</td><td class="sm">${{g.length}}</td>`+
          `<td class="${{gb!==null?cls(gb):''}}">${{gb!==null?fmt(gb*100)+'%':'—'}}</td>`+
          `<td class="${{ex!==null?cls(ex):''}}">${{ex!==null?fmt(ex)+'%':'—'}}</td></tr>`;
  }}
  document.getElementById('periodtable').innerHTML = pt+'</table>';
}}

/* ================= 時間軸區間選擇器 ================= */
let I0 = 0, I1 = S.length-1;          // 目前選取的索引範圍(唯一真實來源)
const NS = S.length;

function idxOfDate(d) {{               // 找 >= d 的第一個索引
  let lo=0, hi=NS-1, r=NS-1;
  while (lo<=hi) {{ const m=(lo+hi)>>1; if (S[m].d >= d) {{ r=m; hi=m-1; }} else lo=m+1; }}
  return r;
}}

/* 唯一入口: 所有改動(日期框/按鈕/拖曳)都走這裡, 保證三者同步 */
function applyIdx(a, b, opts) {{
  opts = opts||{{}};
  a = Math.max(0, Math.min(NS-1, Math.round(a)));
  b = Math.max(0, Math.min(NS-1, Math.round(b)));
  if (a > b) {{ const t=a; a=b; b=t; }}
  if (b - a < 1) {{ if (b < NS-1) b = a+1; else a = b-1; }}   // 至少2天才算得出指標
  I0 = a; I1 = b;
  document.getElementById('d0').value = S[I0].d;
  document.getElementById('d1').value = S[I1].d;
  syncBrush();
  if (!opts.noRender) render();
}}

function syncBrush() {{
  const t = document.getElementById('btrack');
  /* ★寬度可能為0: 頁面在背景分頁/隱藏狀態載入時沒有 layout。
     此時先跳過, 交給下面的 ResizeObserver 在拿到真實寬度後補畫,
     否則所有把手會塌成1px且永遠不會自己恢復。 */
  const w = t.clientWidth;
  if (!w) return;
  const x0 = I0/(NS-1)*w, x1 = I1/(NS-1)*w;
  document.getElementById('bsel').style.left = x0+'px';
  document.getElementById('bsel').style.width = Math.max(2, x1-x0)+'px';
  document.getElementById('bh0').style.left = x0+'px';
  document.getElementById('bh1').style.left = x1+'px';
  document.getElementById('bdimL').style.left='0px';
  document.getElementById('bdimL').style.width=x0+'px';
  document.getElementById('bdimR').style.left=x1+'px';
  document.getElementById('bdimR').style.width=Math.max(0,w-x1)+'px';
  const li = idxOfDate(LIVE0), lx = li/(NS-1)*w;
  document.getElementById('bmark').style.left = lx+'px';
  const lbl = document.getElementById('bmarklbl');
  lbl.style.left = Math.min(w-90, lx+4)+'px';
  document.getElementById('blab0').textContent = S[I0].d;
  document.getElementById('blab1').textContent = S[I1].d;
}}

/* 背景縮圖: 整段歷史的累積損益(log軸, 因為跨越10倍以上) */
function drawSpark() {{
  let eq=1; const ys=[];
  for (const r of S) {{ eq *= (1+combo(r)); ys.push(Math.log(Math.max(eq,1e-9))); }}
  const lo=Math.min(...ys), hi=Math.max(...ys), rng=(hi-lo)||1;
  const pts = ys.map((v,i)=>[i/(NS-1)*1000, 60-((v-lo)/rng)*54]);
  const d = pts.map((p,i)=>(i?'L':'M')+p[0].toFixed(1)+','+p[1].toFixed(1)).join(' ');
  document.getElementById('bspark').innerHTML =
    `<path d="${{d}} L1000,64 L0,64 Z" fill="#4ade80" opacity="0.13"/>`+
    `<path d="${{d}}" fill="none" stroke="#4ade80" stroke-width="1.2" opacity="0.75"/>`;
}}

(function initBrush() {{
  const track = document.getElementById('btrack');
  let mode = null, grabDX = 0, pending = null;

  const xToIdx = e => {{
    const r = track.getBoundingClientRect();
    const x = Math.max(0, Math.min(r.width, e.clientX - r.left));
    return x/(r.width||1)*(NS-1);
  }};
  /* 拖曳中用 rAF 節流, 避免每個 pointermove 都重畫整頁 */
  const schedule = () => {{
    if (pending) return;
    pending = requestAnimationFrame(()=>{{ pending=null; render(); }});
  }};

  const start = (m) => (e) => {{
    e.preventDefault(); e.stopPropagation();
    mode = m;
    if (m==='pan') grabDX = xToIdx(e) - I0;
    track.setPointerCapture && track.setPointerCapture(e.pointerId);
    document.querySelectorAll('.pbtn').forEach(b=>b.classList.remove('on'));
  }};
  document.getElementById('bh0').addEventListener('pointerdown', start('h0'));
  document.getElementById('bh1').addEventListener('pointerdown', start('h1'));
  document.getElementById('bsel').addEventListener('pointerdown', start('pan'));

  /* 點軌道空白處: 把較近的把手移過去 */
  track.addEventListener('pointerdown', (e)=>{{
    if (mode) return;
    const i = xToIdx(e);
    if (Math.abs(i-I0) < Math.abs(i-I1)) {{ mode='h0'; }} else {{ mode='h1'; }}
    track.setPointerCapture && track.setPointerCapture(e.pointerId);
    document.querySelectorAll('.pbtn').forEach(b=>b.classList.remove('on'));
    if (mode==='h0') applyIdx(i, I1, {{noRender:true}}); else applyIdx(I0, i, {{noRender:true}});
    schedule();
  }});

  track.addEventListener('pointermove', (e)=>{{
    if (!mode) return;
    e.preventDefault();
    const i = xToIdx(e);
    if (mode==='h0') applyIdx(i, I1, {{noRender:true}});
    else if (mode==='h1') applyIdx(I0, i, {{noRender:true}});
    else if (mode==='pan') {{
      const span = I1-I0;
      let a = i - grabDX;
      a = Math.max(0, Math.min(NS-1-span, a));
      applyIdx(a, a+span, {{noRender:true}});
    }}
    schedule();
  }});
  const end = ()=>{{ if (mode) {{ mode=null; render(); }} }};
  track.addEventListener('pointerup', end);
  track.addEventListener('pointercancel', end);
  window.addEventListener('resize', syncBrush);
  /* 拿到真實寬度(從隱藏變可見/轉向/視窗縮放)時補畫把手位置 */
  if (window.ResizeObserver) new ResizeObserver(syncBrush).observe(track);
}})();

function preset(k) {{
  const last = S[S.length-1].d, first = S[0].d;
  let a = first;
  const dt = new Date(last+'T00:00:00Z');
  if (k==='live') a = LIVE0;
  else if (k==='all') a = first;
  else if (k==='1y') {{ dt.setUTCFullYear(dt.getUTCFullYear()-1); a=dt.toISOString().slice(0,10); }}
  else if (k==='6m') {{ dt.setUTCMonth(dt.getUTCMonth()-6); a=dt.toISOString().slice(0,10); }}
  else if (k==='3m') {{ dt.setUTCMonth(dt.getUTCMonth()-3); a=dt.toISOString().slice(0,10); }}
  else if (k==='ytd') a = last.slice(0,4)+'-01-01';
  if (a < first) a = first;
  document.querySelectorAll('.pbtn').forEach(b=>b.classList.remove('on'));
  if (typeof event!=='undefined' && event && event.target) event.target.classList.add('on');
  applyIdx(idxOfDate(a), NS-1);
}}

document.getElementById('d0').min = S[0].d;
document.getElementById('d0').max = S[S.length-1].d;
document.getElementById('d1').min = S[0].d;
document.getElementById('d1').max = S[S.length-1].d;
document.getElementById('d0').addEventListener('change', ()=>{{
  document.querySelectorAll('.pbtn').forEach(b=>b.classList.remove('on'));
  applyIdx(idxOfDate(document.getElementById('d0').value), I1);
}});
document.getElementById('d1').addEventListener('change', ()=>{{
  document.querySelectorAll('.pbtn').forEach(b=>b.classList.remove('on'));
  applyIdx(I0, idxOfDate(document.getElementById('d1').value));
}});

drawSpark();
applyIdx(idxOfDate(LIVE0), NS-1);
</script>
</body></html>"""

    out = os.path.join(ROOT, "docs", "history.html")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"✅ 歷史儀表板已產生: {out}")
    print(f"   重建段 {len(recon)} 天 + 真實執行 {len(live)} 天 = 共 {len(series)} 天")
    if series:
        print(f"   範圍 {series[0]['d']} → {series[-1]['d']}")


if __name__ == "__main__":
    main()
