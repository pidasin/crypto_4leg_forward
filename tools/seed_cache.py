# -*- coding: utf-8 -*-
"""★冷啟動: 用回測資料初始化本地快取

為什麼需要: DVOL的z分數要30天歷史。若第一次上線時官方API剛好掛掉,
快取建不起來 → 備援算得出即時值也沒用 → 這條腿永遠起不來。

解法: 用回測階段已抓好的歷史(dvol_full.pkl, 2021-03起5.2年)直接種進快取。
之後每次成功抓取會自動更新, 快取永遠不會斷。

用法: python tools/seed_cache.py
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import pickle, pandas as pd
import cache

# 回測階段抓好的資料 (scratchpad)
BACKTEST = r"C:\Users\waner\AppData\Local\Temp\claude\C--Users-waner-Desktop-claudetest\4cf91cb9-0b32-4134-9080-deff62332aee\scratchpad"

SOURCES = [
    ("dvol_btc", os.path.join(BACKTEST, "dvol_full.pkl"), "Deribit BTC DVOL"),
]

def seed():
    for name, path, desc in SOURCES:
        if not os.path.exists(path):
            print(f"  ❌ {desc}: 找不到 {path}")
            continue
        s = pickle.load(open(path, "rb"))
        if not isinstance(s, pd.Series):
            print(f"  ❌ {desc}: 格式不符"); continue
        # 只保留最近的部分即可 (z窗只需720點, 留2000點緩衝)
        s = s.tail(2000)
        cache.save_series(name, s, max_points=2000)
        print(f"  ✅ {desc} → cache_{name}: {len(s)}點  {s.index[0]} ~ {s.index[-1]}")

if __name__ == "__main__":
    print("=== 種入快取 (冷啟動用) ===")
    seed()
    print("\n=== 快取狀態 ===")
    for k, v in cache.cache_status().items():
        print(f"  {k}: {v['points']}點, 跨度{v['span_days']:.0f}天, 最新資料{v['age_hours']:.1f}h前")
