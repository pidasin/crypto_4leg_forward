# -*- coding: utf-8 -*-
"""每條規則的歷史合理性檢查 — 「這條規則套在歷史上, 會不會殺掉我們認為是好的腿?」

★為什麼需要 (2026-11-25)
  DVOL 特別條款在 2026-11-15 觸發後才發現: 它在回測期間會觸發 8 次, 第一次在資料起點後
  第 22 天 —— 照它, DVOL 腿根本不可能被選進組合。規則旁的註解(「歷史4次崩盤0/4全虧」)
  本身就證明了它會誤殺, 但從 7 月寫下到 11 月觸發, 沒有人拿它去跑歷史。
  std 地板規則(v2.2 推翻)是同一種錯。
  → 用戶要求: 每條規則都檢查, 歷史上不應觸發, 也不應該很靠近。

★判準
  歷史 = 我們據以選入這四條腿的期間 = 對「正常運作的腿」的定義。
  處決類規則在這段期間觸發 → 它會殺掉正常運作的腿 = 誤殺產生器。
  只標記/警示類規則在這段期間常常觸發 → 噪音, 會讓人習慣性忽略或被嚇到。

★兩個必須記得的限制
  1. 歷史是【樣本內】(這四條腿就是在這段期間被挑出來的), 表現比未來樂觀。
     所以「歷史上沒觸發」是必要條件, 不是充分條件 —— 這就是為什麼也要看「多靠近」。
  2. 「從不觸發」也可能代表規則太鬆、什麼都抓不到。本檢查只回答誤殺問題。

★方法: 沿用 decay_study 的資料與重建(參數全讀 config), 淨值扣 maker 2bp, 另報 taker 5bp 敏感度。
  Sharpe 先彙總成日報酬再 ×√365。
"""
import sys, os, json
import numpy as np, pandas as pd
from datetime import datetime, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tools"))
import config as C
import decay_study as DS          # 資料抓取 (dvol_hist 已修正分頁) + to_daily

CUT = "2018-01-01"               # 2017 CB 殘缺
LEGS = ["premium", "dvol", "aleg", "tleg"]
NAME = {"premium": "溢價", "dvol": "DVOL", "aleg": "A腿", "tleg": "T腿"}
MO = 30.44


# ---------- 重建: 回傳 (日淨報酬@2bp, 日淨報酬@5bp, 部位DataFrame) ----------
def _net(pos, px, fee):
    return (pos * px.pct_change().shift(-1) - pos.diff().abs().fillna(0) * fee / 1e4)

def build_premium(data):
    p = C.PREMIUM; r2 = r5 = None; P = {}; STD = {}
    for c in p["coins"]:
        df = pd.DataFrame({"bn": data[f"bn1h_{c}"], "cb": data[f"cb1h_{c}"]}).dropna()
        prem = (df["cb"] / df["bn"] - 1) * 10000
        sm = prem.rolling(p["smooth_hours"]).mean()
        z = (sm - sm.rolling(p["z_window"]).mean()) / sm.rolling(p["z_window"]).std()
        pos = np.clip(z, -p["clip"], p["clip"]) / 2 * 0.5
        a, b = _net(pos, df["bn"], 2.0), _net(pos, df["bn"], 5.0)
        r2 = a if r2 is None else r2.add(a, fill_value=0)
        r5 = b if r5 is None else r5.add(b, fill_value=0)
        P[c] = pos; STD[c] = prem.rolling(p["z_window"]).std()
    return r2.dropna(), r5.dropna(), pd.DataFrame(P), pd.DataFrame(STD)

def build_dvol(data):
    p = C.DVOL
    dv, px = data["dvol"], data[f"bn1h_{p['coin']}"]
    idx = dv.index.intersection(px.index); dv, px = dv.loc[idx], px.loc[idx]
    z = (dv - dv.rolling(p["z_window"]).mean()) / dv.rolling(p["z_window"]).std()
    pos = np.clip(z, -p["clip"], p["clip"]) / 2
    return _net(pos, px, 2.0).dropna(), _net(pos, px, 5.0).dropna(), pd.DataFrame({p["coin"]: pos})

def build_aleg(data, fng):
    p = C.ALEG; r2 = r5 = None; P = {}
    for c in p["coins"]:
        b, k = data.get(f"bn1d_{c}"), data.get(f"cb1d_{c}")
        if b is None or k is None: continue
        df = pd.DataFrame({"bn": b, "cb": k}).dropna()
        prem = ((df["cb"] / df["bn"] - 1) * 10000).rolling(p["prem_ma_days"]).mean()
        f = fng.reindex(df.index.normalize(), method="ffill"); f.index = df.index
        cl, gr = prem > 0, f > p["fng_threshold"]
        v = pd.Series(np.where(cl & gr, 1.0, np.where(cl & ~gr, 0.5, np.where(~cl & gr, -0.5, -1.0))), index=df.index)
        v[prem.isna() | f.isna()] = np.nan
        pos = v / len(p["coins"])
        a, bb = _net(pos, df["bn"], 2.0), _net(pos, df["bn"], 5.0)
        r2 = a if r2 is None else r2.add(a, fill_value=0)
        r5 = bb if r5 is None else r5.add(bb, fill_value=0)
        P[c] = pos
    return r2.dropna(), r5.dropna(), pd.DataFrame(P)

def build_tleg(data):
    p = C.TLEG; r2 = r5 = None; P = {}
    for c in p["coins"]:
        px = data.get(f"bn1d_{c}")
        if px is None: continue
        ma = px.rolling(p["ma_days"]).mean()
        pos = (px > ma).astype(float) / len(p["coins"]); pos[ma.isna()] = np.nan
        a, b = _net(pos, px, 2.0), _net(pos, px, 5.0)
        r2 = a if r2 is None else r2.add(a, fill_value=0)
        r5 = b if r5 is None else r5.add(b, fill_value=0)
        P[c] = pos
    return r2.dropna(), r5.dropna(), pd.DataFrame(P)


# ---------- 規則檢查 ----------
def rolling_sharpe(d, months):
    """每一個可能的起始日, 往後 N 個月的 Sharpe (= 若判決時鐘從那天起算, 檢查點會看到的值)"""
    w = int(round(months * MO))
    if len(d) < w + 30: return None
    m = d.rolling(w).mean(); s = d.rolling(w).std()
    out = (m / s * np.sqrt(365)).dropna()
    out.index = out.index - pd.Timedelta(days=w - 1)          # 標成「起始日」
    return out

def judge_row(label, d, months, kill, survive):
    rs = rolling_sharpe(d, months)
    if rs is None:
        return dict(rule=label, ok=None, note=f"資料不足 {months} 個月")
    worst = float(rs.min()); worst_at = rs.idxmin().date().isoformat()
    return dict(rule=label, months=months, n_starts=int(len(rs)), worst=worst, worst_start=worst_at,
                p5=float(rs.quantile(.05)), frac_kill=float((rs < kill).mean()),
                frac_gray=float(((rs >= kill) & (rs <= survive)).mean()),
                frac_near=float(((rs >= kill) & (rs < kill + 0.3)).mean()),
                margin=worst - kill, ok=bool(rs.min() >= kill))

def frozen_runs(P, freq):
    """部位【整組】完全不變的最長連續期間 (與 book.frozen_days 同口徑: 四捨五入到6位)"""
    X = P.dropna(how="all").round(6).fillna(0.0)
    if freq == "D": X = X.resample("D").last().dropna(how="all")
    key = X.astype(str).agg("|".join, axis=1)
    ch = key != key.shift()
    grp = ch.cumsum()
    runs = X.index.to_series().groupby(grp.values).agg(["first", "last"])
    runs["days"] = (runs["last"] - runs["first"]).dt.total_seconds() / 86400
    runs["state"] = [key.loc[a] for a in runs["first"]]
    return runs.sort_values("days", ascending=False)


def fmt(v, pct=False, sign=True):
    if v is None: return "—"
    return (f"{v*100:.1f}%" if pct else (f"{v:+.2f}" if sign else f"{v:.2f}"))


if __name__ == "__main__":
    print(f"=== 每條規則的歷史合理性檢查 @ {DS.NOW.isoformat()} ===", flush=True)
    data = {}
    for c in C.PREMIUM["coins"]:
        data[f"bn1h_{c}"] = DS.bn(C.PREMIUM["bn_symbols"][c], "1h", datetime(2017, 9, 1, tzinfo=timezone.utc))
        data[f"cb1h_{c}"] = DS.cb(C.PREMIUM["cb_products"][c], 3600, datetime(2017, 9, 1, tzinfo=timezone.utc))
    for c in sorted(set(C.ALEG["coins"]) | set(C.TLEG["coins"])):
        data[f"bn1d_{c}"] = DS.bn(c + "USDT", "1d", datetime(2017, 8, 1, tzinfo=timezone.utc))
        data[f"cb1d_{c}"] = DS.cb(c + "-USD", 86400, datetime(2017, 8, 1, tzinfo=timezone.utc))
    data["dvol"] = DS.dvol_hist(C.DVOL["currency"])
    fng = DS.fng_hist()
    print(f"  資料: DVOL {len(data['dvol'])} 點(缺口檢查已通過)  FNG {len(fng)} 天", flush=True)

    B = {}
    r2, r5, P, STD = build_premium(data); B["premium"] = (r2.loc[CUT:], r5.loc[CUT:], P.loc[CUT:]); PSTD = STD.loc[CUT:]
    B["dvol"] = build_dvol(data)
    r2, r5, P = build_aleg(data, fng); B["aleg"] = (r2.loc[CUT:], r5.loc[CUT:], P.loc[CUT:])
    B["tleg"] = build_tleg(data)
    D2 = {l: DS.to_daily(B[l][0]) for l in LEGS}
    D5 = {l: DS.to_daily(B[l][1]) for l in LEGS}
    for l in LEGS:
        d = D2[l]
        print(f"  {NAME[l]}: {d.index[0].date()} → {d.index[-1].date()} ({len(d)/365.25:.1f}年)  "
              f"淨Sharpe@2bp {d.mean()/d.std()*np.sqrt(365):+.2f}  @5bp {D5[l].mean()/D5[l].std()*np.sqrt(365):+.2f}")
    combo2 = pd.concat(D2, axis=1).dropna().mean(axis=1)
    combo5 = pd.concat(D5, axis=1).dropna().mean(axis=1)
    print(f"  組合(四腿共同期間): {combo2.index[0].date()} → {combo2.index[-1].date()}  "
          f"Sharpe@2bp {combo2.mean()/combo2.std()*np.sqrt(365):+.2f}  @5bp {combo5.mean()/combo5.std()*np.sqrt(365):+.2f}")

    L3 = C.LAYER3; KILL, SURV = L3["sharpe_kill"], L3["sharpe_survive"]
    rows = []

    print("\n" + "=" * 110)
    print(f"【A】績效判決 —— 若判決時鐘從歷史上【任何一天】起算, 檢查點看到的 Sharpe 會不會低於處決線 {KILL}?")
    print("=" * 110)
    print(f"  {'規則':30} {'成本':>4} {'起算日數':>7} {'最差':>7} {'最差起算日':>11} {'5%分位':>7} "
          f"{'<處決線':>8} {'離線<0.3':>8} {'灰色區':>7}  判定")
    plan = [(f"36個月 逐腿處決 ☠️", 36, "leg"), (f"24個月 組合處決 ☠️", 24, "combo"),
            (f"24個月 逐腿標記 🔻", 24, "leg"), (f"12個月 逐腿標記 🔻", 12, "leg")]
    for label, mo, kind in plan:
        targets = [("組合", combo2, combo5)] if kind == "combo" else [(NAME[l], D2[l], D5[l]) for l in LEGS]
        for nm, d2, d5 in targets:
            for cost, d in (("2bp", d2), ("5bp", d5)):
                r = judge_row(f"{label} · {nm}", d, mo, KILL, SURV); r["cost"] = cost; rows.append(r)
                if r.get("ok") is None:
                    print(f"  {r['rule']:30} {cost:>4} {r['note']}"); continue
                tag = ("❌ 會誤殺" if not r["ok"] else ("⚠️ 很靠近" if r["frac_near"] > 0 else "✅"))
                if "標記" in label and not r["ok"]: tag = "⚠️ 會誤標"
                print(f"  {r['rule']:30} {cost:>4} {r['n_starts']:7d} {r['worst']:+7.2f} {r['worst_start']:>11} "
                      f"{r['p5']:+7.2f} {fmt(r['frac_kill'],True):>8} {fmt(r['frac_near'],True):>8} "
                      f"{fmt(r['frac_gray'],True):>7}  {tag}")

    print("\n" + "=" * 110)
    print(f"【B】部位凍結 ≥ {L3['mech_frozen_days']} 天 —— 歷史上最長「部位完全不動」的期間")
    print("=" * 110)
    for l, freq in (("premium", "H"), ("dvol", "H"), ("aleg", "D"), ("tleg", "D")):
        runs = frozen_runs(B[l][2], freq)
        top = runs.head(3)
        hit = runs[runs.days >= L3["mech_frozen_days"]]
        lvl = "可處決" if l in ("premium", "dvol") else "只標記"
        print(f"  {NAME[l]}({lvl}): 最長 {top.days.iloc[0]:.0f} 天 ({top['first'].iloc[0].date()}→{top['last'].iloc[0].date()})  "
              f"≥{L3['mech_frozen_days']}天的次數 {len(hit)}  ≥60天 {int((runs.days>=60).sum())}  "
              f"{'❌' if len(hit) else ('⚠️ 很靠近' if top.days.iloc[0] >= 60 else '✅')}")
        for _, rr in top.iterrows():
            st = rr["state"]; st = st if len(st) < 70 else st[:67] + "..."
            print(f"      {rr['days']:6.0f} 天  {rr['first'].date()} → {rr['last'].date()}  部位={st}")
        rows.append(dict(rule=f"部位凍結 · {NAME[l]}", worst=float(top.days.iloc[0]), hits=int(len(hit)),
                         ok=bool(len(hit) == 0)))

    print("\n" + "=" * 110)
    print(f"【C】DVOL 特別條款 (v3 刪除前的最後紀錄): BTC 7日 ≤ {L3.get('dvol_crisis_btc_drop')} 而 DVOL腿當週沒賺")
    print("=" * 110)
    dvn = (1 + DS.to_daily(B["dvol"][0])).cumprod()
    btc = data[f"bn1h_{C.DVOL['coin']}"].resample("D").last()
    w = btc.pct_change(7); ev, prev = [], False
    for ts, v in w.items():
        h = bool(v <= -0.15) if pd.notna(v) else False
        if h and not prev and ts in dvn.index and (ts - pd.Timedelta(days=7)) in dvn.index:
            ev.append((ts.date(), float(v), float(dvn[ts] / dvn[ts - pd.Timedelta(days=7)] - 1)))
        prev = h
    fails = [e for e in ev if e[2] <= 0]
    print(f"  事件 {len(ev)} 次, 會觸發處決 {len(fails)} 次, 第一次 {fails[0][0] if fails else '—'}  → ❌ (v3 刪除)")
    rows.append(dict(rule="DVOL特別條款", events=len(ev), hits=len(fails), ok=not fails))

    print("\n" + "=" * 110)
    print(f"【D】第二層警示 (每月1號檢查, 只發 WARN/INFO 不處決) —— 歷史上的誤報率")
    print("=" * 110)
    ms = PSTD.resample("MS").first().dropna(how="all")
    hi = (ms > C.LAYER2["premium_std_high"]).any(axis=1)
    for since in ("2018", "2021", "2023"):
        h = hi.loc[since:]
        print(f"  溢價std > {C.LAYER2['premium_std_high']}bp: {since}年起 {int(h.sum())}/{len(h)} 個月觸發 ({h.mean()*100:.0f}%)")
    rows.append(dict(rule="溢價std>12bp WARN", frac_2018=float(hi.mean()), frac_2021=float(hi.loc['2021':].mean()),
                     frac_2023=float(hi.loc['2023':].mean())))
    J = pd.concat(D2, axis=1).dropna()
    for win in (90, 180, 365):
        bad_abs = bad_pos = n = 0
        for end in J.index[win::30]:
            cm = J.loc[end - pd.Timedelta(days=win):end].corr(); n += 1
            vals = [cm.loc[a, b] for i, a in enumerate(LEGS) for b in LEGS[i+1:]]
            bad_abs += any(abs(v) > C.LAYER2["leg_corr_max"] for v in vals)
            bad_pos += any(v > C.LAYER2["leg_corr_max"] for v in vals)
        print(f"  腿間相關 (近{win}天視窗): |相關|>{C.LAYER2['leg_corr_max']} 觸發 {bad_abs}/{n} ({bad_abs/n*100:.0f}%)   "
              f"只看正相關>{C.LAYER2['leg_corr_max']} {bad_pos}/{n} ({bad_pos/n*100:.0f}%)")
        rows.append(dict(rule=f"腿間相關 WARN {win}d", frac_abs=bad_abs / n, frac_pos=bad_pos / n))
    cap = {"premium": 0.5, "dvol": 1.0, "aleg": 1 / 8, "tleg": 1 / 3}
    for l in LEGS:
        P = B[l][2].resample("MS").first().dropna(how="all")
        pin = (P.abs().max(axis=1) / cap[l] > C.LAYER2["exposure_pinned"])
        print(f"  曝險貼上限 INFO · {NAME[l]}: {int(pin.sum())}/{len(pin)} 個月觸發 ({pin.mean()*100:.0f}%)")
        rows.append(dict(rule=f"曝險貼上限 · {NAME[l]}", frac=float(pin.mean())))

    out = os.path.join(ROOT, "data", "rule_history_check.json")
    with open(out, "w", encoding="utf-8") as f:
        json.dump(dict(ts=DS.NOW.isoformat(), rows=rows), f, ensure_ascii=False, indent=1, default=str)
    print(f"\n✅ 結果已存 {out}")
