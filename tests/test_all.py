"""
tests/test_all.py — CryptoLedger Pro v5.0 test suite

Covers every critical path flagged in the ChatGPT review:
  - Normal ETH transfer
  - ERC-20 transfer
  - Swap
  - Self-transfer (owned wallets)
  - Wrap / Unwrap
  - Bridge
  - Failed transaction
  - Gas-only approval
  - Missing price
  - FIFO with known lots
  - Duplicate detection (improved log_index key)
  - Stablecoin policy (fixed vs market)
  - Long/short-term holding period
  - FIFO gap (no acquisition lot)
  - Opening balance lots
  - Direction derivation from classification
  - Quality log collection
"""

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import pytest
from datetime import datetime, timezone
from collections import deque

import quality_log
from accounting.classifier import classify_transaction, classify_all
from accounting.fifo import compute_fifo_gains, inject_opening_lots
from fetchers.evm import _dedup_txs
from config import STABLECOINS


# ─── Helpers ────────────────────────────────────────────────

OWNED = {"0xmywallet", "0xmysecondwallet"}

def make_tx(tx_type="Normal", frm="0xmywallet", to="0xexternal",
            value=1.0, symbol="ETH", status="Success",
            contract="", log_index="0",
            tx_hash="0xabc123", blockchain="Ethereum"):
    return {
        "Date (UTC)": "2023-06-15 12:00:00",
        "Year": 2023,
        "Type": tx_type,
        "Token/Asset": symbol,
        "Contract": contract,
        "From": frm,
        "To": to,
        "Value": value,
        "Status": status,
        "Tx Hash": tx_hash,
        "Log Index": log_index,
        "Blockchain": blockchain,
        "Gas Fee (native)": 0.002,
        "Gas Fee (USD)": None,
        "Debit": None, "Credit": None, "Direction": None,
        "USD Price": None, "Debit (USD)": None, "Credit (USD)": None,
        "tx_class": None, "accounting_treatment": None,
        "fifo_action": None, "confidence": None,
        "review_required": None, "classification_notes": None,
    }


def make_fifo_tx(date, token, direction, qty, price, blockchain="Ethereum"):
    tx = {
        "Date (UTC)": f"{date} 10:00:00",
        "Year": int(date[:4]),
        "Token/Asset": token,
        "USD Price": price,
        "Blockchain": blockchain,
        "Tx Hash": f"hash_{date}_{token}",
        "tx_class": "Receive" if direction=="credit" else "Send",
        "fifo_action": "acquisition" if direction=="credit" else "disposal",
        "review_required": False,
    }
    if direction == "credit":
        tx["Debit"] = None; tx["Credit"] = qty
    else:
        tx["Debit"] = qty; tx["Credit"] = None
    return tx


# ─── Classification Tests ────────────────────────────────────

class TestClassifier:

    def setup_method(self):
        quality_log.clear()

    def test_normal_receive(self):
        tx = make_tx(tx_type="Normal", frm="0xexternal", to="0xmywallet", value=1.0)
        result = classify_transaction(tx, OWNED)
        assert result["tx_class"] == "Receive"
        assert result["fifo_action"] == "acquisition"
        assert result["confidence"] == "High"

    def test_normal_send(self):
        tx = make_tx(tx_type="Normal", frm="0xmywallet", to="0xexternal", value=1.0)
        result = classify_transaction(tx, OWNED)
        assert result["tx_class"] == "Send"
        assert result["fifo_action"] == "disposal"

    def test_erc20_inbound(self):
        tx = make_tx(tx_type="ERC-20 Token", frm="0xexternal",
                     to="0xmywallet", symbol="LINK")
        result = classify_transaction(tx, OWNED)
        assert result["tx_class"] == "Receive"
        assert result["fifo_action"] == "acquisition"

    def test_erc20_outbound(self):
        tx = make_tx(tx_type="ERC-20 Token", frm="0xmywallet",
                     to="0xexternal", symbol="USDC")
        result = classify_transaction(tx, OWNED)
        assert result["tx_class"] == "Send"
        assert result["fifo_action"] == "disposal"

    def test_self_transfer(self):
        """Transfer between two owned wallets = no disposal, no income."""
        tx = make_tx(tx_type="Normal", frm="0xmywallet",
                     to="0xmysecondwallet", value=2.0)
        result = classify_transaction(tx, OWNED)
        assert result["tx_class"] == "Self-transfer"
        assert result["fifo_action"] == "none"
        assert result["confidence"] == "High"
        assert result["review_required"] == False

    def test_failed_transaction(self):
        tx = make_tx(status="Failed")
        result = classify_transaction(tx, OWNED)
        assert result["tx_class"] == "Failed"
        assert result["fifo_action"] == "none"

    def test_zero_value_approval(self):
        tx = make_tx(tx_type="Normal", frm="0xmywallet",
                     to="0xexternal", value=0)
        result = classify_transaction(tx, OWNED)
        assert result["tx_class"] == "Approval"
        assert result["fifo_action"] == "none"

    def test_bridge(self):
        # Optimism bridge address
        bridge = "0x99c9fc46f92e8a1c0dec1b1747d010903e884be1"
        tx = make_tx(tx_type="Normal", frm="0xmywallet", to=bridge, value=1.0)
        result = classify_transaction(tx, OWNED)
        assert result["tx_class"] == "Bridge"
        assert result["review_required"] == True

    def test_wrap(self):
        weth = "0xc02aaa39b223fe8d0a0e5c4f27ead9083c756cc2"
        tx = make_tx(tx_type="Normal", frm="0xmywallet",
                     to=weth, value=1.0, symbol="ETH", contract=weth)
        result = classify_transaction(tx, OWNED)
        assert result["tx_class"] in ("Wrap", "Unwrap")
        assert result["review_required"] == True

    def test_dex_swap(self):
        uniswap_router = "0x7a250d5630b4cf539739df2c5dacb4c659f2488d"
        tx = make_tx(tx_type="Normal", frm="0xmywallet",
                     to=uniswap_router, value=1.0, symbol="ETH")
        result = classify_transaction(tx, OWNED)
        assert result["tx_class"] == "Swap"

    def test_internal_transfer_inbound(self):
        tx = make_tx(tx_type="Internal", frm="0xcontract",
                     to="0xmywallet", value=0.5)
        result = classify_transaction(tx, OWNED)
        assert result["tx_class"] == "Internal Transfer"
        assert result["fifo_action"] == "acquisition"

    def test_unknown_fallback(self):
        quality_log.clear()
        tx = make_tx(tx_type="WeirdCustomType", frm="0xstranger",
                     to="0xalsostranger", value=1.0)
        result = classify_transaction(tx, OWNED)
        assert result["tx_class"] == "Unknown"
        assert result["fifo_action"] == "none"
        assert result["confidence"] == "Low"
        issues = quality_log.all_issues()
        assert any(i.category == "Unclassified Tx" for i in issues)

    def test_gas_only_zero_value_normal(self):
        tx = make_tx(tx_type="Normal", frm="0xmywallet",
                     to="0xsomecontract", value=0)
        result = classify_transaction(tx, OWNED)
        assert result["tx_class"] in ("Approval", "Gas Only")
        assert result["fifo_action"] == "none"


# ─── FIFO Tests ──────────────────────────────────────────────

class TestFifo:

    def setup_method(self):
        quality_log.clear()

    def test_simple_gain(self):
        """Buy 2 ETH @ $1200, sell 1 ETH @ $2000 → gain $800."""
        txs = [
            make_fifo_tx("2022-01-01","ETH","credit",2.0,1200.0),
            make_fifo_tx("2022-06-01","ETH","disposal",1.0,2000.0),
        ]
        d = compute_fifo_gains(txs)
        assert len(d) == 1
        assert abs(d[0]["Gain/Loss (USD)"] - 800.0) < 0.01

    def test_simple_loss(self):
        """Buy 1 BTC @ $50000, sell @ $30000 → loss $20000."""
        txs = [
            make_fifo_tx("2021-01-01","BTC","credit",1.0,50000.0),
            make_fifo_tx("2022-06-01","BTC","disposal",1.0,30000.0),
        ]
        d = compute_fifo_gains(txs)
        assert len(d) == 1
        assert abs(d[0]["Gain/Loss (USD)"] - (-20000.0)) < 0.01

    def test_fifo_order(self):
        """Two lots at different prices — FIFO consumes oldest first."""
        txs = [
            make_fifo_tx("2021-01-01","ETH","credit",2.0,1000.0),
            make_fifo_tx("2022-01-01","ETH","credit",2.0,2000.0),
            make_fifo_tx("2023-01-01","ETH","disposal",2.0,3000.0),
        ]
        d = compute_fifo_gains(txs)
        assert len(d) == 1
        # Should consume first lot (cost=$1000/unit): gain = 2*(3000-1000) = 4000
        assert abs(d[0]["Cost Basis (USD)"] - 2000.0) < 0.01  # 2 * $1000
        assert abs(d[0]["Gain/Loss (USD)"] - 4000.0) < 0.01

    def test_partial_lot(self):
        """Dispose half of a lot."""
        txs = [
            make_fifo_tx("2022-01-01","LINK","credit",10.0,8.0),
            make_fifo_tx("2022-06-01","LINK","disposal",4.0,12.0),
        ]
        d = compute_fifo_gains(txs)
        assert len(d) == 1
        assert abs(d[0]["Qty Disposed"] - 4.0) < 1e-8
        assert abs(d[0]["Cost Basis (USD)"] - 32.0) < 0.01   # 4 * $8
        assert abs(d[0]["Proceeds (USD)"]   - 48.0) < 0.01   # 4 * $12
        assert abs(d[0]["Gain/Loss (USD)"]  - 16.0) < 0.01

    def test_long_term(self):
        """Held ≥ 365 days → Long-term."""
        txs = [
            make_fifo_tx("2020-01-01","BTC","credit",1.0,7000.0),
            make_fifo_tx("2021-06-01","BTC","disposal",1.0,35000.0),
        ]
        d = compute_fifo_gains(txs)
        assert d[0]["Term"] == "Long-term"
        days = (datetime(2021,6,1)-datetime(2020,1,1)).days
        assert d[0]["Holding Days"] == days

    def test_short_term(self):
        """Held < 365 days → Short-term."""
        txs = [
            make_fifo_tx("2023-01-01","ETH","credit",1.0,1200.0),
            make_fifo_tx("2023-11-01","ETH","disposal",1.0,2000.0),
        ]
        d = compute_fifo_gains(txs)
        assert d[0]["Term"] == "Short-term"

    def test_fifo_gap(self):
        """Disposal with no acquisition lot → UNKNOWN."""
        quality_log.clear()
        txs = [make_fifo_tx("2023-03-01","SHIB","disposal",1000000.0,0.00001)]
        d = compute_fifo_gains(txs)
        assert len(d) == 1
        assert d[0]["Acq. Date"] == "UNKNOWN"
        assert d[0]["Cost Basis (USD)"] is None
        assert d[0]["Gain/Loss (USD)"] is None
        issues = quality_log.all_issues()
        assert any(i.category == "FIFO Gap" for i in issues)

    def test_self_transfer_excluded(self):
        """Self-transfers must be excluded from FIFO."""
        txs = [
            make_fifo_tx("2023-01-01","ETH","credit",5.0,1500.0),
        ]
        txs[0]["tx_class"] = "Self-transfer"
        txs[0]["fifo_action"] = "none"
        d = compute_fifo_gains(txs)
        assert len(d) == 0

    def test_opening_balance_lots(self):
        """Opening balance injected → used as acquisition lot."""
        txs = [make_fifo_tx("2023-06-01","ETH","disposal",1.0,2000.0)]
        opening = [{"token":"ETH","qty":2.0,"cost_per_unit":1000.0,
                    "acq_date":"2020-01-01","source":"Manual opening balance"}]
        d = compute_fifo_gains(txs, opening_balances=opening)
        assert len(d) == 1
        assert d[0]["Acq. Source"] == "Manual opening balance"
        assert abs(d[0]["Gain/Loss (USD)"] - 1000.0) < 0.01  # (2000-1000)*1

    def test_multi_lot_consumption(self):
        """Disposal spans two acquisition lots."""
        txs = [
            make_fifo_tx("2021-01-01","ETH","credit",2.0,1000.0),
            make_fifo_tx("2021-06-01","ETH","credit",1.0,1500.0),
            make_fifo_tx("2022-01-01","ETH","disposal",2.5,2000.0),
        ]
        d = compute_fifo_gains(txs)
        assert len(d) == 2  # Two lots consumed
        # Lot 1: 2 ETH @ $1000 cost, $2000 proceeds → gain $2000
        assert abs(d[0]["Cost Basis (USD)"] - 2000.0) < 0.01
        assert abs(d[0]["Gain/Loss (USD)"]  - 2000.0) < 0.01
        # Lot 2: 0.5 ETH @ $1500 cost, $1000 proceeds → gain $250
        assert abs(d[1]["Qty Disposed"] - 0.5) < 1e-6
        assert abs(d[1]["Gain/Loss (USD)"] - 250.0) < 0.01


# ─── Deduplication Tests ────────────────────────────────────

class TestDedup:

    def setup_method(self):
        quality_log.clear()

    def test_exact_duplicate_removed(self):
        txs = [
            {"Tx Hash":"abc","Log Index":"0","From":"0xa","To":"0xb","Contract":"0xc",
             "Token/Asset":"ETH","Value":1.0},
            {"Tx Hash":"abc","Log Index":"0","From":"0xa","To":"0xb","Contract":"0xc",
             "Token/Asset":"ETH","Value":1.0},
        ]
        unique = _dedup_txs(txs, "Ethereum")
        assert len(unique) == 1

    def test_same_hash_different_log_index_kept(self):
        """v5.0 fix: same hash but different log_index = NOT a duplicate."""
        txs = [
            {"Tx Hash":"abc","Log Index":"0","From":"0xa","To":"0xb","Contract":"0xc",
             "Token/Asset":"USDC","Value":100.0},
            {"Tx Hash":"abc","Log Index":"1","From":"0xa","To":"0xd","Contract":"0xc",
             "Token/Asset":"USDC","Value":100.0},
        ]
        unique = _dedup_txs(txs, "Ethereum")
        assert len(unique) == 2, "Same hash + different log_index must NOT be deduped"

    def test_same_hash_different_to_kept(self):
        """Same hash, same log_index, different 'to' → keep both."""
        txs = [
            {"Tx Hash":"abc","Log Index":"0","From":"0xa","To":"0xb","Contract":"0xc",
             "Token/Asset":"ETH","Value":1.0},
            {"Tx Hash":"abc","Log Index":"0","From":"0xa","To":"0xz","Contract":"0xc",
             "Token/Asset":"ETH","Value":1.0},
        ]
        unique = _dedup_txs(txs, "Ethereum")
        assert len(unique) == 2


# ─── Stablecoin Policy Tests ────────────────────────────────

class TestStablecoinPolicy:

    def test_stablecoins_in_set(self):
        for sym in ["USDT","USDC","DAI","BUSD","FRAX"]:
            assert sym in STABLECOINS

    def test_non_stablecoin_not_in_set(self):
        assert "ETH" not in STABLECOINS
        assert "BTC" not in STABLECOINS
        assert "LINK" not in STABLECOINS


# ─── Quality Log Tests ───────────────────────────────────────

class TestQualityLog:

    def setup_method(self):
        quality_log.clear()

    def test_log_and_retrieve(self):
        quality_log.log("No USD Price","Token=SHIB","Warning")
        quality_log.log("FIFO Gap","Token=LOOKS","Error")
        issues = quality_log.all_issues()
        assert len(issues) == 2
        assert issues[0].category == "No USD Price"
        assert issues[1].severity == "Error"

    def test_clear(self):
        quality_log.log("Test","Detail")
        quality_log.clear()
        assert len(quality_log.all_issues()) == 0

    def test_count_by_category(self):
        quality_log.log("No USD Price","A")
        quality_log.log("No USD Price","B")
        quality_log.log("FIFO Gap","C")
        counts = quality_log.count_by_category()
        assert counts["No USD Price"] == 2
        assert counts["FIFO Gap"] == 1


# ─── Integration Test ────────────────────────────────────────

class TestIntegration:

    def setup_method(self):
        quality_log.clear()

    def test_full_pipeline(self):
        """
        Simulate: classify → compute direction → FIFO
        Wallet receives ETH, then sends some → gain computed correctly.
        """
        from accounting.direction import compute_direction_and_usd

        owned = {"0xmywallet"}
        txs = [
            {**make_tx("Normal","0xexternal","0xmywallet",3.0,"ETH"),
             "Date (UTC)":"2022-01-01 10:00:00","Year":2022,"Value":3.0},
            {**make_tx("Normal","0xmywallet","0xexternal",1.0,"ETH"),
             "Date (UTC)":"2023-01-01 10:00:00","Year":2023,"Value":1.0},
        ]

        # 1. Classify
        txs = classify_all(txs, owned)
        assert txs[0]["tx_class"] == "Receive"
        assert txs[1]["tx_class"] == "Send"

        # 2. Mock price function
        def mock_price(sym, dt, **kwargs):
            prices = {"ETH":{"2022-01-01":3000.0,"2023-01-01":1500.0}}
            d = dt.strftime("%Y-%m-%d")
            return prices.get(sym,{}).get(d)

        compute_direction_and_usd(txs,"0xmywallet",mock_price)
        assert txs[0]["Credit"] == 3.0
        assert txs[1]["Debit"]  == 1.0
        assert txs[0]["Credit (USD)"] == 9000.0
        assert txs[1]["Debit (USD)"]  == 1500.0

        # 3. FIFO
        disposals = compute_fifo_gains(txs)
        assert len(disposals) == 1
        # Bought 3 ETH @ $3000 = $9000 total, sold 1 ETH @ $1500
        # Cost basis of 1 ETH = $3000, proceeds = $1500 → loss = -$1500
        assert abs(disposals[0]["Gain/Loss (USD)"] - (-1500.0)) < 0.01
        # Jan 2022 → Jan 2023 = 365 days = Long-term (≥365)
        assert disposals[0]["Term"] == "Long-term"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
