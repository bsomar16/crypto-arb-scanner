#!/usr/bin/env python3
"""Shared helpers: http, escaping, json persistence, telegram sending (chunked)."""

import json
import os
import re
import time
import urllib.error
import urllib.request

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
JSON_LOGS = False


def set_json_logs(flag):
    global JSON_LOGS
    JSON_LOGS = bool(flag)


def log(*parts):
    """Human-readable by default; one JSON line per call with --json-logs."""
    msg = " ".join(str(p) for p in parts)
    if JSON_LOGS:
        print(json.dumps({"ts": int(time.time()), "msg": msg}))
    else:
        print(msg)


def elog(event, **fields):
    """Structured progress line (JSON when --json-logs)."""
    row = {"event": event, **fields}
    if JSON_LOGS:
        print(json.dumps(row))
    else:
        print(event, " ".join(f"{k}={v}" for k, v in sorted(fields.items())))


def env_float(name, default):
    try:
        return float(os.environ.get(name, ""))
    except (TypeError, ValueError):
        return float(default)


def env_int(name, default):
    return int(env_float(name, default))


def fmt_price(p):
    try:
        p = float(p)
    except (TypeError, ValueError):
        return "$?"
    if p >= 1000:
        return f"${p:,.0f}"
    if p >= 1:
        return f"${p:,.2f}"
    if p >= 0.01:
        return f"${p:,.4f}"
    return f"${p:.6g}"


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


def _format_buy_message(text):
    """Convert the legacy BUY block into the compact signal format used by Telegram."""
    if "<b>CRYPTO BUY SIGNALS</b>" not in text:
        return text
    lines = text.splitlines()
    out = []
    i = 0
    while i < len(lines):
        line = lines[i]
        if re.fullmatch(r"<b>\#\d+</b>(?: ⭐)?", line.strip()):
            block = []
            j = i + 1
            while j < len(lines) and not re.fullmatch(r"<b>\#\d+</b>(?: ⭐)?", lines[j].strip()):
                if lines[j].startswith("🔔 <b>PRICE ALERTS</b>") or lines[j].startswith("━━━━━━━━"):
                    break
                block.append(lines[j])
                j += 1
            joined = "\n".join(block)
            m = re.search(r"🚀 <b>([^<]+)</b> · ([^·]+) · ([^\n]+)", joined)
            if not m:
                i = j
                continue
            coin, kind, interval = m.groups()
            setup = re.search(r"Setup: <b>([^<]+)</b>", joined)
            entry = re.search(r"Entry: <b>([^<]+)</b>", joined)
            stop = re.search(r"Stop: ([^\n]+)", joined)
            t1 = re.search(r"T1: ([^ ]+)", joined)
            t2 = re.search(r"T2: ([^ ]+)", joined)
            t3 = re.search(r"T3: ([^\n]+)", joined)
            metrics = re.search(r"Potential: <b>\+?([^<]+)</b> · Risk: ([^ ]+) · R:R ([^\n]+)", joined)
            score = re.search(r"Score: <b>([^<]+)</b> · RSI ([^ ]+) · volume ×([^ ]+) · 24h ([^\n]+)", joined)
            why = re.search(r"Why: ([^\n]+)", joined)
            if not (entry and stop and t1 and t2 and t3 and metrics and score):
                i = j
                continue
            out.extend([
                f"🟢 <b>CONFIRMED BUY SIGNAL | ${coin}</b> 🚀",
                f"⏱️ <b>{kind.strip().title()} ({interval.strip()})</b> | ⚡ <b>Setup:</b> {setup.group(1) if setup else 'Momentum'}",
                "",
                "<b>Action:</b> 🟢 BUY",
                f"<b>Entry:</b> {entry.group(1)}",
                f"<b>SL:</b> {stop.group(1)} ⛔",
                "",
                "🎯 <b>Targets:</b>",
                f"⏳ <b>TP1:</b> {t1.group(1)}",
                f"⏳ <b>TP2:</b> {t2.group(1)}",
                f"⏳ <b>TP3:</b> {t3.group(1)}",
                "",
                "📊 <b>Trade Metrics:</b>",
                f"📈 <b>Potential:</b> +{metrics.group(1)} | 📉 <b>Risk:</b> {metrics.group(2)} | ⚖️ <b>R:R:</b> {metrics.group(3)}",
                f"⭐ <b>Score:</b> {score.group(1)}",
                "",
                "🔍 <b>Technical Context:</b>",
                f"• <b>Data:</b> RSI {score.group(2)} | Vol ×{score.group(3)} | 24h {score.group(4)} | 4h: {re.search(r'4h: ([^\\n]+)', joined).group(1) if re.search(r'4h: ([^\\n]+)', joined) else '?'}",
                f"• <b>Why:</b> {why.group(1) if why else 'EMA structure &amp; MACD confirmation'}",
            ])
            if j < len(lines) and lines[j].startswith("🔔 <b>PRICE ALERTS</b>"):
                i = j
            else:
                i = j
            continue
        i += 1
    if not out:
        return text
    return "\n".join(out)


def telegram_msg(token, chat_id, text):
    if not token or not chat_id:
        raise ValueError("missing telegram credentials")
    text = _format_buy_message(text)
    if len(text) <= 4000:
        _send(token, chat_id, text)
        return
    chunk = ""

    def flush():
        nonlocal chunk
        if chunk.strip():
            _send(token, chat_id, chunk.rstrip())
        chunk = ""

    for raw in text.split("\n"):
        pieces = [raw[i:i + 3800] for i in range(0, len(raw), 3800)] or [""]
        for part in pieces:
            if len(chunk) + len(part) + 1 > 3900:
                flush()
            chunk = part if not chunk else chunk + "\n" + part
    flush()


def _send(token, chat_id, text):
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {"chat_id": chat_id, "text": text, "parse_mode": "HTML",
               "disable_web_page_preview": True}
    req = urllib.request.Request(url, data=json.dumps(payload).encode(),
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=25) as r:
            return r.read()
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", "replace")
        raise RuntimeError(f"telegram HTTP {e.code}: {body[:400]}") from e
