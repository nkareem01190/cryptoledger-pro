"""
accounting/direction.py — Debit/Credit/USD valuation for CryptoLedger Pro v5.0

Runs AFTER classification. Uses tx_class and fifo_action to set
direction instead of raw from/to addresses.
"""

import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from datetime import datetime, timezone
import quality_log
from config import STABLECOINS, GAS_TOKEN
from logger import get_logger


def compute_direction_and_usd(txs: list, wallet: str,
                               price_fn, raw_dir: str = "") -> list:
    """
    Sets: Debit, Credit, Direction, USD Price,
          Debit (USD), Credit (USD), Gas Fee (USD)

    Direction is derived from fifo_action (set by classifier),
    not from raw from/to address comparison.

    Unknown transactions: both Debit and Credit = None.
    """
    log     = get_logger()
    unk_cnt = 0
    no_price_tokens: set = set()

    for tx in txs:
        val        = float(tx.get("Value", 0) or 0)
        fifo_act   = tx.get("fifo_action", "none")
        tx_class   = tx.get("tx_class", "Unknown")
        sym        = tx.get("Token/Asset", "").upper().strip()
        contract   = tx.get("Contract", "").lower().strip()
        blockchain = tx.get("Blockchain", "")

        # Direction from classifier
        if fifo_act == "acquisition":
            direction = "credit"
        elif fifo_act == "disposal":
            direction = "debit"
        else:
            direction = "none"   # no asset movement (approval, failed, etc.)

        # Classify "none" as unknown for display purposes only if class is Unknown
        if tx_class == "Unknown":
            direction = "unknown"
            unk_cnt  += 1

        if direction == "debit":
            tx["Debit"]  = round(val, 8)
            tx["Credit"] = None
        elif direction == "credit":
            tx["Debit"]  = None
            tx["Credit"] = round(val, 8)
        else:
            tx["Debit"]  = None
            tx["Credit"] = None

        tx["Direction"] = direction

        # ── Asset USD price ──────────────────────────────────
        dt = None
        try:
            dt = datetime.strptime(tx["Date (UTC)"], "%Y-%m-%d %H:%M:%S")
            dt = dt.replace(tzinfo=timezone.utc)
        except Exception:
            pass

        usd = None
        if sym and dt is not None:
            usd = price_fn(sym, dt, contract=contract,
                           chain_name=blockchain, raw_dir=raw_dir)
            if usd is None and sym not in STABLECOINS:
                no_price_tokens.add(sym)
                quality_log.log("No USD Price",
                    f"Token={sym}  Date={tx['Date (UTC)'][:10]}  "
                    f"Hash={tx.get('Tx Hash','?')[:16]}…",
                    severity="Warning",
                    tx_hash=tx.get("Tx Hash","")[:20],
                    token=sym, chain=blockchain)

        tx["USD Price"]    = round(usd, 6) if usd is not None else None
        tx["Debit (USD)"]  = round(val * usd, 2) if usd and direction == "debit"   else None
        tx["Credit (USD)"] = round(val * usd, 2) if usd and direction == "credit"  else None

        # ── Gas fee USD ──────────────────────────────────────
        fee       = tx.get("Gas Fee (native)")
        gas_token = GAS_TOKEN.get(blockchain, "ETH")
        if isinstance(fee, (int, float)) and fee > 0 and dt is not None:
            gas_price = price_fn(gas_token, dt, chain_name=blockchain,
                                 raw_dir=raw_dir)
            tx["Gas Fee (USD)"] = round(fee * gas_price, 2) if gas_price else None
        else:
            tx["Gas Fee (USD)"] = None

    if unk_cnt:
        log.warning(f"  ⚠️   {unk_cnt} transactions remain unclassified (amber rows)")
    if no_price_tokens:
        log.warning(f"  ⚠️   Tokens with no USD price: {', '.join(sorted(no_price_tokens))}")
        log.warning("       Add contract address to CONTRACT_TO_COINGECKO in config.py, or DefiLlama will resolve it automatically via chain:contract lookup")

    return txs
