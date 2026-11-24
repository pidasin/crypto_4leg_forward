# -*- coding: utf-8 -*-
"""機制層合成測試 —— 該響的會響、不該響的不響、已刪除的規則不會偷偷復活

★為什麼放進 repo (2026-11-25)
  2026-11-03 寫過 18 個測試, 但只放在本機暫存資料夾, 只用本機的 pandas 2.2 跑過;
  正式環境是 pandas 3.x。暫存資料夾清掉後測試就不見了。
  現在放在 tests/, 由 .github/workflows/test.yml 在每次 .py 有變更時用正式環境的套件版本跑。

★記憶 silent-judgment-killing-bugs 的鐵律: 判決層必須用已知答案的合成資料驗, 不能只看「沒噴錯」。

用法: python tests/test_mech.py     (失敗時 exit code 1)
"""
import sys, os, json, tempfile, shutil
from unittest import mock
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass
import pandas as pd
import config as C
import book
from health import checks

TMP = tempfile.mkdtemp()
PASS, FAIL = 0, []


def chk(name, cond, got=""):
    global PASS
    if cond:
        PASS += 1; print(f"  ✅ {name}")
    else:
        FAIL.append(name); print(f"  ❌ {name}   實際: {got}")


def setup(navs, recon=None, vitals=None):
    """把 book 的所有檔案路徑導到全新的暫存目錄 —— 絕不碰真的 state/"""
    d = tempfile.mkdtemp(dir=TMP)
    book.NAV_F = os.path.join(d, "nav.jsonl")
    book.MECH_F = os.path.join(d, "mech_vitals.jsonl")
    book.MECH_VERDICT_F = os.path.join(d, "mech_verdicts.json")
    book.RECON_F = os.path.join(d, "recon.jsonl")
    with open(book.NAV_F, "w", encoding="utf-8") as f:
        for n in navs: f.write(json.dumps(n) + "\n")
    for path, rows in ((book.MECH_F, vitals), (book.RECON_F, recon)):
        if rows is not None:
            with open(path, "w", encoding="utf-8") as f:
                for r in rows: f.write(json.dumps(r) + "\n")


def mk_navs(btc, dvnav, pos_dvol=-0.5, pos_tleg=0.0, start="2026-01-01", vary=False):
    ts = pd.date_range(start, periods=len(btc), freq="D", tz="UTC")
    out = []
    for i, (t, b, dn) in enumerate(zip(ts, btc, dvnav)):
        pd_ = pos_dvol + (0.001 * (i % 7) if vary else 0.0)
        out.append(dict(ts=t.isoformat(), prices=dict(BTC=float(b)),
                        legs=dict(premium=dict(pos={"BTC": 0.1 + 0.0001 * i}, nav=1.0),
                                  dvol=dict(pos={"BTC": pd_}, nav=float(dn)),
                                  aleg=dict(pos={"BTC": -0.125 if i % 20 < 10 else 0.0625}, nav=1.0),
                                  tleg=dict(pos={"BTC": pos_tleg}, nav=1.0)),
                        total_nav=1.0))
    return out


class temp_cfg:
    """暫時改 config.LAYER3 的某個值, 離開時一定還原"""
    def __init__(self, **kw): self.kw = kw
    def __enter__(self):
        self.old = {k: C.LAYER3.get(k) for k in self.kw}; C.LAYER3.update(self.kw)
    def __exit__(self, *a):
        C.LAYER3.update(self.old)


CRASH_BTC = [100000] * 10 + [80000] * 10          # 第11天起 -20%
CRASH_DV_LOSS = [1.0] * 10 + [0.95] * 10          # DVOL 同期 -5%

print("\n【1】DVOL 特別條款已於 v3 刪除 → 崩盤且 DVOL 虧錢也【不可】產生判決")
chk("config 確實已刪除 (None)", C.LAYER3.get("dvol_crisis_btc_drop") is None, C.LAYER3.get("dvol_crisis_btc_drop"))
setup(mk_navs(CRASH_BTC, CRASH_DV_LOSS, vary=True))
a = checks.mechanism({})
chk("沒有任何 DVOL 判決", not [x for x in a if "DVOL" in x["msg"] and "特別條款" in x["msg"]], [x["msg"] for x in a])
chk("沒有落檔任何判決", book.load_mech_verdicts() == {}, book.load_mech_verdicts())

print("\n【2】若日後重新啟用, 偵測邏輯仍須正確 (防止「刪除 = 程式爛掉」)")
with temp_cfg(dvol_crisis_btc_drop=-0.15):
    setup(mk_navs(CRASH_BTC, CRASH_DV_LOSS, vary=True))
    a1 = checks.mechanism({}); a2 = checks.mechanism({})
    j = [x for x in a1 if "特別條款觸發" in x["msg"]]
    chk("重新啟用後會觸發", len(j) == 1, [x["msg"] for x in a1])
    chk("等級 JUDGMENT", bool(j) and j[0]["level"] == "JUDGMENT", j[0]["level"] if j else "-")
    chk("警報指回 CHANGELOG v3", bool(j) and "v3" in j[0]["action"], j[0]["action"] if j else "-")
    chk("同一事件第二次不重複發", not [x for x in a2 if "特別條款觸發" in x["msg"]], [x["msg"] for x in a2])
    setup(mk_navs(CRASH_BTC, [1.0] * 10 + [1.08] * 10, vary=True))
    a = checks.mechanism({})
    chk("DVOL 腿賺錢時不處決", not [x for x in a if "特別條款觸發" in x["msg"]], [x["msg"] for x in a])
    setup(mk_navs([100000] * 10 + [90000] * 10, CRASH_DV_LOSS, vary=True))
    a = checks.mechanism({})
    chk("BTC 只跌 10% 不觸發", not [x for x in a if "特別條款" in x["msg"]], [x["msg"] for x in a])

print("\n【3】部位凍結")
setup(mk_navs([100000] * 100, [1.0] * 100, pos_dvol=-0.5))
a = checks.mechanism({})
fz = [x for x in a if "dvol腿部位" in x["msg"]]
chk("DVOL(z分數腿) 100天不動 → CRITICAL", bool(fz) and fz[0]["level"] == "CRITICAL", [(x["level"], x["msg"]) for x in a])
chk("「全期從未變動」被正確辨認(不是被跳過)", bool(fz) and "全期從未變動" in fz[0]["msg"], fz[0]["msg"] if fz else "-")
tz = [x for x in a if "tleg腿部位" in x["msg"]]
chk("T腿 100天不動 → 只標記 JUDGMENT", bool(tz) and tz[0]["level"] == "JUDGMENT", [(x["level"], x["msg"]) for x in a])
setup(mk_navs([100000] * 80, [1.0] * 80))
a = checks.mechanism({})
chk("80天不動 → 不觸發", not [x for x in a if "未變動" in x["msg"] or "從未變動" in x["msg"]], [x["msg"] for x in a])

print("\n【4】對帳連續失敗 (這條在 2026-11-25 之前從未被測試過)")
navs = mk_navs([100000] * 20, [1.0] * 20, vary=True)
def recon_rows(pattern):         # pattern: 每天一個 bool, True=ok
    t0 = pd.Timestamp("2026-01-01", tz="UTC")
    return [dict(ts=(t0 + pd.Timedelta(days=i, hours=h)).isoformat(), ok=ok)
            for i, ok in enumerate(pattern) for h in (1, 9, 17)]
setup(navs, recon=recon_rows([True] * 5 + [False] * 8))
a = checks.mechanism({})
rf = [x for x in a if "對帳連續" in x["msg"]]
chk("連續 8 天失敗 → CRITICAL", bool(rf) and rf[0]["level"] == "CRITICAL", [x["msg"] for x in a])
setup(navs, recon=recon_rows([True] * 5 + [False] * 6))
chk("連續 6 天失敗 → 不觸發", not [x for x in checks.mechanism({}) if "對帳連續" in x["msg"]])
setup(navs, recon=recon_rows([False] * 8 + [True]))
chk("最後一天恢復 → 不觸發", not [x for x in checks.mechanism({}) if "對帳連續" in x["msg"]])
setup(navs, recon=None)
chk("沒有對帳資料 → 不觸發(沒資料 ≠ 失敗)", book.recon_fail_days() == (None, None), book.recon_fail_days())

print("\n【5】溢價 std 地板 (v2.2 停用)")
ts = pd.date_range("2026-01-01", periods=40 * 24, freq="h", tz="UTC")
vit = [dict(ts=t.isoformat(), prem_std_BTC=5.5, prem_std_ETH=2.1) for t in ts]
setup(mk_navs([100000] * 40, [1.0] * 40, vary=True), vitals=vit)
chk("停用中 → 不產生判定", not [x for x in checks.mechanism({}) if "溢價std" in x["msg"]])
with temp_cfg(mech_premium_std_floor=4.0):
    setup(mk_navs([100000] * 40, [1.0] * 40, vary=True), vitals=vit)
    sf = [x for x in checks.mechanism({}) if "溢價std 連續" in x["msg"]]
    chk("重新啟用後抓得到, 且用 min(ETH 2.10) 而非 max", bool(sf) and "2.10" in sf[0]["msg"], sf[0]["msg"] if sf else "-")

print("\n【6】警報送達: 只有 JUDGMENT 時也必須呼叫發送 (2026-11-03 修過的漏報)")
import run
with mock.patch.object(run.notify, "send_alerts") as m:
    run._report([dict(level="JUDGMENT", msg="測試判決", action="")], None, "test")
    chk("JUDGMENT 單獨出現時有送出", m.called)
with mock.patch.object(run.notify, "send_alerts") as m:
    run._report([dict(level="INFO", msg="只是資訊", action="")], None, "test")
    chk("只有 INFO 時不送", not m.called)

print("\n【7】判決時鐘")
chk("JUDGMENT_START = 2026-11-25 (v3 重置)", C.JUDGMENT_START == "2026-11-25", C.JUDGMENT_START)
chk("資料起點 START_DATE 不變 (2026-07-17)", C.START_DATE == "2026-07-17", C.START_DATE)

shutil.rmtree(TMP, ignore_errors=True)
print(f"\n{'=' * 50}\n通過 {PASS} / 失敗 {len(FAIL)}")
if FAIL:
    print("失敗項目:", FAIL)
sys.exit(1 if FAIL else 0)
