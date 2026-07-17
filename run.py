# -*- coding: utf-8 -*-
"""主執行入口
用法:
  python run.py hourly    # 每小時: 溢價腿 + DVOL腿 + 第一層檢查
  python run.py daily     # 每日:   A腿 + T腿 + 第一層檢查 + NAV記錄
  python run.py monthly   # 每月:   第二層檢查
  python run.py judge     # 判決:   第三層 (12/18個月)
  python run.py status    # 看目前狀態
"""
import sys, os, json
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import numpy as np, pandas as pd
from datetime import datetime, timezone
import config as C
import datafeed as D
import book
from legs import signals
from health import checks
import notify

HOURLY_LEGS = ["premium", "dvol"]
DAILY_LEGS  = ["aleg", "tleg"]

def get_prices(coins):
    px = {}
    for c in coins:
        s = D.binance_klines(c+"USDT", "1h", 2)
        if s is not None: px[c] = round(float(s.iloc[-1]), 4)
    return px

def stale_legs(max_age_h=25):
    """★哪些日級腿過期了? (需要補跑)

    為什麼需要補跑:
      A/T腿原本只在 UTC 00:10 的 daily workflow 跑。若那次掛掉(API故障/Actions排隊/剛啟動),
      這兩腿就會停在舊部位等整整24小時 —— 而市場不會等你。
    為什麼補跑是安全的:
      A/T腿看的是【日K】, 訊號一天只變一次。錯過00:10在05:00補跑, 算出來的訊號完全一樣。
    """
    out = []
    for leg in DAILY_LEGS:
        age = book.leg_age_hours(leg)
        if age is None or age > max_age_h:
            out.append((leg, age))
    return out

def run_cycle(which):
    legs = list(HOURLY_LEGS if which=="hourly" else DAILY_LEGS)
    print(f"=== {which} cycle @ {datetime.now(timezone.utc).isoformat()} ===")

    # ★hourly 順便檢查日級腿有沒有過期 → 有就補跑 (冷啟動 / daily掛掉時自癒)
    if which == "hourly":
        for leg, age in stale_legs():
            legs.append(leg)
            why = "從未更新(冷啟動)" if age is None else f"已{age:.1f}h未更新(daily可能掛了)"
            print(f"  ⏳ 補跑 {leg}腿: {why}")

    res = signals.compute_all(only=legs)

    # 記帳: 每腿獨立
    old = book.load_positions()
    new = dict(old)
    updated = []

    # ★幣價必須涵蓋【所有腿】的所有幣, 不只本cycle更新的腿
    #   因為未更新的腿(如hourly時的A腿/T腿)其部位仍在市場中, PnL照樣要算
    all_coins = set()
    for leg_pos in list(old.values()) + [r["pos"] for r in res.values()]:
        all_coins |= set(leg_pos.keys())
    prices = get_prices(sorted(all_coins))
    turnovers = {}
    for leg, r in res.items():
        if not r["ok"]:
            print(f"  [{leg}] ❌ {r['diag'].get('error','無訊號')} → 維持原部位")
            continue
        t = book.record_trades(leg, old.get(leg, {}), r["pos"], prices)
        turnovers[leg] = round(t, 4)
        new[leg] = r["pos"]
        updated.append(leg)
        print(f"  [{leg}] ✅ 部位={ {k:round(v,4) for k,v in r['pos'].items()} } 換手={t:.4f}")
    book.save_positions(new, updated_legs=updated)

    # NAV (paper mode: 記錄部位與價格, 由分析時重建報酬)
    legs_state = {leg: dict(pos=new.get(leg,{}), turnover=turnovers.get(leg,0.0)) for leg in new}
    book.record_nav(legs_state, prices)

    # 🔴 第一層
    dh = D.health_report()
    a1 = checks.layer1(dh, res)
    book.record_health([dict(a) for a in a1], "layer1")
    _report(a1, res, which)
    return res, a1

def _report(alerts, res=None, which=""):
    crit = [a for a in alerts if a["level"]=="CRITICAL"]
    warn = [a for a in alerts if a["level"]=="WARN"]
    if crit:
        print(f"\n🔴 {len(crit)} 個 CRITICAL:")
        for a in crit: print(f"   • {a['msg']}\n     → {a['action']}")
    if warn:
        print(f"\n🟡 {len(warn)} 個 WARN:")
        for a in warn: print(f"   • {a['msg']}")
    if not crit and not warn:
        print("\n✅ 第一層全數通過")
    if crit or warn:
        notify.send_alerts(alerts, which)

def daily_summary():
    """📅 每日摘要 — 在daily cycle之後發, 一天一次"""
    pos = book.load_positions()
    # 讀今天的健康記錄
    H = book.read_jsonl(book.HEALTH_F)
    alerts_today = []
    if not H.empty:
        H["ts"] = pd.to_datetime(H["ts"])
        today = H[H["ts"] > pd.Timestamp.now(tz="UTC") - pd.Timedelta(hours=24)]
        for _, r in today.iterrows():
            alerts_today += (r["records"] or [])
    notify.send_daily_summary(pos, alerts_today, book.performance(), book.drawdown_now())

def monthly():
    print(f"=== monthly check @ {datetime.now(timezone.utc).isoformat()} ===")
    res = signals.compute_all()
    a2 = checks.layer2(res)
    book.record_health([dict(a) for a in a2], "layer2")
    if a2:
        for a in a2: print(f"  [{a['level']}] {a['msg']}\n     → {a['action']}")
        notify.send_alerts(a2, "monthly")
    else:
        print("  ✅ 第二層全數通過 (訊號健康)")
    return a2

def judge():
    print(f"=== 第三層判決 @ 運行 {C.months_running():.1f} 個月 ===")
    a3 = checks.layer3()
    book.record_health([dict(a) for a in a3], "layer3")
    for a in a3: print(f"  {a['msg']}\n     {a.get('action','')}")
    if a3: notify.send_alerts(a3, "judge")
    return a3

def status():
    m = C.months_running()
    pos = book.load_positions()
    R = book.leg_returns()
    print("="*66)
    print(f"四腿 forward track — 已運行 {m:.2f} 個月 (自 {C.START_DATE})")
    print("="*66)
    print(f"\n模式: {C.MODE}   名目本金: ${C.CAPITAL_USD:,.0f}")
    print(f"誠實預期: Sharpe {C.HONEST['honest_sharpe']} (回測{C.HONEST['backtest_sharpe']}, 已打折)")
    print(f"  {C.HONEST['note']}")
    print(f"\n【當前部位】(對各腿資金的比例)")
    for leg, p in pos.items():
        if p: print(f"  {leg:8} {json.dumps({k:round(v,4) for k,v in p.items()}, ensure_ascii=False)}")
        else: print(f"  {leg:8} (無部位)")
    print(f"\n【下一個檢查點】")
    for name, mo in [("6個月(只看有沒有壞掉)", C.LAYER3["checkpoint_1_months"]),
                     ("12個月(第一次正式判決)", C.LAYER3["checkpoint_2_months"]),
                     ("18個月(灰色地帶延長賽)", C.LAYER3["checkpoint_3_months"])]:
        left = mo - m
        print(f"  {name:26} {'✅ 已到' if left<=0 else f'還有 {left:.1f} 個月'}")
    tr = book.read_jsonl(book.TRADE_F)
    print(f"\n【累計】交易 {len(tr)} 筆   NAV記錄 {len(book.read_jsonl(book.NAV_F))} 筆")
    if not R.empty and len(R)>30:
        print(f"\n【各腿forward表現】(僅供參考, 12個月前不做判決)")
        for leg in R.columns:
            s=R[leg].dropna()
            if len(s)>30 and s.std()>0:
                print(f"  {leg:8} Sharpe {s.mean()/s.std()*np.sqrt(365):+.2f}")

if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv)>1 else "status"
    if cmd == "hourly": run_cycle("hourly")
    elif cmd == "daily":
        run_cycle("daily")
        daily_summary()          # ★每天一次的摘要 (你平常唯一會收到的東西)
    elif cmd == "summary": daily_summary()
    elif cmd=="monthly": monthly()
    elif cmd=="judge": judge()
    else: status()
