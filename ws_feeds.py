#!/usr/bin/env python3
"""WebSocket real-time market data feeds.

Provides sub-100ms latency for trades, orderbook, klines, and funding rates.
Replaces REST polling for time-critical strategies.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections import defaultdict, deque
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Callable, Any, Set
from urllib.parse import urljoin

try:
    import websockets
    from websockets.client import WebSocketClientProtocol
    WEBSOCKETS_AVAILABLE = True
except ImportError:
    WEBSOCKETS_AVAILABLE = False
    websockets = None
    WebSocketClientProtocol = Any

logger = logging.getLogger(__name__)


@dataclass
class WSConfig:
    """WebSocket connection configuration."""
    url: str
    subscriptions: List[Dict]
    ping_interval: int = 20
    ping_timeout: int = 10
    max_reconnect_attempts: int = 10
    reconnect_delay: float = 5.0
    max_message_size: int = 16 * 1024 * 1024  # 16MB


@dataclass
class MarketData:
    """Standardized market data point."""
    exchange: str
    symbol: str
    timestamp: int
    data_type: str  # trade, kline, depth, funding, ticker
    payload: Dict


class WSConnection:
    """Manages a single WebSocket connection with auto-reconnect."""
    
    def __init__(self, config: WSConfig, handler: Callable[[MarketData], None]):
        self.config = config
        self.handler = handler
        self.ws: Optional[WebSocketClientProtocol] = None
        self.running = False
        self.reconnect_count = 0
        self.last_ping = 0
        self.subscribed = False
        
    async def connect(self) -> bool:
        """Establish WebSocket connection."""
        if not WEBSOCKETS_AVAILABLE:
            logger.warning("websockets library not available")
            return False
        try:
            self.ws = await websockets.connect(
                self.config.url,
                ping_interval=self.config.ping_interval,
                ping_timeout=self.config.ping_timeout,
                max_size=self.config.max_message_size
            )
            self.reconnect_count = 0
            logger.info(f"Connected to {self.config.url}")
            return True
        except Exception as e:
            logger.error(f"Connection failed to {self.config.url}: {e}")
            return False
    
    async def subscribe(self):
        """Send subscription messages."""
        if not self.ws or self.subscribed:
            return
        for sub in self.config.subscriptions:
            try:
                await self.ws.send(json.dumps(sub))
            except Exception as e:
                logger.error(f"Subscription failed: {e}")
        self.subscribed = True
    
    async def handle_messages(self):
        """Main message handling loop."""
        if not self.ws:
            return
            
        try:
            async for message in self.ws:
                try:
                    data = json.loads(message)
                    await self.process_message(data)
                except json.JSONDecodeError:
                    logger.warning(f"Invalid JSON from {self.config.url}")
                except Exception as e:
                    logger.error(f"Message processing error: {e}")
        except Exception as e:
            if WEBSOCKETS_AVAILABLE and isinstance(e, getattr(websockets.exceptions, "ConnectionClosed", ())):
                logger.warning(f"Connection closed: {self.config.url}")
            else:
                logger.error(f"Handler error: {e}")
    
    async def process_message(self, data: Dict):
        """Process incoming message - override in subclass."""
        pass
    
    async def run(self):
        """Run connection with auto-reconnect."""
        self.running = True
        while self.running and self.reconnect_count < self.config.max_reconnect_attempts:
            if await self.connect():
                await self.subscribe()
                await self.handle_messages()
            else:
                self.reconnect_count += 1
                if self.reconnect_count < self.config.max_reconnect_attempts:
                    delay = self.config.reconnect_delay * (2 ** min(self.reconnect_count, 5))
                    logger.info(f"Reconnecting in {delay}s (attempt {self.reconnect_count})")
                    await asyncio.sleep(delay)
        
        if self.reconnect_count >= self.config.max_reconnect_attempts:
            logger.error(f"Max reconnect attempts reached for {self.config.url}")


class BinanceWS(WSConnection):
    """Binance Spot + Futures WebSocket."""
    
    def __init__(self, symbols: List[str], handler: Callable[[MarketData], None], 
                 testnet: bool = False, futures: bool = True):
        base = "wss://stream.binancefuture.com" if futures else "wss://stream.binance.com"
        if testnet:
            base = "wss://stream.binancefuture.com" if futures else "wss://testnet.binance.vision"
        
        streams = []
        for sym in symbols:
            s = sym.lower()
            streams.append(f"{s}@trade")
            streams.append(f"{s}@kline_1m")
            streams.append(f"{s}@depth20@100ms")
            if futures:
                streams.append(f"{s}@markPrice@1s")
                streams.append(f"{s}@forceOrder")
        
        config = WSConfig(
            url=f"{base}/stream?streams={'/'.join(streams)}",
            subscriptions=[]
        )
        super().__init__(config, handler)
        self.symbols = {s.upper() for s in symbols}
    
    async def process_message(self, data: Dict):
        """Process Binance combined stream message."""
        if "stream" not in data or "data" not in data:
            return
            
        stream = data["stream"]
        payload = data["data"]
        parts = stream.split("@")
        if len(parts) != 2:
            return
            
        symbol = parts[0].upper()
        stream_type = parts[1]
        
        if "trade" in stream_type:
            await self.handle_trade(symbol, payload)
        elif "kline" in stream_type:
            await self.handle_kline(symbol, payload)
        elif "depth" in stream_type:
            await self.handle_depth(symbol, payload)
        elif "markPrice" in stream_type:
            await self.handle_mark_price(symbol, payload)
        elif "forceOrder" in stream_type:
            await self.handle_liquidation(symbol, payload)
    
    async def handle_trade(self, symbol: str, data: Dict):
        """Process trade data."""
        md = MarketData(
            exchange="BINANCE",
            symbol=symbol,
            timestamp=data.get("T", int(time.time() * 1000)),
            data_type="trade",
            payload={
                "price": float(data.get("p", 0)),
                "quantity": float(data.get("q", 0)),
                "side": "BUY" if not data.get("m", True) else "SELL",  # m = isBuyerMaker
                "trade_id": data.get("t", 0)
            }
        )
        self.handler(md)
    
    async def handle_kline(self, symbol: str, data: Dict):
        """Process kline data."""
        k = data.get("k", {})
        if not k.get("x", False):  # Only closed klines
            return
            
        md = MarketData(
            exchange="BINANCE",
            symbol=symbol,
            timestamp=k.get("t", 0),
            data_type="kline",
            payload={
                "interval": k.get("i", ""),
                "open": float(k.get("o", 0)),
                "high": float(k.get("h", 0)),
                "low": float(k.get("l", 0)),
                "close": float(k.get("c", 0)),
                "volume": float(k.get("v", 0)),
                "quote_volume": float(k.get("q", 0)),
                "trades": int(k.get("n", 0)),
                "closed": True
            }
        )
        self.handler(md)
    
    async def handle_depth(self, symbol: str, data: Dict):
        """Process orderbook depth."""
        md = MarketData(
            exchange="BINANCE",
            symbol=symbol,
            timestamp=int(time.time() * 1000),
            data_type="depth",
            payload={
                "bids": [[float(p), float(q)] for p, q in data.get("b", [])],
                "asks": [[float(p), float(q)] for p, q in data.get("a", [])],
                "last_update_id": data.get("u", 0)
            }
        )
        self.handler(md)
    
    async def handle_mark_price(self, symbol: str, data: Dict):
        """Process mark price (funding rate)."""
        md = MarketData(
            exchange="BINANCE",
            symbol=symbol,
            timestamp=data.get("E", int(time.time() * 1000)),
            data_type="funding",
            payload={
                "mark_price": float(data.get("p", 0)),
                "index_price": float(data.get("i", 0)),
                "funding_rate": float(data.get("r", 0)) * 100,  # to %
                "next_funding_time": data.get("T", 0)
            }
        )
        self.handler(md)
    
    async def handle_liquidation(self, symbol: str, data: Dict):
        """Process liquidation order."""
        md = MarketData(
            exchange="BINANCE",
            symbol=symbol,
            timestamp=data.get("T", int(time.time() * 1000)),
            data_type="liquidation",
            payload={
                "side": data.get("S", ""),
                "quantity": float(data.get("q", 0)),
                "price": float(data.get("p", 0)),
                "order_id": data.get("o", "")
            }
        )
        self.handler(md)


class BybitWS(WSConnection):
    """Bybit V5 WebSocket (Spot + Linear/Inverse Futures)."""
    
    def __init__(self, symbols: List[str], handler: Callable[[MarketData], None],
                 testnet: bool = False, futures: bool = True):
        base = "wss://stream.bybit.com/v5/public/linear" if futures else "wss://stream.bybit.com/v5/public/spot"
        if testnet:
            base = "wss://stream-testnet.bybit.com/v5/public/linear" if futures else "wss://stream-testnet.bybit.com/v5/public/spot"
        
        config = WSConfig(
            url=base,
            subscriptions=[]
        )
        super().__init__(config, handler)
        self.symbols = symbols
        self.futures = futures
        self._subscribed = False
    
    async def subscribe(self):
        """Send Bybit subscription messages."""
        if self._subscribed or not self.ws:
            return
            
        args = []
        for sym in self.symbols:
            args.append(f"{sym}.trade")
            args.append(f"{sym}.kline.1")
            args.append(f"{sym}.orderbook.50")
            if self.futures:
                args.append(f"{sym}.markPrice")
                args.append(f"{sym}.liquidation")
        
        sub_msg = {
            "op": "subscribe",
            "args": args
        }
        await self.ws.send(json.dumps(sub_msg))
        self._subscribed = True
        logger.info(f"Bybit subscribed to {len(args)} streams")
    
    async def process_message(self, data: Dict):
        """Process Bybit V5 message."""
        topic = data.get("topic", "")
        if not topic or "data" not in data:
            return
            
        # Format: publicTrade.BTCUSDT, kline.1.BTCUSDT, orderbook.50.BTCUSDT
        parts = topic.split(".")
        if len(parts) < 2:
            return
            
        data_type = parts[0]
        symbol = parts[-1]
        
        for item in data.get("data", []):
            if data_type == "publicTrade":
                await self.handle_trade(symbol, item)
            elif data_type.startswith("kline"):
                await self.handle_kline(symbol, item)
            elif data_type == "orderbook":
                await self.handle_depth(symbol, item)
            elif data_type == "markPrice":
                await self.handle_mark_price(symbol, item)
            elif data_type == "liquidation":
                await self.handle_liquidation(symbol, item)
    
    async def handle_trade(self, symbol: str, data: Dict):
        md = MarketData(
            exchange="BYBIT",
            symbol=symbol,
            timestamp=data.get("T", int(time.time() * 1000)),
            data_type="trade",
            payload={
                "price": float(data.get("p", 0)),
                "quantity": float(data.get("v", 0)),
                "side": data.get("S", ""),
                "trade_id": data.get("i", "")
            }
        )
        self.handler(md)
    
    async def handle_kline(self, symbol: str, data: Dict):
        if not data.get("confirm", False):
            return
        md = MarketData(
            exchange="BYBIT",
            symbol=symbol,
            timestamp=data.get("start", 0),
            data_type="kline",
            payload={
                "interval": data.get("interval", ""),
                "open": float(data.get("open", 0)),
                "high": float(data.get("high", 0)),
                "low": float(data.get("low", 0)),
                "close": float(data.get("close", 0)),
                "volume": float(data.get("volume", 0)),
                "quote_volume": float(data.get("turnover", 0)),
                "closed": True
            }
        )
        self.handler(md)
    
    async def handle_depth(self, symbol: str, data: Dict):
        md = MarketData(
            exchange="BYBIT",
            symbol=symbol,
            timestamp=int(time.time() * 1000),
            data_type="depth",
            payload={
                "bids": [[float(p), float(q)] for p, q in data.get("b", [])],
                "asks": [[float(p), float(q)] for p, q in data.get("a", [])],
                "timestamp": data.get("ts", 0)
            }
        )
        self.handler(md)
    
    async def handle_mark_price(self, symbol: str, data: Dict):
        md = MarketData(
            exchange="BYBIT",
            symbol=symbol,
            timestamp=int(time.time() * 1000),
            data_type="funding",
            payload={
                "mark_price": float(data.get("markPrice", 0)),
                "index_price": float(data.get("indexPrice", 0)),
                "funding_rate": float(data.get("fundingRate", 0)) * 100,
                "next_funding_time": data.get("nextFundingTime", 0)
            }
        )
        self.handler(md)
    
    async def handle_liquidation(self, symbol: str, data: Dict):
        md = MarketData(
            exchange="BYBIT",
            symbol=symbol,
            timestamp=data.get("time", int(time.time() * 1000)),
            data_type="liquidation",
            payload={
                "side": data.get("side", ""),
                "quantity": float(data.get("qty", 0)),
                "price": float(data.get("price", 0))
            }
        )
        self.handler(md)


class OKXWS(WSConnection):
    """OKX WebSocket (Spot + Swap)."""
    
    def __init__(self, symbols: List[str], handler: Callable[[MarketData], None],
                 testnet: bool = False, futures: bool = True):
        base = "wss://ws.okx.com:8443/api/v5/market" if futures else "wss://ws.okx.com:8443/api/v5/market"
        if testnet:
            base = "wss://wspap.okx.com:8443/api/v5/market"
        
        config = WSConfig(url=base, subscriptions=[])
        super().__init__(config, handler)
        self.symbols = symbols
        self.futures = futures
    
    async def subscribe(self):
        """Send OKX subscription."""
        if not self.ws:
            return
            
        channels = []
        for sym in self.symbols:
            inst_id = sym if "-" in sym else f"{sym}-USDT"
            channels.append({"channel": "trades", "instId": inst_id})
            channels.append({"channel": "candle1m", "instId": inst_id})
            channels.append({"channel": "books50-l2-tbt", "instId": inst_id})
            if self.futures:
                channels.append({"channel": "mark-price", "instId": inst_id})
                channels.append({"channel": "liquidation", "instId": inst_id})
        
        await self.ws.send(json.dumps({
            "op": "subscribe",
            "args": channels
        }))
        logger.info(f"OKX subscribed to {len(channels)} channels")
    
    async def process_message(self, data: Dict):
        """Process OKX message."""
        if "event" in data:  # Subscription confirmation
            return
            
        arg = data.get("arg", {})
        channel = arg.get("channel", "")
        inst_id = arg.get("instId", "")
        symbol = inst_id.replace("-", "")
        
        for item in data.get("data", []):
            if channel == "trades":
                await self.handle_trade(symbol, item)
            elif channel.startswith("candle"):
                await self.handle_kline(symbol, item)
            elif channel.startswith("books"):
                await self.handle_depth(symbol, item)
            elif channel == "mark-price":
                await self.handle_mark_price(symbol, item)
            elif channel == "liquidation":
                await self.handle_liquidation(symbol, item)
    
    async def handle_trade(self, symbol: str, data: Dict):
        md = MarketData(
            exchange="OKX",
            symbol=symbol,
            timestamp=int(data.get("ts", 0)),
            data_type="trade",
            payload={
                "price": float(data.get("px", 0)),
                "quantity": float(data.get("sz", 0)),
                "side": data.get("side", ""),
                "trade_id": data.get("tradeId", "")
            }
        )
        self.handler(md)
    
    async def handle_kline(self, symbol: str, data: List):
        # OKX: [ts, o, h, l, c, vol, volCcy, confirm]
        if len(data) < 8 or data[7] != "1":
            return
        md = MarketData(
            exchange="OKX",
            symbol=symbol,
            timestamp=int(data[0]),
            data_type="kline",
            payload={
                "interval": "1m",
                "open": float(data[1]),
                "high": float(data[2]),
                "low": float(data[3]),
                "close": float(data[4]),
                "volume": float(data[5]),
                "quote_volume": float(data[6]),
                "closed": True
            }
        )
        self.handler(md)
    
    async def handle_depth(self, symbol: str, data: Dict):
        bids = data.get("bids", [])
        asks = data.get("asks", [])
        md = MarketData(
            exchange="OKX",
            symbol=symbol,
            timestamp=int(data.get("ts", 0)),
            data_type="depth",
            payload={
                "bids": [[float(p), float(q)] for p, q, *_ in bids],
                "asks": [[float(p), float(q)] for p, q, *_ in asks],
                "timestamp": int(data.get("ts", 0))
            }
        )
        self.handler(md)
    
    async def handle_mark_price(self, symbol: str, data: Dict):
        md = MarketData(
            exchange="OKX",
            symbol=symbol,
            timestamp=int(time.time() * 1000),
            data_type="funding",
            payload={
                "mark_price": float(data.get("markPx", 0)),
                "index_price": float(data.get("indexPx", 0)),
                "funding_rate": float(data.get("fundingRate", 0)) * 100,
                "next_funding_time": int(data.get("nextFundingTime", 0))
            }
        )
        self.handler(md)
    
    async def handle_liquidation(self, symbol: str, data: Dict):
        md = MarketData(
            exchange="OKX",
            symbol=symbol,
            timestamp=int(data.get("ts", 0)),
            data_type="liquidation",
            payload={
                "side": data.get("side", ""),
                "quantity": float(data.get("sz", 0)),
                "price": float(data.get("bx", 0)),  # bankruptcy price
                "order_id": data.get("ordId", "")
            }
        )
        self.handler(md)


class WSManager:
    """Manages multiple WebSocket connections."""
    
    def __init__(self):
        self.connections: List[WSConnection] = []
        self.handlers: Dict[str, List[Callable]] = defaultdict(list)
        self.data_buffer: Dict[str, deque] = defaultdict(lambda: deque(maxlen=1000))
        self.latest: Dict[str, MarketData] = {}
        self.running = False
    
    def add_handler(self, data_type: str, handler: Callable[[MarketData], None]):
        """Add handler for specific data type."""
        self.handlers[data_type].append(handler)
    
    def _route_message(self, md: MarketData):
        """Route market data to handlers and buffer."""
        key = f"{md.exchange}:{md.symbol}:{md.data_type}"
        self.data_buffer[key].append(md)
        self.latest[key] = md
        
        for handler in self.handlers.get(md.data_type, []):
            try:
                handler(md)
            except Exception as e:
                logger.error(f"Handler error for {md.data_type}: {e}")
    
    def add_connection(self, conn: WSConnection):
        """Add connection with message routing."""
        original_handler = conn.handler
        conn.handler = self._route_message
        self.connections.append(conn)
    
    async def start_all(self):
        """Start all connections."""
        self.running = True
        tasks = [conn.run() for conn in self.connections]
        await asyncio.gather(*tasks, return_exceptions=True)
    
    def stop_all(self):
        """Stop all connections."""
        self.running = False
        for conn in self.connections:
            conn.running = False
            if conn.ws:
                asyncio.create_task(conn.ws.close())
    
    def get_latest(self, exchange: str, symbol: str, data_type: str) -> Optional[MarketData]:
        """Get latest market data point."""
        key = f"{exchange}:{symbol}:{data_type}"
        return self.latest.get(key)
    
    def get_buffer(self, exchange: str, symbol: str, data_type: str, max_items: int = 100) -> List[MarketData]:
        """Get buffered data."""
        key = f"{exchange}:{symbol}:{data_type}"
        return list(self.data_buffer[key])[-max_items:]


async def create_ws_manager(symbols: List[str], 
                           exchanges: List[str] = None,
                           futures: bool = True) -> WSManager:
    """Factory to create configured WSManager."""
    manager = WSManager()
    exchanges = exchanges or ["BINANCE", "BYBIT", "OKX"]
    
    if "BINANCE" in exchanges:
        manager.add_connection(BinanceWS(symbols, manager._route_message, futures=futures))
    
    if "BYBIT" in exchanges:
        manager.add_connection(BybitWS(symbols, manager._route_message, futures=futures))
    
    if "OKX" in exchanges:
        manager.add_connection(OKXWS(symbols, manager._route_message, futures=futures))
    
    return manager