#!/usr/bin/env python3
"""
cli.py — CryptoLedger Pro v5.0 — Main Entry Point

Usage (interactive):
  python cli.py

Usage (CLI arguments):
  python cli.py --wallet 0x123... --chains 1,3,6 --output ledger.xlsx
  python cli.py --wallet 0x123... --chains 1 --owned-wallets 0xabc,0xdef
  python cli.py --wallet 0x123... --chains 10 --solana-limit 0
  python cli.py --wallet 0x123... --chains 1 --stablecoin-policy market_price
  python cli.py --wallet 0x123... --chains 1 --save-raw --from-date 2023-01-01

All arguments optional — falls back to interactive prompts if omitted.
"""

import os, sys, argparse
from datetime import datetime

# ── Path setup ────────────────────────────────────────────────
sys.path.insert(0, os.path.dirname(__file__))

import config as cfg
import quality_log
from logger import setup_logger, banner, section, ok, warn, err, info


def parse_args():
    p = argparse.ArgumentParser(
        description=f"CryptoLedger Pro v{cfg.VERSION} — Accounting-ready crypto ledger",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Interactive mode (recommended for first use):
  python cli.py

  # Ethereum wallet, save raw API responses:
  python cli.py --wallet 0x742d... --chains 1 --save-raw

  # Multi-chain with owned wallets (prevents self-transfer errors):
  python cli.py --wallet 0x742d... --chains 1,3,6 \\
    --owned-wallets 0x742d...,0xSecondWallet...

  # Full Solana fetch (no truncation):
  python cli.py --wallet YourSolAddr --chains 10 --solana-limit 0

  # Market price for stablecoins (audit-grade):
  python cli.py --wallet 0x742d... --chains 1 --stablecoin-policy market_price
        """
    )
    p.add_argument("--wallet",       help="Primary wallet address to analyse")
    p.add_argument("--chains",       help="Chain numbers comma-separated (e.g. 1,3,6 or A for all)")
    p.add_argument("--owned-wallets",help="Other wallets you own, comma-separated (prevents self-transfer errors)")
    p.add_argument("--output",       help="Output Excel filename (default: auto-generated)")
    p.add_argument("--solana-limit", type=int, default=500,
                   help="Max Solana transactions to fetch detail for (0=unlimited, default=500)")
    p.add_argument("--stablecoin-policy", choices=["fixed_1","market_price"],
                   default="fixed_1",
                   help="Stablecoin price policy: fixed_1=$1.00 always, market_price=fetch real price")
    p.add_argument("--save-raw",     action="store_true",
                   help="Save raw API responses for audit trail (creates raw_data/ folder)")
    p.add_argument("--opening-balances", help="CSV file with opening balance lots")
    p.add_argument("--run-tests",    action="store_true",
                   help="Run test suite before processing")
    return p.parse_args()


def run_tests() -> bool:
    """Run pytest test suite. Returns True if all pass."""
    import subprocess
    result = subprocess.run(
        [sys.executable, "-m", "pytest",
         os.path.join(os.path.dirname(__file__), "tests"),
         "-v", "--tb=short"],
        capture_output=False
    )
    return result.returncode == 0


def load_opening_balances(csv_path: str) -> list:
    """
    Load opening balance lots from CSV.
    Expected columns: token,qty,cost_per_unit,acq_date,source
    """
    if not csv_path or not os.path.exists(csv_path):
        return []
    lots = []
    try:
        import csv
        with open(csv_path) as f:
            for row in csv.DictReader(f):
                lots.append({
                    "token":         row["token"].strip().upper(),
                    "qty":           float(row["qty"]),
                    "cost_per_unit": float(row.get("cost_per_unit", 0)),
                    "acq_date":      row.get("acq_date","2000-01-01").strip(),
                    "source":        row.get("source","Opening balance").strip(),
                })
        info(f"Loaded {len(lots)} opening balance lot(s) from {csv_path}")
    except Exception as e:
        warn(f"Could not load opening balances: {e}")
    return lots


def choose_chains_interactive() -> list:
    section("Select Blockchain(s)")
    print()
    for key, cfg_chain in cfg.CHAINS.items():
        note = "  (no key needed)" if cfg_chain["type"] in ("bitcoin","solana") else ""
        print(f"    [{key:>2}]  {cfg_chain['name']:<22} ({cfg_chain['symbol']}){note}")
    print("\n    [ A]  All blockchains")
    print("    [ 0]  Exit\n")

    while True:
        choice = input("  Enter number(s) comma-separated (e.g. 1  or  1,3,6  or  A): ").strip()
        if choice == "0":
            sys.exit(0)
        if not choice:
            warn("Please enter a chain number (e.g. 1) or A for all.")
            continue
        selected = _parse_chain_choice(choice)
        if selected:
            return selected
        warn("No valid chains selected. Try again — valid numbers are 1-10, or A for all.")


def _parse_chain_choice(choice: str) -> list:
    choice = choice.strip().upper()
    if choice in ("A", "ALL"):
        return list(cfg.CHAINS.values())
    selected = []
    for c in choice.split(","):
        c = c.strip()
        if not c:
            continue
        if c in cfg.CHAINS:
            selected.append(cfg.CHAINS[c])
        else:
            warn(f"Unknown chain '{c}' — valid numbers are 1-10, or A for all.")
    return selected


def main():
    args = parse_args()

    # ── Apply CLI policy overrides ────────────────────────────
    if args.stablecoin_policy:
        cfg.STABLECOIN_POLICY = args.stablecoin_policy

    # ── Setup logging ─────────────────────────────────────────
    output_dir = os.path.dirname(
        os.path.abspath(args.output or os.getcwd())
    ) if args.output else os.getcwd()
    log = setup_logger(output_dir)

    banner(cfg.VERSION)

    # ── Optional test run ────────────────────────────────────
    if args.run_tests:
        section("Running test suite …")
        if not run_tests():
            err("Tests failed — fix issues before processing real data.")
            sys.exit(1)
        ok("All tests passed.")

    # ── Wallet address ───────────────────────────────────────
    section("Step 1 — Wallet Address")
    wallet = args.wallet or input("  Enter wallet address: ").strip()
    if not wallet:
        err("No address provided."); sys.exit(1)
    info(f"Primary wallet: {wallet}")

    # ── Owned wallets ────────────────────────────────────────
    section("Step 2 — Owned Wallets (prevents self-transfer errors)")
    owned_wallets_raw = []
    if args.owned_wallets:
        owned_wallets_raw = [w.strip() for w in args.owned_wallets.split(",") if w.strip()]
    else:
        info("Enter other wallet addresses you own (same owner).")
        info("This prevents transfers between your own wallets being misclassified as disposals.")
        inp = input("  Other owned wallets (comma-separated, or Enter to skip): ").strip()
        if inp:
            owned_wallets_raw = [w.strip() for w in inp.split(",") if w.strip()]

    # Always include the primary wallet
    owned_wallets = set([wallet.lower()] + [w.lower() for w in owned_wallets_raw])
    info(f"Owned wallets: {len(owned_wallets)} address(es)")

    # ── Chain selection ──────────────────────────────────────
    section("Step 3 — Blockchain Selection")
    if args.chains:
        selected_chains = _parse_chain_choice(args.chains)
    else:
        selected_chains = choose_chains_interactive()
    if not selected_chains:
        err("No valid chains selected."); sys.exit(1)

    # ── Etherscan key ────────────────────────────────────────
    from fetchers.evm import get_etherscan_key, test_etherscan_key
    evm_chains    = [c for c in selected_chains if c["type"] == "evm"]
    etherscan_key = None
    if evm_chains:
        section("Step 4 — Etherscan API Key")
        while True:
            etherscan_key = get_etherscan_key()
            if not etherscan_key:
                warn("Skipping all EVM chains.")
                selected_chains = [c for c in selected_chains if c["type"] != "evm"]
                break
            if test_etherscan_key(etherscan_key):
                break
            if input("\n  Try a different key? [Y/n]: ").strip().lower() in ("n","no"):
                warn("Skipping all EVM chains.")
                selected_chains = [c for c in selected_chains if c["type"] != "evm"]
                break
        if not selected_chains:
            err("Nothing to download."); sys.exit(1)

    # ── Output file ──────────────────────────────────────────
    section("Step 5 — Output File")
    default_name = f"ledger_{wallet[:8]}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx"
    out_name = (args.output or
                input(f"  Filename [{default_name}]: ").strip() or
                default_name)
    if not out_name.endswith(".xlsx"):
        out_name += ".xlsx"
    output_path = os.path.join(os.getcwd(), out_name)
    raw_dir     = os.path.join(os.getcwd(), "raw_data") if args.save_raw else ""

    if args.save_raw:
        info(f"Raw API responses will be saved to: {raw_dir}/")

    # ── Opening balances ─────────────────────────────────────
    opening_balances = load_opening_balances(args.opening_balances or "")

    # ── Download ─────────────────────────────────────────────
    from fetchers.evm     import fetch_evm, fetch_current_balance
    from fetchers.bitcoin  import fetch_bitcoin
    from fetchers.solana   import fetch_solana

    all_txs:          list = []
    chains_used:      list = []
    on_chain_balances: dict = {}

    for chain_cfg in selected_chains:
        section(f"Downloading — {chain_cfg['name']}")
        try:
            if chain_cfg["type"] == "evm":
                txs = fetch_evm(wallet, chain_cfg, etherscan_key, raw_dir)
                # Fetch current balance for reconciliation
                bal = fetch_current_balance(wallet, chain_cfg, etherscan_key)
                if bal["native"] is not None:
                    on_chain_balances[chain_cfg["symbol"]] = bal["native"]
                on_chain_balances.update(bal["tokens"])
            elif chain_cfg["type"] == "bitcoin":
                txs = fetch_bitcoin(wallet, chain_cfg, raw_dir)
            elif chain_cfg["type"] == "solana":
                txs = fetch_solana(wallet, chain_cfg,
                                   max_detail=args.solana_limit,
                                   raw_dir=raw_dir)
            else:
                warn(f"Unknown chain type: {chain_cfg['type']}"); continue

            all_txs.extend(txs)
            if txs:
                chains_used.append(chain_cfg["name"])

        except KeyboardInterrupt:
            warn("Interrupted — saving what was collected …"); break
        except Exception as e:
            err(f"{chain_cfg['name']} failed: {e}")
            quality_log.log("Fetch Error", f"{chain_cfg['name']}: {e}", "Error",
                            chain=chain_cfg["name"])

    if not all_txs:
        warn("No transactions found. Nothing to export."); sys.exit(0)

    all_txs.sort(key=lambda x: x.get("Date (UTC)", ""))
    info(f"Total transactions collected: {len(all_txs):,}")

    # ── Classify ─────────────────────────────────────────────
    section("Step 6 — Transaction Classification")
    from accounting.classifier import classify_all
    all_txs = classify_all(all_txs, owned_wallets)

    # ── Price fetch ──────────────────────────────────────────
    section("Step 7 — Historical USD Prices (DefiLlama batch)")
    from pricing.defillama import batch_fetch_prices, get_usd_price
    batch_fetch_prices(all_txs, cfg.GAS_TOKEN, raw_dir)

    # ── Direction + USD valuation ─────────────────────────────
    section("Step 8 — Debit / Credit / USD Valuation")
    from accounting.direction import compute_direction_and_usd
    all_txs = compute_direction_and_usd(all_txs, wallet, get_usd_price, raw_dir)

    # Report unknown-price tokens
    no_price = set()
    for tx in all_txs:
        sym = tx.get("Token/Asset","").upper()
        if tx.get("USD Price") is None and sym and sym not in cfg.STABLECOINS:
            no_price.add(sym)
    if no_price:
        warn(f"Tokens with no USD price: {', '.join(sorted(no_price))}")
        warn("Add these to COINGECKO_IDS or CONTRACT_TO_COINGECKO in config.py")

    # ── FIFO Capital Gains ────────────────────────────────────
    section("Step 9 — FIFO Capital Gains")
    from accounting.fifo import compute_fifo_gains
    disposals = compute_fifo_gains(all_txs, opening_balances)

    # ── Export ────────────────────────────────────────────────
    section("Step 10 — Excel Export")
    from reports.excel import export_excel
    export_excel(
        all_txs, disposals, wallet,
        chains_used, list(owned_wallets),
        on_chain_balances, output_path,
    )

    # ── Final summary ─────────────────────────────────────────
    def _s(f): return sum(tx.get(f) or 0 for tx in all_txs
                          if isinstance(tx.get(f),(int,float)))
    cr  = _s("Credit (USD)"); db  = _s("Debit (USD)")
    fu  = _s("Gas Fee (USD)"); net = cr - db
    gl  = sum(d.get("Gain/Loss (USD)") or 0 for d in disposals
              if isinstance(d.get("Gain/Loss (USD)"),(int,float)))
    st  = sum(d.get("Gain/Loss (USD)") or 0 for d in disposals
              if d.get("Term")=="Short-term"
              and isinstance(d.get("Gain/Loss (USD)"),(int,float)))
    lt  = gl - st
    rev = sum(1 for tx in all_txs if tx.get("review_required"))
    unk = sum(1 for tx in all_txs if tx.get("tx_class")=="Unknown")
    qi  = quality_log.count_by_category()

    section("✅  Complete!")
    log.info(f"""
  📁  File             : {output_path}
  📋  Log file         : {log.handlers[-1].baseFilename if hasattr(log.handlers[-1],'baseFilename') else 'see logs/'}
  🔢  Transactions     : {len(all_txs):,}
  ⛓️   Chains           : {', '.join(chains_used)}

  💰  Credits (USD)    : ${cr:>16,.2f}
  💸  Debits  (USD)    : ${db:>16,.2f}
  ⛽  Gas Fees (USD)   : ${fu:>16,.2f}
  📊  Net Position     : ${net:>16,.2f}

  📈  Realized Gain    : ${gl:>16,.2f}
       Short-term      : ${st:>16,.2f}
       Long-term       : ${lt:>16,.2f}

  ⚠️   Needs Review    : {rev:,} transactions (purple rows in ledger)
  ❓   Unknown Class   : {unk:,} transactions (amber rows)
  🔍  Quality Issues   : {sum(qi.values()):,} total
       {chr(10).join(f'       {k}: {v}' for k,v in sorted(qi.items()))}

  Excel sheets:
    📋  Cover          — info, policy, disclaimers
    📊  Summary        — KPIs, asset flow, classification breakdown
    📈  Capital Gains  — FIFO disposals with gain/loss
    🏷️   Classification — every tx classified with confidence score
    ⚖️   Reconciliation — ledger vs on-chain balance
    📅  Per year       — red=debit · green=credit · amber=unknown · purple=review
    🔍  Data Quality   — all warnings and data gaps

  ⚠️  DISCLAIMER: This report is ACCOUNTING-READY, not audit-grade.
     Do not use for tax filing without professional accountant review.
    """)


if __name__ == "__main__":
    main()
