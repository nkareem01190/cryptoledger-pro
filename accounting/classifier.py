"""
accounting/classifier.py — Transaction Classification Engine v5.0

Classifies every transaction into an accounting type BEFORE
FIFO or USD valuation runs.  Classification drives:
  - Whether a tx counts as acquisition, disposal, income, expense
  - Whether FIFO should process it
  - Whether it is excluded (self-transfer, approval, failed tx)

Classification types:
  Buy              — purchase with fiat/stablecoin
  Sell             — disposal to fiat/stablecoin
  Swap             — token-for-token exchange
  Bridge           — cross-chain transfer (usually no disposal)
  Wrap             — ETH→WETH or equivalent
  Unwrap           — WETH→ETH or equivalent
  Stake            — deposit to staking contract
  Unstake          — withdrawal from staking contract
  Reward           — staking/farming reward received
  Airdrop          — unsolicited token receipt
  Liquidity Add    — LP position creation
  Liquidity Remove — LP position closure
  Borrow           — loan received
  Repay            — loan repayment
  Lending Deposit  — deposit to lending protocol
  Lending Withdraw — withdrawal from lending protocol
  NFT Buy          — NFT purchase
  NFT Sell         — NFT sale
  NFT Transfer     — NFT transfer (no value)
  Self-transfer    — movement between owned wallets
  Internal Transfer— ETH movement within same hash
  Approval         — ERC-20 approval (no asset movement)
  Gas Only         — fee-only contract call
  Failed           — failed transaction
  Receive          — simple inbound transfer
  Send             — simple outbound transfer
  Unknown          — could not classify
"""

import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from config import WRAP_CONTRACTS, BRIDGE_CONTRACTS, CONFIDENCE_HIGH, CONFIDENCE_MEDIUM, CONFIDENCE_LOW
import quality_log
from logger import get_logger

# Known DEX/AMM router addresses (swap signatures)
DEX_ROUTERS = {
    "0x7a250d5630b4cf539739df2c5dacb4c659f2488d",  # Uniswap V2 Router
    "0xe592427a0aece92de3edee1f18e0157c05861564",  # Uniswap V3 Router
    "0x68b3465833fb72a70ecdf485e0e4c7bd8665fc45",  # Uniswap Universal Router
    "0xd9e1ce17f2641f24ae83637ab66a2cca9c378b9f",  # SushiSwap Router
    "0x1111111254fb6c44bac0bed2854e76f90643097d",  # 1inch v4
    "0x1111111254eeb25477b68fb85ed929f73a960582",  # 1inch v5
    "0xdef1c0ded9bec7f1a1670819833240f027b25eff",  # 0x Exchange Proxy
    "0x9923573104957bf457a3d4360c8c7a5951f2a0a0",  # Curve Router
    "0x6131b5fae19ea4f9d964eac0408e4408b66337b5",  # Kyber Router
}

# Known staking contracts
STAKE_CONTRACTS = {
    "0xae7ab96520de3a18e5e111b5eaab095312d7fe84",  # Lido stETH
    "0x00000000219ab540356cbb839cbe05303d7705fa",  # ETH2 Deposit Contract
    # Add real staking contract addresses here as needed
}

# Known lending protocols
LENDING_CONTRACTS = {
    "0x7d2768de32b0b80b7a3454c06bdac94a69ddc7a9",  # Aave V2
    "0x87870bca3f3fd6335c3f4ce8392d69350b4fa4e2",  # Aave V3
    "0x3d9819210a31b4961b30ef54be2aed79b9c9cd3b",  # Compound V2
    "0xc3d688b66703497daa19211eedff47f25384cdc3",  # Compound V3
}

# Known NFT marketplaces
NFT_MARKETPLACES = {
    "0x00000000006c3852cbef3e08e8df289169ede581",  # OpenSea Seaport
    "0x7be8076f4ea4a4ad08075c2508e481d6c946d12b",  # OpenSea V1
    "0x7f268357a8c2552623316e2562d90e642bb538e5",  # OpenSea V2
    "0x9757f2d2b135150bbeb65308d4a91804107cd8d6",  # Rarible
    "0x74312363e45dcaba76c59ec49a13aa114034c39b",  # X2Y2
    "0x000000000000ad05ccc4f10045630fb830b95127",  # Blur
}


def classify_transaction(tx: dict, owned_wallets: set) -> dict:
    """
    Classifies a single transaction and adds classification fields:
      tx_class        — the accounting classification type
      accounting_treatment — what to do for books
      fifo_action     — "acquisition" | "disposal" | "none" | "swap"
      confidence      — High | Medium | Low
      review_required — bool
      classification_notes — human-readable explanation
    """
    tx_type    = tx.get("Type", "")
    frm        = tx.get("From", "").lower().strip()
    to         = tx.get("To",   "").lower().strip()
    value      = float(tx.get("Value", 0) or 0)
    status     = tx.get("Status", "")
    token      = tx.get("Token/Asset", "").upper().strip()
    contract   = tx.get("Contract", "").lower().strip()

    owned = {w.lower().strip() for w in owned_wallets}

    # ── 1. Failed transaction ────────────────────────────────
    if status == "Failed":
        return _cls(tx, "Failed",
                    "No asset movement — only gas fee may apply",
                    "none", CONFIDENCE_HIGH, False,
                    "Transaction failed on-chain. No tokens transferred.")

    # ── 2. Zero-value approval ───────────────────────────────
    if value == 0 and tx_type in ("Normal", "ERC-20 Token"):
        if frm in owned and to not in owned:
            return _cls(tx, "Approval",
                        "Non-accounting — ERC-20 approval only",
                        "none", CONFIDENCE_MEDIUM, False,
                        "Zero-value outbound tx suggests token approval.")

    # ── 3. Self-transfer (between owned wallets) ─────────────
    if frm in owned and to in owned:
        return _cls(tx, "Self-transfer",
                    "Internal wallet move — no disposal, no income",
                    "none", CONFIDENCE_HIGH, False,
                    f"Both sender ({frm[:10]}…) and receiver ({to[:10]}…) "
                    f"are in owned_wallets. Asset location change only.")

    # ── 4. Bridge ────────────────────────────────────────────
    if to in BRIDGE_CONTRACTS or frm in BRIDGE_CONTRACTS:
        return _cls(tx, "Bridge",
                    "Cross-chain bridge — no disposal in most regimes; review policy",
                    "none", CONFIDENCE_MEDIUM, True,
                    "Counterparty is a known bridge contract. "
                    "Treatment depends on jurisdiction — mark for review.")

    # ── 5. Wrap / Unwrap ─────────────────────────────────────
    if contract in WRAP_CONTRACTS or to in WRAP_CONTRACTS:
        if token in ("ETH", "WETH", "AVAX", "WAVAX", "BNB", "WBNB"):
            label = "Unwrap" if token.startswith("W") else "Wrap"
            return _cls(tx, label,
                        "Wrap/unwrap — generally no disposal; review policy",
                        "none", CONFIDENCE_MEDIUM, True,
                        f"{label} detected via known wrapper contract. "
                        f"Most regimes treat this as non-disposal, but verify.")

    # ── 6. Swap (DEX router) ─────────────────────────────────
    if to in DEX_ROUTERS or frm in DEX_ROUTERS:
        direction = "out" if frm in owned else "in"
        return _cls(tx, "Swap",
                    "DEX swap — disposal of token-out, acquisition of token-in",
                    "disposal" if direction == "out" else "acquisition",
                    CONFIDENCE_HIGH, False,
                    f"Counterparty is a known DEX router ({to[:10]}…). "
                    f"Token flowing {'out of' if direction=='out' else 'into'} wallet.")

    # ── 7. Staking ───────────────────────────────────────────
    if to in STAKE_CONTRACTS:
        return _cls(tx, "Stake",
                    "Staking deposit — asset locked; no disposal in most regimes",
                    "none", CONFIDENCE_MEDIUM, True,
                    "Sent to known staking contract.")

    # ── 8. Lending deposit / withdrawal ──────────────────────
    if to in LENDING_CONTRACTS or frm in LENDING_CONTRACTS:
        if frm in owned:
            return _cls(tx, "Lending Deposit",
                        "Lending deposit — no disposal; asset on loan",
                        "none", CONFIDENCE_MEDIUM, True,
                        "Sent to known lending protocol.")
        else:
            return _cls(tx, "Lending Withdraw",
                        "Lending withdrawal — return of deposited asset",
                        "none", CONFIDENCE_MEDIUM, True,
                        "Received from known lending protocol.")

    # ── 9. NFT marketplace ───────────────────────────────────
    if to in NFT_MARKETPLACES or frm in NFT_MARKETPLACES:
        if frm in owned:
            return _cls(tx, "NFT Sell",
                        "NFT sale — disposal event",
                        "disposal", CONFIDENCE_MEDIUM, True,
                        "Interacted with known NFT marketplace (outbound).")
        else:
            return _cls(tx, "NFT Buy",
                        "NFT purchase — acquisition event",
                        "acquisition", CONFIDENCE_MEDIUM, True,
                        "Interacted with known NFT marketplace (inbound).")

    # ── 10. Internal transfer ────────────────────────────────
    if tx_type == "Internal":
        if to.lower() in owned:
            return _cls(tx, "Internal Transfer",
                        "Internal ETH movement — credit to wallet",
                        "acquisition", CONFIDENCE_MEDIUM, False,
                        "Internal ETH transaction credited to owned wallet.")
        else:
            return _cls(tx, "Internal Transfer",
                        "Internal ETH movement — debit from wallet",
                        "disposal", CONFIDENCE_MEDIUM, False,
                        "Internal ETH transaction debited from owned wallet.")

    # ── 11. Simple Receive ───────────────────────────────────
    if tx_type in ("Receive",) or (to in owned and frm not in owned):
        # Check if it looks like a staking reward (small amount, known reward tokens)
        reward_tokens = {"COMP","CRV","CVX","LDO","RPL","AAVE","UNI","SUSHI","BAL"}
        if token in reward_tokens and value < 100:
            return _cls(tx, "Reward",
                        "Staking/farming reward — income acquisition at FMV",
                        "acquisition", CONFIDENCE_MEDIUM, True,
                        "Small inbound amount of known reward token suggests reward claim.")

        # Possible airdrop — very small value, unknown sender
        if value < 1 and frm not in owned:
            return _cls(tx, "Airdrop",
                        "Possible airdrop — income at FMV or zero-cost basis; review policy",
                        "acquisition", CONFIDENCE_LOW, True,
                        "Small inbound transfer from unknown address. May be airdrop or spam.")

        return _cls(tx, "Receive",
                    "Inbound transfer — acquisition",
                    "acquisition", CONFIDENCE_HIGH, False,
                    f"Token received from external address ({frm[:10]}…).")

    # ── 12. Simple Send ──────────────────────────────────────
    if tx_type in ("Send",) or (frm in owned and to not in owned):
        return _cls(tx, "Send",
                    "Outbound transfer — disposal",
                    "disposal", CONFIDENCE_HIGH, False,
                    f"Token sent to external address ({to[:10]}…).")

    # ── 13. ERC-20 Token transfers ───────────────────────────
    if tx_type == "ERC-20 Token":
        if to in owned:
            return _cls(tx, "Receive",
                        "ERC-20 inbound — acquisition",
                        "acquisition", CONFIDENCE_HIGH, False,
                        "ERC-20 token transfer into owned wallet.")
        elif frm in owned:
            return _cls(tx, "Send",
                        "ERC-20 outbound — disposal",
                        "disposal", CONFIDENCE_HIGH, False,
                        "ERC-20 token transfer out of owned wallet.")

    # ── 14. Normal tx with value to/from owned ───────────────
    if tx_type == "Normal":
        if to in owned and value > 0:
            return _cls(tx, "Receive",
                        "Inbound ETH transfer — acquisition",
                        "acquisition", CONFIDENCE_MEDIUM, False,
                        "Normal ETH transaction received.")
        elif frm in owned and value > 0:
            return _cls(tx, "Send",
                        "Outbound ETH transfer — disposal",
                        "disposal", CONFIDENCE_MEDIUM, False,
                        "Normal ETH transaction sent.")
        elif value == 0:
            return _cls(tx, "Gas Only",
                        "Zero-value contract call — gas expense only",
                        "none", CONFIDENCE_MEDIUM, False,
                        "Zero-value normal transaction; likely contract interaction.")

    # ── 15. Fallback ─────────────────────────────────────────
    quality_log.log("Unclassified Tx",
        f"Could not classify: type={tx_type} from={frm[:10]}… "
        f"to={to[:10]}… token={token} value={value}",
        severity="Warning",
        tx_hash=tx.get("Tx Hash", "")[:20],
        token=token)

    return _cls(tx, "Unknown",
                "Could not classify — manual review required",
                "none", CONFIDENCE_LOW, True,
                f"No classification rule matched: type={tx_type}, "
                f"from_owned={frm in owned}, to_owned={to in owned}.")


def _cls(tx: dict, tx_class: str, treatment: str,
         fifo_action: str, confidence: str,
         review: bool, notes: str) -> dict:
    """Apply classification fields to the transaction dict."""
    tx["tx_class"]               = tx_class
    tx["accounting_treatment"]   = treatment
    tx["fifo_action"]            = fifo_action
    tx["confidence"]             = confidence
    tx["review_required"]        = review
    tx["classification_notes"]   = notes
    return tx


def classify_all(txs: list, owned_wallets: set) -> list:
    """Classify all transactions. Logs stats on completion."""
    log = get_logger()
    counts = {}
    for tx in txs:
        classify_transaction(tx, owned_wallets)
        c = tx.get("tx_class", "Unknown")
        counts[c] = counts.get(c, 0) + 1

    log.info(f"  ✅  Classification complete — {len(txs):,} transactions:")
    for cls_type, n in sorted(counts.items(), key=lambda x: -x[1]):
        review_n = sum(1 for tx in txs
                       if tx.get("tx_class") == cls_type
                       and tx.get("review_required"))
        log.info(f"       {cls_type:<22} {n:>5}  {'⚠ review' if review_n else ''}")
    return txs
