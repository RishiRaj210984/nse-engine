"""Static configuration.

Everything the self-learning loop is allowed to tune lives in DEFAULT_PARAMS and
PARAM_GRID. The learner can only pick values from the grid and moves one grid
step per day, so a bad week cannot throw the system far off.
"""
import os
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
STATE_DIR = os.getenv("STATE_DIR", os.path.join(BASE_DIR, "..", "state"))
PUBLIC_DIR = os.getenv("PUBLIC_DIR", os.path.join(BASE_DIR, "..", "docs"))

# Display name -> Yahoo Finance symbol
GLOBAL_TICKERS = {
    "S&P 500": "^GSPC",
    "Nasdaq": "^IXIC",
    "Dow Jones": "^DJI",
    "US 10Y Yield": "^TNX",
    "Crude Oil": "CL=F",
    "Dollar Index": "DX-Y.NYB",
    "Nikkei 225": "^N225",
    "Hang Seng": "^HSI",
    "India VIX": "^INDIAVIX",
    "Nifty 50": "^NSEI",
    "Bank Nifty": "^NSEBANK",
}

# Lot sizes and expiry rules change by NSE circular. VERIFY on nseindia.com
# before trading and update here (or via env vars).
INDEX_SPECS = {
    "NIFTY": {
        "ticker": "^NSEI",
        "step": 50,
        "lot": int(os.getenv("NIFTY_LOT", 65)),
        "expiry": "weekly",
        "weekday": int(os.getenv("NIFTY_EXPIRY_WEEKDAY", 1)),  # 0=Mon, 1=Tue
    },
    "BANKNIFTY": {
        "ticker": "^NSEBANK",
        "step": 100,
        "lot": int(os.getenv("BANKNIFTY_LOT", 30)),
        "expiry": "monthly",
        "weekday": int(os.getenv("BANKNIFTY_EXPIRY_WEEKDAY", 1)),
    },
}
RISK_FREE_RATE = 0.065

# Liquid F&O stocks. Symbols that Yahoo no longer serves are skipped silently.
UNIVERSE = {
    "RELIANCE": "Reliance Industries", "TCS": "Tata Consultancy Services",
    "HDFCBANK": "HDFC Bank", "ICICIBANK": "ICICI Bank", "INFY": "Infosys",
    "BHARTIARTL": "Bharti Airtel", "SBIN": "State Bank of India", "ITC": "ITC",
    "LT": "Larsen & Toubro", "HINDUNILVR": "Hindustan Unilever",
    "KOTAKBANK": "Kotak Mahindra Bank", "AXISBANK": "Axis Bank",
    "BAJFINANCE": "Bajaj Finance", "MARUTI": "Maruti Suzuki",
    "SUNPHARMA": "Sun Pharma", "HCLTECH": "HCL Technologies",
    "M&M": "Mahindra & Mahindra", "TITAN": "Titan Company",
    "ULTRACEMCO": "UltraTech Cement", "NTPC": "NTPC", "ONGC": "ONGC",
    "POWERGRID": "Power Grid", "TATASTEEL": "Tata Steel",
    "ADANIENT": "Adani Enterprises", "ADANIPORTS": "Adani Ports",
    "ASIANPAINT": "Asian Paints", "BAJAJFINSV": "Bajaj Finserv",
    "WIPRO": "Wipro", "NESTLEIND": "Nestle India", "JSWSTEEL": "JSW Steel",
    "COALINDIA": "Coal India", "GRASIM": "Grasim", "TECHM": "Tech Mahindra",
    "HINDALCO": "Hindalco", "DRREDDY": "Dr Reddy's", "CIPLA": "Cipla",
    "BPCL": "BPCL", "EICHERMOT": "Eicher Motors", "HEROMOTOCO": "Hero MotoCorp",
    "BRITANNIA": "Britannia", "APOLLOHOSP": "Apollo Hospitals",
    "DIVISLAB": "Divi's Laboratories", "SBILIFE": "SBI Life",
    "HDFCLIFE": "HDFC Life", "INDUSINDBK": "IndusInd Bank",
    "BAJAJ-AUTO": "Bajaj Auto", "TRENT": "Trent", "BEL": "Bharat Electronics",
    "HAL": "Hindustan Aeronautics", "ETERNAL": "Eternal Zomato", "DLF": "DLF",
    "VEDL": "Vedanta", "TATAPOWER": "Tata Power", "PFC": "Power Finance Corp",
    "RECLTD": "REC Limited", "BANKBARODA": "Bank of Baroda",
    "PNB": "Punjab National Bank", "CANBK": "Canara Bank", "GAIL": "GAIL",
    "IOC": "Indian Oil", "SIEMENS": "Siemens India", "ABB": "ABB India",
    "DIXON": "Dixon Technologies", "PERSISTENT": "Persistent Systems",
    "COFORGE": "Coforge", "POLYCAB": "Polycab",
    "CHOLAFIN": "Cholamandalam Finance", "SHRIRAMFIN": "Shriram Finance",
    "TVSMOTOR": "TVS Motor", "GODREJCP": "Godrej Consumer",
    "PIDILITIND": "Pidilite", "HAVELLS": "Havells", "INDIGO": "InterGlobe Aviation",
    "JIOFIN": "Jio Financial", "BHEL": "BHEL", "SAIL": "SAIL", "NMDC": "NMDC",
    "LUPIN": "Lupin", "AUROPHARMA": "Aurobindo Pharma",
    "MUTHOOTFIN": "Muthoot Finance", "IDFCFIRSTB": "IDFC First Bank",
    "FEDERALBNK": "Federal Bank",
}

DEFAULT_PARAMS = {
    # Module B screener rules (your spec)
    "vol_mult": 2.5,
    "rsi_lo": 55,
    "rsi_hi": 70,
    "near_high_pct": 2.0,
    # Exit geometry in ATR multiples (targets also respect R1/R2 pivots)
    "sl_atr": 1.0,
    "t1_atr": 1.0,
    "t2_atr": 2.0,
    # Module A
    "bias_threshold": 0.35,  # z-score needed to call BULLISH/BEARISH
    "vix_high": 18.0,
    "news_weight": 0.0,   # starts OFF; learned from >=20 live days of bias results
    # Evidence-based filters (see README "Research"): the learner keeps them only if they help
    "max_day_chg": 7.0,   # skip stocks already up > X% today (short-term reversal risk)
    "regime_gate": 1,     # momentum longs only when Nifty > 50-DMA and up over 20 days
    "cost_pct": 0.25,     # round-trip delivery costs (STT, brokerage, charges) in %
}

PARAM_GRID = {
    "vol_mult": [1.5, 2.0, 2.5, 3.0, 3.5],
    "rsi_lo": [50, 55, 60],
    "rsi_hi": [65, 70, 75],
    "near_high_pct": [1.0, 2.0, 3.0, 4.0],
    "sl_atr": [0.75, 1.0, 1.25, 1.5],
    "t1_atr": [0.75, 1.0, 1.5],
    "t2_atr": [1.5, 2.0, 2.5, 3.0],
    "bias_threshold": [0.2, 0.3, 0.35, 0.45, 0.6],
    "max_day_chg": [4.0, 5.0, 7.0, 99.0],
    "regime_gate": [0, 1],
}

# Wider gate used for the journal: near-misses are recorded too, so the learner
# can see what would have happened with looser thresholds.
JOURNAL_GATE = {"vol_mult": 1.5, "rsi_lo": 50, "rsi_hi": 78, "near_high_pct": 4.0}

HORIZON_DAYS = 2            # 1-2 day holding period
MIN_SAMPLES_TUNE = 40       # min trades before a threshold may change
MIN_SAMPLES_MODEL = 80      # min trades before the ML ranker is used
LIVE_SAMPLE_WEIGHT = 3.0    # live outcomes count more than backtest outcomes
RECENCY_HALF_LIFE = 120     # trading days; older outcomes fade
