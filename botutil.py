#!/usr/bin/env python3
"""Shared helpers: http, escaping, json persistence, telegram sending (chunked)."""

import json
import urllib.request

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"


def http_json(url, timeout=20, retries=1):
    last = None
    for _ in range(retries + 1):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.loads(r.read().decode("utf-8"))
        except Exception as e:
            last = e
    raise last


def http_text(url, timeout=20):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8", "replace")


def esc(s):
    return str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def load_json(path, default):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def save_json(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)


def telegram_msg(token, chat_id, text):
    if not token or not chat_id:
        raise ValueError("missing telegram credentials")
    if len(text) <= 4000:
        _send(token, chat_id, text)
        return
    chunk = ""
    for part in text.split("\n"):
        if len(chunk) + len(part) + 1 > 3900:
            _send(token, chat_id, chunk.rstrip())
            chunk = part
        else:
            chunk = part if not chunk else chunk + "\n" + part
    if chunk.strip():
        _send(token, chat_id, chunk)


def _send(token, chat_id, text):
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {"chat_id": chat_id, "text": text, "parse_mode": "HTML",
               "disable_web_page_preview": True}
    req = urllib.request.Request(url, data=json.dumps(payload).encode(),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=25) as r:
        return r.read()