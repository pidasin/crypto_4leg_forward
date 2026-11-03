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
        """★2026-07-19 修正致命bug: 原版對【小時級】報酬用 √365 年化
           → 低估 √24 = 4.9倍 → 真實Sharpe 1.45會被算成0.30, 正好踩處決線,
             四條腿不管表現多好都會在12個月被判死刑。

           正確做法: 先把不規則的逐筆報酬【彙總成日報酬】再用√365。
           這樣同時解決兩件事:
             (1) 單位一致 (日報酬配√365)
             (2) GitHub排程到達率只有~60%, 間隔在1~3.7h之間跳動,
                 逐筆算等於把長間隔和短間隔當成同一單位 —— 彙總到日就免疫。
        """
        s = s.dropna()
        if len(s) == 0: return None
        d = s.resample("D").sum()          # 逐筆(約小時) → 日報酬
        d = d[d.index >= s.index[0].normalize()]
        if len(d) < 30 or d.std() == 0: return None
        return float(d.mean()/d.std()*np.sqrt(365))

    # 檢查點1: 6個月 — 只看第一層, 不看賺賠
    if m >= C.LAYER3["checkpoint_1_months"]:
        alerts.append(_alert("INFO", f"已運行 {m:.1f} 個月 — 6個月檢查點",
            "只檢查『有沒有壞掉』(第一層), 不看賺賠。因為DVOL平時本來就在睡覺"))

    def _verdict(s, executable):
        """executable=False → 只標記不處決 (12/24個月); True → 真正處決 (36個月)"""
        if s > C.LAYER3["sharpe_survive"]:
            return f"✅ 續命 (Sharpe {s:.2f} > {C.LAYER3['sharpe_survive']})"
        if s < C.LAYER3["sharpe_kill"]:
            return (f"☠️ 處決 (Sharpe {s:.2f} < {C.LAYER3['sharpe_kill']})" if executable
                    else f"🔻 標記:低於處決線 (Sharpe {s:.2f} < {C.LAYER3['sharpe_kill']}) "
                         f"— 但12/24個月【不處決】, 單腿Sharpe此時誤差±1.0, 誤殺率過高")
        return f"⚠️ 灰色地帶 (Sharpe {s:.2f})"

    # 檢查點2: 12個月 — ★只標記觀察, 不處決
    if m >= C.LAYER3["checkpoint_2_months"] and not R.empty:
        for leg in R.columns:
            s = sharpe(R[leg])
            if s is None: continue
            alerts.append(_alert("JUDGMENT", f"[12個月·僅標記] {leg}腿: {_verdict(s, False)}",
                "12個月不處決: 單腿波動32~53%, 1年資料Sharpe標準誤≈1.0, "
                "四腿至少誤殺一條的機率53.3%。正式逐腿判決在36個月。"))

    # 檢查點3: 24個月 — 逐腿仍只標記; 但【組合層】可判決(組合波動小, 誤差小得多)
    if m >= C.LAYER3["checkpoint_3_months"] and not R.empty:
        for leg in R.columns:
            s = sharpe(R[leg])
            if s is None: continue
            alerts.append(_alert("JUDGMENT", f"[24個月·僅標記] {leg}腿: {_verdict(s, False)}"))
        combo = R.mean(axis=1)
        cs = sharpe(combo)
        if cs is not None:
            if cs < C.LAYER3["sharpe_kill"]:
                v = f"☠️ 組合層處決 (Sharpe {cs:.2f} < {C.LAYER3['sharpe_kill']})"
            elif cs > C.LAYER3["sharpe_survive"]:
                v = f"✅ 組合續命 (Sharpe {cs:.2f})"
            else:
                v = f"⚠️ 組合灰色地帶 (Sharpe {cs:.2f})"
            alerts.append(_alert("JUDGMENT", f"[24個月·組合判決] {v}",
                "組合波動僅約23%(單腿32~53%), 誤差小得多, 故組合層此時可判決"))

    # 檢查點4: 36個月 — ★逐腿正式判決 (誤殺率降到19.9%)
    if m >= C.LAYER3["checkpoint_4_months"] and not R.empty:
        for leg in R.columns:
            s = sharpe(R[leg])
            if s is None: continue
            alerts.append(_alert("JUDGMENT", f"[36個月·正式判決] {leg}腿: {_verdict(s, True)}"))

    # ★特別條款(DVOL危機)與其他機制死亡判定已獨立成 mechanism(), 由每個 cycle 呼叫 ——
    #   不能放在這裡: layer3 只在 `run.py judge` 手動執行時跑, 而機制死亡的全部價值在於【即時】。
    return alerts

# ==================== ☠️ 機制死亡 (每個cycle都跑, 不等判決時鐘) ====================
def mechanism(legs_result=None):
    """★2026-11-03 接線。此前 config.LAYER3 的 mech_* 參數與 dvol_crisis_check()
       自 v2(2026-09-05) 寫入後【從未被任何程式碼讀取】—— 規則存在於紙上但不會執行。

       為什麼必須每個cycle跑(而非放在 layer3):
         機制死亡的全部價值是「看得到就是死了, 立刻處決」。若只在手動 judge 時檢查,
         等於要人記得去按 —— 而這條規則存在的理由正是「不要依賴當下的你」。

       分級原則(依使用者原則「寧願多虧錢也不要誤判」):
         ☠️ 可處決  = 事實型單一事件, 誤判率確實趨近零 (DVOL危機條款、對帳連續失敗)
         ⚖️ 需人工裁決 = 本次接線時發現【寫死的規則本身會誤判】的兩條 (見各自註解)
       絕不自動處決任何一條我無法證明誤判率極低的規則。
    """
    alerts = []
    P = C.LAYER3

    # ---- 1. DVOL 特別條款: BTC 7日跌 >15% 而 DVOL 腿沒賺錢 → 處決 ----
    #    事實型判定: 保單在理賠日有沒有賠付。無灰色地帶, 故維持可處決。
    #    掃全史: 這條從未執行過, 必須能回答「過去有沒有漏掉的觸發」。
    #    ★判定本身一律走 dvol_crisis_check() —— 不在這裡重寫一份。
    #      否則那個函式又會變成「定義了但沒人呼叫」的死程式碼, 正是本次要修掉的病。
    for ev in book.crisis_windows(P["dvol_crisis_btc_drop"]):
        if ev["dvol_ret"] is None:
            continue
        a = dvol_crisis_check(ev["btc_ret"], ev["dvol_ret"])
        if a is None:
            continue
        a = dict(a, msg=f"{a['msg']} [事件日 {ev['end']}]")
        if book.save_mech_verdict(f"dvol_crisis_{ev['end']}", dict(rule="dvol_crisis", **ev)):
            alerts.append(a)                        # ★只在第一次偵測到時發 → 不每小時轟炸

    # ---- 2. 對帳連續失敗 → 執行層壞了 ----
    #    事實型判定。注意: 這殺的是「執行層」不是某條腿的策略, 動作是修水管。
    rf, last_day = book.recon_fail_days()
    if rf is not None and rf >= P["mech_recon_fail_days"]:
        if book.save_mech_verdict(f"recon_fail_{last_day}", dict(rule="recon_fail", days=rf)):
            alerts.append(_alert("CRITICAL", f"☠️ 測試網對帳連續 {rf} 天無法通過 (上限{P['mech_recon_fail_days']}天)",
                "執行層壞了。這不是策略問題 —— paper track 不受影響, 但真錢上線前必須修好水管"))

    # ---- 3. 部位凍結 → ⚖️ 需人工裁決 (不自動處決) ----
    #    ★接線時發現寫死的規則會誤判, 故降級為裁決:
    #      T腿是 long-only SMA50, 空頭市場中「三幣全部跌破均線→空手」會讓部位合法地長期不動;
    #      A腿是四象限模型, 象限不變時部位也合法地釘在 ±1。
    #      對這兩條腿, 90天不動可能是【設計如此】而非 z分數失去區辨力。
    #    誤殺一條好腿 = 永久損失(型I), 故此處只標記、要求人工看一眼。
    DESIGN_FLAT = {"tleg": "long-only SMA50: 空頭中空手是設計行為",
                   "aleg": "四象限模型: 象限不變時部位合法釘住"}
    for leg in C.LEG_WEIGHTS:
        d, ts, ever = book.frozen_days(leg)
        if d is None or d < P["mech_frozen_days"]:
            continue
        note = DESIGN_FLAT.get(leg)
        lvl = "JUDGMENT" if note else "CRITICAL"
        act = (f"⚖️ 需人工裁決 — {note}。請確認是『訊號仍在動但剛好指向同一邊』(正常) "
               f"還是『訊號源停更/z分數失去區辨力』(機制死亡)" if note else
               "z分數失去區辨力 = 機制死亡。依 CHANGELOG v2 可立即處決")
        span = "全期從未變動過" if not ever else f"連續 {d:.0f} 天未變動"
        if book.save_mech_verdict(f"frozen_{leg}_{str(ts)[:10]}",
                                  dict(rule="frozen", leg=leg, days=round(d,1), ever_changed=ever)):
            alerts.append(_alert(lvl, f"{'⚖️' if note else '☠️'} {leg}腿部位{span} "
                                      f"({d:.0f}天, 上限{P['mech_frozen_days']}天)", act))

    # ---- 4. 溢價 std 地板 → ⚖️ 需人工裁決 (不自動處決) ----
    #    ★接線時發現【規則與實測證據互相矛盾】, 絕不可自動處決:
    #      config/CHANGELOG: std ≤4bp = 物理極限(≈2×成本) → 處決
    #      但 LAYER2 註解與記憶實測: 「std變小不是問題, <4bp區間 Sharpe 1.56」, 危險的是變大
    #    兩者不可能同時為真。在用資料解決這個矛盾之前, 這條只標記。
    run, cur, n = book.premium_std_floor_days(P["mech_premium_std_floor"])
    if cur is not None:
        if run is None:
            alerts.append(_alert("INFO", f"溢價std 地板監控啟動中 (目前最低幣 {cur:.2f}bp, "
                                         f"地板{P['mech_premium_std_floor']}bp, 已累積{n}天歷史)",
                "需≥2天日中位數才能判定『持續』。歷史在今天之前沒有被記錄過, 故從今天開始累積"))
        elif run >= 30:
            if book.save_mech_verdict(f"prem_std_floor_{run}d", dict(rule="prem_std_floor", days=run, cur=cur)):
                alerts.append(_alert("JUDGMENT",
                    f"⚖️ 溢價std 連續 {run} 天 ≤ {P['mech_premium_std_floor']}bp (目前 {cur:.2f}bp)",
                    "⚠️ 本條規則與實測證據衝突, 故【不自動處決】: config說這是物理極限該處決, "
                    "但 LAYER2 註解與回測記憶說『<4bp區間 Sharpe 1.56, std變小是健康』。"
                    "必須先用資料裁決哪個對, 再決定要不要動這條腿。"))

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
