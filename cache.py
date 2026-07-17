# -*- coding: utf-8 -*-
"""本地資料快取 — 消除單點故障
★為什麼需要: DVOL的z分數需要30天歷史。官方API掛掉時, 備援只能算「當下這一點」,
  沒有歷史序列就算不出z分數 → 備援等於沒用。
  所以必須每次成功抓取就存本地, API掛了才有東西接。

★這正是「DVOL API最可能掛的時候=市場最恐慌時=DVOL腿唯一賺大錢的時候」這個
  諷刺的最終解法。
"""
import json, os
import pandas as pd
from datetime import datetime, timezone

STATE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "state")
os.makedirs(STATE, exist_ok=True)

def _path(name):
    return os.path.join(STATE, f"cache_{name}.json")

def save_series(name, s, max_points=2000):
    """存序列到本地快取 (與既有快取合併, 保留最新max_points點)"""
    if s is None or len(s)==0: return False
    old = load_series(name)
    if old is not None and len(old):
        merged = pd.concat([old, s])
        merged = merged[~merged.index.duplicated(keep="last")].sort_index()
    else:
        merged = s.sort_index()
    merged = merged.tail(max_points)
    data = dict(
        updated=datetime.now(timezone.utc).isoformat(),
        index=[t.isoformat() for t in merged.index],
        values=[float(v) for v in merged.values],
    )
    with open(_path(name), "w", encoding="utf-8") as f:
        json.dump(data, f)
    return True

def load_series(name):
    p = _path(name)
    if not os.path.exists(p): return None
    try:
        with open(p, encoding="utf-8") as f: d = json.load(f)
        s = pd.Series(d["values"], index=pd.to_datetime(d["index"]))
        return s[~s.index.duplicated(keep="last")].sort_index()
    except Exception:
        return None

def cache_age_hours(name):
    """快取有多舊? (用於判斷快取是否還可信)"""
    s = load_series(name)
    if s is None or len(s)==0: return None
    latest = s.index[-1]
    if latest.tzinfo is None: latest = latest.tz_localize("UTC")
    return (datetime.now(timezone.utc) - latest).total_seconds()/3600

def cache_status():
    out = {}
    for f in os.listdir(STATE):
        if f.startswith("cache_") and f.endswith(".json"):
            name = f[6:-5]
            s = load_series(name)
            out[name] = dict(points=len(s) if s is not None else 0,
                             age_hours=round(cache_age_hours(name),2) if s is not None else None,
                             span_days=round((s.index[-1]-s.index[0]).total_seconds()/86400,1) if s is not None and len(s)>1 else 0)
    return out
