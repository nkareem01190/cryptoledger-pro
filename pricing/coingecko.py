"""
pricing/coingecko.py — CoinGecko price engine for CryptoLedger Pro v5.0

Key improvements over v4.0:
  • Contract-address-first resolution prevents symbol collision
  • Stablecoin policy: fixed_1 OR market_price (configurable)
  • Hourly candles via market_chart/range (not EOD /history)
  • Exponential backoff on rate limits
  • All price fetches logged with source evidence
  • Raw API responses optionally saved for audit trail
"""

import time, json, os, requests
from datetime import datetime, timezone
from typing import Optional

import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from config import (
    COINGECKO_URL, COINGECKO_IDS, CONTRACT_TO_COINGECKO,
    STABLECOINS, STABLECOIN_POLICY, COINGECKO_SLEEP,
)
import quality_log
from logger import get_logger, dbg

# ── Caches ───────────────────────────────────────────────────
_price_cache: dict  = {}   # (coin_id, hour_ts) → float
_range_fetched: set = set()  # (coin_id, midnight_ts) already fetched
_contract_cache: dict = {}   # contract_address → coin_id


def _round_to_hour(ts: int) -> int:
    return ts - (ts % 3600)


def _save_raw(data: dict, coin_id: str, date_str: str, raw_dir: str):
    """Save raw CoinGecko API response for audit trail."""
    if not raw_dir:
        return
    try:
        os.makedirs(raw_dir, exist_ok=True)
        fname = f"coingecko_{coin_id}_{date_str}.json"
        path  = os.path.join(raw_dir, fname)
        if not os.path.exists(path):
            with open(path, "w") as f:
                json.dump({"fetched_at": datetime.utcnow().isoformat(),
                           "coin_id": coin_id, "data": data}, f)
    except Exception:
        pass


def resolve_contract_to_coin_id(contract: str, chain_name: str) -> Optional[str]:
    """
    Resolve an ERC-20 contract address to a CoinGecko coin_id.
    Uses local CONTRACT_TO_COINGECKO map first, then CoinGecko API.
    Prevents symbol-collision pricing errors.
    """
    if not contract:
        return None
    addr = contract.lower().strip()
    if addr in _contract_cache:
        return _contract_cache[addr]
    if addr in CONTRACT_TO_COINGECKO:
        _contract_cache[addr] = CONTRACT_TO_COINGECKO[addr]
        return _contract_cache[addr]

    # Try CoinGecko /coins/{platform}/contract/{address}
    platform_map = {
        "Ethereum":        "ethereum",
        "BNB Smart Chain": "binance-smart-chain",
        "Polygon":         "polygon-pos",
        "Avalanche":       "avalanche",
        "Arbitrum":        "arbitrum-one",
        "Optimism":        "optimistic-ethereum",
        "Base":            "base",
        "Fantom":          "fantom",
    }
    platform = platform_map.get(chain_name)
    if not platform:
        return None
    try:
        url = f"{COINGECKO_URL}/coins/{platform}/contract/{addr}"
        r   = requests.get(url, timeout=15)
        if r.status_code == 200:
            coin_id = r.json().get("id")
            if coin_id:
                _contract_cache[addr] = coin_id
                dbg(f"Contract resolved: {addr[:10]}… → {coin_id}")
                return coin_id
        time.sleep(COINGECKO_SLEEP)
    except Exception:
        pass
    _contract_cache[addr] = None
    return None


def _fetch_range(coin_id: str, from_ts: int, to_ts: int,
                 raw_dir: str = "") -> dict:
    """
    Fetch hourly price candles from CoinGecko market_chart/range.
    Returns {hour_ts: price_usd}.
    Handles rate-limits with exponential backoff up to 5 attempts.
    """
    url    = f"{COINGECKO_URL}/coins/{coin_id}/market_chart/range"
    params = {"vs_currency": "usd", "from": from_ts, "to": to_ts}
    log    = get_logger()

    for attempt in range(5):
        try:
            r = requests.get(url, params=params, timeout=25)
            if r.status_code == 429:
                wait = int(r.headers.get("Retry-After", 65))
                log.warning(f"\r  ⚠️   CoinGecko rate-limit [{coin_id}] — waiting {wait}s …")
                time.sleep(wait)
                continue
            if r.status_code == 404:
                quality_log.log("No USD Price",
                    f"CoinGecko 404 for {coin_id} — coin not found",
                    severity="Warning", token=coin_id)
                return {}
            if r.status_code != 200:
                log.debug(f"CoinGecko HTTP {r.status_code} for {coin_id}")
                time.sleep(2 ** attempt)
                continue

            data   = r.json()
            prices = data.get("prices", [])
            result = {}
            for ms_ts, price in prices:
                hour_ts = _round_to_hour(int(ms_ts // 1000))
                result[hour_ts] = float(price)

            date_str = datetime.utcfromtimestamp(from_ts).strftime("%Y%m%d")
            _save_raw(data, coin_id, date_str, raw_dir)
            return result

        except requests.exceptions.Timeout:
            log.warning(f"  ⚠️   CoinGecko timeout [{coin_id}] attempt {attempt+1}/5")
            time.sleep(2 ** attempt)
        except Exception as e:
            log.debug(f"CoinGecko error [{coin_id}]: {e}")
            time.sleep(2 ** attempt)
    return {}


def _ensure_day_cached(coin_id: str, day_ts: int,
                       raw_dir: str = "") -> None:
    """Ensure the full day containing day_ts is in _price_cache."""
    midnight = day_ts - (day_ts % 86400)
    key      = (coin_id, midnight)
    if key in _range_fetched:
        return
    _range_fetched.add(key)
    from_ts = midnight - 7200
    to_ts   = midnight + 93600
    candles = _fetch_range(coin_id, from_ts, to_ts, raw_dir)
    for hour_ts, price in candles.items():
        cache_key = (coin_id, hour_ts)
        if cache_key not in _price_cache:
            _price_cache[cache_key] = price


def get_usd_price(symbol: str, dt: datetime,
                  contract: str = "",
                  chain_name: str = "",
                  raw_dir: str = "") -> Optional[float]:
    """
    Returns USD price of token at datetime dt.

    Resolution order:
      1. Stablecoin policy check
      2. Contract address → CoinGecko ID (prevents symbol collision)
      3. Symbol → CoinGecko ID (fallback for native tokens)
      4. Hourly candle lookup (intra-day accurate)

    Returns None if price cannot be determined.
    """
    sym = symbol.upper().strip()

    # 1. Stablecoin handling
    if sym in STABLECOINS:
        if STABLECOIN_POLICY == "fixed_1":
            return 1.0
        # else fall through to market price fetch

    # 2. Resolve coin_id — contract address first
    coin_id = None
    if contract:
        coin_id = resolve_contract_to_coin_id(contract, chain_name)

    # 3. Fallback to symbol map
    if not coin_id:
        coin_id = COINGECKO_IDS.get(sym)

    if not coin_id:
        return None   # truly unknown

    # 4. Ensure UTC-aware datetime
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    tx_ts   = int(dt.timestamp())
    tx_hour = _round_to_hour(tx_ts)

    _ensure_day_cached(coin_id, tx_ts, raw_dir)
    time.sleep(COINGECKO_SLEEP)

    # Walk back up to 4 hours to find nearest candle
    for offset in range(5):
        val = _price_cache.get((coin_id, tx_hour - offset * 3600))
        if val is not None:
            return val

    return None


def batch_fetch_prices(txs: list, gas_token_map: dict,
                       raw_dir: str = "") -> None:
    """
    Pre-fetch all unique (coin_id, day) pairs needed.
    Groups by day to minimise API calls.
    Includes gas token prices for gas USD valuation.
    """
    from config import GAS_TOKEN
    log    = get_logger()
    needed: set = set()

    for tx in txs:
        try:
            dt       = datetime.strptime(tx["Date (UTC)"], "%Y-%m-%d %H:%M:%S")
            dt       = dt.replace(tzinfo=timezone.utc)
            midnight = int(dt.timestamp())
            midnight = midnight - (midnight % 86400)
        except Exception:
            continue

        sym      = tx.get("Token/Asset", "").upper().strip()
        contract = tx.get("Contract", "").lower().strip()

        # Resolve coin_id for the transferred token
        coin_id = None
        if contract:
            coin_id = resolve_contract_to_coin_id(contract, tx.get("Blockchain",""))
        if not coin_id:
            coin_id = COINGECKO_IDS.get(sym)
        if sym in STABLECOINS and STABLECOIN_POLICY == "fixed_1":
            coin_id = None   # no fetch needed
        if coin_id:
            needed.add((coin_id, midnight))

        # Gas token
        gas_tok = GAS_TOKEN.get(tx.get("Blockchain", ""), "ETH")
        gas_id  = COINGECKO_IDS.get(gas_tok)
        fee     = tx.get("Gas Fee (native)")
        if gas_id and isinstance(fee, (int, float)) and fee:
            if gas_tok not in STABLECOINS or STABLECOIN_POLICY == "market_price":
                needed.add((gas_id, midnight))

    total = len(needed)
    if not total:
        return

    log.info(f"  ℹ️   Fetching intra-day USD prices — {total} unique coin/day pairs …")
    log.info("  ℹ️   (CoinGecko hourly candles — intra-day accurate, not end-of-day)")
    log.info(f"  ℹ️   (estimated time: ~{total * COINGECKO_SLEEP:.0f}s at safe rate)")

    for i, (coin_id, midnight) in enumerate(sorted(needed, key=lambda x: x[1]), 1):
        if (coin_id, midnight) in _range_fetched:
            continue
        dt_str = datetime.utcfromtimestamp(midnight).strftime("%Y-%m-%d")
        print(f"\r  ⏳  [{i:>4}/{total}]  {coin_id:<32} {dt_str}   ",
              end="", flush=True)
        _ensure_day_cached(coin_id, midnight + 43200, raw_dir)
        time.sleep(COINGECKO_SLEEP)

    print(f"\r  ✅  Price cache ready: {len(_price_cache)} hourly candles.{' '*30}")
    log.info(f"  ✅  Price cache: {len(_price_cache)} candles, {len(_range_fetched)} coin/day pairs fetched")
