"""
fetchers/evm.py — EVM chain transaction fetcher for CryptoLedger Pro v5.0

Improvements over v4.0:
  • Stores contract_address + chain_id on every ERC-20 row
  • Dedup key: hash + log_index + from + to + contract (not just hash+symbol+value)
  • Saves raw Etherscan responses for audit trail
  • Proper retry/backoff on rate limits
  • Validates API key before fetching
"""

import os, sys, time, json, requests
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from datetime import datetime
import quality_log
from config import ETHERSCAN_V2
from logger import get_logger, ok, warn, err, prog


def get_etherscan_key() -> str | None:
    key = os.environ.get("ETHERSCAN_API_KEY", "").strip()
    if key:
        get_logger().info("  ℹ️   Etherscan key loaded from ETHERSCAN_API_KEY env var.")
        return key
    print("""
  🔑  ONE Etherscan key covers ALL EVM chains (V2 API).
      Get free key: https://etherscan.io/apidashboard
      KEY MUST be from etherscan.io — not bscscan/polygonscan/arbiscan.
    """)
    while True:
        key = input("  Enter Etherscan API key: ").strip()
        if not key:
            err("No key entered."); return None
        if len(key) < 20 or not key.isalnum():
            warn(f"Looks wrong ({len(key)} chars). Keys are ~34 alphanumeric chars.")
            if input("  Try again? [Y/n]: ").strip().lower() in ("n","no"):
                return None
            continue
        return key


def test_etherscan_key(api_key: str) -> bool:
    prog("Verifying Etherscan API key …")
    params = {
        "chainid":1,"module":"account","action":"balance",
        "address":"0xd8dA6BF26964aF9D7eEd9e03E53415D37aA96045",
        "tag":"latest","apikey":api_key,
    }
    for attempt in range(3):
        try:
            r    = requests.get(ETHERSCAN_V2, params=params, timeout=15)
            if r.status_code != 200:
                warn(f"HTTP {r.status_code}"); return False
            data  = r.json()
            combo = (str(data.get("message","")) + " " + str(data.get("result",""))).lower()
            if data.get("status") == "1": ok("API key verified ✓"); return True
            if "api key" in combo or "apikey" in combo:
                err("Key rejected by Etherscan."); return False
            if "rate limit" in combo:
                warn("Rate limited — waiting 15s …"); time.sleep(15); continue
            ok("API key accepted ✓"); return True
        except Exception as e:
            warn(f"Key test error (attempt {attempt+1}/3): {e}"); time.sleep(3)
    warn("Could not verify (network issue). Proceeding anyway."); return True


def _make_tx(dt, year, tx_type, hash_, frm, to, value, symbol,
             fee_native, block, status, chain_name, explorer,
             contract="", log_index="") -> dict:
    return {
        "Date (UTC)":      dt.strftime("%Y-%m-%d %H:%M:%S"),
        "Year":            year,
        "Type":            tx_type,
        "Token/Asset":     symbol,
        "Contract":        contract,
        "From":            frm,
        "To":              to,
        "Value":           value,
        "Debit":           None,
        "Credit":          None,
        "Direction":       None,
        "USD Price":       None,
        "Debit (USD)":     None,
        "Credit (USD)":    None,
        "Gas Fee (native)":fee_native,
        "Gas Fee (USD)":   None,
        "Status":          status,
        "Tx Hash":         hash_,
        "Log Index":       log_index,
        "Explorer Link":   f"{explorer}/tx/{hash_}",
        "Blockchain":      chain_name,
        # Classification fields (filled by classifier)
        "tx_class":              None,
        "accounting_treatment":  None,
        "fifo_action":           None,
        "confidence":            None,
        "review_required":       None,
        "classification_notes":  None,
    }


def _dedup_txs(txs: list, chain_name: str) -> list:
    """
    v5.0 FIX: Dedup key = hash + log_index + from + to + contract
    Prevents removing legitimate same-token/same-amount events in one tx.
    """
    seen    = set()
    unique  = []
    removed = 0
    for tx in txs:
        key = (
            tx.get("Tx Hash", ""),
            str(tx.get("Log Index", "")),
            tx.get("From", "").lower(),
            tx.get("To",   "").lower(),
            tx.get("Contract", "").lower(),
        )
        if key in seen:
            removed += 1
            quality_log.log("Duplicate Removed",
                f"Chain={chain_name}  Hash={tx.get('Tx Hash','?')[:20]}…  "
                f"LogIdx={tx.get('Log Index','')}  Token={tx.get('Token/Asset','?')}",
                severity="Info", tx_hash=tx.get("Tx Hash","")[:20],
                token=tx.get("Token/Asset",""), chain=chain_name)
        else:
            seen.add(key)
            unique.append(tx)
    if removed:
        warn(f"{chain_name}: {removed} duplicate(s) removed (improved log_index dedup)")
    return unique


def _save_raw_response(data: dict, chain_name: str, action: str,
                       wallet: str, raw_dir: str):
    if not raw_dir:
        return
    try:
        os.makedirs(raw_dir, exist_ok=True)
        ts    = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
        fname = f"etherscan_{chain_name}_{action}_{wallet[:8]}_{ts}.json"
        path  = os.path.join(raw_dir, fname)
        with open(path, "w") as f:
            json.dump({"fetched_at": datetime.utcnow().isoformat(),
                       "chain": chain_name, "action": action,
                       "wallet": wallet, "data": data}, f)
    except Exception:
        pass


def fetch_evm(address: str, chain: dict, api_key: str,
              raw_dir: str = "") -> list:
    txs     = []
    chainid = chain["chainid"]
    explorer= chain["explorer"]
    cname   = chain["name"]

    endpoints = [
        ("txlist",         "Normal"),
        ("txlistinternal", "Internal"),
        ("tokentx",        "ERC-20 Token"),
    ]

    for action, tx_type in endpoints:
        prog(f"  {cname} — {tx_type} …")
        page = 1
        while True:
            params = {
                "chainid":chainid, "module":"account", "action":action,
                "address":address, "startblock":0, "endblock":99999999,
                "page":page, "offset":10000, "sort":"asc", "apikey":api_key,
            }
            try:
                r = requests.get(ETHERSCAN_V2, params=params, timeout=30)
                if r.status_code != 200:
                    warn(f"HTTP {r.status_code}: {r.text[:80]}"); break
                data = r.json()
            except requests.exceptions.Timeout:
                warn(f"{cname} timeout on {action} page {page}"); break
            except Exception as e:
                warn(f"{cname} request error: {e}"); break

            if data.get("status") != "1":
                msg   = str(data.get("message",""))
                res   = str(data.get("result", ""))
                combo = (msg + " " + res).lower()
                if "no transactions" in combo or res.strip() == "[]":
                    pass
                elif "api key" in combo or "apikey" in combo:
                    err(f"API key rejected — {msg}"); break
                elif "rate limit" in combo or "max rate" in combo:
                    warn("Rate limited — waiting 12s …"); time.sleep(12); continue
                else:
                    warn(f"{tx_type}: {msg} | {res[:80]}")
                break

            results = data.get("result", [])
            _save_raw_response(data, cname, action, address, raw_dir)

            for tx in results:
                ts        = int(tx.get("timeStamp", 0))
                dt        = datetime.utcfromtimestamp(ts)
                dec       = int(tx.get("tokenDecimal", 18) or 18)
                value     = int(tx.get("value", 0) or 0) / (10 ** dec)
                symbol    = tx.get("tokenSymbol") or chain["symbol"]
                contract  = (tx.get("contractAddress") or "").lower()
                log_index = tx.get("transactionIndex", "") or tx.get("logIndex", "")
                gas_u     = int(tx.get("gasUsed",  0) or 0)
                gas_p     = int(tx.get("gasPrice", 0) or 0)
                # Internal txns don't carry gasPrice — fee already charged on the parent tx
                fee_nat   = round(gas_u * gas_p / 1e18, 8) if tx_type != "Internal" else 0.0

                txs.append(_make_tx(
                    dt, dt.year, tx_type,
                    tx.get("hash", ""),
                    tx.get("from", ""), tx.get("to", ""),
                    round(value, 8), symbol,
                    fee_nat,
                    tx.get("blockNumber", ""),
                    "Success" if tx.get("isError","0") == "0" else "Failed",
                    cname, explorer,
                    contract=contract,
                    log_index=str(log_index),
                ))

            if len(results) < 10000:
                break
            page += 1
            time.sleep(0.25)
        time.sleep(0.3)

    txs = _dedup_txs(txs, cname)
    ok(f"{cname}: {len(txs):,} transactions (after improved dedup)")
    return txs


def fetch_current_balance(address: str, chain: dict,
                          api_key: str) -> dict:
    """
    Fetch current token balances for reconciliation report.
    Returns {"ETH": float, "ERC-20": {symbol: float, ...}}
    """
    log     = get_logger()
    chainid = chain["chainid"]
    result  = {"native": None, "tokens": {}}

    # Native balance
    try:
        params = {"chainid":chainid,"module":"account","action":"balance",
                  "address":address,"tag":"latest","apikey":api_key}
        r = requests.get(ETHERSCAN_V2, params=params, timeout=15)
        if r.status_code == 200:
            data = r.json()
            if data.get("status") == "1":
                result["native"] = int(data["result"]) / 1e18
    except Exception as e:
        log.debug(f"Balance fetch error {chain['name']}: {e}")

    # ERC-20 balances (tokenlist)
    try:
        params = {"chainid":chainid,"module":"account","action":"tokenlist",
                  "address":address,"apikey":api_key}
        r = requests.get(ETHERSCAN_V2, params=params, timeout=15)
        if r.status_code == 200:
            data = r.json()
            if data.get("status") == "1":
                for token in (data.get("result") or []):
                    sym = token.get("symbol","?")
                    dec = int(token.get("tokenDecimal",18) or 18)
                    bal = int(token.get("balance",0) or 0) / (10**dec)
                    result["tokens"][sym] = bal
    except Exception as e:
        log.debug(f"Token list fetch error {chain['name']}: {e}")

    return result
