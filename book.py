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
    return _load(POS_F, {k: {} for k in C.LEG_WEIGHTS})

def save_positions(pos):
    with open(POS_F, "w", encoding="utf-8") as f:
        json.dump(pos, f, ensure_ascii=False, indent=2)

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
    eq = equity_curve()
    if eq is None or len(eq)<2: return None
    peak = eq.cummax()
    dd = (eq/peak - 1)
    return dict(current=round(float(dd.iloc[-1])*100,2), max=round(float(dd.min())*100,2),
                underwater_days=int(((dd<-0.001).iloc[::-1].cumprod()).sum()*0+ (0 if dd.iloc[-1]>=-0.001 else
                    (eq.index[-1]-eq.index[dd[dd>=-0.001].index[-1]]).days if (dd>=-0.001).any() else 0)))

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
