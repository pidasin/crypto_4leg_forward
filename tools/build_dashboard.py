# -*- coding: utf-8 -*-
"""手機儀表板產生器 → docs/index.html (GitHub Pages)
★資料全部來自 state_testnet/ (真模擬金) + state/ (paper帳面)
★每小時由 hourly workflow 重建; 頁面本身每5分鐘自動刷新
★純SVG手刻圖表, 零外部依賴, 手機優先
"""
import json, os, sys
from datetime import datetime, timezone, timedelta

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import config as C

TW = timezone(timedelta(hours=8))

def read_jsonl(path):
    if not os.path.exists(path): return []
    out = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            if line.strip():
                try: out.append(json.loads(line))
                except Exception: pass
    return out

def tw(ts_iso, fmt="%m/%d %H:%M"):
    try:
        d = datetime.fromisoformat(ts_iso.replace("Z", "+00:00"))
        if d.tzinfo is None: d = d.replace(tzinfo=timezone.utc)
        return d.astimezone(TW).strftime(fmt)
    except Exception:
        return ts_iso[:16]

# ---------- SVG 折線圖 ----------
def line_chart(pts, w=700, h=200, color="#4ade80", fill=True, ylabel="", baseline=None):
    if len(pts) < 2:
        return f'<div class="empty">資料累積中… (目前 {len(pts)} 筆, 每小時+1)</div>'
    ys = [p[1] for p in pts]
    ymin, ymax = min(ys), max(ys)
    if baseline is not None:
        ymin, ymax = min(ymin, baseline), max(ymax, baseline)
    pad = (ymax - ymin) * 0.12 or abs(ymax) * 0.01 or 1
    ymin -= pad; ymax += pad
    ml, mb = 8, 22
    def X(i): return ml + i * (w - ml - 8) / (len(pts) - 1)
    def Y(v): return 8 + (h - mb - 8) * (1 - (v - ymin) / (ymax - ymin))
    path = " ".join(f"{'M' if i==0 else 'L'}{X(i):.1f},{Y(p[1]):.1f}" for i, p in enumerate(pts))
    s = [f'<svg viewBox="0 0 {w} {h}" preserveAspectRatio="none" class="chart">']
    for fr in (0.25, 0.5, 0.75):
        gy = 8 + (h - mb - 8) * fr
        s.append(f'<line x1="{ml}" y1="{gy:.0f}" x2="{w-8}" y2="{gy:.0f}" stroke="#2a2f3a" stroke-width="1"/>')
    if baseline is not None and ymin < baseline < ymax:
        by = Y(baseline)
        s.append(f'<line x1="{ml}" y1="{by:.1f}" x2="{w-8}" y2="{by:.1f}" stroke="#888" stroke-dasharray="4,4" stroke-width="1"/>')
    if fill:
        s.append(f'<path d="{path} L{X(len(pts)-1):.1f},{h-mb} L{ml},{h-mb} Z" fill="{color}" opacity="0.12"/>')
    s.append(f'<path d="{path}" fill="none" stroke="{color}" stroke-width="2.2"/>')
    lx, ly = X(len(pts)-1), Y(pts[-1][1])
    s.append(f'<circle cx="{lx:.1f}" cy="{ly:.1f}" r="3.5" fill="{color}"/>')
    n = len(pts)
    for i in (0, n // 2, n - 1):
        anchor = "start" if i == 0 else ("end" if i == n-1 else "middle")
        s.append(f'<text x="{X(i):.0f}" y="{h-6}" fill="#6b7280" font-size="11" text-anchor="{anchor}">{pts[i][0]}</text>')
    s.append(f'<text x="{w-10}" y="18" fill="{color}" font-size="13" text-anchor="end" font-weight="600">{ylabel}</text>')
    s.append('</svg>')
    return "".join(s)

def main():
    eq = read_jsonl(os.path.join(ROOT, "state_testnet", "equity.jsonl"))
    orders = read_jsonl(os.path.join(ROOT, "state_testnet", "orders.jsonl"))
    recons = read_jsonl(os.path.join(ROOT, "state_testnet", "recon.jsonl"))
    navs = read_jsonl(os.path.join(ROOT, "state", "nav.jsonl"))
    try:
        posj = json.load(open(os.path.join(ROOT, "state", "positions.json"), encoding="utf-8"))
    except Exception:
        posj = {}

    now_tw = datetime.now(TW).strftime("%Y-%m-%d %H:%M")
    cur = eq[-1] if eq else {}
    first = eq[0] if eq else {}
    base = first.get("equity") or 5000.0
    equity = cur.get("equity", base)
    pnl = equity - base
    pnl_pct = pnl / base * 100 if base else 0

    # 期間報酬
    def eq_ago(hours):
        cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
        past = [e for e in eq if datetime.fromisoformat(e["ts"]).replace(tzinfo=timezone.utc) <= cutoff]
        return past[-1]["equity"] if past else None
    d1, d7, d30 = eq_ago(24), eq_ago(24*7), eq_ago(24*30)
    def pct(a, b): return (a / b - 1) * 100 if (a and b) else None

    # MDD / 目前回撤
    peak, mdd, cur_dd = -1e18, 0.0, 0.0
    for e in eq:
        v = e.get("equity", 0)
        peak = max(peak, v)
        dd = (v / peak - 1) * 100 if peak > 0 else 0
        mdd = min(mdd, dd); cur_dd = dd

    # 手續費/成交統計 (market單=taker 5bp)
    ok_orders = [o for o in orders if o.get("http") == 200]
    rejected  = [o for o in orders if o.get("http") != 200]
    traded_usd = sum(abs(o.get("qty", 0)) * (o.get("ref_px") or 0) for o in ok_orders)
    fee_est = traded_usd * C.FEE_TAKER_BP / 10000
    gross = cur.get("gross", 0)
    lev = gross / equity if equity else 0
    days = 0.0
    if len(eq) >= 2:
        days = (datetime.fromisoformat(eq[-1]["ts"]) - datetime.fromisoformat(eq[0]["ts"])).total_seconds() / 86400

    last_recon = recons[-1] if recons else {}
    recon_ok = last_recon.get("ok", False)

    # ---------- 統計卡 ----------
    def card(label, val, sub="", cls=""):
        return f'<div class="card {cls}"><div class="cl">{label}</div><div class="cv">{val}</div><div class="cs">{sub}</div></div>'
    def money(v, dec=2): return ("+" if v >= 0 else "") + format(v, f",.{dec}f")
    def pc(v): return "—" if v is None else f"{v:+.2f}%"
    g = lambda v: "pos" if (v or 0) >= 0 else "neg"

    cards = "".join([
        card("錢包(已實現)", f"${cur.get('wallet', base):,.2f}"),
        card("未實現損益", money(cur.get("upnl", 0)), cls=g(cur.get("upnl", 0))),
        card("今日", pc(pct(equity, d1)), cls=g(pct(equity, d1))),
        card("7日", pc(pct(equity, d7)), cls=g(pct(equity, d7))),
        card("30日", pc(pct(equity, d30)), cls=g(pct(equity, d30))),
        card("目前回撤", f"{cur_dd:.2f}%", f"最深 {mdd:.2f}%", "neg" if cur_dd < -0.5 else ""),
        card("總曝險", f"${gross:,.0f}", f"槓桿 {lev:.2f}x"),
        card("成交", f"{len(ok_orders)} 筆", f"拒單 {len(rejected)}"),
        card("手續費(taker估)", f"${fee_est:,.2f}", f"{fee_est/base*100:.3f}%"),
        card("運行", f"{days:.1f} 天", f"快照 {len(eq)} 筆"),
        card("對帳", "✅ 通過" if recon_ok else "🔴 異常",
             f"偏差 ${last_recon.get('max_diff_usd', 0):.2f}", "" if recon_ok else "neg"),
    ])

    # ---------- 權益圖 + 回撤圖 ----------
    eq_pts = [(tw(e["ts"], "%m/%d %H:%M" if days < 3 else "%m/%d"), e["equity"]) for e in eq]
    eq_svg = line_chart(eq_pts, color="#4ade80" if pnl >= 0 else "#f87171",
                        ylabel=f"${equity:,.2f}", baseline=base)
    dd_pts, peak2 = [], -1e18
    for e in eq:
        peak2 = max(peak2, e["equity"])
        dd_pts.append((tw(e["ts"], "%m/%d %H:%M" if days < 3 else "%m/%d"),
                       round((e["equity"] / peak2 - 1) * 100, 3)))
    dd_svg = line_chart(dd_pts, h=120, color="#f87171", ylabel=f"{cur_dd:.2f}%")

    # ---------- 部位表 ----------
    rows = []
    for p in sorted(cur.get("positions", []), key=lambda x: -x["notional"]):
        side = "多" if p["amt"] > 0 else "空"
        sc = "pos" if p["amt"] > 0 else "neg"
        uc = "pos" if p["upnl"] >= 0 else "neg"
        rows.append(f'<tr><td><b>{p["coin"]}</b></td><td class="{sc}">{side}</td>'
                    f'<td>${p["notional"]:,.0f}</td><td>{p["entry"]:,.4g}</td>'
                    f'<td class="{uc}">{money(p["upnl"])}</td></tr>')
    pos_table = (f'<table><tr><th>幣</th><th>方向</th><th>名目</th><th>進場價</th><th>未實現</th></tr>'
                 f'{"".join(rows)}</table>') if rows else '<div class="empty">無部位</div>'

    # ---------- 四腿帳面 ----------
    NAMES = {"premium": ("🟦", "溢價腿"), "dvol": ("🟨", "DVOL腿"),
             "aleg": ("🟩", "A腿"), "tleg": ("🟥", "T腿")}
    last_nav = navs[-1].get("legs", {}) if navs else {}
    leg_rows = []
    for leg in ["premium", "dvol", "aleg", "tleg"]:
        emo, nm = NAMES[leg]
        pos = posj.get(leg) or {}
        net = sum(pos.values())
        nav = last_nav.get(leg, {}).get("nav", 1.0)
        navc = "pos" if nav >= 1 else "neg"
        detail = " ".join(f"{k}{v:+.2f}" for k, v in sorted(pos.items(), key=lambda x: -abs(x[1]))[:4])
        more = f" +{len(pos)-4}" if len(pos) > 4 else ""
        upd = posj.get("_meta", {}).get(leg)
        leg_rows.append(f'<tr><td>{emo} <b>{nm}</b></td><td class="{"pos" if net>=0 else "neg"}">{net:+.2f}</td>'
                        f'<td class="{navc}">{(nav-1)*100:+.2f}%</td>'
                        f'<td class="sm">{detail}{more}</td><td class="sm">{tw(upd) if upd else "—"}</td></tr>')
    leg_table = (f'<table><tr><th>腿</th><th>淨部位</th><th>paper損益</th><th>明細</th><th>更新</th></tr>'
                 f'{"".join(leg_rows)}</table>')

    # ---------- 最近成交 ----------
    trows = []
    for o in list(reversed(ok_orders))[:15]:
        sc = "pos" if o["side"] == "BUY" else "neg"
        trows.append(f'<tr><td class="sm">{tw(o["ts"])}</td><td><b>{o["coin"]}</b></td>'
                     f'<td class="{sc}">{"買" if o["side"]=="BUY" else "賣"}</td>'
                     f'<td>{o["qty"]}</td><td>${abs(o["qty"]*(o.get("ref_px") or 0)):,.0f}</td></tr>')
    trade_table = (f'<table><tr><th>時間(台)</th><th>幣</th><th>方向</th><th>數量</th><th>名目</th></tr>'
                   f'{"".join(trows)}</table>') if trows else '<div class="empty">尚無成交</div>'

    pnl_color = "#4ade80" if pnl >= 0 else "#f87171"
    html = f"""<!DOCTYPE html>
<html lang="zh-Hant"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="refresh" content="300">
<title>四腿 forward 模擬金</title>
<style>
*{{margin:0;padding:0;box-sizing:border-box}}
body{{background:#0f1117;color:#e5e7eb;font-family:-apple-system,"PingFang TC","Microsoft JhengHei",sans-serif;padding:14px;max-width:760px;margin:0 auto}}
h1{{font-size:17px;color:#9ca3af;font-weight:600}}
.big{{font-size:40px;font-weight:800;margin:2px 0;letter-spacing:-1px}}
.sub{{color:#9ca3af;font-size:14px;margin-bottom:14px}}
.grid{{display:grid;grid-template-columns:repeat(auto-fill,minmax(105px,1fr));gap:8px;margin:14px 0}}
.card{{background:#171a23;border-radius:10px;padding:10px}}
.cl{{color:#6b7280;font-size:11px}}
.cv{{font-size:16px;font-weight:700;margin-top:2px}}
.cs{{color:#6b7280;font-size:10px;margin-top:1px}}
.card.pos .cv{{color:#4ade80}}.card.neg .cv{{color:#f87171}}
h2{{font-size:14px;color:#9ca3af;margin:20px 0 8px;border-left:3px solid #3b4252;padding-left:8px}}
.chart{{width:100%;height:auto;background:#171a23;border-radius:10px}}
table{{width:100%;border-collapse:collapse;background:#171a23;border-radius:10px;overflow:hidden;font-size:13px}}
th{{color:#6b7280;font-size:11px;text-align:left;padding:8px;border-bottom:1px solid #2a2f3a}}
td{{padding:8px;border-bottom:1px solid #1f2430}}
tr:last-child td{{border:none}}
.pos{{color:#4ade80}}.neg{{color:#f87171}}.sm{{font-size:11px;color:#9ca3af}}
.empty{{background:#171a23;border-radius:10px;padding:24px;text-align:center;color:#6b7280;font-size:13px}}
.note{{background:#1c1f2a;border-radius:10px;padding:12px;font-size:12px;color:#9ca3af;line-height:1.7;margin-top:20px}}
.foot{{color:#4b5563;font-size:11px;text-align:center;margin:18px 0 8px}}
</style></head><body>
<h1>四腿 forward — 合約測試網模擬金</h1>
<div class="big" style="color:{pnl_color}">${equity:,.2f}</div>
<div class="sub">{money(pnl)} ({pnl_pct:+.2f}%) · 更新 {now_tw} 台北 · 每小時更新</div>
{eq_svg}
<div class="grid">{cards}</div>
<h2>回撤</h2>{dd_svg}
<h2>測試網實際部位 ({len(cur.get("positions", []))})</h2>{pos_table}
<h2>四腿帳面 (paper)</h2>{leg_table}
<h2>最近成交</h2>{trade_table}
<div class="note">⚠️ <b>這是水管的數字, 不是策略的數字</b>: 測試網用market單(taker費+合成簿滑價),
損益會系統性差於paper帳面。評估策略看paper; 這頁看的是「下單/對帳有沒有正常運作」和大致盈虧。<br>
誠實預期 Sharpe {C.HONEST["honest_sharpe"]} · 預期MDD {C.HONEST["expected_mdd"]}% ·
預期最長套牢 {C.HONEST["expected_worst_underwater_days"]}天 — 虧損期是預期內的, 判決在12個月。</div>
<div class="foot">paper起算 {C.START_DATE} · 模式 {C.MODE} · 本金基準 ${base:,.0f}</div>
</body></html>"""

    out = os.path.join(ROOT, "docs", "index.html")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"✅ 儀表板已產生: {out}  (權益${equity:,.2f}, 快照{len(eq)}筆)")

if __name__ == "__main__":
    main()
