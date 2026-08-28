# -*- coding: utf-8 -*-
"""手機儀表板產生器 → docs/index.html (GitHub Pages)
★資料全部來自 state_testnet/ (真模擬金) + state/ (paper帳面)
★每小時由 hourly workflow 重建; 頁面本身每5分鐘自動刷新
★純SVG手刻圖表, 零外部依賴, 手機優先
"""
import json, os, sys, math
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

def _dt(ts_iso):
    d = datetime.fromisoformat(ts_iso.replace("Z", "+00:00"))
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


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


def dual_chart(series, w=700, h=220, baseline=0.0, ylabel=""):
    """多條線疊在同一張圖。series = [(label, color, [(xlabel, y), ...]), ...]
       所有線共用 y 軸 (適合都是 % 報酬的對照)。"""
    allpts = [p for _, _, pts in series for p in pts]
    if len(allpts) < 2:
        return f'<div class="empty">資料累積中… (目前 {len(allpts)} 點)</div>'
    ys = [p[1] for p in allpts] + [baseline]
    ymin, ymax = min(ys), max(ys)
    pad = (ymax - ymin) * 0.14 or 0.1
    ymin -= pad; ymax += pad
    # x 軸用「第一條線」的點數當基準 (兩條線點數相近, 各自等距鋪開)
    ml, mb = 8, 22
    def Y(v): return 8 + (h - mb - 8) * (1 - (v - ymin) / (ymax - ymin))
    s = [f'<svg viewBox="0 0 {w} {h}" preserveAspectRatio="none" class="chart">']
    for fr in (0.25, 0.5, 0.75):
        gy = 8 + (h - mb - 8) * fr
        s.append(f'<line x1="{ml}" y1="{gy:.0f}" x2="{w-8}" y2="{gy:.0f}" stroke="#2a2f3a" stroke-width="1"/>')
    if ymin < baseline < ymax:
        by = Y(baseline)
        s.append(f'<line x1="{ml}" y1="{by:.1f}" x2="{w-8}" y2="{by:.1f}" stroke="#888" stroke-dasharray="4,4" stroke-width="1"/>')
    xlabels = None
    for label, color, pts in series:
        if len(pts) < 2: continue
        def X(i, n=len(pts)): return ml + i * (w - ml - 8) / (n - 1)
        path = " ".join(f"{'M' if i==0 else 'L'}{X(i):.1f},{Y(p[1]):.1f}" for i, p in enumerate(pts))
        s.append(f'<path d="{path}" fill="none" stroke="{color}" stroke-width="2.2"/>')
        lx, ly = X(len(pts)-1), Y(pts[-1][1])
        s.append(f'<circle cx="{lx:.1f}" cy="{ly:.1f}" r="3.5" fill="{color}"/>')
        if xlabels is None: xlabels = pts
    n = len(xlabels)
    for i in (0, n // 2, n - 1):
        anchor = "start" if i == 0 else ("end" if i == n-1 else "middle")
        xx = ml + i * (w - ml - 8) / (n - 1)
        s.append(f'<text x="{xx:.0f}" y="{h-6}" fill="#6b7280" font-size="11" text-anchor="{anchor}">{xlabels[i][0]}</text>')
    # 圖例
    lgx = ml + 4
    for label, color, _ in series:
        s.append(f'<rect x="{lgx}" y="8" width="10" height="10" rx="2" fill="{color}"/>')
        s.append(f'<text x="{lgx+14}" y="17" fill="#cbd5e1" font-size="11">{label}</text>')
        lgx += 14 + len(label) * 12 + 16
    if ylabel:
        s.append(f'<text x="{w-10}" y="17" fill="#9ca3af" font-size="12" text-anchor="end" font-weight="600">{ylabel}</text>')
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

    # ---------- 四腿淨曝險 ----------
    # ★為什麼要有這格(2026-08-01): 曲線震盪度是由「四腿抵不抵銷」決定的, 不是由市場波動決定。
    #   實測: 07/17~23 淨曝險≈0(溢價多+T腿多 對上 A腿空+DVOL空) → 組合年化波動 2.91%;
    #         07/23~30 溢價腿翻空, 變成三腿空一腿多, 淨曝險 -0.43 → 波動 5.96% (翻倍),
    #         同期 BTC 日波動 1.33%→1.29% 幾乎沒變 = 不是市場的錯, 是抵銷結構壞了。
    #   這個變數決定了曲線的形狀, 卻是全儀表板唯一看不見的東西 → 補上。
    def _net4(legs_snapshot):
        return sum(C.LEG_WEIGHTS.get(lg, 0.0) * sum((d.get("pos") or {}).values())
                   for lg, d in (legs_snapshot or {}).items())
    net_pts = [(tw(n["ts"], "%m/%d %H:%M" if days < 3 else "%m/%d"),
                round(_net4(n.get("legs")), 4)) for n in navs]
    cur_net = net_pts[-1][1] if net_pts else 0.0
    # |淨曝險| 越大 = 越像單向押注; 越接近0 = 四腿互相對沖
    net_abs30 = [abs(v) for _, v in net_pts[-30:]]
    net_avg = sum(net_abs30) / len(net_abs30) if net_abs30 else 0.0

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
        card("四腿淨曝險", f"{cur_net:+.3f}", f"近30筆|淨| {net_avg:.3f} · 0=完全對沖",
             "neg" if abs(cur_net) > 0.25 else ""),
        card("成交", f"{len(ok_orders)} 筆", f"拒單 {len(rejected)}"),
        card("手續費(taker估)", f"${fee_est:,.2f}", f"{fee_est/base*100:.3f}%"),
        card("運行", f"{days:.1f} 天", f"快照 {len(eq)} 筆"),
        card("對帳", "✅ 通過" if recon_ok else "🔴 異常",
             f"偏差 ${last_recon.get('max_diff_usd', 0):.0f} / 容忍 ${last_recon.get('tolerance_usd', 70):.0f}",
             "" if recon_ok else "neg"),
    ])

    # ---------- 權益圖 + 回撤圖 ----------
    eq_pts = [(tw(e["ts"], "%m/%d %H:%M" if days < 3 else "%m/%d"), e["equity"]) for e in eq]
    eq_svg = line_chart(eq_pts, color="#4ade80" if pnl >= 0 else "#f87171",
                        ylabel=f"${equity:,.2f}", baseline=base)
    # ★對數權益圖: 同一份資料, y軸換成log後再線性內插 → 等比例(%)漲跌在圖上等高。
    #   線性圖上, 早期本金小時的波動會被近期較大的絕對金額壓得看不出來; log視角修正這個。
    eq_pts_log = [(lbl, math.log(v)) for lbl, v in eq_pts if v > 0]
    eq_svg_log = line_chart(eq_pts_log, color="#4ade80" if pnl >= 0 else "#f87171",
                            ylabel=f"${equity:,.2f}",
                            baseline=math.log(base) if base > 0 else None)
    dd_pts, peak2 = [], -1e18
    for e in eq:
        peak2 = max(peak2, e["equity"])
        dd_pts.append((tw(e["ts"], "%m/%d %H:%M" if days < 3 else "%m/%d"),
                       round((e["equity"] / peak2 - 1) * 100, 3)))
    # ★baseline=0: 沒有這條線就看不出「創新高→回撤歸零」發生在哪。
    #   pad 會在上緣留 12% 空白, 所以 0% 不在圖頂; 而歸零常常只有單一格(180點畫700px,
    #   手機上約2px) → 沒有參考線時肉眼完全無法判讀。
    dd_svg = line_chart(dd_pts, h=120, color="#f87171", ylabel=f"{cur_dd:.2f}%",
                        baseline=0.0)
    # 淨曝險走勢: baseline=0 是關鍵參考 —— 貼著0=四腿互相對沖, 離0越遠=越像單向押注
    net_svg = line_chart(net_pts, h=120, color="#a78bfa",
                         ylabel=f"{cur_net:+.3f}", baseline=0.0)

    # ---------- 模擬(paper) vs 實際模擬金(testnet) 對照 ----------
    # ★2026-08-24 修正: 舊版把testnet換算成「相對testnet自己本金的報酬%」(equity/tn_base-1)。
    #   但testnet的美元部位名目是用 C.CAPITAL_USD($10000, 跟paper同一套權重×10000) 訂出來的,
    #   testnet實際本金卻只有 tn_base(~$5000, 約一半) —— 兩邊部位名目同源, 分母卻不同,
    #   導致 tn_r 系統性放大成 pa_r 的 ~1.86倍(實測迴歸 tn_r=1.86·pa_r+0.02, R²=0.997)。
    #   後果: 下面的「追蹤誤差」(tn_r-pa_r) 幾乎完全等於 0.86·pa_r 的縮放版(對pa_r迴歸R²=0.984)
    #   ——長得像獨立的執行誤差線, 其實只是損益曲線被放大後再減自己, 真正的誤差被蓋掉了。
    #   修法: testnet也用【美元損益 / CAPITAL_USD】, 跟paper用同一個分母比較 —— 兩邊才是
    #   「同一套$10000名目部位規則下, 理想成交 vs 真實成交」的公平比較。
    #   修正後 diff 對 pa_r 的迴歸R²從0.984掉到0.608, 標準差從2.17%收斂到0.22%,
    #   這時候的曲線才是看得出來的真實追蹤誤差。
    tn_base = first.get("equity") or base
    tn_ret = [(tw(e["ts"], "%m/%d %H:%M" if days < 3 else "%m/%d"),
               round((e["equity"] - tn_base) / C.CAPITAL_USD * 100, 4)) for e in eq]
    pa_ret = [(tw(n["ts"], "%m/%d %H:%M" if days < 3 else "%m/%d"),
               round((n.get("total_nav", 1.0) - 1) * 100, 4)) for n in navs]
    # ★對照組: 買進持有BTC不動 — 用nav.jsonl本來就有的prices.BTC, 不必抓新資料源。
    #   同一根k棒對齊paper的起點, 兩邊比的才是「同一天開始, 動 vs 不動」。
    btc0 = navs[0].get("prices", {}).get("BTC") if navs else None
    bh_ret = ([(tw(n["ts"], "%m/%d %H:%M" if days < 3 else "%m/%d"),
                round((n.get("prices", {}).get("BTC", btc0) / btc0 - 1) * 100, 4)) for n in navs]
              if btc0 else [])
    cmp_series = [("模擬 paper", "#4ade80", pa_ret),
                  ("實際模擬金 testnet", "#60a5fa", tn_ret)]
    if bh_ret: cmp_series.append(("買進持有BTC", "#f97316", bh_ret))
    cmp_svg = dual_chart(cmp_series, ylabel="累積報酬 %")
    bh_now = bh_ret[-1][1] if bh_ret else None
    pa_now = pa_ret[-1][1] if pa_ret else None
    bh_excess = (pa_now - bh_now) if (bh_now is not None and pa_now is not None) else None
    # 追蹤誤差: 用最近鄰把兩序列對齊, 算 testnet − paper
    import bisect
    nav_ts = [_dt(n["ts"]) for n in navs]
    diffs = []
    for e in eq:
        et = _dt(e["ts"])
        k = bisect.bisect_left(nav_ts, et)
        cand = [j for j in (k-1, k) if 0 <= j < len(navs)]
        if not cand: continue
        j = min(cand, key=lambda j: abs((nav_ts[j] - et).total_seconds()))
        if abs((nav_ts[j] - et).total_seconds()) > 1800: continue
        tn_r = (e["equity"] - tn_base) / C.CAPITAL_USD * 100
        pa_r = (navs[j].get("total_nav", 1.0) - 1) * 100
        diffs.append((tw(e["ts"], "%m/%d %H:%M" if days < 3 else "%m/%d"), round(tn_r - pa_r, 4)))
    cur_diff = diffs[-1][1] if diffs else None
    avg_diff = sum(d[1] for d in diffs) / len(diffs) if diffs else None
    diff_svg = line_chart(diffs, h=120, color="#fbbf24", ylabel=f"{cur_diff:+.3f}%" if cur_diff is not None else "—", baseline=0.0)
    cur_diff_s = f"{cur_diff:+.3f}%" if cur_diff is not None else "—"
    avg_diff_s = f"{avg_diff:+.3f}%" if avg_diff is not None else "—"

    # ---------- 部位表 ----------
    def fmt_px(v):
        """價格智慧格式: 大數千分位, 小數保留有效位 (不要科學記號)"""
        if v >= 1000: return format(v, ",.1f")
        if v >= 1:    return format(v, ",.2f")
        return f"{v:.4f}"

    rows = []
    for p in sorted(cur.get("positions", []), key=lambda x: -x["notional"]):
        side = "多" if p["amt"] > 0 else "空"
        sc = "pos" if p["amt"] > 0 else "neg"
        uc = "pos" if p["upnl"] >= 0 else "neg"
        rows.append(f'<tr><td><b>{p["coin"]}</b></td><td class="{sc}">{side}</td>'
                    f'<td>${p["notional"]:,.0f}</td><td>{fmt_px(p["entry"])}</td>'
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

    # ========== 健康度監控 (機制, 不是績效) ==========
    try:
        sd = json.load(open(os.path.join(ROOT, "state", "signals_diag.json"), encoding="utf-8"))
    except Exception:
        sd = {}

    # ---- 判決時鐘 ----
    months = C.months_running()
    prog = min(months / 18 * 100, 100)
    clock_html = f"""
<div class="clock"><div class="clockbar"><div class="clockfill" style="width:{prog:.1f}%"></div>
<div class="ck" style="left:33.3%"><span>6月</span></div>
<div class="ck" style="left:66.7%"><span>12月</span></div>
<div class="ck" style="left:99.7%"><span>18月</span></div></div>
<div class="sm" style="margin-top:14px">已運行 <b>{months:.2f}</b> 個月 ·
6月=只看有沒有壞掉 · 12月=正式判決 (Sharpe&gt;{C.LAYER3["sharpe_survive"]}續命 / &lt;{C.LAYER3["sharpe_kill"]}處決) · 判準已寫死不可事後修改</div></div>"""

    # ---- 各腿生命徵象卡 ----
    def leg_diag(leg): return (sd.get(leg) or {}).get("diag", {})
    def vital(label, val, ok=True, sub=""):
        cls = "" if ok is None else ("vok" if ok else "vbad")
        return f'<div class="vital {cls}"><span class="vl">{label}</span><span class="vv">{val}</span>{f"<span class=vs>{sub}</span>" if sub else ""}</div>'

    pd_ = leg_diag("premium")
    prem_stds = [v.get("prem_std") for v in pd_.values() if isinstance(v, dict) and v.get("prem_std") is not None]
    prem_std = max(prem_stds) if prem_stds else None
    prem_zs = {k: v.get("z") for k, v in pd_.items() if isinstance(v, dict) and v.get("z") is not None}
    dd_ = leg_diag("dvol")
    ad_ = leg_diag("aleg")
    fng_vals = [v for v in [ad_.get(c, {}).get("fng") if isinstance(ad_.get(c), dict) else None for c in ["BTC"]] if v is not None]
    fng_v = fng_vals[0] if fng_vals else None
    td_ = leg_diag("tleg")
    t_above = sum(1 for v in td_.values() if isinstance(v, dict) and v.get("above"))
    t_total = sum(1 for v in td_.values() if isinstance(v, dict) and "above" in v)

    legs_health = f"""
<div class="hgrid">
<div class="hcard"><div class="ht">🟦 溢價腿 <span class="sm">擇時·非套利</span></div>
{vital("溢價std", f"{prem_std}bp" if prem_std is not None else "—", (prem_std or 0) <= C.LAYER2["premium_std_high"], f"警戒>{C.LAYER2['premium_std_high']}bp·變小=健康")}
{"".join(vital(f"z {k}", f"{v:+.2f}", abs(v or 0) < 1.9, "貼±2=釘住" if abs(v or 0) >= 1.9 else "") for k, v in prem_zs.items())}</div>
<div class="hcard"><div class="ht">🟨 DVOL腿 <span class="sm">恐慌=買</span></div>
{vital("DVOL", dd_.get("dvol", "—"))}
{vital("z", f"{dd_.get('z', 0):+.2f}" if dd_.get("z") is not None else "—", abs(dd_.get("z") or 0) < 1.9)}
{vital("資料", "⚠️降級(備援)" if dd_.get("degraded") else "官方正常", not dd_.get("degraded"))}
{vital("特別條款", "BTC週跌>15%而本腿沒賺→立即處決", None)}</div>
<div class="hcard"><div class="ht">🟩 A腿 <span class="ht_sm"></span><span class="sm">CB×FNG四象限</span></div>
{vital("FNG", f"{fng_v} ({'貪婪' if (fng_v or 0) > C.ALEG['fng_threshold'] else '平淡'})" if fng_v is not None else "—", fng_v is not None, "alternative.me·最脆弱資料源")}
{vital("狀態", "⚠️降級(純CB多空 Sh0.96)" if ad_.get("degraded") else "四象限正常", not ad_.get("degraded"))}</div>
<div class="hcard"><div class="ht">🟥 T腿 <span class="sm">⚠️觀察中(ETF後1.78→0.58)</span></div>
{vital("SMA50上方", f"{t_above}/{t_total} 幣" if t_total else "—", None)}
{vital("判準", "forward持續<0.3→12月砍", None)}</div>
</div>"""

    # ---- 資料源狀態 ----
    feeds = sd.get("datafeed", {})
    KNOWN = [("coinbase_", "Coinbase", "溢價腿+A腿的分子"), ("binance_", "幣安行情", "全部腿的價格基準"),
             ("deribit", "Deribit DVOL", "DVOL腿·有選擇權鏈備援"), ("fng", "FNG (alternative.me)", "A腿·掛了自動降級純CB")]
    feed_rows = []
    for prefix, name, role in KNOWN:
        recs = [v for k, v in feeds.items() if k.startswith(prefix)]
        if not recs:
            feed_rows.append(f'<tr><td>{name}</td><td>—</td><td class="sm">尚無記錄</td><td class="sm">{role}</td></tr>'); continue
        ok = all(r.get("ok") for r in recs)
        ages = [r.get("age_hours") for r in recs if r.get("age_hours") is not None]
        age_s = f"{max(ages):.1f}h前" if ages else "—"
        st = "✅" if ok else "🔴"
        feed_rows.append(f'<tr><td>{name}</td><td>{st}</td><td class="sm">{age_s}</td><td class="sm">{role}</td></tr>')
    feed_table = f'<table><tr><th>來源</th><th>狀態</th><th>最新資料</th><th>角色</th></tr>{"".join(feed_rows)}</table>'

    # ---- 腿間相關性 (30天滾動, 從paper逐時報酬) ----
    LEG_ORDER = ["premium", "dvol", "aleg", "tleg"]
    SHORT = {"premium": "溢價", "dvol": "DVOL", "aleg": "A腿", "tleg": "T腿"}
    series = {l: [] for l in LEG_ORDER}
    for n in navs[-720:]:
        for l in LEG_ORDER:
            series[l].append((n.get("legs", {}).get(l) or {}).get("pnl_pct", 0.0))
    def corr(a, b):
        n = len(a)
        if n < 72: return None
        ma, mb2 = sum(a)/n, sum(b)/n
        va = sum((x-ma)**2 for x in a); vb = sum((x-mb2)**2 for x in b)
        if va <= 0 or vb <= 0: return None
        return sum((a[i]-ma)*(b[i]-mb2) for i in range(n)) / (va*vb) ** 0.5
    corr_cells, max_corr = [], 0.0
    for i, l1 in enumerate(LEG_ORDER):
        row = [f"<td class='sm'><b>{SHORT[l1]}</b></td>"]
        for j, l2 in enumerate(LEG_ORDER):
            if j <= i: row.append("<td></td>"); continue
            c = corr(series[l1], series[l2])
            if c is None: row.append('<td class="sm">…</td>')
            else:
                max_corr = max(max_corr, abs(c))
                cls = "neg" if abs(c) > C.LAYER2["leg_corr_max"] else ("sm" if abs(c) < 0.3 else "")
                row.append(f'<td class="{cls}">{c:+.2f}</td>')
        corr_cells.append("<tr>" + "".join(row) + "</tr>")
    hdr = "".join(f"<th>{SHORT[l]}</th>" for l in LEG_ORDER)
    n_pts = len(series["premium"])
    corr_note = f'需72筆起算 (目前{n_pts}筆)' if n_pts < 72 else f'警戒>{C.LAYER2["leg_corr_max"]} · 回測基準0.002~0.31 · 同時升高=分散失效'
    corr_table = f'<table><tr><th></th>{hdr}</tr>{"".join(corr_cells)}</table><div class="sm" style="margin-top:6px">{corr_note}</div>'

    # ---- 失效模式監控表 ----
    def fm(name, status, detect, cls=""):
        return f'<tr><td><b>{name}</b></td><td class="{cls}">{status}</td><td class="sm">{detect}</td></tr>'
    fng_ok = fng_v is not None
    fail_rows = [
        fm("④ 贏家詛咒", "已確定·已定價", "40+家族挑4條 → 預期打折至Sh1.45, 別看回測2.10"),
        fm("⑤ 資料源消失", "✅ 全部在線" if (fng_ok and not dd_.get("degraded")) else "⚠️ 有降級", "上表資料源狀態 · FNG掛→純CB / DVOL掛→選擇權鏈自算", "" if (fng_ok and not dd_.get("degraded")) else "neg"),
        fm("⑥ 溢價訊號", f"std {prem_std}bp" if prem_std is not None else "—", f"警戒>{C.LAYER2['premium_std_high']}bp (高std=混亂期才危險; 變小=健康, 實測<4bp區Sh1.56)", "" if (prem_std or 0) <= C.LAYER2["premium_std_high"] else "neg"),
        fm("⑦ T腿衰減", "觀察中", "ETF後1.78→0.58 · forward<0.3持續→12月砍"),
        fm("⑧ 腿間相關飆升", f"max {max_corr:.2f}" if n_pts >= 72 else "累積中", f"任一對>{C.LAYER2['leg_corr_max']}=變成同一個賭注", "" if max_corr <= C.LAYER2["leg_corr_max"] else "neg"),
        fm("⑨ 交易所倒閉", "只能防範", "FTX前例·真錢階段: 定期提領獲利, 不押身家"),
        fm("⑩ 我們自己的錯", "✅ 對帳通過" if recon_ok else "🔴 對帳異常", f"帳面vs測試網實際逐幣對帳 · 容忍${last_recon.get('tolerance_usd',70):.0f}(=最小可下單額, 以下修不了)", "" if recon_ok else "neg"),
    ]
    fail_table = f'<table><tr><th>失效模式</th><th>狀態</th><th>偵測/應對</th></tr>{"".join(fail_rows)}</table>'

    # ---------- 最近成交 ----------
    trows = []
    for o in list(reversed(ok_orders))[:15]:
        sc = "pos" if o["side"] == "BUY" else "neg"
        trows.append(f'<tr><td class="sm">{tw(o["ts"])}</td><td><b>{o["coin"]}</b></td>'
                     f'<td class="{sc}">{"買" if o["side"]=="BUY" else "賣"}</td>'
                     f'<td>{o["qty"]}</td><td>${abs(o["qty"]*(o.get("ref_px") or 0)):,.0f}</td></tr>')
    trade_table = (f'<table><tr><th>時間(台)</th><th>幣</th><th>方向</th><th>數量</th><th>名目</th></tr>'
                   f'{"".join(trows)}</table>') if trows else '<div class="empty">尚無成交</div>'

    # 資料新鮮度 (GitHub排程實測只有~60%到達率, 斷幾小時是常態不是故障)
    stale_h = 0.0
    if eq:
        stale_h = (datetime.now(timezone.utc) - datetime.fromisoformat(cur["ts"]).replace(tzinfo=timezone.utc)).total_seconds()/3600
    stale_s = (f' · <span style="color:#fbbf24">資料{stale_h:.1f}小時前</span>' if stale_h > 2.5
               else " · 每小時更新")
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
.chartToggle{{display:flex;gap:6px;margin:10px 0 6px}}
.ctbtn{{background:#171a23;color:#6b7280;border:1px solid #2a2f3a;border-radius:8px;padding:5px 12px;font-size:12px}}
.ctbtn.active{{color:#0f1117;background:#4ade80;border-color:#4ade80;font-weight:700}}
table{{width:100%;border-collapse:collapse;background:#171a23;border-radius:10px;overflow:hidden;font-size:13px}}
th{{color:#6b7280;font-size:11px;text-align:left;padding:8px;border-bottom:1px solid #2a2f3a}}
td{{padding:8px;border-bottom:1px solid #1f2430}}
tr:last-child td{{border:none}}
.pos{{color:#4ade80}}.neg{{color:#f87171}}.sm{{font-size:11px;color:#9ca3af}}
.empty{{background:#171a23;border-radius:10px;padding:24px;text-align:center;color:#6b7280;font-size:13px}}
.note{{background:#1c1f2a;border-radius:10px;padding:12px;font-size:12px;color:#9ca3af;line-height:1.7;margin-top:20px}}
.foot{{color:#4b5563;font-size:11px;text-align:center;margin:18px 0 8px}}
.clock{{background:#171a23;border-radius:10px;padding:16px 12px 10px}}
.clockbar{{position:relative;height:10px;background:#2a2f3a;border-radius:5px}}
.clockfill{{height:100%;background:linear-gradient(90deg,#3b82f6,#8b5cf6);border-radius:5px}}
.ck{{position:absolute;top:-3px;width:2px;height:16px;background:#6b7280}}
.ck span{{position:absolute;top:18px;left:-12px;font-size:10px;color:#6b7280}}
.hgrid{{display:grid;grid-template-columns:repeat(auto-fill,minmax(165px,1fr));gap:8px}}
.hcard{{background:#171a23;border-radius:10px;padding:10px}}
.ht{{font-size:13px;font-weight:700;margin-bottom:6px}}
.vital{{display:flex;justify-content:space-between;align-items:baseline;flex-wrap:wrap;padding:3px 0;border-bottom:1px solid #1f2430;font-size:12px}}
.vital:last-child{{border:none}}
.vl{{color:#6b7280}}
.vv{{font-weight:600}}
.vs{{width:100%;font-size:10px;color:#4b5563}}
.vital.vok .vv{{color:#4ade80}}.vital.vbad .vv{{color:#f87171}}
</style></head><body>
<h1>四腿 forward — 合約測試網模擬金</h1>
<div class="big" style="color:{pnl_color}">${equity:,.2f}</div>
<div class="sub">{money(pnl)} ({pnl_pct:+.2f}%) · 更新 {now_tw} 台北{stale_s}</div>
<div class="chartToggle">
<button type="button" class="ctbtn active" id="ctbtn-lin" onclick="showEqChart('lin')">線性</button>
<button type="button" class="ctbtn" id="ctbtn-log" onclick="showEqChart('log')">對數</button>
</div>
<div id="eqchart-lin">{eq_svg}</div>
<div id="eqchart-log" style="display:none">{eq_svg_log}</div>
<script>
function showEqChart(which){{
  document.getElementById('eqchart-lin').style.display = which==='lin' ? '' : 'none';
  document.getElementById('eqchart-log').style.display = which==='log' ? '' : 'none';
  document.getElementById('ctbtn-lin').classList.toggle('active', which==='lin');
  document.getElementById('ctbtn-log').classList.toggle('active', which==='log');
}}
</script>
<div class="grid">{cards}</div>
<h2>回撤</h2>{dd_svg}
<h2>模擬 vs 實際模擬金 vs 買進持有 <span class="sm">(同軸累積報酬%)</span></h2>
{cmp_svg}
<div class="sub" style="margin:6px 2px 0">綠=模擬paper(完美成交假設) · 藍=實際模擬金testnet(真下單,含滑點與最小下單量卡單) · 橘=買進持有BTC不動(同一天起算)</div>
<div class="sub" style="margin:2px 2px 0">paper {f"{pa_now:+.2f}%" if pa_now is not None else "—"} vs 買進持有 {f"{bh_now:+.2f}%" if bh_now is not None else "—"}
→ 超額 {f"{bh_excess:+.2f}%" if bh_excess is not None else "—"}
<span class="sm">(正值=四腿贏過純抱BTC,負值=不如不動;判決仍在12個月,現階段僅供參考不代表結論)</span></div>
<h3 style="font-size:14px;margin:16px 0 4px">追蹤誤差 <span class="sm">(testnet − paper, 負值=執行成本, 越平穩越好)</span></h3>
{diff_svg}
<div class="sub" style="margin:6px 2px 0">目前 {cur_diff_s} · 平均 {avg_diff_s} · 這就是「理想回測 vs 真實執行」的全部差距</div>
<h2>測試網實際部位 ({len(cur.get("positions", []))})</h2>{pos_table}
<h2>四腿帳面 (paper)</h2>{leg_table}
<h3 style="font-size:14px;margin:16px 0 4px">淨曝險走勢 <span class="sm">(四腿加權淨和 · 貼0=互相對沖, 離0=單向押注)</span></h3>
{net_svg}
<div class="sub" style="margin:6px 2px 0">目前 {cur_net:+.3f} · 近30筆平均|淨| {net_avg:.3f} ·
這條線決定曲線的震盪度: 貼0時四腿互相抵銷, 離0時波動會放大 (實測淨曝險 0→-0.43 時組合波動由 2.91% 翻倍到 5.96%, 而同期BTC波動沒變)</div>
<div style="margin:14px 0"><a href="./odds.html" style="display:block;background:#171a23;border-radius:10px;padding:13px;color:#4ade80;text-decoration:none;font-size:14px">🎚️ <b>勝率拉桿</b> — 拉一下看「持有N天賺錢機率多少」<span style="color:#6b7280;font-size:12px"> ›</span></a></div>
<div style="margin:14px 0"><a href="./history.html" style="display:block;background:#171a23;border-radius:10px;padding:13px;color:#60a5fa;text-decoration:none;font-size:14px">📅 <b>完整歷史</b> — 自選任意區間, 看該期間全部指標與損益圖<span style="color:#6b7280;font-size:12px"> ›</span></a></div>
<h2>⏳ 判決時鐘</h2>{clock_html}
<h2>🩺 各腿生命徵象 <span class="sm">(機制監控, 比績效早發警訊)</span></h2>{legs_health}
<h2>📡 資料源</h2>{feed_table}
<h2>🔗 腿間相關性 (30天)</h2>{corr_table}
<h2>☠️ 失效模式監控</h2>{fail_table}
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
