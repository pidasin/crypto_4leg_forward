# -*- coding: utf-8 -*-
"""Discord 通知 — 只在有事時發, 避免通知疲勞
★原則: CRITICAL立刻發, WARN彙整發, INFO只記錄不發
"""
import os, json, requests
from datetime import datetime, timezone
import config as C

def _webhook():
    return os.environ.get(C.DISCORD_WEBHOOK_ENV, "").strip()

EMO = {"CRITICAL":"🔴", "WARN":"🟡", "INFO":"🔵", "JUDGMENT":"⚖️"}

def send_alerts(alerts, context=""):
    wh = _webhook()
    crit = [a for a in alerts if a["level"]=="CRITICAL"]
    judg = [a for a in alerts if a["level"]=="JUDGMENT"]
    warn = [a for a in alerts if a["level"]=="WARN"]
    if not (crit or judg or warn):
        return False
    lines = [f"**四腿 forward — {context}** ({datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M')} UTC)"]
    for a in crit+judg+warn:
        lines.append(f"{EMO.get(a['level'],'')} {a['msg']}")
        if a.get("action"):
            lines.append(f"   ↳ {a['action']}")
    msg = "\n".join(lines)[:1900]
    print("\n--- 通知內容 ---\n"+msg+"\n----------------")
    if not wh:
        print("(未設定 DISCORD_WEBHOOK, 僅印出)")
        return False
    try:
        r = requests.post(wh, json={"content": msg}, timeout=15)
        return r.status_code < 300
    except Exception as e:
        print("Discord發送失敗:", e)
        return False

def send_status(text):
    wh = _webhook()
    if not wh:
        print(text); return False
    try:
        requests.post(wh, json={"content": text[:1900]}, timeout=15)
        return True
    except Exception:
        return False
