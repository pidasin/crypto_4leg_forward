# -*- coding: utf-8 -*-
"""溢價腿 std 分層研究 — 裁決 config 裡那條自相矛盾的規則

★為什麼要有這支程式 (2026-11-03)
  config.LAYER3 與 config.LAYER2 對「溢價 std 變小」給出完全相反的判定:
    LAYER3: mech_premium_std_floor = 4.0  → std ≤4bp = 物理極限(≈2×成本) → 處決
    LAYER2: 「std變小不是問題(<4bp區間Sharpe 1.56), 危險的是變大」
  兩者不可能同時為真。在用資料裁決之前, health/checks.py 的機制層對這條只標記不處決。
  本研究的唯一目的: 給出可稽核的答案, 讓那條規則定下來。

★先釐清一個概念問題 (這可能就是矛盾的來源)
  「std ≤ 2×成本 → 物理極限」是【套利】的邏輯: 你賺的是價差本身, 價差小於成本就沒得賺。
  但溢價腿是【擇時】訊號 (儀表板自己標的就是「擇時·非套利」): 它不捕捉溢價價差,
  而是用溢價 z 分數預測 BTC/ETH 的【方向】, 損益來自幣價波動, 不是來自溢價波動。
  若如此, 溢價 std 就不構成獲利上限, 只影響訊號雜訊比 —— 那是實證問題, 不是物理極限。
  本程式用 H3 直接檢驗這點。

★方法學 (遵循本專案既有鐵律)
  - 無未來函數: 第 t 小時持有的部位, 只用到 t-1 收盤為止的資料
  - 分層用的 std 也必須是 t-1 當下【已知】的滾動 std (與策略本身用的同一個)
  - 淨報酬: maker 2bp 與 taker 5bp 都算 (「edge < 成本」的主張必須用淨值檢驗)
  - Sharpe 先彙總成【日報酬】再 ×√365 (記憶: 對小時級報酬直接年化會差 √24 = 4.9 倍)
  - 區塊 bootstrap (block=20日) 給信賴區間, 不用常態假設 (報酬有肥尾與自相關)
  - 2017 年資料殘缺 (覆蓋率37%, std 196bp) → 一律從 2018 起算
"""
import sys, os, time, json
import numpy as np, pandas as pd
from datetime import datetime, timezone, timedelta

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import config as C

try:
    import requests
except ImportError:
    sys.exit("需要 requests")

START = datetime(2017, 9, 1, tzinfo=timezone.utc)
CUT = "2018-01-01"            # 2017 殘缺, 不可用
COINS = ["BTC", "ETH"]
rng = np.random.default_rng(20261103)


def _get(url, params, tries=4):
    for i in range(tries):
        try:
            r = requests.get(url, params=params, timeout=30)
            if r.status_code == 200:
                return r.json()
        except Exception:
            pass
        time.sleep(1.5 * (i + 1))
    return None


def binance_hourly(sym):
    out, st = [], int(START.timestamp() * 1000)
    while True:
        r = _get("https://data-api.binance.vision/api/v3/klines",
                 dict(symbol=sym, interval="1h", startTime=st, limit=1000))
        if not r:
            break
        out += r
        st = r[-1][0] + 3600_000
        if len(r) < 1000:
            break
    df = pd.DataFrame(out).iloc[:, :5]
    df.columns = list("tohlc")
    df["t"] = pd.to_datetime(df["t"], unit="ms")
    return df.drop_duplicates("t").set_index("t")["c"].astype(float)


def coinbase_hourly(product):
    """★分頁 295 根/次 (Coinbase 單次上限 300)"""
    out, cur, now = [], START, datetime.now(timezone.utc)
    while cur < now:
        seg = min(cur + timedelta(hours=295), now)
        r = _get("https://api.exchange.coinbase.com/products/%s/candles" % product,
                 dict(granularity=3600, start=cur.isoformat(), end=seg.isoformat()))
        if isinstance(r, list):
            out += r
        cur = seg
        time.sleep(0.16)
    df = pd.DataFrame(out, columns=["t", "l", "h", "o", "c", "v"])
    df["t"] = pd.to_datetime(df["t"], unit="s")
    return df.drop_duplicates("t").sort_values("t").set_index("t")["c"].astype(float)


def load():
    D = {}
    for c in COINS:
        D[f"bn_{c}"] = binance_hourly(C.PREMIUM["bn_symbols"][c])
        print(f"  幣安 {c}: {len(D[f'bn_{c}'])} 根  {D[f'bn_{c}'].index[0]} → {D[f'bn_{c}'].index[-1]}", flush=True)
        D[f"cb_{c}"] = coinbase_hourly(C.PREMIUM["cb_products"][c])
        print(f"  CB   {c}: {len(D[f'cb_{c}'])} 根  {D[f'cb_{c}'].index[0]} → {D[f'cb_{c}'].index[-1]}", flush=True)
    return pd.DataFrame(D)


def build(H):
    """完全照 legs/signals.py premium_leg() 的規則重建, 參數一律讀 config"""
    p = C.PREMIUM
    out = {}
    for c in COINS:
        df = pd.DataFrame({"bn": H[f"bn_{c}"], "cb": H[f"cb_{c}"]}).dropna()   # inner join, 無 ffill
        prem = (df["cb"] / df["bn"] - 1) * 10000
        sm = prem.rolling(p["smooth_hours"]).mean()
        mu = sm.rolling(p["z_window"]).mean()
        sd = sm.rolling(p["z_window"]).std()
        z = (sm - mu) / sd
        out[c] = pd.DataFrame({
            "px": df["bn"],
            "prem": prem,
            # ★分層用的 std: 原始溢價的滾動 std —— 與 signals.py diag 的 prem_std 同定義
            "std": prem.rolling(p["z_window"]).std(),
            "z": z,
            "pos": np.clip(z, -p["clip"], p["clip"]) / 2 * 0.5,
        })
    return out


def _daily(h):
    """小時級 → 日報酬, 並丟掉「完全不在本區間」的日子 (那些被遮罩成 0)

    ★2026-11-03 合成測試抓到的bug: 原本只有 daily_sharpe 丟零日, block_boot 沒丟
    → 兩者算的是【不同序列】, 導致點估計 Sharpe +6.10 卻落在自己的 CI (+1.59~+2.63) 之外。
      分層分析一定要兩邊用同一個轉換, 否則信賴區間會系統性灌水/縮水, 判決就錯了。
    """
    d = h.resample("D").sum().dropna()
    return d[d != 0] if (d != 0).sum() > 0 else d


def daily_sharpe(h):
    """★小時級 → 日 → ×√365。直接對小時報酬年化會差 √24 = 4.9 倍"""
    d = _daily(h)
    if len(d) < 20 or d.std() == 0:
        return None, None, len(d)
    return float(d.mean() / d.std() * np.sqrt(365)), float(d.mean() * 365), len(d)


def block_boot(h, n=600, block=20):
    """區塊 bootstrap (日報酬, 20日一塊) → Sharpe 的 5~95 分位
    ★必須與 daily_sharpe 走同一個 _daily(), 否則 CI 與點估計對不起來
    """
    d = _daily(h)
    if len(d) < block * 3:
        return None, None
    v = d.values
    nb = max(1, len(v) // block)
    sh = []
    for _ in range(n):
        idx = rng.integers(0, len(v) - block, nb)
        s = np.concatenate([v[i:i + block] for i in idx])
        if s.std() > 0:
            sh.append(s.mean() / s.std() * np.sqrt(365))
    if not sh:
        return None, None
    return float(np.percentile(sh, 5)), float(np.percentile(sh, 95))


BUCKETS = [(0, 3), (3, 4), (4, 6), (6, 8), (8, 12), (12, 20), (20, 1e9)]


def study(S):
    floor = C.LAYER3["mech_premium_std_floor"]
    print("\n" + "=" * 100)
    print("【H1】各 std 區間, 溢價腿的淨報酬表現 (逐幣, 每幣是該腿的一半)")
    print("=" * 100)
    rows = []
    for c in COINS:
        d = S[c].loc[CUT:].dropna(subset=["pos", "std"])
        ret = d["px"].pct_change().shift(-1)            # 第t根的訊號 → 吃 t→t+1 的報酬
        pos = d["pos"]
        turn = pos.diff().abs().fillna(0)
        gross = pos * ret
        print(f"\n--- {c}  ({len(d)} 小時, {d.index[0].date()} → {d.index[-1].date()}, "
              f"std 中位數 {d['std'].median():.2f}bp) ---")
        print(f"{'std區間':>12} {'佔時間':>7} {'日數':>6} {'毛Sh':>7} {'淨Sh@2bp':>9} {'淨Sh@5bp':>9} "
              f"{'淨年報酬':>9} {'成本佔毛':>9} {'|z|>1.9':>8} {'5~95%CI':>16}")
        for lo, hi in BUCKETS:
            m = (d["std"] >= lo) & (d["std"] < hi)
            if m.sum() < 24 * 30:                        # 不足30天不報
                print(f"{f'{lo}-{hi if hi<1e9 else 999}bp':>12} {m.mean()*100:6.1f}% "
                      f"{'樣本不足 (<30天)':>40}")
                continue
            g = (gross * m).fillna(0)
            n2 = (gross - turn * 2 / 1e4) * m
            n5 = (gross - turn * 5 / 1e4) * m
            sg, _, _ = daily_sharpe(g.fillna(0))
            s2, r2, nd = daily_sharpe(n2.fillna(0))
            s5, _, _ = daily_sharpe(n5.fillna(0))
            lo_ci, hi_ci = block_boot(n2.fillna(0))
            cost = (turn * m).sum() * 2 / 1e4
            gsum = abs(g.sum())
            pinned = (d.loc[m, "z"].abs() > 1.9).mean()
            rows.append(dict(coin=c, lo=lo, hi=hi, frac=float(m.mean()), days=nd,
                             sh_gross=sg, sh2=s2, sh5=s5, ret2=r2,
                             cost_ratio=(cost / gsum if gsum > 0 else None), pinned=float(pinned)))
            f = lambda v: "—" if v is None else f"{v:+.2f}"
            print(f"{f'{lo}-{hi if hi<1e9 else 999}bp':>12} {m.mean()*100:6.1f}% {nd:6d} "
                  f"{f(sg):>7} {f(s2):>9} {f(s5):>9} "
                  f"{('—' if r2 is None else f'{r2*100:+.1f}%'):>9} "
                  f"{('—' if gsum<=0 else f'{cost/gsum*100:.0f}%'):>9} "
                  f"{pinned*100:7.0f}% "
                  f"{('—' if lo_ci is None else f'{lo_ci:+.2f}~{hi_ci:+.2f}'):>16}")

    print("\n" + "=" * 100)
    print(f"【H2】地板檢驗: std ≤ {floor}bp vs > {floor}bp  (直接對應 config 那條處決線)")
    print("=" * 100)
    for c in COINS:
        d = S[c].loc[CUT:].dropna(subset=["pos", "std"])
        ret = d["px"].pct_change().shift(-1)
        pos, turn = d["pos"], d["pos"].diff().abs().fillna(0)
        for lab, m in [(f"≤{floor}bp (config說該處決)", d["std"] <= floor),
                       (f">{floor}bp", d["std"] > floor)]:
            if m.sum() < 24 * 30:
                print(f"  {c} {lab:28} 樣本不足 ({m.sum()/24:.0f}天)")
                continue
            n2 = ((pos * ret - turn * 2 / 1e4) * m).fillna(0)
            s, r, nd = daily_sharpe(n2)
            lo_ci, hi_ci = block_boot(n2)
            print(f"  {c} {lab:28} 佔時間{m.mean()*100:5.1f}%  {nd:4d}天  "
                  f"淨Sharpe@2bp {('—' if s is None else f'{s:+.2f}')}  "
                  f"年報酬 {('—' if r is None else f'{r*100:+.1f}%')}  "
                  f"CI {('—' if lo_ci is None else f'{lo_ci:+.2f}~{hi_ci:+.2f}')}")

    print("\n" + "=" * 100)
    print("【H3】概念檢驗: 損益是否正比於溢價 std? (套利=是 → std是獲利上限; 擇時=否)")
    print("=" * 100)
    print("  做法: 每個 std 區間算『每單位部位的毛報酬』(= 毛報酬 / 平均|部位|)。")
    print("        若是套利邏輯, 低 std 區間的單位毛報酬應該同比例萎縮到成本以下。")
    for c in COINS:
        d = S[c].loc[CUT:].dropna(subset=["pos", "std"])
        ret = d["px"].pct_change().shift(-1)
        print(f"\n  --- {c} ---")
        print(f"  {'std區間':>12} {'平均std':>8} {'單位毛報酬(年化)':>18} {'幣價年化波動':>13} {'z對後續報酬相關':>16} {'t值':>7}")
        for lo, hi in BUCKETS:
            m = (d["std"] >= lo) & (d["std"] < hi)
            if m.sum() < 24 * 30:
                continue
            ap = d.loc[m, "pos"].abs().mean()
            gr = (d.loc[m, "pos"] * ret[m]).sum() / max(m.sum(), 1) * 8760
            unit = gr / ap if ap > 0 else np.nan
            vol = ret[m].std() * np.sqrt(8760)
            zz, rr = d.loc[m, "z"], ret[m]
            ok = zz.notna() & rr.notna()
            corr = zz[ok].corr(rr[ok]) if ok.sum() > 100 else np.nan
            tv = corr * np.sqrt(ok.sum() - 2) / np.sqrt(1 - corr ** 2) if np.isfinite(corr) else np.nan
            print(f"  {f'{lo}-{hi if hi<1e9 else 999}bp':>12} {d.loc[m,'std'].mean():7.2f}bp "
                  f"{unit*100:17.1f}% {vol*100:12.1f}% {corr:16.4f} {tv:7.2f}")

    print("\n" + "=" * 100)
    print("【H4】組合層 (BTC+ETH 合成完整溢價腿), 用 min(兩幣std) 分層")
    print("      ← 這是 health/checks.py 機制層實際使用的分層方式")
    print("=" * 100)
    idx = S["BTC"].index.intersection(S["ETH"].index)
    pos = sum(S[c].loc[idx, "pos"] for c in COINS)
    ret = sum(S[c].loc[idx, "pos"] * S[c].loc[idx, "px"].pct_change().shift(-1) for c in COINS)
    turn = sum(S[c].loc[idx, "pos"].diff().abs().fillna(0) for c in COINS)
    smin = pd.concat([S[c].loc[idx, "std"] for c in COINS], axis=1).min(axis=1)
    mask_all = (smin.notna()) & (idx >= pd.Timestamp(CUT))
    n2all = (ret - turn * 2 / 1e4)[mask_all].fillna(0)
    s, r, nd = daily_sharpe(n2all)
    print(f"  全期 (2018起): 淨Sharpe@2bp {s:+.2f}  年報酬 {r*100:+.1f}%  {nd}天")
    print(f"\n  {'min(std)區間':>14} {'佔時間':>7} {'日數':>6} {'淨Sh@2bp':>9} {'年報酬':>9} {'5~95%CI':>16}")
    for lo, hi in BUCKETS:
        m = mask_all & (smin >= lo) & (smin < hi)
        if m.sum() < 24 * 30:
            continue
        n2 = ((ret - turn * 2 / 1e4) * m).fillna(0)
        s, r, nd = daily_sharpe(n2)
        lo_ci, hi_ci = block_boot(n2)
        print(f"  {f'{lo}-{hi if hi<1e9 else 999}bp':>14} {m.mean()*100:6.1f}% {nd:6d} "
              f"{('—' if s is None else f'{s:+.2f}'):>9} "
              f"{('—' if r is None else f'{r*100:+.1f}%'):>9} "
              f"{('—' if lo_ci is None else f'{lo_ci:+.2f}~{hi_ci:+.2f}'):>16}")

    print("\n" + "=" * 100)
    print("【H5】std 的時間趨勢 — 它是否正在單調走向地板?")
    print("=" * 100)
    for c in COINS:
        s = S[c]["std"].loc[CUT:].dropna()
        y = s.resample("YE").agg(["mean", "median", "min"])
        print(f"\n  {c}: " + "  ".join(f"{k.year}={v['mean']:.1f}" for k, v in y.iterrows()))
        print(f"      目前 {s.iloc[-1]:.2f}bp · 近90天 min {s.tail(2160).min():.2f} / "
              f"max {s.tail(2160).max():.2f} · 全期 ≤{floor}bp 佔 {(s<=floor).mean()*100:.1f}%")

    print("\n" + "=" * 100)
    print(f"【H6】★反事實: 若這條規則真的接成「連續30天 min(std) ≤ {floor}bp → 處決」, 歷史上何時會殺掉溢價腿?")
    print("=" * 100)
    idx = S["BTC"].index.intersection(S["ETH"].index)
    smin = pd.concat([S[c].loc[idx, "std"] for c in COINS], axis=1).min(axis=1)
    smin = smin.loc[CUT:].dropna()
    daily = smin.resample("D").median().dropna()      # 與 book.premium_std_floor_days 同口徑
    below = (daily <= floor).astype(int)
    # 找出所有連續 ≥30 天的區段
    runs, start = [], None
    for ts, v in below.items():
        if v and start is None:
            start = ts
        elif not v and start is not None:
            if (ts - start).days >= 30:
                runs.append((start, ts))
            start = None
    if start is not None and (daily.index[-1] - start).days >= 30:
        runs.append((start, daily.index[-1]))
    if not runs:
        print("  從未連續30天在地板下 → 這條規則歷史上不會觸發")
    else:
        print(f"  ⚠️ 共 {len(runs)} 次會觸發處決。第一次就會殺掉溢價腿, 之後的報酬全部拿不到:")
        first = runs[0][0]
        for a, b in runs[:8]:
            print(f"     {a.date()} → {b.date()}  ({(b-a).days} 天)")
        if len(runs) > 8:
            print(f"     ... 另有 {len(runs)-8} 段")
        # 被砍之後, 這條腿實際上還賺了多少?
        pos = sum(S[c].loc[idx, "pos"] for c in COINS)
        ret = sum(S[c].loc[idx, "pos"] * S[c].loc[idx, "px"].pct_change().shift(-1) for c in COINS)
        turn = sum(S[c].loc[idx, "pos"].diff().abs().fillna(0) for c in COINS)
        net = (ret - turn * 2 / 1e4).fillna(0)
        kill_ts = pd.Timestamp(first) + pd.Timedelta(days=30)
        after = net[net.index >= kill_ts]
        s_a, r_a, nd_a = daily_sharpe(after)
        print(f"\n  第一次處決日 ≈ {kill_ts.date()} (連續30天達標的那天)")
        print(f"  處決後到今天: {nd_a} 天, 淨Sharpe@2bp {s_a:+.2f}, 年報酬 {r_a*100:+.1f}%, "
              f"累積 {((1+after).prod()-1)*100:+.1f}%")
        print(f"  → 若照 config 原文處決, 這些全部拿不到。")
    return rows


if __name__ == "__main__":
    print(f"=== 溢價腿 std 分層研究 @ {datetime.now(timezone.utc).isoformat()} ===")
    print(f"參數全部讀自 config: smooth={C.PREMIUM['smooth_hours']}h z窗={C.PREMIUM['z_window']}h "
          f"clip=±{C.PREMIUM['clip']} 地板={C.LAYER3['mech_premium_std_floor']}bp\n下載中...", flush=True)
    H = load()
    S = build(H)
    rows = study(S)
    out = os.path.join(ROOT, "data", "std_regime_study.json")
    with open(out, "w", encoding="utf-8") as f:
        json.dump(dict(ts=datetime.now(timezone.utc).isoformat(), cut=CUT, rows=rows), f,
                  ensure_ascii=False, indent=1, default=float)
    print(f"\n✅ 結果已存 {out}")
