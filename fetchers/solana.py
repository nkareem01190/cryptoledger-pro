"""
fetchers/solana.py — Solana transaction fetcher for CryptoLedger Pro v5.0

Improvements over v4.0:
  • Configurable limit via CLI (default 500, 0 = unlimited)
  • Proper SPL token owner check
  • Saves raw RPC responses for audit trail
  • Clear warning and log entry if truncation applied
  • Progress bar with estimated time
"""

import time, json, os, sys, requests
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from datetime import datetime
import quality_log
from logger import get_logger, ok, warn, prog


def _save_raw(data: dict, wallet: str, sig: str, raw_dir: str):
    if not raw_dir:
        return
    try:
        os.makedirs(raw_dir, exist_ok=True)
        fname = f"solana_tx_{wallet[:8]}_{sig[:12]}.json"
        path  = os.path.join(raw_dir, fname)
        if not os.path.exists(path):
            with open(path, "w") as f:
                json.dump({"fetched_at": datetime.utcnow().isoformat(),
                           "signature": sig, "data": data}, f)
    except Exception:
        pass


def fetch_solana(address: str, chain: dict,
                 max_detail: int = 500,
                 raw_dir: str = "") -> list:
    """
    Fetches Solana transactions.

    max_detail: max number of transactions to fetch full detail for.
      0 = no limit (fetch all — may be very slow for active wallets).
      Default 500. Set via --solana-limit CLI arg.
    """
    log    = get_logger()
    txs    = []
    url    = chain["api_url"]
    before = None
    BATCH  = 100

    prog("Solana — fetching signatures …")

    all_sigs = []
    while True:
        payload = {
            "jsonrpc":"2.0","id":1,
            "method":"getSignaturesForAddress",
            "params":[address, {"limit":BATCH,
                                **({"before":before} if before else {})}],
        }
        try:
            r    = requests.post(url, json=payload, timeout=30)
            sigs = r.json().get("result", [])
        except requests.exceptions.Timeout:
            warn("Solana signatures fetch timeout"); break
        except Exception as e:
            warn(f"Solana signatures error: {e}"); break
        if not sigs:
            break
        all_sigs.extend(sigs)
        before = sigs[-1].get("signature")
        if len(sigs) < BATCH:
            break
        time.sleep(0.4)

    total_sigs = len(all_sigs)
    log.info(f"  ℹ️   Solana: {total_sigs} signatures found")

    # Truncation handling
    fetch_sigs = all_sigs
    if max_detail > 0 and total_sigs > max_detail:
        warn(f"Solana: {total_sigs} txs found — fetching detail for first {max_detail}. "
             f"Use --solana-limit 0 for complete data.")
        quality_log.log("Solana Truncated",
            f"Wallet has {total_sigs} transactions; only {max_detail} fetched in detail. "
            f"Rerun with --solana-limit 0 for full coverage.",
            severity="Warning", chain="Solana")
        fetch_sigs = all_sigs[:max_detail]

    log.info(f"  ℹ️   Solana: fetching transaction detail for {len(fetch_sigs)} txs …")
    est_secs = len(fetch_sigs) * 0.2
    log.info(f"  ℹ️   Estimated time: ~{est_secs:.0f}s")

    for i, sig_info in enumerate(fetch_sigs):
        sig = sig_info.get("signature")
        if not sig:
            continue

        print(f"\r  ⏳  [{i+1:>4}/{len(fetch_sigs)}]  {sig[:28]}…  ", end="", flush=True)

        payload = {
            "jsonrpc":"2.0","id":1,
            "method":"getTransaction",
            "params":[sig, {"encoding":"jsonParsed",
                            "maxSupportedTransactionVersion":0}],
        }
        try:
            r    = requests.post(url, json=payload, timeout=30)
            data = r.json().get("result")
        except requests.exceptions.Timeout:
            warn(f"\nSolana getTransaction timeout: {sig[:16]}")
            continue
        except Exception as e:
            warn(f"\nSolana getTransaction error: {e}"); continue

        if not data:
            continue

        _save_raw(data, address, sig, raw_dir)

        block_time = data.get("blockTime")
        dt         = (datetime.utcfromtimestamp(block_time)
                      if block_time else datetime.utcnow())
        meta       = data.get("meta", {}) or {}
        err_flag   = meta.get("err")
        status_str = "Failed" if err_flag else "Success"
        fee_lamps  = meta.get("fee", 0)
        fee_sol    = round(fee_lamps / 1e9, 9)

        pre_bals   = meta.get("preBalances",  [])
        post_bals  = meta.get("postBalances", [])
        tx_msg     = (data.get("transaction") or {}).get("message", {})
        acct_keys  = tx_msg.get("accountKeys", [])

        # Find our wallet index
        our_idx = None
        for idx, acc in enumerate(acct_keys):
            key_addr = acc if isinstance(acc, str) else acc.get("pubkey","")
            if key_addr == address:
                our_idx = idx
                break

        sol_delta = 0.0
        if (our_idx is not None and
                our_idx < len(pre_bals) and
                our_idx < len(post_bals)):
            sol_delta = (post_bals[our_idx] - pre_bals[our_idx]) / 1e9

        # SPL token transfers
        pre_tok  = {t.get("accountIndex"): t for t in meta.get("preTokenBalances",  [])}
        post_tok = {t.get("accountIndex"): t for t in meta.get("postTokenBalances", [])}
        spl_added = False

        for tok_idx in set(pre_tok) | set(post_tok):
            pre  = pre_tok.get(tok_idx,  {})
            post = post_tok.get(tok_idx, {})
            owner = post.get("owner") or pre.get("owner") or ""
            if owner != address:
                continue
            pre_amt  = float((pre.get("uiTokenAmount")  or {}).get("uiAmount") or 0)
            post_amt = float((post.get("uiTokenAmount") or {}).get("uiAmount") or 0)
            delta    = post_amt - pre_amt
            if abs(delta) < 1e-12:
                continue
            mint   = post.get("mint") or pre.get("mint") or ""
            symbol = ((post.get("uiTokenAmount") or {}).get("symbol")
                      or mint[:8] or "SPL")

            txs.append(_sol_tx(dt, "Receive" if delta>0 else "Send",
                               sig, address, abs(delta), symbol,
                               fee_sol, data.get("slot",""), status_str,
                               chain, ""))
            spl_added = True

        # Native SOL row
        if abs(sol_delta) > 0.000001:
            txs.append(_sol_tx(dt, "Receive" if sol_delta>0 else "Send",
                               sig, address, abs(round(sol_delta,9)), "SOL",
                               fee_sol, data.get("slot",""), status_str, chain, ""))
        elif not spl_added:
            # Contract interaction / approval
            txs.append(_sol_tx(dt, "Transaction",
                               sig, address, 0.0, "SOL",
                               fee_sol, data.get("slot",""), status_str, chain, ""))

        time.sleep(0.15)

    print(f"\r  ✅  Solana detail fetched: {len(txs)} transaction rows.{' '*30}")

    # Dedup
    seen   = set()
    unique = []
    for tx in txs:
        key = (tx["Tx Hash"], tx["Token/Asset"], str(tx["Value"]))
        if key not in seen:
            seen.add(key); unique.append(tx)

    removed = len(txs) - len(unique)
    if removed:
        warn(f"Solana: {removed} duplicate(s) removed")

    ok(f"Solana: {len(unique):,} transaction rows")
    return unique


def _sol_tx(dt, tx_type, sig, address, value, symbol,
            fee_sol, slot, status, chain, contract) -> dict:
    return {
        "Date (UTC)":      dt.strftime("%Y-%m-%d %H:%M:%S"),
        "Year":            dt.year,
        "Type":            tx_type,
        "Token/Asset":     symbol,
        "Contract":        contract,
        "From":            address if tx_type in ("Send","Transaction") else "",
        "To":              address if tx_type == "Receive" else "",
        "Value":           value,
        "Debit":           None,
        "Credit":          None,
        "Direction":       None,
        "USD Price":       None,
        "Debit (USD)":     None,
        "Credit (USD)":    None,
        "Gas Fee (native)":fee_sol,
        "Gas Fee (USD)":   None,
        "Status":          status,
        "Tx Hash":         sig,
        "Log Index":       "",
        "Explorer Link":   f"{chain['explorer']}/tx/{sig}",
        "Blockchain":      "Solana",
        "tx_class":              None,
        "accounting_treatment":  None,
        "fifo_action":           None,
        "confidence":            None,
        "review_required":       None,
        "classification_notes":  None,
    }
