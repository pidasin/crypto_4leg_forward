# -*- coding: utf-8 -*-
"""資料抓取 + 健康檢查 — 每個來源都回報自己的健康狀態
★第一層檢查的核心: 資料源是最脆弱、也最容易救的一環
"""
import requests, time, json, os
import pandas as pd, numpy as np
from datetime import datetime, timezone, timedelta

class DataHealth:
    """每次抓取都記錄健康狀態, 供第一層檢查使用"""
    def __init__(self):
        self.records = []
    def log(self, source, ok, latest_ts=None, note="", daily=False):
        age_h = None
        if latest_ts is not None:
            age_h = (datetime.now(timezone.utc) - latest_ts.replace(tzinfo=timezone.utc)).total_seconds()/3600
        self.records.append(dict(source=source, ok=ok, latest=str(latest_ts) if latest_ts is not None else None,
                                 age_hours=round(age_h,2) if age_h is not None else None, note=note,
                                 daily=daily))   # ★標記頻率: 日級資料的過期閾值不同
        return ok
    def failures(self):
        return [r for r in self.records if not r['ok']]

H = DataHealth()

def _get(url, params=None, timeout=20, retries=3):
    for i in range(retries):
        try:
            r = requests.get(url, params=params, timeout=timeout)
            if r.status_code == 200:
                return r.json()
        except Exception:
            pass
        time.sleep(1.5*(i+1))
    return None

# ---------- 幣安 ----------
def binance_klines(symbol, interval="1h", limit=1000):
    r = _get("https://data-api.binance.vision/api/v3/klines",
             dict(symbol=symbol, interval=interval, limit=limit))
    if not isinstance(r, list) or not r:
        H.log(f"binance_{symbol}_{interval}", False, note="無回應")
        return None
    df = pd.DataFrame(r, columns=list("tohlcv")+["a","b","c2","d","e","f"])
    df["t"] = pd.to_datetime(df["t"], unit="ms")
    s = df.drop_duplicates("t").set_index("t")["c"].astype(float)
    H.log(f"binance_{symbol}_{interval}", True, s.index[-1], daily=(interval=="1d"))
    return s

# ---------- Coinbase ----------
def coinbase_candles(product, granularity=3600, hours=800):
    """★分頁大小必須依 granularity 而定 (Coinbase單次上限300根)
       bug修正(2026-07-17): 原本硬編碼295小時 → 日K每次只拿到12根(效率4%),
       A腿抓8幣200天要128次請求。修正後每次拿295根 → 8次請求。GitHub Actions額度直接省16倍。
    """
    end = datetime.now(timezone.utc)
    out = []
    cur = end - timedelta(hours=hours)
    seg_hours = 295 * granularity / 3600.0     # 295根 × 每根幾小時
    while cur < end:
        seg = min(cur + timedelta(hours=seg_hours), end)
        r = _get("https://api.exchange.coinbase.com/products/%s/candles" % product,
                 dict(granularity=granularity, start=cur.isoformat(), end=seg.isoformat()))
        if isinstance(r, list):
            out += r
        cur = seg
        time.sleep(0.15)
    if not out:
        H.log(f"coinbase_{product}", False, note="無回應")
        return None
    df = pd.DataFrame(out, columns=["t","l","h","o","c","v"])
    df["t"] = pd.to_datetime(df["t"], unit="s")
    s = df.drop_duplicates("t").sort_values("t").set_index("t")["c"].astype(float)
    H.log(f"coinbase_{product}", True, s.index[-1], daily=(granularity>=86400))
    return s

# ---------- Deribit DVOL ----------
def deribit_dvol(currency="BTC", hours=800):
    end = int(datetime.now(timezone.utc).timestamp()*1000)
    start = end - hours*3600*1000
    r = _get("https://www.deribit.com/api/v2/public/get_volatility_index_data",
             dict(currency=currency, start_timestamp=start, end_timestamp=end, resolution=3600))
    d = (r or {}).get("result", {}).get("data", [])
    if not d:
        H.log("deribit_dvol", False, note="無資料 → 應啟用選擇權鏈自算備援")
        return None
    df = pd.DataFrame(d, columns=["t","o","h","l","c"])
    df["t"] = pd.to_datetime(df["t"], unit="ms")
    s = df.drop_duplicates("t").set_index("t")["c"].astype(float)
    H.log("deribit_dvol", True, s.index[-1])
    return s

# ---------- FNG (最脆弱的一環: alternative.me 是小網站) ----------
def fng_index(limit=400):
    r = _get("https://api.alternative.me/fng/", dict(limit=limit, format="json"))
    d = (r or {}).get("data", [])
    if not d:
        H.log("fng_alternative_me", False, note="⚠️FNG掛了 → A腿應降級為純CB多空(Sharpe 0.96)")
        return None
    df = pd.DataFrame(d)
    df["value"] = df["value"].astype(int)
    df["date"] = pd.to_datetime(df["timestamp"].astype(int), unit="s").dt.normalize()
    s = df.sort_values("date").set_index("date")["value"]
    H.log("fng_alternative_me", True, s.index[-1], daily=True)
    return s

# ---------- 備援場館 (Coinbase掛掉時用) ----------
def bitstamp_hourly(pair="btcusd", hours=800):
    start = int((datetime.now(timezone.utc)-timedelta(hours=hours)).timestamp())
    r = _get(f"https://www.bitstamp.net/api/v2/ohlc/{pair}/", dict(step=3600, limit=1000, start=start))
    d = (r or {}).get("data", {}).get("ohlc", [])
    if not d:
        H.log(f"bitstamp_{pair}", False)
        return None
    df = pd.DataFrame(d)
    df["t"] = pd.to_datetime(df["timestamp"].astype(int), unit="s")
    s = df.drop_duplicates("t").set_index("t")["close"].astype(float)
    H.log(f"bitstamp_{pair}", True, s.index[-1])
    return s

def health_report():
    return H.records
