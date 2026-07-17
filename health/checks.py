# -*- coding: utf-8 -*-
"""三層健康檢查
★核心原則: 不要監控績效, 要監控機制
   績效偵測慢到沒用(等權vs RP需要79年才能分辨), 但機制的生命徵象今天就能測
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import numpy as np, pandas as pd
from datetime import datetime, timezone
import config as C
import book

def _alert(level, msg, action=""):
    return dict(level=level, msg=msg, action=action)

# ==================== 🔴 第一層: 每次執行都檢查 ====================
def layer1(data_health, legs_result):
    """立即可測的東西 — 你今天問的失效模式裡, 能救的都在這層"""
    alerts = []

    # 1. 資料源健康
    for r in data_health:
        if not r["ok"]:
            src = r["source"]
            if "fng" in src:
                alerts.append(_alert("CRITICAL", f"FNG資料源掛了 ({r.get('note','')})",
                    "A腿已自動降級為純CB多空(回測Sharpe 0.96 vs 原1.14)。若持續>7天, 考慮找替代情緒指標"))
            elif "dvol" in src:
                alerts.append(_alert("CRITICAL", "Deribit DVOL不可用",
                    "啟用選擇權鏈自算備援(已驗證: 30天附近誤差僅0.5-3點)。DVOL腿暫停"))
            elif "coinbase" in src:
                alerts.append(_alert("CRITICAL", f"Coinbase不可用 ({src})",
                    "切換Bitstamp備援(回測1.05 vs 原1.12)"))
            else:
                alerts.append(_alert("WARN", f"資料源異常: {src}", "檢查API"))
        elif r.get("age_hours") is not None:
            src = r["source"]
            # ★閾值必須依資料頻率而定: 日K一天才更新一次, 用小時級閾值會每天誤報
            if "fng" in src:
                limit = C.LAYER1["fng_stale_hours"]
            elif r.get("daily"):
                limit = C.LAYER1["daily_stale_hours"]
            else:
                limit = C.LAYER1["data_stale_hours"]
            if r["age_hours"] > limit:
                alerts.append(_alert("WARN", f"{src} 資料過期 {r['age_hours']:.1f}h (上限{limit}h)",
                                     "檢查資料源是否停更"))

    # 2. 各腿是否活著
    for leg, res in legs_result.items():
        if not res["ok"]:
            err = res["diag"].get("error", "未知")
            alerts.append(_alert("CRITICAL", f"{leg}腿無法產生訊號: {err}",
                                 "該腿本期空手。若連續3次, 人工介入"))
        elif res["diag"].get("degraded"):
            alerts.append(_alert("WARN", f"{leg}腿降級運作", res["diag"].get("warning","")))

    # 3. maker 成交率 (testnet mode)
    if C.MODE == "testnet":
        fr = book.maker_fill_rate(30)
        if fr is not None and fr < C.LAYER1["maker_fill_min"]:
            alerts.append(_alert("CRITICAL", f"maker成交率 {fr*100:.0f}% < {C.LAYER1['maker_fill_min']*100:.0f}%",
                "改用taker執行(回測: 全taker組合仍有1.96)。或檢查掛單價是否太保守"))

    return alerts

# ==================== 🟡 第二層: 每月檢查 ====================
def layer2(legs_result):
    """訊號健康度 — 不是看賺賠, 是看機制還在不在"""
    alerts = []

    # 1. ★溢價std — 注意方向! std「變小」不是問題(實測<4bp區間Sharpe 1.56)
    #    危險的是「變大」(12-20bp區間Sharpe僅0.24 = 市場混亂期)
    pdiag = legs_result.get("premium", {}).get("diag", {})
    for coin, d in pdiag.items():
        if isinstance(d, dict) and "prem_std" in d:
            std = d["prem_std"]
            if std > C.LAYER2["premium_std_high"]:
                alerts.append(_alert("WARN", f"溢價std({coin}) = {std:.1f}bp > {C.LAYER2['premium_std_high']}bp",
                    "高std=市場混亂期, 實測此區間Sharpe僅0.24。這才是溢價腿危險的時候(不是std變小)"))

    # 2. 腿間相關 (需要足夠的forward資料)
    R = book.leg_returns()
    if len(R) > 60:
        corr = R.corr()
        for i in R.columns:
            for j in R.columns:
                if i < j and abs(corr.loc[i,j]) > C.LAYER2["leg_corr_max"]:
                    alerts.append(_alert("WARN", f"腿間相關過高: {i}↔{j} = {corr.loc[i,j]:.2f}",
                        f"基準是0.002~0.31。>0.6表示它們變成同一個賭注, 分散失效"))

    # 3. 曝險是否貼在上限 (z分數失去區辨力)
    for leg, res in legs_result.items():
        pos = res.get("pos", {})
        if not pos: continue
        mx = max(abs(v) for v in pos.values()) if pos else 0
        # 各腿的理論上限
        cap = {"premium":0.5, "dvol":1.0, "aleg":1/8, "tleg":1/3}.get(leg, 1.0)
        if cap>0 and mx/cap > C.LAYER2["exposure_pinned"]:
            alerts.append(_alert("INFO", f"{leg}腿曝險貼近上限 ({mx/cap*100:.0f}%)",
                "單次不是問題; 若長期如此=z分數失去區辨力"))

    return alerts

# ==================== 🟢 第三層: 預先寫死的判決 ====================
def layer3():
    """★這層的價值不在標準, 在『事先寫死』
       否則你會在虧損時找理由續命、在賺錢時忘記檢查
    """
    alerts = []
    m = C.months_running()
    R = book.leg_returns()

    def sharpe(s):
        s = s.dropna()
        if len(s) < 30 or s.std()==0: return None
        return float(s.mean()/s.std()*np.sqrt(365))

    # 檢查點1: 6個月 — 只看第一層, 不看賺賠
    if m >= C.LAYER3["checkpoint_1_months"]:
        alerts.append(_alert("INFO", f"已運行 {m:.1f} 個月 — 6個月檢查點",
            "只檢查『有沒有壞掉』(第一層), 不看賺賠。因為DVOL平時本來就在睡覺"))

    # 檢查點2: 12個月 — 第一次正式判決
    if m >= C.LAYER3["checkpoint_2_months"] and not R.empty:
        for leg in R.columns:
            s = sharpe(R[leg])
            if s is None: continue
            if s > C.LAYER3["sharpe_survive"]:
                v = f"✅ 續命 (Sharpe {s:.2f} > {C.LAYER3['sharpe_survive']})"
            elif s < C.LAYER3["sharpe_kill"]:
                v = f"☠️ 處決 (Sharpe {s:.2f} < {C.LAYER3['sharpe_kill']})"
            else:
                v = f"⚠️ 灰色地帶 (Sharpe {s:.2f}) → 延長觀察到{C.LAYER3['checkpoint_3_months']}個月"
            alerts.append(_alert("JUDGMENT", f"[12個月判決] {leg}腿: {v}"))

    # 特別條款: DVOL在危機時沒賺錢 → 立即處決
    # (需要BTC週報酬資料, 由caller提供)
    return alerts

def dvol_crisis_check(btc_weekly_return, dvol_weekly_return):
    """★特別條款: BTC單週跌>15%而DVOL腿沒賺錢 → 立即處決, 不用等12個月
       理由: DVOL是危機保單, 危機時不賺就是廢的
    """
    if btc_weekly_return is None or dvol_weekly_return is None:
        return None
    if btc_weekly_return <= C.LAYER3["dvol_crisis_btc_drop"]:
        if dvol_weekly_return <= 0:
            return _alert("JUDGMENT",
                f"☠️ DVOL特別條款觸發: BTC單週{btc_weekly_return*100:.1f}% 而DVOL腿{dvol_weekly_return*100:+.1f}%",
                "DVOL是危機保單, 危機時沒賺錢=死刑證據。立即處決, 不用等12個月")
        else:
            return _alert("INFO",
                f"✅ DVOL通過危機測試: BTC單週{btc_weekly_return*100:.1f}% 而DVOL腿{dvol_weekly_return*100:+.1f}%",
                "這是它存在的意義")
    return None
