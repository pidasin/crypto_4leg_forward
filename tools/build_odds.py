# -*- coding: utf-8 -*-
"""勝率拉桿頁 → docs/odds.html

★資料來源: 四腿等權回測日報酬 (2021-04~2026-07, 1906天) 的 block bootstrap
  - block=20天 → 保留自相關與肥尾 (kurt 15.7, 不能用常態公式)
  - 20000次模擬 × 28個持有期
  - 三情境: 回測2.10 / 誠實1.45 / 保守1.00 (縮放均值, 波動與形狀不變)
★曲線是【靜態】的(來自回測), 只有「你在這裡」的標記會隨實際運行時間移動
"""
import json, os, sys
from datetime import datetime, timezone, timedelta
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import config as C

TW = timezone(timedelta(hours=8))
H = [1,2,3,5,7,10,14,21,30,45,60,90,120,150,180,270,365,
     456,547,730,913,1095,1460,1825,2190,2555,2920,3285]

def label(d):
    if d < 30:  return f"{d} 天"
    if d < 365: return f"{d} 天 ({d/30.44:.1f} 個月)"
    y = d/365
    return f"{d} 天 ({y:.1f} 年)" if y < 2 else f"{y:.1f} 年"

def main():
    curves = json.load(open(os.path.join(ROOT, "tools", "winrate_curves.json"), encoding="utf-8"))
    days_run = (datetime.now(timezone.utc) - C.start_dt()).total_seconds()/86400
    # 目前落在哪一格
    idx_now = min(range(len(H)), key=lambda i: abs(H[i]-days_run))
    now_tw = datetime.now(TW).strftime("%Y-%m-%d %H:%M")

    js_h = json.dumps(H)
    js_c = json.dumps(curves, ensure_ascii=False)

    # 勝率曲線SVG (三條線, 對數x軸)
    import math
    W, HT, ML, MB = 700, 230, 34, 26
    def X(d): return ML + (math.log10(d)-0)/(math.log10(3285)) * (W-ML-10)
    def Y(p): return 10 + (HT-MB-10) * (1 - (p-40)/60)      # 40%~100%
    paths = []
    COLORS = {"optimistic":"#60a5fa","honest":"#4ade80","conservative":"#fbbf24"}
    for name, col in COLORS.items():
        pts = [(X(d), Y(max(curves[name][str(d)]["win"], 40))) for d in H]
        dd = " ".join(f"{'M' if i==0 else 'L'}{x:.1f},{y:.1f}" for i,(x,y) in enumerate(pts))
        w = 3 if name=="honest" else 1.8
        op = 1 if name=="honest" else 0.65
        paths.append(f'<path d="{dd}" fill="none" stroke="{col}" stroke-width="{w}" opacity="{op}"/>')
    grid = []
    for p in [50,60,70,80,90,100]:
        y = Y(p)
        grid.append(f'<line x1="{ML}" y1="{y:.0f}" x2="{W-10}" y2="{y:.0f}" stroke="#2a2f3a"/>'
                    f'<text x="{ML-6}" y="{y+4:.0f}" fill="#6b7280" font-size="11" text-anchor="end">{p}%</text>')
    for d, lab in [(7,"7天"),(30,"1月"),(90,"3月"),(365,"1年"),(1095,"3年"),(3285,"9年")]:
        x = X(d)
        grid.append(f'<line x1="{x:.0f}" y1="10" x2="{x:.0f}" y2="{HT-MB}" stroke="#1f2430"/>'
                    f'<text x="{x:.0f}" y="{HT-8}" fill="#6b7280" font-size="11" text-anchor="middle">{lab}</text>')
    marker = f'<line id="mk" x1="0" y1="10" x2="0" y2="{HT-MB}" stroke="#fff" stroke-width="1.5" stroke-dasharray="3,3" opacity="0.85"/>'
    youare = X(max(days_run,1))
    svg = (f'<svg viewBox="0 0 {W} {HT}" class="chart">{"".join(grid)}{"".join(paths)}'
           f'<line x1="{youare:.1f}" y1="10" x2="{youare:.1f}" y2="{HT-MB}" stroke="#f472b6" stroke-width="2"/>'
           f'<text x="{youare+5:.0f}" y="22" fill="#f472b6" font-size="11">你在這裡</text>{marker}</svg>')

    html = f"""<!DOCTYPE html>
<html lang="zh-Hant"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>四腿勝率拉桿</title>
<style>
*{{margin:0;padding:0;box-sizing:border-box}}
body{{background:#0f1117;color:#e5e7eb;font-family:-apple-system,"PingFang TC","Microsoft JhengHei",sans-serif;padding:14px;max-width:760px;margin:0 auto}}
h1{{font-size:17px;color:#9ca3af;font-weight:600}}
a{{color:#60a5fa;text-decoration:none;font-size:13px}}
.hero{{background:#171a23;border-radius:12px;padding:18px;margin:14px 0;text-align:center}}
.hl{{color:#6b7280;font-size:12px}}
.big{{font-size:52px;font-weight:800;color:#4ade80;letter-spacing:-2px;line-height:1.1}}
.range{{width:100%;margin:16px 0 6px;accent-color:#4ade80;height:28px}}
.days{{font-size:19px;font-weight:700;margin-top:4px}}
.grid3{{display:grid;grid-template-columns:repeat(3,1fr);gap:8px;margin-top:14px}}
.sc{{background:#1c1f2a;border-radius:9px;padding:9px}}
.sc .n{{font-size:11px;color:#6b7280}}
.sc .v{{font-size:19px;font-weight:700;margin-top:2px}}
.sc.o .v{{color:#60a5fa}} .sc.h .v{{color:#4ade80}} .sc.c .v{{color:#fbbf24}}
.stats{{display:grid;grid-template-columns:1fr 1fr 1fr;gap:8px;margin-top:12px}}
.st{{background:#171a23;border-radius:9px;padding:10px}}
.st .n{{font-size:11px;color:#6b7280}} .st .v{{font-size:16px;font-weight:700;margin-top:2px}}
.chart{{width:100%;height:auto;background:#171a23;border-radius:10px;margin-top:6px}}
h2{{font-size:14px;color:#9ca3af;margin:20px 0 8px;border-left:3px solid #3b4252;padding-left:8px}}
.note{{background:#1c1f2a;border-radius:10px;padding:13px;font-size:12px;color:#9ca3af;line-height:1.75;margin-top:14px}}
.lg{{display:flex;gap:14px;font-size:11px;color:#9ca3af;margin-top:8px;flex-wrap:wrap}}
.dot{{display:inline-block;width:9px;height:3px;vertical-align:middle;margin-right:4px}}
b.g{{color:#4ade80}} b.r{{color:#f87171}}
</style></head><body>
<h1>四腿組合 — 持有多久,賺錢機率多少</h1>
<a href="./">← 回儀表板</a>

<div class="hero">
  <div class="hl">誠實預期下,持有這段時間結束時為正報酬的機率</div>
  <div class="big" id="win">—</div>
  <input type="range" class="range" id="sl" min="0" max="{len(H)-1}" value="{idx_now}">
  <div class="days" id="dl">—</div>
  <div class="grid3">
    <div class="sc o"><div class="n">回測 2.10</div><div class="v" id="w_o">—</div></div>
    <div class="sc h"><div class="n">★誠實 1.45</div><div class="v" id="w_h">—</div></div>
    <div class="sc c"><div class="n">保守 1.00</div><div class="v" id="w_c">—</div></div>
  </div>
  <div class="stats">
    <div class="st"><div class="n">中位報酬</div><div class="v" id="med">—</div></div>
    <div class="st"><div class="n">壞情況(10%分位)</div><div class="v" id="p10">—</div></div>
    <div class="st"><div class="n">好情況(90%分位)</div><div class="v" id="p90">—</div></div>
  </div>
</div>

<h2>勝率曲線</h2>
{svg}
<div class="lg">
 <span><i class="dot" style="background:#60a5fa"></i>回測2.10</span>
 <span><i class="dot" style="background:#4ade80"></i>誠實1.45</span>
 <span><i class="dot" style="background:#fbbf24"></i>保守1.00</span>
 <span><i class="dot" style="background:#f472b6"></i>你在這裡 ({days_run:.1f}天)</span>
</div>

<div class="note">
<b>這張圖怎麼算的</b>:四腿等權回測日報酬(2021-04~2026-07,1906天)做 block bootstrap
(block=20天保留自相關與肥尾,20000次模擬)。<b>不是常態公式</b> —— 這組報酬峰度 15.7,用常態會低估尾部。<br><br>
<b class="r">三個必須知道的限制</b>:<br>
① <b>勝率不等於安全</b>。1年勝率 91% 聽起來很高,但那 9% 是「一整年結束時還在虧」,
而且路上的回撤更兇 —— 預期 MDD <b>{C.HONEST["expected_mdd"]}%</b>、最長套牢 <b>{C.HONEST["expected_worst_underwater_days"]}天</b>。
勝率高不代表過程不痛。<br>
② <b>曲線來自回測,不是承諾</b>。它假設 edge 在未來 9 年持續存在。而我們已知
T腿在 ETF 後從 1.78 衰減到 0.58 —— 十年內大概率會死一到兩條腿。<b>越右邊的數字越該打折</b>。<br>
③ <b>看綠線,別看藍線</b>。藍線是回測原值(2.10),已知有 winner's curse(40+家族挑4條)。
綠線 1.45 才是誠實預期,黃線 1.00 是「edge 比想的弱一半」的情境。
</div>

<script>
const H={js_h}, C={js_c};
const el=(i)=>document.getElementById(i);
function lab(d){{
  if(d<30) return d+" 天";
  if(d<365) return d+" 天 ("+(d/30.44).toFixed(1)+" 個月)";
  const y=d/365; return y<2 ? d+" 天 ("+y.toFixed(1)+" 年)" : y.toFixed(1)+" 年";
}}
function upd(){{
  const d=H[+el("sl").value], k=String(d);
  const h=C.honest[k], o=C.optimistic[k], c=C.conservative[k];
  el("win").textContent=h.win.toFixed(1)+"%";
  el("dl").textContent=lab(d);
  el("w_o").textContent=o.win.toFixed(1)+"%";
  el("w_h").textContent=h.win.toFixed(1)+"%";
  el("w_c").textContent=c.win.toFixed(1)+"%";
  const sg=(v)=>(v>=0?"+":"")+v.toFixed(1)+"%";
  el("med").textContent=sg(h.med);
  el("p10").textContent=sg(h.p10);
  el("p90").textContent=sg(h.p90);
  el("p10").style.color = h.p10>=0 ? "#4ade80" : "#f87171";
  el("med").style.color = h.med>=0 ? "#4ade80" : "#f87171";
  el("p90").style.color = "#4ade80";
  const x = 34 + Math.log10(d)/Math.log10(3285)*(700-34-10);
  const mk=document.getElementById("mk");
  mk.setAttribute("x1",x); mk.setAttribute("x2",x);
}}
el("sl").addEventListener("input",upd); upd();
</script>
<div style="color:#4b5563;font-size:11px;text-align:center;margin:18px 0">
更新 {now_tw} 台北 · 已運行 {days_run:.1f} 天 · 曲線為靜態回測推估
</div>
</body></html>"""
    out = os.path.join(ROOT, "docs", "odds.html")
    with open(out, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"✅ 勝率頁: {out}  (你在第{idx_now}格 = {H[idx_now]}天)")

if __name__ == "__main__":
    main()
