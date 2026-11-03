# -*- coding: utf-8 -*-
"""四腿獨立記帳 — 這是整個 forward 最重要的檔案
★為什麼必須獨立記帳: 混在一起的話, 12個月後你不知道是哪條腿死了
"""
import json, os
import numpy as np, pandas as pd
from datetime import datetime, timezone
import config as C

STATE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "state")
os.makedirs(STATE, exist_ok=True)
POS_F   = os.path.join(STATE, "positions.json")
TRADE_F = os.path.join(STATE, "trades.jsonl")
NAV_F   = os.path.join(STATE, "nav.jsonl")
HEALTH_F= os.path.join(STATE, "health.jsonl")
DIAG_F  = os.path.join(STATE, "signals_diag.json")

def _load(path, default):
    if not os.path.exists(path): return default
    try:
        with open(path, encoding="utf-8") as f: return json.load(f)
    except Exception: return default

def _append(path, obj):
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(obj, ensure_ascii=False)+"\n")

def load_positions():
    """{leg: {coin: 部位(對該腿資金的比例)}}"""
    d = _load(POS_F, {k: {} for k in C.LEG_WEIGHTS})
    return {k: v for k, v in d.items() if k in C.LEG_WEIGHTS}   # 排除 _meta

def save_positions(pos, updated_legs=None):
    """存部位, 並記錄各腿的最後更新時間 (供補跑判斷)"""
    meta = _load(POS_F, {}).get("_meta", {})
    now = datetime.now(timezone.utc).isoformat()
    for leg in (updated_legs or []):
        meta[leg] = now
    out = dict(pos); out["_meta"] = meta
    with open(POS_F, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)

def leg_age_hours(leg):
    """該腿距離上次更新幾小時? None=從未更新
    ★用於補跑: A/T腿若>25h沒更新 (daily workflow掛了或剛啟動), hourly會補上
    """
    meta = _load(POS_F, {}).get("_meta", {})
    ts = meta.get(leg)
    if not ts: return None
    try:
        return (datetime.now(timezone.utc) - datetime.fromisoformat(ts)).total_seconds()/3600
    except Exception:
        return None

def record_trades(leg, old, new, prices, ts=None):
    """記錄該腿的部位變化 → 交易明細 (含理論成本)
    回傳: 本次該腿的換手量(對該腿資金)
    """
    ts = ts or datetime.now(timezone.utc).isoformat()
    turnover = 0.0
    for coin in set(list(old.keys())+list(new.keys())):
        o = float(old.get(coin, 0.0)); n = float(new.get(coin, 0.0))
        d = n - o
        if abs(d) < 1e-6: continue
        turnover += abs(d)
        notional = abs(d) * C.CAPITAL_USD * C.LEG_WEIGHTS[leg]
        _append(TRADE_F, dict(
            ts=ts, leg=leg, coin=coin, side=("BUY" if d>0 else "SELL"),
            delta=round(d,6), old=round(o,6), new=round(n,6),
            price=prices.get(coin), notional_usd=round(notional,2),
            fee_maker_usd=round(notional*C.FEE_MAKER_BP/10000, 4),
            mode=C.MODE,
        ))
    return turnover

def last_nav():
    """讀最後一筆NAV記錄"""
    if not os.path.exists(NAV_F): return None
    last = None
    with open(NAV_F, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                try: last = json.loads(line)
                except Exception: pass
    return last

def record_nav(legs_state, prices, ts=None):
    """★記錄每腿淨值 + 計算PnL

    bug修正(2026-07-17): 原版只記錄部位與價格, 從未計算 pnl_pct
    → leg_returns() 永遠回傳0 → 第三層判決會拿全0的資料算Sharpe = 整個track是廢的

    PnL算法(paper mode):
      各腿PnL% = Σ(上次部位_該幣 × 該幣價格變化%) − 換手成本
      各腿NAV  = 上次NAV × (1 + PnL%)
      總NAV    = Σ(各腿NAV × 該腿權重)
    """
    ts = ts or datetime.now(timezone.utc).isoformat()
    prev = last_nav()

    for leg, st in legs_state.items():
        prev_leg = (prev or {}).get("legs", {}).get(leg, {})
        prev_pos = prev_leg.get("pos", {})
        prev_nav = prev_leg.get("nav", 1.0)
        prev_px  = (prev or {}).get("prices", {})

        # PnL = 上次部位 × 價格變化 (部位在價格變化期間持有)
        pnl = 0.0
        for coin, pos in (prev_pos or {}).items():
            p0 = prev_px.get(coin); p1 = prices.get(coin)
            if p0 and p1 and p0 > 0:
                pnl += float(pos) * (p1/p0 - 1)
        # 扣本次換手成本 (maker假設)
        cost = float(st.get("turnover", 0.0)) * C.FEE_MAKER_BP / 10000
        pnl_net = pnl - cost

        st["pnl_pct"] = round(pnl_net, 8)
        st["nav"] = round(prev_nav * (1 + pnl_net), 8)
        st["cost_pct"] = round(cost, 8)

    # 組合總NAV (各腿等權)
    total_nav = sum(legs_state[l]["nav"] * C.LEG_WEIGHTS.get(l, 0) for l in legs_state)
    # 未被本cycle更新的腿, 沿用上次NAV
    for leg, w in C.LEG_WEIGHTS.items():
        if leg not in legs_state:
            total_nav += (prev or {}).get("legs", {}).get(leg, {}).get("nav", 1.0) * w

    _append(NAV_F, dict(ts=ts, legs=legs_state, prices=prices,
                        total_nav=round(total_nav, 8),
                        equity_usd=round(C.CAPITAL_USD * total_nav, 2)))

def save_diag(res, datafeed_records=None, ts=None):
    """★持久化訊號診斷值 → 儀表板的「腿健康度」資料來源
    每腿只在本cycle有更新時覆寫 (hourly不會洗掉daily腿的診斷);
    資料源記錄按source合併, 保留各自最後一次的狀態。
    """
    ts = ts or datetime.now(timezone.utc).isoformat()
    d = _load(DIAG_F, {})
    for leg, r in (res or {}).items():
        d[leg] = dict(ts=ts, ok=r.get("ok", False), diag=r.get("diag", {}))
    if datafeed_records:
        feeds = d.get("datafeed", {})
        for rec in datafeed_records:
            feeds[rec["source"]] = dict(ts=ts, **rec)
        d["datafeed"] = feeds
    with open(DIAG_F, "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False, indent=1)

def record_health(records, layer, ts=None):
    ts = ts or datetime.now(timezone.utc).isoformat()
    _append(HEALTH_F, dict(ts=ts, layer=layer, records=records))

# ---------- 讀取與分析 ----------
def read_jsonl(path):
    if not os.path.exists(path): return pd.DataFrame()
    rows = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line=line.strip()
            if line:
                try: rows.append(json.loads(line))
                except Exception: pass
    return pd.DataFrame(rows)

def leg_returns():
    """從 nav.jsonl 重建各腿的報酬序列 → 供第三層判決使用"""
    df = read_jsonl(NAV_F)
    if df.empty: return pd.DataFrame()
    rows=[]
    for _, r in df.iterrows():
        d = {"ts": pd.to_datetime(r["ts"])}
        for leg, st in (r["legs"] or {}).items():
            d[leg] = st.get("pnl_pct", 0.0)
        rows.append(d)
    out = pd.DataFrame(rows).set_index("ts").sort_index()
    return out

def performance():
    """★績效摘要 — 日報與status用
    回傳: dict(equity_usd, total_nav, pnl_usd, pnl_pct, legs={leg:{nav,pnl_pct,contrib}}, since, days)
    """
    df = read_jsonl(NAV_F)
    if df.empty or "total_nav" not in df.columns:
        return None
    df["ts"] = pd.to_datetime(df["ts"])
    df = df.sort_values("ts")
    cur = df.iloc[-1]
    first = df.iloc[0]
    days = max((cur["ts"] - first["ts"]).total_seconds()/86400, 0.001)

    legs_perf = {}
    for leg, w in C.LEG_WEIGHTS.items():
        nav = (cur["legs"] or {}).get(leg, {}).get("nav", 1.0)
        legs_perf[leg] = dict(
            nav=round(nav, 6),
            pnl_pct=round((nav-1)*100, 3),
            contrib_usd=round(C.CAPITAL_USD * w * (nav-1), 2),   # 該腿對總資產的貢獻
        )
    total_nav = float(cur["total_nav"])
    equity = float(cur.get("equity_usd", C.CAPITAL_USD*total_nav))

    # 期間報酬
    def nav_at(hours_ago):
        cutoff = cur["ts"] - pd.Timedelta(hours=hours_ago)
        past = df[df["ts"] <= cutoff]
        return float(past.iloc[-1]["total_nav"]) if len(past) else None
    d1 = nav_at(24); d7 = nav_at(24*7); d30 = nav_at(24*30)

    return dict(
        equity_usd=round(equity,2),
        total_nav=round(total_nav,6),
        pnl_usd=round(equity - C.CAPITAL_USD, 2),
        pnl_pct=round((total_nav-1)*100, 3),
        day_pct=round((total_nav/d1-1)*100,3) if d1 else None,
        week_pct=round((total_nav/d7-1)*100,3) if d7 else None,
        month_pct=round((total_nav/d30-1)*100,3) if d30 else None,
        legs=legs_perf, days=round(days,2), records=len(df),
        since=str(first["ts"])[:10],
    )

def equity_curve():
    """總資產曲線 (供計算MDD/Sharpe)"""
    df = read_jsonl(NAV_F)
    if df.empty or "total_nav" not in df.columns: return None
    df["ts"] = pd.to_datetime(df["ts"])
    return df.sort_values("ts").set_index("ts")["total_nav"].astype(float)

def drawdown_now():
    """★2026-07-18 修正: 原版 eq.index[dd[dd>=-0.001].index[-1]] 用Timestamp當位置索引
    → pandas新版直接IndexError。daily_summary必經此函式, 一旦NAV記錄夠多就每天必炸,
    Commit state被跳過(workflow沒設if:always()) → 當天算出的A/T腿部位沒存進去。
    """
    eq = equity_curve()
    if eq is None or len(eq)<2: return None
    peak = eq.cummax()
    dd = (eq/peak - 1)
    if dd.iloc[-1] >= -0.001:                  # 目前在高點附近 → 沒有套牢
        underwater_days = 0
    else:
        at_peak = dd[dd >= -0.001]             # 曾經"回到高點附近"的時間點
        since = at_peak.index[-1] if len(at_peak) > 0 else eq.index[0]
        underwater_days = (eq.index[-1] - since).days
    return dict(current=round(float(dd.iloc[-1])*100,2), max=round(float(dd.min())*100,2),
                underwater_days=int(underwater_days))

# ==================== 機制生命徵象 (供第三層「機制死亡」判定) ====================
# ★2026-11-03 新增。背景: config.LAYER3 的 mech_* 參數與 dvol_crisis_check() 自 v2(2026-09-05)
#   寫入後【從未被任何程式碼讀取】—— 參數是裝飾品, dvol_crisis_check 定義了但沒人呼叫。
#   這是記憶 silent-judgment-killing-bugs 的第五次: 不噴錯, 但該響的時候不會響。
#   以下函式是把那些規則接上線所需的資料存取層。

MECH_F = os.path.join(STATE, "mech_vitals.jsonl")
MECH_VERDICT_F = os.path.join(STATE, "mech_verdicts.json")

def record_mech_vitals(res, ts=None):
    """★每個 cycle 記錄機制生命徵象 → 讓「持續N天」類的判定日後可驗證

    為什麼需要獨立檔案: signals_diag.json 只存【最新一筆】(每次覆寫),
    所以「溢價std 連續30天 ≤4bp」這種判定在歷史上根本無法回答。
    nav.jsonl 不動 (加欄位會破壞 history.html 的重建邏輯), 改用這個附加檔。
    """
    ts = ts or datetime.now(timezone.utc).isoformat()
    row = dict(ts=ts)
    pd_ = (res or {}).get("premium", {}).get("diag", {}) or {}
    for coin, d in pd_.items():
        if isinstance(d, dict) and d.get("prem_std") is not None:
            row[f"prem_std_{coin}"] = d["prem_std"]
    dv = (res or {}).get("dvol", {}).get("diag", {}) or {}
    if dv.get("z") is not None:
        row["dvol_z"] = dv["z"]
    if len(row) > 1:
        _append(MECH_F, row)

def mech_vitals():
    df = read_jsonl(MECH_F)
    if df.empty: return df
    df["ts"] = pd.to_datetime(df["ts"], format="mixed", utc=True)
    return df.sort_values("ts").set_index("ts")

def premium_std_floor_days(floor):
    """溢價std 連續幾天【所有被監控的幣】都 ≤ floor?

    ★取各幣的 min (最低的那個幣先碰到地板), 而非儀表板用的 max —— max 會把快碰線的幣藏起來。
    回傳 (連續天數, 目前min值, 樣本天數)。樣本不足時連續天數回傳 None 而非 0,
    以免「沒資料」被當成「沒問題」。
    """
    df = mech_vitals()
    cols = [c for c in getattr(df, "columns", []) if c.startswith("prem_std_")]
    if df.empty or not cols: return None, None, 0
    s = df[cols].min(axis=1).dropna()
    if s.empty: return None, None, 0
    daily = s.resample("D").median().dropna()      # 用日中位數, 免得單一小時雜訊觸發
    cur = float(s.iloc[-1])
    if len(daily) < 2: return None, cur, len(daily)
    below = daily <= floor
    run = 0
    for v in reversed(below.tolist()):
        if v: run += 1
        else: break
    return run, cur, len(daily)

def frozen_days(leg):
    """該腿距離上次【部位實際變動】幾天。

    回傳 (天數, 起算時間, ever_changed)。ever_changed=False 表示【全期從未變動過】——
    此時天數 = 整段歷史長度。

    ★ever_changed 這個旗標不可省 (2026-11-03 合成測試抓到的自造bug):
      原版「從未變動」回傳 None, 而呼叫端寫 `if d is None: continue` → 最嚴重的情況
      (一條腿從上線就沒動過) 被當成「沒問題」直接跳過, 判定方向完全反了。
    ★為什麼不能用 positions.json 的 _meta: 那記的是「被重算」的時間, 每小時都更新,
      所以永遠看起來很新鮮 (2026-08-29 實例: A腿連續31天部位沒動而儀表板全綠)。
    """
    df = read_jsonl(NAV_F)
    if df.empty or "legs" not in df.columns: return None, None, True
    df["ts"] = pd.to_datetime(df["ts"], format="mixed", utc=True)
    df = df.sort_values("ts")
    last_sig, last_ts, now_ts = None, None, df["ts"].iloc[-1]
    for ts, legs in zip(reversed(df["ts"].tolist()), reversed(df["legs"].tolist())):
        pos = ((legs or {}).get(leg) or {}).get("pos", {})
        sig = json.dumps({k: round(float(v), 6) for k, v in pos.items()}, sort_keys=True)
        if last_sig is None:
            last_sig, last_ts = sig, ts; continue
        if sig != last_sig:
            return (now_ts - last_ts).total_seconds()/86400, last_ts, True
        last_ts = ts
    # 掃完整段歷史都沒變過
    return (now_ts - df["ts"].iloc[0]).total_seconds()/86400, df["ts"].iloc[0], False

def recon_fail_days():
    """測試網對帳【連續】幾天完全無法通過? (某日只要有一次 ok 就不算失敗日)

    回傳 (連續失敗天數, 最近一天有記錄的日期)。沒有任何對帳記錄 → (None, None)。
    ★必須區分「對帳失敗」與「根本沒在跑」: 後者是 None, 不是 0 —— 否則本機沒金鑰時
      會把「沒資料」誤讀成「一切正常」。
    """
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "state_testnet", "recon.jsonl")
    df = read_jsonl(path)
    if df.empty or "ok" not in df.columns: return None, None
    df["ts"] = pd.to_datetime(df["ts"], format="mixed", utc=True)
    ok_by_day = df.set_index("ts")["ok"].astype(bool).resample("D").max().dropna()
    if ok_by_day.empty: return None, None
    run = 0
    for v in reversed(ok_by_day.tolist()):
        if not v: run += 1
        else: break
    return run, ok_by_day.index[-1].date().isoformat()

def crisis_windows(btc_drop, lookback_days=None):
    """★掃出【所有】BTC 7日跌幅 ≤ btc_drop 的視窗, 並算同期 DVOL 腿報酬

    為什麼要掃全史而不只看當下: 這條特別條款自 v2 寫入後從未執行過,
    必須能回答「過去有沒有漏掉的觸發」, 否則接線等於默認過去都沒事。

    回傳 list[dict(end, btc_ret, dvol_ret, passed)], 以事件為單位 (連續觸發日合併成一次)。
    """
    df = read_jsonl(NAV_F)
    if df.empty or "prices" not in df.columns: return []
    df["ts"] = pd.to_datetime(df["ts"], format="mixed", utc=True)
    df = df.sort_values("ts")
    btc = pd.Series([(p or {}).get("BTC") for p in df["prices"]], index=df["ts"]).dropna().astype(float)
    dvn = pd.Series([((l or {}).get("dvol") or {}).get("nav") for l in df["legs"]], index=df["ts"])
    dvn = dvn.astype(float).ffill().dropna()
    if btc.empty: return []
    b = btc.resample("D").last().dropna()
    d = dvn.resample("D").last().dropna()
    if lookback_days:
        b = b[b.index >= b.index[-1] - pd.Timedelta(days=lookback_days)]
    w = b.pct_change(7).dropna()
    hit = w <= btc_drop
    out, prev = [], False
    for ts, is_hit in hit.items():
        if is_hit and not prev:                     # 事件起點 (連續觸發只記第一天)
            d0 = ts - pd.Timedelta(days=7)
            dv0 = d.asof(d0); dv1 = d.asof(ts)
            dret = (dv1/dv0 - 1) if (dv0 and dv1 and dv0 > 0) else None
            out.append(dict(end=ts.date().isoformat(), btc_ret=float(w[ts]),
                            dvol_ret=(float(dret) if dret is not None else None),
                            # ★bool() 不可省: numpy.bool_ 不能 json 序列化, 會讓落檔整個炸掉
                            passed=bool(dret is not None and dret > 0)))
        prev = bool(is_hit)
    return out

def load_mech_verdicts():
    return _load(MECH_VERDICT_F, {})

def save_mech_verdict(key, payload):
    """★機制判定一旦成立就落檔 → (1)不會每小時重複轟炸Discord (2)留下可稽核的判決紀錄"""
    d = load_mech_verdicts()
    if key in d: return False                       # 已記錄過 → 不重複發
    d[key] = dict(ts=datetime.now(timezone.utc).isoformat(), **payload)
    with open(MECH_VERDICT_F, "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False, indent=2)
    return True

def maker_fill_rate(days=30):
    """從 trades.jsonl 算 maker 成交率 (testnet mode 才有意義)
    ★回測基準: 85-88%。跌破70%要警報
    """
    df = read_jsonl(TRADE_F)
    if df.empty or "filled" not in df.columns: return None
    df["ts"] = pd.to_datetime(df["ts"])
    recent = df[df["ts"] > pd.Timestamp.now(tz="UTC")-pd.Timedelta(days=days)]
    if len(recent)==0: return None
    return float(recent["filled"].mean())
