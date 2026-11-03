# -*- coding: utf-8 -*-
"""四腿衰減檢驗 — 「策略有變爛嗎?」用資料回答, 不用感覺

★為什麼需要這支 (2026-11-03)
  config.LAYER3 有 `tleg_watch = True`, 註解寫「T腿ETF後已從1.78衰減到0.58」——
  但那是一次性的觀察, 沒有程式在持續檢驗, 也沒有對其他三腿做同樣的檢驗。
  forward 只有 3.6 個月, 統計上不可能回答衰減問題; 但各腿的【歷史重建】有 5~9 年,
  那個樣本才有機會說話。

★方法學 (這題特別容易自我欺騙, 所以把陷阱先寫下來)
  1. 【不可用滾動窗口做顯著性檢定】滾動 12 月 Sharpe 彼此高度重疊, 會把 n 灌水,
     算出假的 p=0.0000 (本專案 2026-09 已犯過一次)。
     → 顯著性一律用【不重疊的年度區塊】, n = 年數, 誠實地小。
  2. 【Sharpe 先彙總成日報酬再年化】對小時級報酬直接 ×√365 會差 √24 = 4.9 倍。
  3. 【檢定力要先報】n 年資料能偵測到的最小 Sharpe 差距 ≈ 2/√n。
     若 n=9, 那是 0.67 —— 小於這個的衰減, 本檢定【原則上看不見】, 不是「沒有衰減」。
  4. 【無未來函數】t 期部位只用到 t-1 收盤。
  5. 2017 年 CB 資料殘缺 (覆蓋率37%), 溢價/A腿一律從 2018 起算。
"""
import sys, os, time, json
import numpy as np, pandas as pd
from datetime import datetime, timezone, timedelta

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import config as C
import requests

rng = np.random.default_rng(20261103)
NOW = datetime.now(timezone.utc)


def _get(url, params=None, tries=4):
    for i in range(tries):
        try:
            r = requests.get(url, params=params, timeout=30)
            if r.status_code == 200:
                return r.json()
        except Exception:
            pass
        time.sleep(1.2 * (i + 1))
    return None


def bn(sym, interval, start):
    out, st = [], int(start.timestamp() * 1000)
    while True:
        r = _get("https://data-api.binance.vision/api/v3/klines",
                 dict(symbol=sym, interval=interval, startTime=st, limit=1000))
        if not r:
            break
        out += r
        st = r[-1][0] + (3600_000 if interval == "1h" else 86400_000)
        if len(r) < 1000:
            break
    if not out:
        return None
    df = pd.DataFrame(out).iloc[:, :5]
    df.columns = list("tohlc")
    df["t"] = pd.to_datetime(df["t"], unit="ms")
    return df.drop_duplicates("t").set_index("t")["c"].astype(float)


def cb(product, gran, start):
    out, cur = [], start
    seg_h = 295 * gran / 3600.0
    while cur < NOW:
        seg = min(cur + timedelta(hours=seg_h), NOW)
        r = _get("https://api.exchange.coinbase.com/products/%s/candles" % product,
                 dict(granularity=gran, start=cur.isoformat(), end=seg.isoformat()))
        if isinstance(r, list):
            out += r
        cur = seg
        time.sleep(0.15)
    if not out:
        return None
    df = pd.DataFrame(out, columns=["t", "l", "h", "o", "c", "v"])
    df["t"] = pd.to_datetime(df["t"], unit="s")
    return df.drop_duplicates("t").sort_values("t").set_index("t")["c"].astype(float)


def dvol_hist(cur="BTC"):
    out, c0 = [], datetime(2020, 1, 1, tzinfo=timezone.utc)
    while c0 < NOW:
        seg = min(c0 + timedelta(days=60), NOW)
        r = _get("https://www.deribit.com/api/v2/public/get_volatility_index_data",
                 dict(currency=cur, start_timestamp=int(c0.timestamp() * 1000),
                      end_timestamp=int(seg.timestamp() * 1000), resolution=3600))
        out += ((r or {}).get("result", {}) or {}).get("data", [])
        c0 = seg
    if not out:
        return None
    df = pd.DataFrame(out, columns=["t", "o", "h", "l", "c"])
    df["t"] = pd.to_datetime(df["t"], unit="ms")
    return df.drop_duplicates("t").sort_values("t").set_index("t")["c"].astype(float)


def fng_hist():
    r = _get("https://api.alternative.me/fng/", dict(limit=0, format="json"))
    d = (r or {}).get("data", [])
    if not d:
        return None
    s = pd.Series({pd.to_datetime(int(x["timestamp"]), unit="s").normalize(): float(x["value"]) for x in d})
    return s.sort_index()


# ---------- 四腿重建 (規則與 legs/signals.py 一致, 參數全讀 config) ----------
def leg_premium(data):
    p = C.PREMIUM
    tot = None
    for c in p["coins"]:
        df = pd.DataFrame({"bn": data[f"bn1h_{c}"], "cb": data[f"cb1h_{c}"]}).dropna()
        prem = (df["cb"] / df["bn"] - 1) * 10000
        z = (prem.rolling(p["smooth_hours"]).mean() - prem.rolling(p["smooth_hours"]).mean().rolling(p["z_window"]).mean()) \
            / prem.rolling(p["smooth_hours"]).mean().rolling(p["z_window"]).std()
        pos = np.clip(z, -p["clip"], p["clip"]) / 2 * 0.5
        r = pos * df["bn"].pct_change().shift(-1) - pos.diff().abs().fillna(0) * C.FEE_MAKER_BP / 1e4
        tot = r if tot is None else tot.add(r, fill_value=0)
    return tot.dropna()


def leg_dvol(data):
    p = C.DVOL
    dv = data["dvol"]
    px = data[f"bn1h_{p['coin']}"]
    z = (dv - dv.rolling(p["z_window"]).mean()) / dv.rolling(p["z_window"]).std()
    pos = np.clip(z, -p["clip"], p["clip"]) / 2
    idx = pos.index.intersection(px.index)
    pos = pos.loc[idx]
    r = pos * px.loc[idx].pct_change().shift(-1) - pos.diff().abs().fillna(0) * C.FEE_MAKER_BP / 1e4
    return r.dropna()


def leg_aleg(data, fng):
    p = C.ALEG
    tot = None
    for c in p["coins"]:
        b, k = data.get(f"bn1d_{c}"), data.get(f"cb1d_{c}")
        if b is None or k is None:
            print(f"    (A腿略過 {c}: 無資料)")
            continue
        df = pd.DataFrame({"bn": b, "cb": k}).dropna()
        prem = ((df["cb"] / df["bn"] - 1) * 10000).rolling(p["prem_ma_days"]).mean()
        f = fng.reindex(df.index.normalize(), method="ffill")
        f.index = df.index
        cb_long, greed = prem > 0, f > p["fng_threshold"]
        v = pd.Series(np.where(cb_long & greed, 1.0,
                      np.where(cb_long & ~greed, 0.5,
                      np.where(~cb_long & greed, -0.5, -1.0))), index=df.index)
        pos = v / len(p["coins"])
        r = pos * df["bn"].pct_change().shift(-1) - pos.diff().abs().fillna(0) * C.FEE_MAKER_BP / 1e4
        tot = r if tot is None else tot.add(r, fill_value=0)
    return tot.dropna()


def leg_tleg(data):
    p = C.TLEG
    tot = None
    for c in p["coins"]:
        px = data.get(f"bn1d_{c}")
        if px is None:
            continue
        pos = (px > px.rolling(p["ma_days"]).mean()).astype(float) / len(p["coins"])
        r = pos * px.pct_change().shift(-1) - pos.diff().abs().fillna(0) * C.FEE_MAKER_BP / 1e4
        tot = r if tot is None else tot.add(r, fill_value=0)
    return tot.dropna()


# ---------- 統計 ----------
def to_daily(r):
    """小時/日報酬 → 日報酬。

    ★2026-11-03 測試抓到的方法學 bug: 原本寫 `d[d != 0]` (丟掉零報酬日)。
      那個寫法是為【分層遮罩】設計的(區間外的小時被設成0), 搬到這裡就錯了:
      T腿是 long-only SMA50, 三幣全部跌破均線時【合法地空手】, 當日報酬正好是 0。
      那是真實的交易日, 丟掉等於只留下有部位的日子 → 系統性高估 Sharpe。
      實測影響: T腿 9.05 年被砍成 5.9 年(丟掉1159個空手日), 而且它目前(10-23起)
      正在空手, 連最近12個月的樣本都被切掉 —— 正好毀掉「現在是否變爛」這個問題。
    正確做法: min_count=1 → 完全沒有觀測值的日子才是 NaN(真的沒資料), 零報酬日保留。
    """
    return r.resample("D").sum(min_count=1).dropna()


def sharpe(r):
    d = to_daily(r)
    if len(d) < 20 or d.std() == 0:
        return None
    return float(d.mean() / d.std() * np.sqrt(365))


def boot_diff(r1, r2, n=2000, block=20):
    """兩段期間 Sharpe 差距的區塊 bootstrap 分佈 → 差距的 CI 與 p(差距≥0)"""
    d1, d2 = to_daily(r1).values, to_daily(r2).values
    if len(d1) < block * 3 or len(d2) < block * 3:
        return None
    def bs(v):
        nb = max(1, len(v) // block)
        i = rng.integers(0, len(v) - block, nb)
        s = np.concatenate([v[j:j + block] for j in i])
        return s.mean() / s.std() * np.sqrt(365) if s.std() > 0 else np.nan
    diffs = np.array([bs(d2) - bs(d1) for _ in range(n)])
    diffs = diffs[np.isfinite(diffs)]
    if not len(diffs):
        return None
    return dict(lo=float(np.percentile(diffs, 5)), hi=float(np.percentile(diffs, 95)),
                p_worse=float((diffs < 0).mean()), mean=float(diffs.mean()))


def report(name, r):
    d = to_daily(r)
    yrs = len(d) / 365.25
    print(f"\n{'='*94}\n【{name}】 {d.index[0].date()} → {d.index[-1].date()}  ({yrs:.1f} 年, {len(d)} 個交易日)")
    print(f"{'='*94}")
    full = sharpe(r)
    print(f"  全期淨Sharpe@{C.FEE_MAKER_BP:.0f}bp: {full:+.2f}")
    print(f"  ★檢定力: {yrs:.1f} 年 → Sharpe 估計標準誤 ≈ {1/np.sqrt(yrs):.2f}; "
          f"能偵測到的最小衰減 ≈ {2/np.sqrt(yrs):.2f}")

    # 逐年 (不重疊 = 可用於檢定)
    yr = {}
    for y, g in d.groupby(d.index.year):
        if len(g) >= 120 and g.std() > 0:
            yr[y] = float(g.mean() / g.std() * np.sqrt(365))
    print(f"\n  逐年淨Sharpe (不重疊年度區塊, n={len(yr)}):")
    print("    " + "  ".join(f"{y}={v:+.2f}" for y, v in yr.items()))

    out = dict(leg=name, years=round(yrs, 2), sharpe_full=full, yearly=yr)
    if len(yr) >= 4:
        ys = np.array(sorted(yr)); vs = np.array([yr[y] for y in ys])
        # 年度 Sharpe 對年份的 OLS 斜率 (n = 年數, 誠實的小樣本)
        sl, ic = np.polyfit(ys, vs, 1)
        resid = vs - (sl * ys + ic)
        se = np.sqrt((resid ** 2).sum() / max(len(ys) - 2, 1) / ((ys - ys.mean()) ** 2).sum())
        t = sl / se if se > 0 else np.nan
        print(f"\n  年度趨勢: 斜率 {sl:+.3f} Sharpe/年  (SE {se:.3f}, t={t:+.2f}, n={len(ys)})")
        print(f"    → {'有統計意義的衰減' if t < -2 else ('有統計意義的改善' if t > 2 else '⚪ 看不出趨勢 (|t|<2)')}")
        out.update(slope=float(sl), slope_t=float(t))
        # 前半 vs 後半
        mid = ys[len(ys) // 2]
        r1, r2 = r[r.index.year < mid], r[r.index.year >= mid]
        s1, s2 = sharpe(r1), sharpe(r2)
        bd = boot_diff(r1, r2)
        print(f"\n  前半 (<{mid}) Sharpe {s1:+.2f}   後半 (≥{mid}) Sharpe {s2:+.2f}   差 {s2-s1:+.2f}")
        if bd:
            print(f"    區塊bootstrap 差距 5~95%: {bd['lo']:+.2f} ~ {bd['hi']:+.2f}  "
                  f"P(後半較差) = {bd['p_worse']*100:.0f}%")
            print(f"    → {'後半顯著較差' if bd['hi'] < 0 else ('後半顯著較好' if bd['lo'] > 0 else '⚪ 區間跨0, 分不出來')}")
            out.update(half1=s1, half2=s2, diff_lo=bd["lo"], diff_hi=bd["hi"], p_worse=bd["p_worse"])
        # 最近 12 個月 vs 全期 (這是「現在是不是變爛了」最直接的問法)
        last = r[r.index >= r.index[-1] - pd.Timedelta(days=365)]
        sl12 = sharpe(last)
        if sl12 is not None:
            print(f"\n  最近12個月 Sharpe {sl12:+.2f} vs 全期 {full:+.2f}  (差 {sl12-full:+.2f})")
            bd2 = boot_diff(r[r.index < r.index[-1] - pd.Timedelta(days=365)], last)
            if bd2:
                print(f"    差距 5~95%: {bd2['lo']:+.2f} ~ {bd2['hi']:+.2f}  "
                      f"P(近12月較差) = {bd2['p_worse']*100:.0f}%  "
                      f"→ {'顯著變爛' if bd2['hi'] < 0 else '⚪ 分不出來'}")
            out.update(last12=sl12)
    return out


if __name__ == "__main__":
    print(f"=== 四腿衰減檢驗 @ {NOW.isoformat()} ===")
    print("下載中 (小時級 2017起 + 日級 + DVOL + FNG)...", flush=True)
    data = {}
    for c in C.PREMIUM["coins"]:
        data[f"bn1h_{c}"] = bn(C.PREMIUM["bn_symbols"][c], "1h", datetime(2017, 9, 1, tzinfo=timezone.utc))
        data[f"cb1h_{c}"] = cb(C.PREMIUM["cb_products"][c], 3600, datetime(2017, 9, 1, tzinfo=timezone.utc))
        print(f"  {c} 小時: bn={len(data[f'bn1h_{c}'])} cb={len(data[f'cb1h_{c}'])}", flush=True)
    allc = sorted(set(C.ALEG["coins"]) | set(C.TLEG["coins"]))
    for c in allc:
        data[f"bn1d_{c}"] = bn(c + "USDT", "1d", datetime(2017, 8, 1, tzinfo=timezone.utc))
        data[f"cb1d_{c}"] = cb(c + "-USD", 86400, datetime(2017, 8, 1, tzinfo=timezone.utc))
        print(f"  {c} 日: bn={len(data[f'bn1d_{c}']) if data[f'bn1d_{c}'] is not None else 0} "
              f"cb={len(data[f'cb1d_{c}']) if data[f'cb1d_{c}'] is not None else 0}", flush=True)
    data["dvol"] = dvol_hist(C.DVOL["currency"])
    fng = fng_hist()
    print(f"  DVOL={len(data['dvol'])}  FNG={len(fng)} ({fng.index[0].date()}→{fng.index[-1].date()})", flush=True)

    CUT = "2018-01-01"      # 2017 CB 殘缺
    legs = {}
    legs["溢價腿"] = leg_premium(data).loc[CUT:]
    legs["DVOL腿"] = leg_dvol(data)
    legs["A腿"] = leg_aleg(data, fng).loc[CUT:]
    legs["T腿"] = leg_tleg(data)
    rows = [report(k, v) for k, v in legs.items()]

    # 組合 (等權, 只在四腿都有資料的日子)
    dl = {k: to_daily(v) for k, v in legs.items()}
    comb = pd.concat(dl, axis=1).dropna()
    if len(comb) > 200:
        cr = comb.mean(axis=1)
        rows.append(report("四腿組合 (等權, 四腿皆有資料的共同期間)", cr))

    out = os.path.join(ROOT, "data", "decay_study.json")
    with open(out, "w", encoding="utf-8") as f:
        json.dump(dict(ts=NOW.isoformat(), rows=rows), f, ensure_ascii=False, indent=1, default=float)
    print(f"\n✅ 結果已存 {out}")
