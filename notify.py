# -*- coding: utf-8 -*-
"""Discord 通知 — ★核心原則: 避免通知疲勞

會轟炸的監控系統, 兩週內就會被靜音, 然後真警報來時你也不會看。
所以:
  📅 每日摘要   → 一天一次 (告訴你系統活著 + 部位 + 健康), WARN彙整在這裡
  🔴 CRITICAL  → 立即發, 但【去重】: 同一問題4小時內只發一次
                 (Deribit掛3天 = 收到18條而不是72條)
  ⚖️ JUDGMENT  → 立即發 (6/12/18個月才有, 不可能吵)
  🟡 WARN      → 不即時發, 只進每日摘要
  🔵 INFO      → 只記錄, 不發
"""
import os, json, hashlib, requests
from datetime import datetime, timezone, timedelta
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
    """同一問題的指紋 (訊息內容, 不含時間戳)"""
    return hashlib.md5(alert["msg"].encode("utf-8")).hexdigest()[:12]

def _should_send(alert):
    """去重: 同一問題4小時內只發一次"""
    if alert["level"] in ("JUDGMENT",):
        return True                      # 判決永遠發
    mute = _load_mute()
    k = _key(alert)
    now = datetime.now(timezone.utc)
    last = mute.get(k)
    if last:
        try:
            if (now - datetime.fromisoformat(last)).total_seconds() < MUTE_HOURS*3600:
                return False             # 靜音中
        except Exception: pass
    mute[k] = now.isoformat()
    # 清掉超過24h的舊記錄
    mute = {kk: vv for kk, vv in mute.items()
            if (now - datetime.fromisoformat(vv)).total_seconds() < 86400}
    _save_mute(mute)
    return True

def _post(msg):
    print("\n--- Discord ---\n"+msg+"\n---------------")
    wh = _webhook()
    if not wh:
        print("(未設定 DISCORD_WEBHOOK, 僅印出)")
        return False
    try:
        r = requests.post(wh, json={"content": msg[:1900]}, timeout=15)
        return r.status_code < 300
    except Exception as e:
        print("Discord發送失敗:", e); return False

EMO = {"CRITICAL":"🔴", "WARN":"🟡", "INFO":"🔵", "JUDGMENT":"⚖️"}

def send_alerts(alerts, context=""):
    """★只有 CRITICAL 和 JUDGMENT 會即時發, 且去重。WARN留給每日摘要"""
    urgent = [a for a in alerts if a["level"] in ("CRITICAL","JUDGMENT")]
    to_send = [a for a in urgent if _should_send(a)]
    muted = len(urgent) - len(to_send)
    if not to_send:
        if muted: print(f"({muted}個警報靜音中, {MUTE_HOURS}h內已發過)")
        return False
    lines = [f"**四腿 forward — {context}** ({datetime.now(timezone.utc).strftime('%m-%d %H:%M')} UTC)"]
    for a in to_send:
        lines.append(f"{EMO.get(a['level'],'')} {a['msg']}")
        if a.get("action"): lines.append(f"   ↳ {a['action']}")
    if muted:
        lines.append(f"_(另有{muted}個重複警報靜音中)_")
    return _post("\n".join(lines))

def send_daily_summary(positions, alerts_today, leg_returns=None, health=None):
    """📅 每日摘要 — 一天一次, 這是你平常唯一會收到的東西"""
    now = datetime.now(timezone.utc)
    m = C.months_running()
    L = [f"📅 **四腿 forward 日報** ({now.strftime('%Y-%m-%d')})",
         f"運行 {m:.1f} 個月 · 模式 `{C.MODE}`"]

    # 部位
    L.append("\n**部位**")
    names = {"premium":"🟦溢價","dvol":"🟨DVOL","aleg":"🟩A腿","tleg":"🟥T腿"}
    for leg, p in (positions or {}).items():
        if p:
            net = sum(p.values())
            detail = " ".join(f"{k}{v:+.2f}" for k,v in sorted(p.items())[:4])
            more = f" +{len(p)-4}幣" if len(p)>4 else ""
            L.append(f"{names.get(leg,leg)} 淨{net:+.2f} · {detail}{more}")
        else:
            L.append(f"{names.get(leg,leg)} ⚠️ 無部位")

    # 今日警報彙整 (WARN在這裡)
    warns = [a for a in (alerts_today or []) if a["level"]=="WARN"]
    crits = [a for a in (alerts_today or []) if a["level"]=="CRITICAL"]
    if crits or warns:
        L.append("\n**今日狀況**")
        for a in crits[:3]: L.append(f"🔴 {a['msg']}")
        for a in warns[:5]: L.append(f"🟡 {a['msg']}")
    else:
        L.append("\n✅ 一切正常")

    # 提醒下一個檢查點
    for name, mo in [("6個月檢查點(只看有沒有壞掉)", C.LAYER3["checkpoint_1_months"]),
                     ("12個月正式判決", C.LAYER3["checkpoint_2_months"])]:
        left = mo - m
        if 0 < left <= 1:
            L.append(f"\n⏰ **{name} 還有 {left*30:.0f} 天**")
            break

    # 誠實提醒 (每週一提醒一次, 避免看到虧損就慌)
    if now.weekday()==0:
        L.append(f"\n_提醒: 誠實預期 Sharpe {C.HONEST['honest_sharpe']}(非回測{C.HONEST['backtest_sharpe']})_"
                 f"_· 預期最長套牢 {C.HONEST['expected_worst_underwater_days']}天_")
    return _post("\n".join(L))
