# -*- coding: utf-8 -*-
"""四腿訊號計算 — 每腿獨立, 各自回報 (目標倉位, 診斷資訊)
★所有規則來自回測定案, 參數在 config.py 鎖死
★每腿都要能獨立失敗而不影響其他腿
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import numpy as np, pandas as pd
import config as C
import datafeed as D

def _z(series, window):
    return (series - series.rolling(window).mean()) / series.rolling(window).std()

# ==================== 🟦 溢價腿 ====================
def premium_leg():
    """(Coinbase/幣安-1)×10000 → 24h平滑 → 30天z → clip(±2)/2, BTC+ETH各半"""
    p = C.PREMIUM
    pos, diag = {}, {}
    for coin in p["coins"]:
        bn = D.binance_klines(p["bn_symbols"][coin], "1h", 1000)
        cb = D.coinbase_candles(p["cb_products"][coin], 3600, 800)
        if bn is None or cb is None:
            diag[coin] = dict(error="資料缺失")
            continue
        df = pd.DataFrame({"bn": bn, "cb": cb}).dropna()
        if len(df) < p["z_window"] + p["smooth_hours"]:
            diag[coin] = dict(error=f"樣本不足({len(df)}<{p['z_window']+p['smooth_hours']})")
            continue
        prem = (df["cb"]/df["bn"] - 1) * 10000
        sm = prem.rolling(p["smooth_hours"]).mean()
        z = _z(sm, p["z_window"])
        z_now = float(z.iloc[-1])
        pos[coin] = float(np.clip(z_now, -p["clip"], p["clip"]) / 2) * 0.5   # 該腿內部各半
        diag[coin] = dict(prem_bp=round(float(prem.iloc[-1]),2), prem_std=round(float(prem.rolling(p["z_window"]).std().iloc[-1]),2),
                          z=round(z_now,3), pos=round(pos[coin],4), bars=len(df))
    return pos, diag

# ==================== 🟨 DVOL腿 ====================
def dvol_leg():
    """Deribit DVOL → 30天z → clip(±2)/2, z高(恐慌)=做多, BTC單幣

    ★備援自動接手: DVOL API最可能掛的時候, 正是市場最恐慌、伺服器最過載的時候
      —— 而那也正是DVOL腿唯一會賺大錢的時候(edge集中在少數危機日)。
      所以備援必須【自動】接手, 不能只發警報叫人手動處理。
      備援用選擇權鏈自算(VIX-style model-free variance), 實測誤差約1.8點。
    """
    import cache
    p = C.DVOL
    dv = D.deribit_dvol(p["currency"], 800)
    degraded = False
    note = None

    if dv is not None and len(dv) >= p["z_window"]:
        # ---- 正常: 官方DVOL可用 → 順便更新本地快取 ----
        cache.save_series("dvol_btc", dv)
    else:
        # ---- 官方DVOL掛了 → 快取(歷史) + 備援(即時值) 自動接手 ----
        hist = cache.load_series("dvol_btc")
        age = cache.cache_age_hours("dvol_btc")
        try:
            from tools.dvol_backup import dvol_from_chain
            est, bdiag = dvol_from_chain(p["currency"], target_days=30)
        except Exception as e:
            est, bdiag = None, dict(error=f"{type(e).__name__}: {e}")

        if hist is None or len(hist) < p["z_window"]:
            return {}, dict(error="DVOL官方掛了, 且本地快取不足(需30天歷史才能算z分數)",
                            cache_points=len(hist) if hist is not None else 0,
                            backup_live=round(est,2) if est else None,
                            action="該腿本期空手。快取會在官方恢復後自動補齊")
        if est is None:
            # 快取有歷史但備援也算不出即時值 → 用快取最後值
            # ★但快取太舊就必須空手: DVOL變化雖慢, 超過48h的值算z分數會失真
            if age is not None and age > C.LAYER1["cache_max_age_h"]:
                return {}, dict(error=f"官方DVOL掛+備援失敗, 且快取已{age:.1f}h(超過{C.LAYER1['cache_max_age_h']}h上限)",
                                action="該腿空手。長時間故障時, 寧可不下注也不要用失真的訊號")
            dv = hist; degraded = True
            note = f"⚠️官方DVOL掛+備援失敗({bdiag.get('error','?')}) → 用快取最後值(已{age:.1f}h前, 上限{C.LAYER1['cache_max_age_h']}h)"
        else:
            # ★最佳降級: 快取歷史 + 備援即時值
            ts = pd.Timestamp.utcnow().tz_localize(None).floor("h")
            dv = pd.concat([hist, pd.Series({ts: est})])
            dv = dv[~dv.index.duplicated(keep="last")].sort_index()
            degraded = True
            note = f"⚠️官方DVOL掛 → 快取歷史({len(hist)}點) + 選擇權鏈自算即時值({est:.1f}, 誤差約1.8點)"

    z = _z(dv, p["z_window"])
    z_now = float(z.iloc[-1])
    pos = {p["coin"]: float(np.clip(z_now, -p["clip"], p["clip"]) / 2)}
    diag = dict(dvol=round(float(dv.iloc[-1]),2), z=round(z_now,3),
                pos=round(pos[p["coin"]],4), bars=len(dv), degraded=degraded)
    if degraded:
        diag["warning"] = note
    return pos, diag

# ==================== 🟩 A腿 ====================
def aleg():
    """CB溢價7日均 × FNG 四象限交乘, 8幣等權
    (CB多,貪婪)=+1 / (CB多,平淡)=+0.5 / (CB空,貪婪)=-0.5 / (CB空,平淡)=-1
    ★FNG掛掉時降級: 全部視為『平淡』→ 等同純CB多空(回測Sharpe 0.96, 活著但較弱)
    """
    p = C.ALEG
    fng = D.fng_index(400)
    degraded = fng is None
    pos, diag = {}, dict(degraded=degraded)
    if degraded:
        diag["warning"] = "⚠️FNG不可用 → A腿降級為純CB多空(Sharpe 0.96 vs 原1.14)"
    for coin in p["coins"]:
        bn = D.binance_klines(coin+"USDT", "1d", 200)
        cb = D.coinbase_candles(coin+"-USD", 86400, 24*200)
        if bn is None or cb is None:
            diag[coin] = dict(error="資料缺失"); continue
        df = pd.DataFrame({"bn": bn, "cb": cb}).dropna()
        if len(df) < p["prem_ma_days"]+2:
            diag[coin] = dict(error="樣本不足"); continue
        prem = ((df["cb"]/df["bn"]-1)*10000).rolling(p["prem_ma_days"]).mean()
        cb_long = float(prem.iloc[-1]) > 0
        if degraded:
            greed = False           # 降級: 一律當平淡 → ±1 = 純CB多空
        else:
            f = fng.reindex(df.index.normalize(), method="ffill")
            greed = float(f.iloc[-1]) > p["fng_threshold"]
        if   cb_long and greed:      v = 1.0
        elif cb_long and not greed:  v = 0.5
        elif not cb_long and greed:  v = -0.5
        else:                        v = -1.0
        pos[coin] = v / len(p["coins"])          # 8幣等權
        diag[coin] = dict(prem_ma=round(float(prem.iloc[-1]),2), cb_long=cb_long,
                          fng=(None if degraded else int(f.iloc[-1])), greed=greed, pos=round(pos[coin],4))
    return pos, diag

# ==================== 🟥 T腿 ====================
def tleg():
    """收盤 vs SMA50 → 1或0 (只做多), BTC/ETH/SOL 等權
    ★注意: 此腿ETF後已從Sharpe 1.78衰減到0.58, 列為觀察對象
    """
    p = C.TLEG
    pos, diag = {}, {}
    for coin in p["coins"]:
        px = D.binance_klines(coin+"USDT", "1d", 200)
        if px is None:
            diag[coin] = dict(error="資料缺失"); continue
        if len(px) < p["ma_days"]+2:
            diag[coin] = dict(error="樣本不足"); continue
        ma = px.rolling(p["ma_days"]).mean()
        above = float(px.iloc[-1]) > float(ma.iloc[-1])
        pos[coin] = (1.0 if above else 0.0) / len(p["coins"])
        diag[coin] = dict(px=round(float(px.iloc[-1]),2), ma50=round(float(ma.iloc[-1]),2),
                          above=above, pos=round(pos[coin],4))
    return pos, diag

# ==================== 統一入口 ====================
LEGS = {"premium": premium_leg, "dvol": dvol_leg, "aleg": aleg, "tleg": tleg}

def compute_all(only=None):
    """回傳 {leg: {"pos": {coin: 對該腿的部位}, "diag": {...}, "ok": bool}}
    ★每腿獨立try, 一腿掛掉不影響其他腿
    """
    out = {}
    for name, fn in LEGS.items():
        if only and name not in only:
            continue
        try:
            pos, diag = fn()
            out[name] = dict(pos=pos, diag=diag, ok=bool(pos))
        except Exception as e:
            out[name] = dict(pos={}, diag=dict(error=f"例外: {type(e).__name__}: {e}"), ok=False)
    return out
