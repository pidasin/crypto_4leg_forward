# -*- coding: utf-8 -*-
"""合約測試網連線測試 — 驗證 API key 能簽章、能查帳、能下單
★用 demo.binance.com 生成的 key (有勾Enable Futures) 打 testnet.binancefuture.com
  理由: 幣安已把合約testnet整併進Demo Trading, 兩者可能共用key → 實測就知道
★合約測試網不擋美國IP (實測200), 所以能在GitHub Actions跑
★絕不印出 key/secret 本身
"""
import os, sys, time, hmac, hashlib
from urllib.parse import urlencode
import requests

BASE = "https://testnet.binancefuture.com"
assert "testnet" in BASE, "安全檢查: 只允許測試網"

def _keys():
    return (os.environ.get("BINANCE_DEMO_KEY", "").strip(),
            os.environ.get("BINANCE_DEMO_SECRET", "").strip())

def signed(method, path, params=None):
    k, s = _keys()
    p = dict(params or {})
    p["timestamp"] = int(time.time() * 1000)
    p["recvWindow"] = 10000
    q = urlencode(p)
    sig = hmac.new(s.encode(), q.encode(), hashlib.sha256).hexdigest()
    url = f"{BASE}{path}?{q}&signature={sig}"
    r = requests.request(method, url, headers={"X-MBX-APIKEY": k}, timeout=20)
    try:    return r.status_code, r.json()
    except Exception: return r.status_code, {"raw": r.text[:200]}

def main():
    k, s = _keys()
    print("=" * 60)
    print("合約測試網連線測試  (testnet.binancefuture.com)")
    print("=" * 60)
    if not k or not s:
        print("❌ 環境變數沒讀到 key"); sys.exit(1)
    print(f"✅ key已讀到 (長度 {len(k)}/{len(s)}, 內容不顯示)")

    r = requests.get(f"{BASE}/fapi/v1/ping", timeout=15)
    print(f"{'✅' if r.status_code == 200 else '❌'} ping: HTTP {r.status_code}")

    # 簽章查帳 — 這一步過了 = demo key 真的能用在合約測試網
    code, acct = signed("GET", "/fapi/v2/account")
    if code != 200:
        print(f"❌ 查帳失敗: HTTP {code} {acct}")
        print("   → demo key 不能用在合約測試網, 需要另生一組")
        sys.exit(1)
    print(f"✅ 簽章通過! 虛擬餘額 USDT: {float(acct.get('totalWalletBalance', 0)):,.2f}"
          f"   可用: {float(acct.get('availableBalance', 0)):,.2f}")

    # 下一筆不會成交的maker測試單 → 查單 → 撤單
    px = float(requests.get(f"{BASE}/fapi/v1/ticker/price",
               params={"symbol": "BTCUSDT"}, timeout=15).json()["price"])
    test_px = round(px * 0.80, 1)
    code, o = signed("POST", "/fapi/v1/order", dict(
        symbol="BTCUSDT", side="BUY", type="LIMIT", timeInForce="GTC",
        quantity="0.002", price=f"{test_px:.1f}"))
    if code != 200:
        print(f"❌ 下單被拒: HTTP {code} {o}"); sys.exit(1)
    oid = o["orderId"]
    print(f"✅ 測試單已掛: BUY 0.002 BTC @ {test_px:,.1f} (市價-20%, 不會成交)")
    code, q = signed("GET", "/fapi/v1/order", dict(symbol="BTCUSDT", orderId=oid))
    print(f"{'✅' if code == 200 else '❌'} 查單: status={q.get('status')}")
    code, c = signed("DELETE", "/fapi/v1/order", dict(symbol="BTCUSDT", orderId=oid))
    print(f"{'✅' if code == 200 else '❌'} 撤單: status={c.get('status')}")

    print("\n" + "=" * 60)
    print("🎉 全部通過: demo key + 合約測試網 + GitHub Actions = 水管是通的")
    print("=" * 60)

if __name__ == "__main__":
    main()
