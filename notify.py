# -*- coding: utf-8 -*-
"""Discord 通知 — ★核心原則: 避免通知疲勞

會轟炸的監控系統, 兩週內就會被靜音, 然後真警報來時你也不會看。
所以:
  📅 每日摘要   → 一天一次 (總資產 + 各腿損益 + 部位 + 狀況), WARN彙整在這裡
  🔴 CRITICAL  → 立即發, 但【去重】: 同一問題4小時內只發一次
                 (Deribit掛3天 = 收到約18條而不是72條)
  ⚖️ JUDGMENT  → 立即發 (6/12/18個月才有, 不可能吵)
  🟡 WARN      → 不即時發, 只進每日摘要
  🔵 INFO      → 只記錄, 不發
"""
import os, json, hashlib, requests
from datetime import datetime, timezone
import config as C

STATE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "state")
MUTE_F = os.path.join(STATE, "notify_mute.json")
MUTE_HOURS = 4          # 同一問題的靜音時間

def _webhook():
    return os.environ.get(C.DISCORD_WEBHOOK_ENV, "").strip()

def _load_mute():
    if not os.path.exists(MUTE_F): return {}
    try:
        with open(MUTE_F, encoding="utf-8") as f: return json.load(f)
    except Exception: return {}

def _save_mute(d):
    os.makedirs(STATE, exist_ok=True)
    with open(MUTE_F, "w", encoding="utf-8") as f: json.dump(d, f)

def _key(alert):
    return hashlib.md5(alert["msg"].encode("utf-8")).hexdigest()[:12]

def _should_send(alert):
    """去重: 同一問題4小時內只發一次 (JUDGMENT永遠發)"""
    if alert["level"] == "JUDGMENT":
        return True
    mute = _load_mute()
    k = _key(alert)
    now = datetime.now(timezone.utc)
    last = mute.get(k)
    if last:
        try:
            if (now - datetime.fromisoformat(last)).total_seconds() < MUTE_HOURS*3600:
                return False
        except Exception:
            pass
    mute[k] = now.isoformat()
    keep = {}
    for kk, vv in mute.items():
        try:
            if (now - datetime.fromisoformat(vv)).total_seconds() < 86400:
                keep[kk] = vv
        except Exception:
            pass
    _save_mute(keep)
    return True

def _post(msg):
    print("\n--- Discord ---\n" + msg + "\n---------------")
    wh = _webhook()
    if not wh:
        print("(未設定 DISCORD_WEBHOOK, 僅印出)")
        return False
    try:
        r = requests.post(wh, json={"content": msg[:1900]}, timeout=15)
        return r.status_code < 300
    except Exception as e:
        print("Discord發送失敗:", e)
        return False

EMO = {"CRITICAL": "🔴", "WARN": "🟡", "INFO": "🔵", "JUDGMENT": "⚖️"}
NAMES = {"premium": "🟦溢價", "dvol": "🟨DVOL", "aleg": "🟩A腿", "tleg": "🟥T腿"}

def send_alerts(alerts, context=""):
    """★只有 CRITICAL 和 JUDGMENT 即時發, 且去重。WARN留給每日摘要"""
    urgent = [a for a in alerts if a["level"] in ("CRITICAL", "JUDGMENT")]
    to_send = [a for a in urgent if _should_send(a)]
    muted = len(urgent) - len(to_send)
    if not to_send:
        if muted:
            print("(%d個警報靜音中, %dh內已發過)" % (muted, MUTE_HOURS))
        return False
    ts = datetime.now(timezone.utc).strftime("%m-%d %H:%M")
    L = ["**四腿 forward — %s** (%s UTC)" % (context, ts)]
    for a in to_send:
        L.append("%s %s" % (EMO.get(a["level"], ""), a["msg"]))
        if a.get("action"):
            L.append("   ↳ %s" % a["action"])
    if muted:
        L.append("_(另有%d個重複警報靜音中)_" % muted)
    return _post("\n".join(L))

def send_daily_summary(positions, alerts_today, perf=None, dd=None):
    """📅 每日摘要 — 一天一次, 這是你平常唯一會收到的東西"""
    now = datetime.now(timezone.utc)
    m = C.months_running()
    L = ["📅 **四腿 forward 日報** (%s)" % now.strftime("%Y-%m-%d"),
         "運行 %.1f 個月 · 模式 `%s`" % (m, C.MODE)]

    # ★總資產
    if perf:
        sign = "🟢" if perf["pnl_usd"] >= 0 else "🔴"
        L.append("")
        L.append("**%s 總資產 $%s**  (%s / %+.2f%%)" % (
            sign, format(perf["equity_usd"], ",.2f"),
            format(perf["pnl_usd"], "+,.2f"), perf["pnl_pct"]))
        parts = []
        for k, lab in [("day_pct", "日"), ("week_pct", "週"), ("month_pct", "月")]:
            if perf.get(k) is not None:
                parts.append("%s %+.2f%%" % (lab, perf[k]))
        if parts:
            L.append("　" + " · ".join(parts))
        if dd:
            uw = ", 已水下 %d天" % dd["underwater_days"] if dd.get("underwater_days") else ""
            L.append("　回撤 %+.2f%% (歷史最深 %.2f%%%s)" % (dd["current"], dd["max"], uw))

    # 各腿: 淨部位 + 損益 + 對總資產貢獻
    L.append("")
    L.append("**各腿** (淨部位 · 損益 · 貢獻)")
    for leg in ["premium", "dvol", "aleg", "tleg"]:
        p = (positions or {}).get(leg, {})
        lp = (perf or {}).get("legs", {}).get(leg, {})
        pnl_s = ""
        if lp:
            pnl_s = " · %+.2f%% ($%s)" % (lp["pnl_pct"], format(lp["contrib_usd"], "+,.0f"))
        nm = NAMES.get(leg, leg)
        if p:
            net = sum(p.values())
            detail = " ".join("%s%+.2f" % (k, v) for k, v in sorted(p.items())[:3])
            more = " +%d幣" % (len(p)-3) if len(p) > 3 else ""
            L.append("%s 淨%+.2f%s" % (nm, net, pnl_s))
            L.append("　　" + detail + more)
        else:
            L.append("%s ⚠️ 無部位%s" % (nm, pnl_s))

    # 今日狀況 (WARN彙整在這)
    warns = [a for a in (alerts_today or []) if a.get("level") == "WARN"]
    crits = [a for a in (alerts_today or []) if a.get("level") == "CRITICAL"]
    L.append("")
    if crits or warns:
        L.append("**今日狀況**")
        for a in crits[:3]:
            L.append("🔴 " + a["msg"])
        for a in warns[:5]:
            L.append("🟡 " + a["msg"])
    else:
        L.append("✅ 一切正常")

    # 檢查點倒數 (剩1個月內才提醒)
    for name, mo in [("6個月檢查點(只看有沒有壞掉)", C.LAYER3["checkpoint_1_months"]),
                     ("12個月正式判決", C.LAYER3["checkpoint_2_months"])]:
        left = mo - m
        if 0 < left <= 1:
            L.append("")
            L.append("⏰ **%s 還有 %.0f 天**" % (name, left*30))
            break

    # 每週一提醒誠實預期 (免得看到虧損就慌)
    if now.weekday() == 0:
        L.append("")
        L.append("_誠實預期 Sharpe %s(非回測%s) · 預期最長套牢 %s天 · 預期MDD %s%%_" % (
            C.HONEST["honest_sharpe"], C.HONEST["backtest_sharpe"],
            C.HONEST["expected_worst_underwater_days"], C.HONEST["expected_mdd"]))
    return _post("\n".join(L))
