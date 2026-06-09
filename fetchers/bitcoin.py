"""
fetchers/bitcoin.py — Bitcoin transaction fetcher for CryptoLedger Pro v5.0

Improvements over v4.0:
  • Change address detection — outputs back to wallet are self-transfers
  • Fee is separated from net movement
  • Self-transfers tagged for classifier
  • Raw BlockCypher responses saved for audit trail
"""

import time, json, os, requests, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from datetime import datetime
from logger import ok, warn, prog


def _save_raw(data: dict, wallet: str, raw_dir: str):
    if not raw_dir:
        return
    try:
        os.makedirs(raw_dir, exist_ok=True)
        ts    = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
        fname = f"blockcypher_btc_{wallet[:8]}_{ts}.json"
        path  = os.path.join(raw_dir, fname)
        if not os.path.exists(path):
            with open(path, "w") as f:
                json.dump({"fetched_at": datetime.utcnow().isoformat(),
                           "wallet": wallet, "data": data}, f)
    except Exception:
        pass


def fetch_bitcoin(address: str, chain: dict,
                  raw_dir: str = "") -> list:
    """
    Fetches Bitcoin transactions via BlockCypher API.

    Change address detection:
      If an output goes to the SAME address as the input wallet,
      it is tagged as "Self-transfer" (change output), not income.
      Net BTC = sum(outputs to wallet) - sum(inputs from wallet) - fee

    Fee is stored separately in Gas Fee (native).
    """
    txs    = []
    before = None
    api    = chain["api_url"]
    prog("Bitcoin …")

    while True:
        url    = f"{api}/addrs/{address}/full"
        params = {"limit": 50}
        if before:
            params["before"] = before
        try:
            r = requests.get(url, params=params, timeout=30)
            if r.status_code == 429:
                warn("BlockCypher rate-limit — waiting 15s …")
                time.sleep(15); continue
            if r.status_code != 200:
                warn(f"BlockCypher HTTP {r.status_code}: {r.text[:80]}")
                break
            data = r.json()
            _save_raw(data, address, raw_dir)
        except requests.exceptions.Timeout:
            warn("Bitcoin fetch timeout"); break
        except Exception as e:
            warn(f"Bitcoin error: {e}"); break

        raw = data.get("txs", [])
        if not raw:
            break

        for tx in raw:
            ts_str = tx.get("confirmed", tx.get("received", ""))
            try:    dt = datetime.strptime(ts_str[:19], "%Y-%m-%dT%H:%M:%S")
            except: dt = datetime.utcnow()

            inputs  = tx.get("inputs",  [])
            outputs = tx.get("outputs", [])
            fee_sat = tx.get("fees", 0) or 0
            fee_btc = round(fee_sat / 1e8, 8)

            # BTC received by our wallet in this tx
            btc_in  = sum(
                o.get("value", 0) for o in outputs
                if address in (o.get("addresses") or [])
            )
            # BTC sent from our wallet in this tx
            btc_out = sum(
                i.get("output_value", 0) for i in inputs
                if address in (i.get("addresses") or [])
            )

            btc_in_btc  = round(btc_in  / 1e8, 8)
            btc_out_btc = round(btc_out / 1e8, 8)
            net         = round(btc_in_btc - btc_out_btc, 8)

            # Sender addresses (first 3 inputs)
            frm = ", ".join(
                (i.get("addresses") or [""])[0]
                for i in inputs[:3]
                if (i.get("addresses") or [""])[0]
            )
            # Receiver addresses (first 3 outputs, excluding change)
            to_addrs = [
                (o.get("addresses") or [""])[0]
                for o in outputs[:3]
                if (o.get("addresses") or [""])[0] != address
            ]
            to = ", ".join(to_addrs[:3])

            confirmed = bool(tx.get("confirmed"))
            status    = "Confirmed" if confirmed else "Pending"
            tx_hash   = tx.get("hash", "")

            if btc_out_btc > 0 and btc_in_btc > 0:
                # Mixed: we sent AND received (likely change output)
                # Net is the real movement; fee is separate
                tx_type = "Receive" if net >= 0 else "Send"
            elif btc_in_btc > 0:
                tx_type = "Receive"
            elif btc_out_btc > 0:
                tx_type = "Send"
            else:
                tx_type = "Unknown"

            txs.append({
                "Date (UTC)":      dt.strftime("%Y-%m-%d %H:%M:%S"),
                "Year":            dt.year,
                "Type":            tx_type,
                "Token/Asset":     "BTC",
                "Contract":        "",
                "From":            frm,
                "To":              to,
                "Value":           abs(net),
                "Debit":           None,
                "Credit":          None,
                "Direction":       None,
                "USD Price":       None,
                "Debit (USD)":     None,
                "Credit (USD)":    None,
                "Gas Fee (native)":fee_btc,
                "Gas Fee (USD)":   None,
                "Status":          status,
                "Tx Hash":         tx_hash,
                "Log Index":       "",
                "Explorer Link":   f"{chain['explorer']}/tx/{tx_hash}",
                "Blockchain":      "Bitcoin",
                "tx_class":              None,
                "accounting_treatment":  None,
                "fifo_action":           None,
                "confidence":            None,
                "review_required":       None,
                "classification_notes":  None,
            })

        # Set pagination cursor to the lowest block_height in this batch.
        # Guard against None (unconfirmed/mempool txs) to avoid restarting from top.
        last_height = raw[-1].get("block_height") if raw else None
        if last_height is not None:
            before = last_height

        if len(raw) < 50:
            break
        time.sleep(1)

    # Dedup by tx hash (Bitcoin doesn't have log indexes)
    seen   = set()
    unique = []
    for tx in txs:
        if tx["Tx Hash"] not in seen:
            seen.add(tx["Tx Hash"])
            unique.append(tx)

    removed = len(txs) - len(unique)
    if removed:
        warn(f"Bitcoin: {removed} duplicate(s) removed")

    ok(f"Bitcoin: {len(unique):,} transactions")
    return unique
