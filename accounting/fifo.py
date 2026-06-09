"""
accounting/fifo.py — FIFO Capital Gains Engine v5.0

Key improvements over v4.0:
  • Runs AFTER classification — only acts on classified events
  • Self-transfers, bridges, wraps, approvals are EXCLUDED
  • Swaps handled correctly: disposal of token-out + acquisition of token-in
  • Rewards/airdrops → zero-cost-basis acquisitions (configurable)
  • Opening balance lots can be manually injected
  • Each disposal records: proceeds, cost basis, gain/loss,
    holding days, short/long term, lot source
  • FIFO gaps flagged with specific reason
"""

import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from collections import defaultdict, deque
from datetime import datetime
from typing import Optional
import quality_log
from logger import get_logger

# Acquisition triggers — these create FIFO lots
ACQUISITION_CLASSES = {
    "Receive", "Buy", "Reward", "Airdrop",
    "Internal Transfer", "Lending Withdraw", "Unstake",
    "Swap",   # token-in side of swap
}

# Disposal triggers — these consume FIFO lots
DISPOSAL_CLASSES = {
    "Send", "Sell", "Swap",   # token-out side of swap
    "Lending Deposit", "Stake",
    "NFT Sell",
}

# These are excluded from FIFO entirely
EXCLUDED_CLASSES = {
    "Self-transfer", "Bridge", "Wrap", "Unwrap",
    "Approval", "Gas Only", "Failed", "Unknown",
    "NFT Transfer", "Borrow", "Repay",
}

# Airdrop cost basis policy
# "zero"  → cost basis = $0 (conservative — may create large gain on disposal)
# "fmv"   → cost basis = FMV at receipt (income recognised at receipt)
AIRDROP_COST_BASIS = "fmv"


def _parse_date(date_str: str) -> Optional[datetime]:
    try:
        return datetime.strptime(date_str[:10], "%Y-%m-%d")
    except Exception:
        return None


def _holding_days(acq_date_str: str, disp_date_str: str) -> int:
    d1 = _parse_date(acq_date_str)
    d2 = _parse_date(disp_date_str)
    if d1 and d2:
        return max(0, (d2 - d1).days)
    return 0


def inject_opening_lots(lots: dict, opening_balances: list) -> None:
    """
    Inject manually provided opening balance lots.
    opening_balances = list of dicts:
      {"token": "ETH", "qty": 2.5, "cost_per_unit": 1800.0,
       "acq_date": "2020-01-01", "source": "Opening balance"}
    """
    for entry in opening_balances:
        token = entry["token"].upper().strip()
        lot   = [
            entry.get("acq_date", "2000-01-01"),
            float(entry["qty"]),
            float(entry.get("cost_per_unit", 0.0)),
            entry.get("source", "Opening balance"),
        ]
        lots[token].appendleft(lot)   # prepend — oldest first


def compute_fifo_gains(txs: list,
                       opening_balances: list = None) -> list:
    """
    Process all transactions chronologically.
    txs must be sorted by Date (UTC) ascending.
    Returns list of disposal records for the Capital Gains sheet.
    """
    log  = get_logger()
    lots: dict = defaultdict(deque)  # token → deque of [date, qty, cost_pu, source]

    if opening_balances:
        inject_opening_lots(lots, opening_balances)
        log.info(f"  ℹ️   Opening balances injected: {len(opening_balances)} lot(s)")

    disposals: list = []
    skipped   = 0

    for tx in txs:
        cls        = tx.get("tx_class", "Unknown")
        fifo_act   = tx.get("fifo_action", "none")
        token      = tx.get("Token/Asset", "").upper().strip()
        usd_price  = tx.get("USD Price")
        date_str   = tx.get("Date (UTC)", "")[:10]
        tx_hash    = tx.get("Tx Hash", "")
        blockchain = tx.get("Blockchain", "")

        if not token:
            continue

        # ── Skip excluded classifications ────────────────────
        if cls in EXCLUDED_CLASSES:
            skipped += 1
            continue

        # ── ACQUISITION ──────────────────────────────────────
        if fifo_act == "acquisition":
            qty = float(tx.get("Credit") or 0)
            if qty <= 0:
                continue

            if usd_price is not None:
                cost_pu = float(usd_price)
                if cls == "Airdrop" and AIRDROP_COST_BASIS == "zero":
                    cost_pu = 0.0
            else:
                cost_pu = 0.0  # unknown cost basis
                quality_log.log("FIFO No Cost Basis",
                    f"Token={token} Date={date_str} — no USD price at acquisition. "
                    f"Cost basis set to $0. Class={cls}",
                    severity="Warning", tx_hash=tx_hash[:20], token=token)

            lots[token].append([date_str, qty, cost_pu, cls])

        # ── DISPOSAL ─────────────────────────────────────────
        elif fifo_act == "disposal":
            qty = float(tx.get("Debit") or 0)
            if qty <= 0:
                continue
            if usd_price is None:
                quality_log.log("FIFO No Proceeds",
                    f"Token={token} Date={date_str} — no USD price at disposal. "
                    f"Gain/loss cannot be computed. Class={cls}",
                    severity="Warning", tx_hash=tx_hash[:20], token=token)
                continue

            proceeds_pu   = float(usd_price)
            qty_remaining = qty

            while qty_remaining > 1e-12 and lots[token]:
                lot         = lots[token][0]
                acq_date    = lot[0]
                lot_qty     = lot[1]
                cost_pu     = lot[2]
                lot_source  = lot[3]

                consumed    = min(qty_remaining, lot_qty)
                proceeds    = round(consumed * proceeds_pu, 2)
                cost_basis  = round(consumed * cost_pu,    2)
                gain_loss   = round(proceeds - cost_basis, 2)
                days        = _holding_days(acq_date, date_str)
                term        = "Long-term" if days >= 365 else "Short-term"

                disposals.append({
                    "Token":            token,
                    "Blockchain":       blockchain,
                    "Disposal Date":    date_str,
                    "Disposal Type":    cls,
                    "Qty Disposed":     round(consumed, 8),
                    "Proceeds/Unit":    round(proceeds_pu, 6),
                    "Proceeds (USD)":   proceeds,
                    "Acq. Date":        acq_date,
                    "Acq. Source":      lot_source,
                    "Cost/Unit":        round(cost_pu, 6),
                    "Cost Basis (USD)": cost_basis,
                    "Gain/Loss (USD)":  gain_loss,
                    "Holding Days":     days,
                    "Term":             term,
                    "Tx Hash":          tx_hash,
                })

                lot[1]         -= consumed
                qty_remaining  -= consumed
                if lot[1] <= 1e-12:
                    lots[token].popleft()

            # Any remaining qty has no matching acquisition lot
            if qty_remaining > 1e-12:
                proceeds = round(qty_remaining * proceeds_pu, 2)
                quality_log.log("FIFO Gap",
                    f"Token={token} Date={date_str} Qty={qty_remaining:.8f} "
                    f"— no acquisition lot found. Possible airdrop, fork, "
                    f"or transactions pre-dating the download period.",
                    severity="Error", tx_hash=tx_hash[:20], token=token)
                disposals.append({
                    "Token":            token,
                    "Blockchain":       blockchain,
                    "Disposal Date":    date_str,
                    "Disposal Type":    cls,
                    "Qty Disposed":     round(qty_remaining, 8),
                    "Proceeds/Unit":    round(proceeds_pu, 6),
                    "Proceeds (USD)":   proceeds,
                    "Acq. Date":        "UNKNOWN",
                    "Acq. Source":      "No matching lot",
                    "Cost/Unit":        None,
                    "Cost Basis (USD)": None,
                    "Gain/Loss (USD)":  None,
                    "Holding Days":     None,
                    "Term":             "Unknown",
                    "Tx Hash":          tx_hash,
                })

        # ── SWAP — both sides ────────────────────────────────
        # Swaps have fifo_action set to "disposal" or "acquisition"
        # depending on which side this row represents.
        # The classifier sets the correct fifo_action per tx row.

    log.info(f"  ✅  FIFO complete: {len(disposals)} disposal events, "
             f"{skipped} transactions excluded (self-transfers, bridges, etc.)")

    # Summary
    known_gl  = [d["Gain/Loss (USD)"] for d in disposals
                 if isinstance(d.get("Gain/Loss (USD)"), (int, float))]
    total_gl  = round(sum(known_gl), 2)
    st_gl     = round(sum(d["Gain/Loss (USD)"] for d in disposals
                          if d.get("Term") == "Short-term"
                          and isinstance(d.get("Gain/Loss (USD)"), (int, float))), 2)
    lt_gl     = round(sum(d["Gain/Loss (USD)"] for d in disposals
                          if d.get("Term") == "Long-term"
                          and isinstance(d.get("Gain/Loss (USD)"), (int, float))), 2)
    gaps      = sum(1 for d in disposals if d["Acq. Date"] == "UNKNOWN")

    log.info(f"  ℹ️   Net realized gain/loss : ${total_gl:,.2f}")
    log.info(f"  ℹ️   Short-term             : ${st_gl:,.2f}")
    log.info(f"  ℹ️   Long-term              : ${lt_gl:,.2f}")
    if gaps:
        log.warning(f"  ⚠️   {gaps} disposal(s) with UNKNOWN cost basis — see Data Quality tab")

    return disposals
