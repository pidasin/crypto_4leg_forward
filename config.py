# -*- coding: utf-8 -*-
"""四腿組合 forward track — 全域設定
★所有參數在此鎖死。任何修改都必須記錄在 CHANGELOG 並重置判決時鐘。
"""
from datetime import datetime, timezone

# ============ 上線日 (判決時鐘的起點, 不可更改) ============
START_DATE = "2026-07-17"

# ============ 資金配置 (等權, 不調整) ============
CAPITAL_USD   = 10000.0        # 名目本金 (paper mode 用)
LEG_WEIGHTS   = {"premium": 0.25, "dvol": 0.25, "aleg": 0.25, "tleg": 0.25}

# ============ 各腿參數 (鎖死, 來自回測定案) ============
PREMIUM = dict(
    smooth_hours = 24,          # 溢價平滑窗
    z_window     = 720,         # 30天 z分數窗
    clip         = 2.0,
    coins        = ["BTC", "ETH"],      # 各佔該腿一半
    cb_products  = {"BTC": "BTC-USD", "ETH": "ETH-USD"},
    bn_symbols   = {"BTC": "BTCUSDT",  "ETH": "ETHUSDT"},
)
DVOL = dict(
    z_window = 720,             # 30天
    clip     = 2.0,
    coin     = "BTC",           # 單幣
    currency = "BTC",           # Deribit DVOL currency
)
ALEG = dict(
    prem_ma_days = 7,           # CB溢價7日均
    fng_threshold = 60,         # FNG貪婪門檻
    coins = ["BTC","ETH","SOL","LTC","LINK","ADA","DOGE","XLM"],   # 8幣等權
    # 四象限: (CB多,FNG貪婪)=+1 / (CB多,FNG平淡)=+0.5 / (CB空,FNG貪婪)=-0.5 / (CB空,FNG平淡)=-1
)
TLEG = dict(
    ma_days = 50,               # SMA50
    coins = ["BTC","ETH","SOL"],        # 3幣等權
    long_only = True,           # 只做多
)

# ============ 執行 ============
MODE = "paper"                  # "paper" = 只記帳 | "testnet" = 幣安測試網下單
FEE_MAKER_BP = 2.0
FEE_TAKER_BP = 5.0

# ============ 第一層警戒閾值 (每次執行都檢查) ============
LAYER1 = dict(
    data_stale_hours   = 6,     # 【小時級資料】超過N小時沒更新 → 警報
    daily_stale_hours  = 30,    # 【日級資料】日K一天才更新一次, 給30h緩衝(避免每天誤報)
    fng_stale_hours    = 48,    # FNG是日級, 48h沒更新 → 警報
    maker_fill_min     = 0.70,  # maker成交率跌破 → 警報 (回測基準85-88%)
    slippage_max_bp    = 20.0,  # 單筆滑價超過 → 警報
    api_fail_max       = 3,     # 連續失敗次數 → 警報
    cache_max_age_h    = 48,    # ★快取超過N小時 → 該腿空手 (資料太舊, z分數會失真)
                                #   實戰教訓(2026-07-17): Deribit整站維護503時, 「選擇權鏈備援」
                                #   跟主來源同一家 → 一起掛。真正的備援是本地快取。
)

# ============ 第二層警戒閾值 (每月檢查) ============
LAYER2 = dict(
    premium_std_high   = 12.0,  # ★溢價std >12bp 才危險 (實測: 12-20bp區間Sharpe僅0.24)
                                #   注意: std「變小」不是問題 (<4bp區間Sharpe 1.56)
    leg_corr_max       = 0.60,  # 腿間相關 >0.6 → 分散失效 (基準: 0.002~0.31)
    exposure_pinned    = 0.90,  # |倉位|長期貼在上限的比例 → z分數失去區辨力
)

# ============ 第三層判決 (預先寫死, 不可事後修改) ============
# ★2026-09-05 修訂 v2 — 詳見 CHANGELOG.md
#   舊版12個月【逐腿處決】的誤殺率無法接受:
#     單腿波動 32~53%(組合只有23%), 1年資料的Sharpe估計標準誤≈1.0。
#     用記憶8.5年窗口的真實值(溢價1.12/DVOL1.39/A腿1.14/T腿1.35)算:
#       12個月 → 至少誤殺一條好腿的機率 53.3%, 期望誤殺 0.69 條
#     而處決不可逆, 且記憶有實例: 2018溢價-22.5%/A腿+129.8%, 2019反過來 —— 它們互救。
#     在2018或2019年底逐腿判決, 會砍掉隔年救命的那條。
#   決策依據(型I/型II錯誤不對稱):
#     誤殺好腿 = 永久失去該腿全部未來貢獻
#     留下死腿 = 只在多等的期間損失, 隨時可在下個檢查點補殺
#     10年淨效益(相對12個月): 18個月+20.7% / 2年+33.1% / 3年+44.6% / 4年+47.3%
#   結論: 績效判決延到36個月(誤殺率降到19.9%); 機制死亡則維持可即刻處決。
LAYER3 = dict(
    checkpoint_1_months = 6,    # 只看第一層, 不看賺賠
    checkpoint_2_months = 12,   # ★改為【只標記觀察, 不處決】
    checkpoint_3_months = 24,   # ★改為【標記 + 組合層可判決】(組合波動小, 誤差小得多)
    checkpoint_4_months = 36,   # ★新增: 逐腿正式判決 (誤殺率19.9%)
    sharpe_survive = 0.69,      # >0.69 續命 (= 誠實預期1.38的一半)
    sharpe_kill    = 0.30,      # <0.30 處決
    # ★機制死亡: 不需統計, 看得到就是死了 → 即刻處決, 不受上面的延長影響
    mech_frozen_days   = 90,    # 部位連續N天凍結 = z分數失去區辨力
    mech_recon_fail_days = 7,   # 對帳連續N天無法通過 = 執行層壞了
    mech_premium_std_floor = 4.0,  # 溢價std ≤ 2×成本(2bp) → 物理極限, 再聰明的正規化也救不了
    # 特別條款: BTC單週跌>15% 而 DVOL腿沒賺錢 → 立即處決DVOL腿
    # ★這條【不延長】: 它是機制判定不是統計判定 —— DVOL是危機保單, 危機沒賠付=保單無效,
    #   單一事件即足以證明。且實測歷史4次真崩盤DVOL腿0/4全虧(平均-15.02%)。
    dvol_crisis_btc_drop = -0.15,
    # T腿觀察: 它ETF後已從1.78衰減到0.58
    tleg_watch = True,
)

# ============ 誠實預期 (寫在這裡提醒自己) ============
HONEST = dict(
    backtest_sharpe = 2.10,     # 5.2年回測
    honest_sharpe   = 1.45,     # 打折後 (winner's curse: 40+家族挑4條)
    expected_mdd    = -24.6,
    expected_worst_underwater_days = 214,
    note = "別看2.10。溢價腿真OOS只有0.81, DVOL單獨DSR只有58%。"
)

# ============ 通知 ============
DISCORD_WEBHOOK_ENV = "DISCORD_WEBHOOK"   # 從環境變數讀

# ★判決時鐘起點 — 與資料起點分開
#   START_DATE 是【資料軌跡】起點, 動它會污染 nav.jsonl / history.html 的重建-真實邊界。
#   JUDGMENT_START 是【判決時鐘】起點; 依本檔開頭「任何修改都必須重置判決時鐘」的規矩,
#   2026-09-05 修訂 LAYER3 時一併重置。代價是損失已跑的1.6個月, 換誤殺率53.3%→19.9%。
#   ★重置是誠實的前提: 此次修訂發生在【尚未看到任何判決結果】之時(首個檢查點6個月未到),
#     動機是統計檢定力分析而非為了救某條腿。日後若在看到結果後才想改, 那就是自欺。
JUDGMENT_START = "2026-09-05"

def start_dt():
    return datetime.fromisoformat(START_DATE).replace(tzinfo=timezone.utc)

def judgment_start_dt():
    return datetime.fromisoformat(JUDGMENT_START).replace(tzinfo=timezone.utc)

def months_running():
    """判決時鐘已跑幾個月 (用於 LAYER3 檢查點)"""
    delta = datetime.now(timezone.utc) - judgment_start_dt()
    return delta.days / 30.44

def months_tracking():
    """資料軌跡已跑幾個月 (用於顯示 forward 累積了多少資料)"""
    delta = datetime.now(timezone.utc) - start_dt()
    return delta.days / 30.44
