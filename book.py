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

def record_nav(legs_state, prices, ts=None):
    """記錄每腿的即時淨值 (依部位×價格變化累計)
    ★paper mode: 用價格變化推算, 不需真實下單
    """
    ts = ts or datetime.now(timezone.utc).isoformat()
    _append(NAV_F, dict(ts=ts, legs=legs_state, prices=prices))

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
