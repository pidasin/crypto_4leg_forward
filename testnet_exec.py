# -*- coding: utf-8 -*-
"""合約測試網執行層 — 把帳面目標部位同步到 testnet.binancefuture.com

★定位: 水管演練, 不是判決記錄。
  判決永遠只看 state/ (paper) 的乾淨序列; 這裡壞了砍掉重來都無所謂。
★它能驗的: 簽章/湊整/最小名目/拒單/部位對帳/開空 —— 全是「按下去才知道」的東西
★它不能驗的: maker成交率 (測試網的簿子是合成的, 你的單沒排在真實隊伍裡,
  跑出來的成交率只是把回測假設再算一次 = 同義反覆)。那一項只有真金白銀能答。

執行方式: MARKET單。
  理由: 演練的標的是「帳面↔實際部位一致」這條水管, market單讓對帳是確定性的。
  maker掛單邏輯(掛單/等待/撤單重掛)反正在測試網也量不出真實成交率, 不在此演練。
"""
import os, time, hmac, hashlib, json
from urllib.parse import urlencode
from datetime import datetime, timezone
from decimal import Decimal
import requests
import config as C

BASE = "https://testnet.binancefuture.com"
assert "testnet" in BASE, "安全檢查: 只允許測試網"

STATE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "state_testnet")
os.makedirs(STATE, exist_ok=True)
ORDERS_F = os.path.join(STATE, "orders.jsonl")
RECON_F  = os.path.join(STATE, "recon.jsonl")
EQUITY_F = os.path.join(STATE, "equity.jsonl")

def _keys():
    return (os.environ.get("BINANCE_DEMO_KEY", "").strip(),
            os.environ.get("BINANCE_DEMO_SECRET", "").strip())

def enabled():
    k, s = _keys()
    return bool(k and s)

def _append(path, obj):
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(obj, ensure_ascii=False) + "\n")

def signed(method, path, params=None):
    k, s = _keys()
    p = dict(params or {})
    p["timestamp"] = int(time.time() * 1000)
    p["recvWindow"] = 10000
    q = urlencode(p)
    sig = hmac.new(s.encode(), q.encode(), hashlib.sha256).hexdigest()
    r = requests.request(method, f"{BASE}{path}?{q}&signature={sig}",
                         headers={"X-MBX-APIKEY": k}, timeout=20)
    try:    return r.status_code, r.json()
    except Exception: return r.status_code, {"raw": r.text[:200]}

def public(path, params=None):
    r = requests.get(BASE + path, params=params or {}, timeout=20)
    return r.json()

# ---------- 交易規則 (湊整/最小名目, 從交易所讀, 不硬編碼) ----------
_FILTERS = None
def filters():
    global _FILTERS
    if _FILTERS is None:
        info = public("/fapi/v1/exchangeInfo")
        _FILTERS = {}
        for s in info.get("symbols", []):
            d = {f["filterType"]: f for f in s.get("filters", [])}
            _FILTERS[s["symbol"]] = dict(
                step=Decimal(d["LOT_SIZE"]["stepSize"]),
                min_qty=Decimal(d["LOT_SIZE"]["minQty"]),
                min_notional=float(d.get("MIN_NOTIONAL", {}).get("notional", 5.0)),
            )
    return _FILTERS

def round_qty(symbol, qty):
    f = filters().get(symbol)
    if f is None: return None
    q = Decimal(str(abs(qty)))
    return float((q // f["step"]) * f["step"])

# ---------- 帳戶 ----------
def wallet_usdt():
    code, r = signed("GET", "/fapi/v2/balance")
    if code != 200: return None
    for b in r:
        if b.get("asset") == "USDT":
            return float(b["balance"])
    return None

def actual_positions():
    """{coin: 有號數量} — 測試網上實際持有的合約部位"""
    code, r = signed("GET", "/fapi/v2/positionRisk")
    if code != 200: return None
    out = {}
    for p in r:
        amt = float(p.get("positionAmt", 0))
        if abs(amt) > 0:
            sym = p["symbol"]
            if sym.endswith("USDT"):
                out[sym[:-4]] = out.get(sym[:-4], 0.0) + amt
    return out

def prices(coins):
    px = {}
    for c in coins:
        try:
            r = public("/fapi/v1/ticker/price", dict(symbol=c + "USDT"))
            px[c] = float(r["price"])
        except Exception:
            pass
    return px

# ---------- 同步 ----------
def net_targets(book_positions):
    """四腿帳面部位 → 各幣淨目標 (對總資金的比例)
    ★淨額互抵: 溢價BTC+0.21 與 DVOL BTC-0.63 只需下 -0.42 一筆
    """
    net = {}
    for leg, w in C.LEG_WEIGHTS.items():
        for coin, p in (book_positions.get(leg) or {}).items():
            net[coin] = net.get(coin, 0.0) + float(p) * w
    return net

def sync(book_positions, ts=None):
    """把測試網部位調整到帳面淨目標。回傳對帳摘要dict。
    任何一步失敗都只記錄不拋出 —— 測試網掛掉絕不能影響paper track。
    """
    ts = ts or datetime.now(timezone.utc).isoformat()
    out = dict(ts=ts, ok=False, orders=[], skipped=[], errors=[])

    tgt = net_targets(book_positions)
    cur = actual_positions()
    if cur is None:
        out["errors"].append("查部位失敗(API)")
        _append(RECON_F, out); return out
    px = prices(sorted(set(list(tgt.keys()) + list(cur.keys()))))
    cap = C.CAPITAL_USD

    for coin in sorted(set(list(tgt.keys()) + list(cur.keys()))):
        p = px.get(coin)
        if not p:
            out["errors"].append(f"{coin}: 無價格"); continue
        sym = coin + "USDT"
        f = filters().get(sym)
        if f is None:
            out["errors"].append(f"{coin}: 測試網無此合約"); continue

        want_qty = tgt.get(coin, 0.0) * cap / p          # 有號目標數量
        have_qty = cur.get(coin, 0.0)
        delta = want_qty - have_qty
        delta_usd = abs(delta) * p

        # 太小就不動 (避免灰塵單被拒/來回磨損)
        if delta_usd < max(f["min_notional"] * 1.1, 10.0):
            out["skipped"].append(f"{coin}: Δ${delta_usd:.2f}太小")
            continue

        qty = round_qty(sym, delta)
        if not qty or qty < float(f["min_qty"]):
            out["skipped"].append(f"{coin}: 湊整後低於minQty")
            continue
        side = "BUY" if delta > 0 else "SELL"
        code, o = signed("POST", "/fapi/v1/order", dict(
            symbol=sym, side=side, type="MARKET", quantity=f"{qty}"))
        rec = dict(ts=ts, coin=coin, side=side, qty=qty, ref_px=p,
                   target_frac=round(tgt.get(coin, 0.0), 6),
                   http=code, orderId=o.get("orderId"),
                   err=(None if code == 200 else str(o)[:150]))
        _append(ORDERS_F, rec)
        if code == 200:
            out["orders"].append(f"{coin} {side} {qty}")
        else:
            out["errors"].append(f"{coin}: 下單被拒 {str(o)[:100]}")

    # ---------- 對帳: 下單後實際 vs 帳面目標 ----------
    time.sleep(1.5)
    after = actual_positions() or {}
    recon, max_diff = [], 0.0
    for coin in sorted(set(list(tgt.keys()) + list(after.keys()))):
        p = px.get(coin)
        if not p: continue
        want_usd = tgt.get(coin, 0.0) * cap
        have_usd = after.get(coin, 0.0) * p
        diff = abs(want_usd - have_usd)
        max_diff = max(max_diff, diff)
        recon.append(dict(coin=coin, want_usd=round(want_usd, 2),
                          have_usd=round(have_usd, 2), diff_usd=round(diff, 2)))
    out["recon"] = recon
    out["max_diff_usd"] = round(max_diff, 2)
    out["wallet_usdt"] = wallet_usdt()
    # 容忍度: 最小名目造成的灰塵 + 湊整, 給$25。超過= 水管在漏
    out["ok"] = (not out["errors"]) and max_diff <= 25.0
    _append(RECON_F, out)
    snapshot(ts)          # ★每小時權益快照 → 儀表板的資料來源
    return out

def snapshot(ts=None):
    """完整帳戶快照 → equity.jsonl (儀表板每小時的一格)"""
    ts = ts or datetime.now(timezone.utc).isoformat()
    code, a = signed("GET", "/fapi/v2/account")
    if code != 200: return None
    pos = []
    for p in a.get("positions", []):
        amt = float(p.get("positionAmt", 0) or 0)
        if abs(amt) > 0 and p["symbol"].endswith("USDT"):
            pos.append(dict(
                coin=p["symbol"][:-4], amt=amt,
                entry=float(p.get("entryPrice", 0) or 0),
                upnl=round(float(p.get("unrealizedProfit", 0) or 0), 4),
                notional=round(abs(float(p.get("notional", 0) or 0)), 2),
            ))
    rec = dict(
        ts=ts,
        wallet=round(float(a.get("totalWalletBalance", 0)), 4),        # 已實現
        equity=round(float(a.get("totalMarginBalance", 0)), 4),        # 含未實現 = 真正的權益
        upnl=round(float(a.get("totalUnrealizedProfit", 0)), 4),
        gross=round(sum(x["notional"] for x in pos), 2),               # 總曝險
        positions=pos,
    )
    _append(EQUITY_F, rec)
    return rec

def recon_line():
    """給日報用的一行摘要"""
    if not os.path.exists(RECON_F): return None
    last = None
    with open(RECON_F, encoding="utf-8") as f:
        for line in f:
            if line.strip():
                try: last = json.loads(line)
                except Exception: pass
    if not last: return None
    if last.get("ok"):
        return "🔧 測試網對帳 ✅ 最大偏差 $%.2f · 錢包 %s USDT" % (
            last.get("max_diff_usd", 0),
            format(last.get("wallet_usdt") or 0, ",.0f"))
    return "🔧 測試網對帳 🔴 %s" % ("; ".join(last.get("errors") or ["偏差$%.2f>容忍" % last.get("max_diff_usd", 0)])[:150])
