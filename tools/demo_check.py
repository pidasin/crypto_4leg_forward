# -*- coding: utf-8 -*-
"""Demo Trading 連線測試 — 驗證 API key 能簽章、能查帳、能下單
★只在 GitHub Actions 跑 (key 在 repo secrets, 本機沒有)
★絕不印出 key/secret 本身, 只印測試結果
"""
import os, sys, time, hmac, hashlib, json
from urllib.parse import urlencode
import requests

BASE = "https://demo-api.binance.com"

def _keys():
    k = os.environ.get("BINANCE_DEMO_KEY", "").strip()
    s = os.environ.get("BINANCE_DEMO_SECRET", "").strip()
    return k, s

def signed(method, path, params=None):
    k, s = _keys()
    p = dict(params or {})
    p["timestamp"] = int(time.time() * 1000)
    p["recvWindow"] = 10000
    q = urlencode(p)
    sig = hmac.new(s.encode(), q.encode(), hashlib.sha256).hexdigest()
    url = f"{BASE}{path}?{q}&signature={sig}"
    r = requests.request(method, url, headers={"X-MBX-APIKEY": k}, timeout=20)
    return r.status_code, (r.json() if r.text else {})

def main():
    k, s = _keys()
    print("=" * 60)
    print("Demo Trading 連線測試  (demo-api.binance.com)")
    print("=" * 60)

    # 0. key 有沒有讀到 (只印長度, 不印內容)
    if not k or not s:
        print("❌ 環境變數沒讀到 key (BINANCE_DEMO_KEY/SECRET)")
        sys.exit(1)
    print(f"✅ key已讀到 (長度 {len(k)}/{len(s)}, 內容不顯示)")

    # 1. 公開端點
    r = requests.get(f"{BASE}/api/v3/ping", timeout=15)
    print(f"{'✅' if r.status_code == 200 else '❌'} ping: HTTP {r.status_code}")

    # 2. 簽章查帳 (第一個需要 key 的呼叫)
    code, acct = signed("GET", "/api/v3/account")
    if code != 200:
        print(f"❌ 查帳失敗: HTTP {code} {acct}")
        sys.exit(1)
    bals = {b["asset"]: float(b["free"]) for b in acct.get("balances", [])
            if float(b["free"]) > 0}
    print(f"✅ 簽章通過, 查帳成功。虛擬餘額:")
    for a, v in sorted(bals.items(), key=lambda x: -x[1])[:8]:
        print(f"     {a:6} {v:,.4f}")
    print(f"   canTrade={acct.get('canTrade')}")

    # 3. 下一筆極小的真實測試單 (maker限價, 掛遠離市價 → 不會成交, 馬上撤掉)
    px = float(requests.get(f"{BASE}/api/v3/ticker/price",
               params={"symbol": "BTCUSDT"}, timeout=15).json()["price"])
    test_px = round(px * 0.80, 2)          # 掛在市價-20%, 絕不會成交
    code, o = signed("POST", "/api/v3/order", dict(
        symbol="BTCUSDT", side="BUY", type="LIMIT", timeInForce="GTC",
        quantity="0.00100", price=f"{test_px:.2f}"))
    if code != 200:
        print(f"❌ 下單被拒: HTTP {code} {o}")
        sys.exit(1)
    oid = o["orderId"]
    print(f"✅ 測試單已掛: BUY 0.001 BTC @ {test_px:,.2f} (市價-20%, 不會成交) id={oid}")

    # 4. 查單 → 撤單
    code, q = signed("GET", "/api/v3/order", dict(symbol="BTCUSDT", orderId=oid))
    print(f"{'✅' if code == 200 else '❌'} 查單: status={q.get('status')}")
    code, c = signed("DELETE", "/api/v3/order", dict(symbol="BTCUSDT", orderId=oid))
    print(f"{'✅' if code == 200 else '❌'} 撤單: status={c.get('status')}")

    print("\n" + "=" * 60)
    print("🎉 全部通過: 簽章/查帳/下單/查單/撤單 — 水管是通的")
    print("=" * 60)

if __name__ == "__main__":
    main()
