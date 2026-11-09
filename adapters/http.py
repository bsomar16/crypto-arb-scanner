#!/usr/bin/env python3
"""Small stdlib-only HTTPS helper used by exchange adapters.

No credentials are logged. The caller owns endpoint-specific authentication.
"""
from __future__ import annotations

import json
import ssl
from typing import Any, Dict, Mapping, Optional
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


class ExchangeHTTPError(RuntimeError):
    pass


def request_json(
    method: str,
    url: str,
    *,
    params: Optional[Mapping[str, Any]] = None,
    body: Optional[Mapping[str, Any]] = None,
    headers: Optional[Mapping[str, str]] = None,
    timeout: float = 10.0,
) -> Dict[str, Any] | list[Any]:
    if params:
        query = urlencode({k: v for k, v in params.items() if v is not None})
        url = f"{url}{'&' if '?' in url else '?'}{query}"
    payload = None
    req_headers = {"Accept": "application/json", **(headers or {})}
    if body is not None:
        payload = json.dumps(body, separators=(",", ":")).encode()
        req_headers["Content-Type"] = "application/json"
    req = Request(url, data=payload, headers=req_headers, method=method.upper())
    try:
        with urlopen(req, timeout=timeout, context=ssl.create_default_context()) as response:
            raw = response.read().decode("utf-8")
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:1000]
        raise ExchangeHTTPError(f"HTTP {exc.code}: {detail}") from exc
    except URLError as exc:
        raise ExchangeHTTPError(f"network error: {exc.reason}") from exc
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ExchangeHTTPError("exchange returned invalid JSON") from exc
