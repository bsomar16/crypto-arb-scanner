#!/usr/bin/env python3
"""Sentiment inputs: Fear & Greed, BTC dominance, keyword news score per symbol."""

import re
import xml.etree.ElementTree as ET
from botutil import http_json, http_text

POS = set("""surge surges surged rally rallies rallied rocket rockets soaring
gains gain gained record high highs all-time ath breakout breaks broke out
above higher upgrade upgrades upgraded bullish bull approve approved approval
adoption inflow inflows etf halving etf launch launches launched partnership
partnerships milestone buy bought accumulation recovery rebounds rebound beat
beats outperform outperforms signaling uptrend lowcap moonshot".split())

NEG = set("""plunge plunges plunged plunging crash crashes crashed dump dumped
collapse collapses collapsed slump slumps slid slide slides drop drops dropped
rejection rejected rejections bearish bear bears outflows outflow lawsuit
lawsuits banned ban sued sues sec hack hacked hacking exploit compromised
vulnerability sell selloff selling delist delisted downgrade downgraded fail
fails failed loss losses fraud arrested arrest probe risky risk warning
warnings fears fear scrutiny mixed outlook".split())

FNG_URL = "https://api.alternative.me/fng/?limit=1&format=json"
GLOBAL_URL = "https://api.coingecko.com/api/v3/global"


def fear_greed():
    try:
        d = http_json(FNG_URL, timeout=10)
        item = d["data"][0]
        return int(item["value"]), item.get("value_classification", "?")
    except Exception:
        return None, None


def btc_dominance():
    try:
        d = http_json(GLOBAL_URL, timeout=10)
        d = d["data"]
        pct = d["market_cap_percentage"]
        return float(pct.get("btc", 0)), float(pct.get("eth", 0)), \
               d["total_market_cap"]["usd"]
    except Exception:
        return None, None, None


def news_score(symbol, market="crypto"):
    """Return (label, pos, neg, count) via Bing news RSS keyword sentiment."""
    q = symbol if market == "stock" else symbol + " crypto"
    try:
        url = ("https://www.bing.com/news/search?q=" +
               urllib_url(q) + "&format=rss&count=15")
        xml = http_text(url, timeout=15)
        root = ET.fromstring(xml)
        items = root.findall(".//item")
        titles = [(i.findtext("title") or "") for i in items]
        pos = neg = 0
        for t in titles:
            words = set(re.findall(r"[a-z]+", t.lower()))
            pos += len(words & POS)
            neg += len(words & NEG)
        if not titles:
            return "No news", 0, 0, 0
        if pos > neg:
            label = "Bullish" if (pos - neg) >= 2 else "Mild bullish"
        elif neg > pos:
            label = "Bearish" if (neg - pos) >= 2 else "Mild bearish"
        else:
            label = "Neutral"
        return label, pos, neg, len(titles)
    except Exception:
        return "?", 0, 0, 0


def urllib_url(s):
    return re.sub(r"[^A-Za-z0-9+]+", "+", s).strip("+")