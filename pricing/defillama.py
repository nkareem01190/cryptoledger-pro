"""
pricing/defillama.py — DefiLlama price engine for CryptoLedger Pro v5.0

Replaces CoinGecko with DefiLlama Coins API:
  • No API key required
  • No hard rate limit — no forced sleep between calls
  • Batch historical endpoint — fetches multiple coins in one POST
  • Contract-address-first resolution (chain:contract format)
  • Falls back to coingecko:{id} for native tokens

DefiLlama coin key format:
  ERC-20 / SPL tokens  →  "{chain}:{contract}"   e.g. "ethereum:0xdac17f..."
  Native / CoinGecko   →  "coingecko:{id}"        e.g. "coingecko:ethereum"

Endpoints used:
  POST https://coins.llama.fi/batchHistorical
       Body: {"coins": {"ethereum:0x...": [ts1,ts2,...], ...}, "searchWidth": "4h"}

  GET  https://coins.llama.fi/prices/historical/{timestamp}/{coin_key}
       Fallback for single lookups when batch misses.
"""

import time, json, os, requests
from datetime import datetime, timezone
from typing import Optional

import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from config import (
    COINGECKO_IDS, CONTRACT_TO_COINGECKO,
    STABLECOINS, STABLECOIN_POLICY,
)
import quality_log
from logger import get_logger, dbg

# ── DefiLlama endpoints ──────────────────────────────────────
DEFILLAMA_BATCH  = "https://coins.llama.fi/batchHistorical"
DEFILLAMA_SINGLE = "https://coins.llama.fi/prices/historical/{timestamp}/{coin_key}"

# ── Chain name → DefiLlama chain slug ───────────────────────
CHAIN_SLUG = {
    "Ethereum":        "ethereum",
    "BNB Smart Chain": "bsc",
    "Polygon":         "polygon",
    "Avalanche":       "avax",
    "Fantom":          "fantom",
    "Arbitrum":        "arbitrum",
    "Optimism":        "optimism",
    "Base":            "base",
    "Solana":          "solana",
    "Bitcoin":         "bitcoin",
}

# ── CoinGecko ID → DefiLlama coingecko: key ─────────────────
# Used for native tokens (ETH, BTC, BNB, SOL, etc.) that have
# no contract address. DefiLlama accepts "coingecko:{id}" directly.
COINGECKO_KEY = {sym: f"coingecko:{cg_id}"
                 for sym, cg_id in COINGECKO_IDS.items()}

# ── Caches ───────────────────────────────────────────────────
_price_cache:   dict = {}   # (coin_key, hour_ts) → float
_contract_cache: dict = {}  # (chain_name, contract) → coin_key | None

# Max coins per batch POST (DefiLlama allows large batches; 50 is safe)
_BATCH_SIZE = 50
# Search width passed to DefiLlama — finds nearest price within ±4h
_SEARCH_WIDTH = "4h"


# ────────────────────────────────────────────────────────────
# Internal helpers
# ────────────────────────────────────────────────────────────

def _round_to_hour(ts: int) -> int:
    return ts - (ts % 3600)


def _save_raw(data: dict, label: str, raw_dir: str) -> None:
    if not raw_dir:
        return
    try:
        os.makedirs(raw_dir, exist_ok=True)
        ts    = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
        fname = f"defillama_{label}_{ts}.json"
        path  = os.path.join(raw_dir, fname)
        if not os.path.exists(path):
            with open(path, "w") as f:
                json.dump({"fetched_at": datetime.utcnow().isoformat(),
                           "data": data}, f)
    except Exception:
        pass


def _contract_to_coin_key(contract: str, chain_name: str) -> Optional[str]:
    """
    Convert a contract address to a DefiLlama coin key.
    Resolution order:
      1. Local CONTRACT_TO_COINGECKO map  → coingecko:{id}
      2. Chain slug + contract            → {chain}:{contract}
    """
    if not contract:
        return None
    addr  = contract.lower().strip()
    cache_key = (chain_name, addr)
    if cache_key in _contract_cache:
        return _contract_cache[cache_key]

    # 1. Known contract → CoinGecko ID → coingecko: key
    if addr in CONTRACT_TO_COINGECKO:
        key = f"coingecko:{CONTRACT_TO_COINGECKO[addr]}"
        _contract_cache[cache_key] = key
        return key

    # 2. Build chain:contract key directly
    slug = CHAIN_SLUG.get(chain_name)
    if slug:
        key = f"{slug}:{addr}"
        _contract_cache[cache_key] = key
        return key

    _contract_cache[cache_key] = None
    return None


def _coin_key_for_tx(sym: str, contract: str,
                     chain_name: str) -> Optional[str]:
    """Return the DefiLlama coin key for a transaction row."""
    # Stablecoin with fixed policy — no lookup needed
    if sym in STABLECOINS and STABLECOIN_POLICY == "fixed_1":
        return None

    # Contract-address lookup first (prevents symbol collision)
    if contract:
        key = _contract_to_coin_key(contract, chain_name)
        if key:
            return key

    # Native / symbol fallback
    return COINGECKO_KEY.get(sym)


# ────────────────────────────────────────────────────────────
# Batch fetch (main workhorse)
# ────────────────────────────────────────────────────────────

def _post_batch(coins_payload: dict, raw_dir: str = "") -> dict:
    """
    POST to DefiLlama batchHistorical.
    coins_payload = { "coin_key": [ts1, ts2, ...], ... }
    Returns { "coin_key": { hour_ts: price, ... }, ... }
    """
    log = get_logger()
    body = {"coins": coins_payload, "searchWidth": _SEARCH_WIDTH}

    for attempt in range(4):
        try:
            r = requests.post(DEFILLAMA_BATCH, json=body, timeout=30)
            if r.status_code == 429:
                wait = int(r.headers.get("Retry-After", 10))
                log.warning(f"  ⚠️   DefiLlama rate-limit — waiting {wait}s …")
                time.sleep(wait)
                continue
            if r.status_code != 200:
                log.debug(f"DefiLlama batch HTTP {r.status_code}")
                time.sleep(2 ** attempt)
                continue

            data = r.json()
            _save_raw(data, "batch", raw_dir)

            result: dict = {}
            for coin_key, entries in (data.get("coins") or {}).items():
                prices_by_hour: dict = {}
                for entry in (entries.get("prices") or []):
                    ts    = int(entry.get("timestamp", 0))
                    price = float(entry.get("price", 0))
                    if ts and price:
                        hour_ts = _round_to_hour(ts)
                        prices_by_hour[hour_ts] = price
                result[coin_key] = prices_by_hour
            return result

        except requests.exceptions.Timeout:
            log.warning(f"  ⚠️   DefiLlama batch timeout (attempt {attempt+1}/4)")
            time.sleep(2 ** attempt)
        except Exception as e:
            log.debug(f"DefiLlama batch error: {e}")
            time.sleep(2 ** attempt)
    return {}


def _fetch_single(coin_key: str, ts: int,
                  raw_dir: str = "") -> Optional[float]:
    """
    Single-price fallback via GET /prices/historical/{ts}/{coin_key}.
    Used when a coin was missed in the batch (new token, unknown slug, etc.)
    """
    log = get_logger()
    url = DEFILLAMA_SINGLE.format(timestamp=ts, coin_key=coin_key)
    for attempt in range(3):
        try:
            r = requests.get(url, timeout=15)
            if r.status_code == 200:
                coins = r.json().get("coins", {})
                entry = coins.get(coin_key, {})
                price = entry.get("price")
                if price is not None:
                    _save_raw(r.json(), f"single_{coin_key[:20]}", raw_dir)
                    return float(price)
            elif r.status_code == 429:
                time.sleep(10); continue
            else:
                log.debug(f"DefiLlama single {coin_key}: HTTP {r.status_code}")
            return None
        except requests.exceptions.Timeout:
            time.sleep(2 ** attempt)
        except Exception as e:
            log.debug(f"DefiLlama single error [{coin_key}]: {e}")
    return None


# ────────────────────────────────────────────────────────────
# Public API — same signatures as coingecko.py
# ────────────────────────────────────────────────────────────

def batch_fetch_prices(txs: list, gas_token_map: dict,
                       raw_dir: str = "") -> None:
    """
    Pre-fetch all prices needed for the transaction list.
    Groups by coin_key and collects all required timestamps,
    then fires them in batched POST requests to DefiLlama.

    This replaces CoinGecko's one-day-at-a-time approach with
    a single pass of large batch calls — much faster overall.
    """
    from config import GAS_TOKEN
    log = get_logger()

    # ── Collect (coin_key → set of timestamps) ───────────────
    needed: dict = {}   # coin_key → set of unix timestamps

    for tx in txs:
        try:
            dt = datetime.strptime(tx["Date (UTC)"], "%Y-%m-%d %H:%M:%S")
            dt = dt.replace(tzinfo=timezone.utc)
            ts = int(dt.timestamp())
        except Exception:
            continue

        sym      = tx.get("Token/Asset", "").upper().strip()
        contract = tx.get("Contract",    "").lower().strip()
        chain    = tx.get("Blockchain",  "")

        # Token price
        key = _coin_key_for_tx(sym, contract, chain)
        if key:
            needed.setdefault(key, set()).add(ts)

        # Gas token price
        gas_sym = GAS_TOKEN.get(chain, "ETH")
        fee     = tx.get("Gas Fee (native)")
        if isinstance(fee, (int, float)) and fee > 0:
            if gas_sym not in STABLECOINS or STABLECOIN_POLICY == "market_price":
                gas_key = COINGECKO_KEY.get(gas_sym)
                if gas_key:
                    needed.setdefault(gas_key, set()).add(ts)

    if not needed:
        return

    log.info(f"  ℹ️   Fetching prices via DefiLlama — "
             f"{len(needed)} unique tokens, {sum(len(v) for v in needed.values())} timestamps …")
    log.info("  ℹ️   (batch POST — no forced sleep, much faster than CoinGecko)")

    # ── Fire in batches of _BATCH_SIZE coins per request ─────
    coin_keys  = list(needed.keys())
    total_done = 0

    for batch_start in range(0, len(coin_keys), _BATCH_SIZE):
        batch_keys = coin_keys[batch_start: batch_start + _BATCH_SIZE]
        payload    = {k: sorted(needed[k]) for k in batch_keys}

        print(f"\r  ⏳  [{batch_start + len(batch_keys):>4}/{len(coin_keys)}] "
              f"coins fetched …  ", end="", flush=True)

        results = _post_batch(payload, raw_dir)

        for coin_key, prices_by_hour in results.items():
            for hour_ts, price in prices_by_hour.items():
                _price_cache[(coin_key, hour_ts)] = price

        total_done += len(batch_keys)
        # Brief pause between batch POSTs to be a polite API citizen
        if batch_start + _BATCH_SIZE < len(coin_keys):
            time.sleep(0.3)

    print(f"\r  ✅  Price cache ready: {len(_price_cache)} price points.{' ' * 30}")
    log.info(f"  ✅  DefiLlama: {len(_price_cache)} prices cached "
             f"across {len(needed)} tokens")


def get_usd_price(symbol: str, dt: datetime,
                  contract: str = "",
                  chain_name: str = "",
                  raw_dir: str = "") -> Optional[float]:
    """
    Return USD price of token at datetime dt.

    Resolution order:
      1. Stablecoin policy check → 1.0 if fixed_1
      2. Cache lookup (populated by batch_fetch_prices)
      3. Walk back up to 4 hours for nearest cached candle
      4. Single-price GET fallback (for tokens missed in batch)
    """
    sym = symbol.upper().strip()

    # 1. Stablecoin
    if sym in STABLECOINS:
        if STABLECOIN_POLICY == "fixed_1":
            return 1.0

    # Resolve coin key
    coin_key = _coin_key_for_tx(sym, contract, chain_name)
    if not coin_key:
        return None

    # Ensure UTC
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    tx_ts   = int(dt.timestamp())
    tx_hour = _round_to_hour(tx_ts)

    # 2 & 3. Cache walk — check current hour and up to 4 hours back
    for offset in range(5):
        val = _price_cache.get((coin_key, tx_hour - offset * 3600))
        if val is not None:
            return val

    # 4. Single-price fallback (token was unknown at batch time)
    dbg(f"Cache miss for {coin_key} @ {dt.date()} — falling back to single fetch")
    price = _fetch_single(coin_key, tx_ts, raw_dir)
    if price is not None:
        _price_cache[(coin_key, tx_hour)] = price
        return price

    quality_log.log("No USD Price",
        f"Token={sym}  CoinKey={coin_key}  Date={dt.date()}  "
        f"— not found on DefiLlama. Add contract to config.py.",
        severity="Warning", token=sym, chain=chain_name)
    return None
