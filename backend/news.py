"""Free news: public RSS feeds (ET Markets, Moneycontrol, Google News) + VADER sentiment
with a finance word list. Headlines only; no paid feeds, no scraping of article bodies."""
import logging
import re
import urllib.parse

import feedparser
import requests
from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer

import store

log = logging.getLogger(__name__)
UA = "Mozilla/5.0 (compatible; MarketResearchBot/1.0)"


def gnews(query):
    q = urllib.parse.quote(query)
    return f"https://news.google.com/rss/search?q={q}&hl=en-IN&gl=IN&ceid=IN:en"


MARKET_FEEDS = [
    ("ET Markets", "https://economictimes.indiatimes.com/markets/rssfeeds/1977021501.cms"),
    ("Moneycontrol", "https://www.moneycontrol.com/rss/marketreports.xml"),
    ("Google News", gnews("Nifty Sensex stock market when:1d")),
]

FIN_LEXICON = {
    "upgrade": 2.0, "upgrades": 2.0, "upgraded": 2.0, "downgrade": -2.0, "downgrades": -2.0,
    "downgraded": -2.0, "beats": 1.6, "beat": 1.2, "miss": -1.6, "misses": -1.6,
    "surge": 1.8, "surges": 1.8, "soars": 2.0, "jumps": 1.5, "rally": 1.6, "rallies": 1.6,
    "gains": 1.2, "plunge": -2.2, "plunges": -2.2, "slumps": -2.0, "tumbles": -2.0,
    "crash": -2.5, "falls": -1.2, "slips": -1.0, "selloff": -2.0, "sell-off": -2.0,
    "bullish": 1.8, "bearish": -1.8, "outperform": 1.5, "underperform": -1.5,
    "buyback": 1.5, "dividend": 0.8, "bags": 1.4, "bagged": 1.4, "wins": 1.4,
    "order": 0.4, "record": 1.0, "profit": 1.0, "loss": -1.3, "losses": -1.3,
    "penalty": -1.6, "fraud": -3.0, "probe": -1.5, "raid": -2.0, "default": -2.2,
    "resigns": -1.2, "pledge": -1.0, "weak": -1.2, "strong": 1.2, "hike": -0.4,
    "cut": -0.3, "inflows": 1.0, "outflows": -1.0, "breakout": 1.5,
}

_an = SentimentIntensityAnalyzer()
_an.lexicon.update(FIN_LEXICON)


def score_text(text):
    return _an.polarity_scores(text)["compound"]


def _fetch(url, source, limit):
    try:
        resp = requests.get(url, headers={"User-Agent": UA}, timeout=10)
        resp.raise_for_status()
        feed = feedparser.parse(resp.content)
    except Exception as exc:
        log.info("news feed failed %s: %s", source, exc)
        return []
    items = []
    for e in feed.entries[:limit]:
        title = e.get("title", "").strip()
        src = source
        if source == "Google News" and " - " in title:  # "Headline - Publisher"
            title, src = title.rsplit(" - ", 1)
        if not title:
            continue
        items.append({
            "title": title,
            "source": src,
            "link": e.get("link"),
            "published": e.get("published", ""),
            "sentiment": round(score_text(title), 3),
        })
    return items


def summarize(items):
    if not items:
        return {"score": 0.0, "count": 0, "label": "NO NEWS"}
    s = sum(i["sentiment"] for i in items) / len(items)
    label = "POSITIVE" if s > 0.1 else "NEGATIVE" if s < -0.1 else "NEUTRAL"
    return {"score": round(s, 3), "count": len(items), "label": label}


def market_news(limit=40):
    cached = store.cache_get("news:market")
    if cached is not None:
        return cached
    items, seen = [], set()
    for source, url in MARKET_FEEDS:
        for it in _fetch(url, source, 20):
            key = re.sub(r"\W+", "", it["title"].lower())[:60]
            if key not in seen:
                seen.add(key)
                items.append(it)
    out = {"summary": summarize(items), "items": items[:limit]}
    store.cache_set("news:market", out, 900)
    return out


def ticker_news(symbol, name, limit=8):
    key = f"news:{symbol}"
    cached = store.cache_get(key)
    if cached is not None:
        return cached
    items = _fetch(gnews(f'"{name}" share when:3d'), "Google News", limit)
    out = {"summary": summarize(items), "items": items}
    store.cache_set(key, out, 1800)
    return out
