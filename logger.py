"""
logger.py — Logging setup for CryptoLedger Pro v5.0
Replaces all print() calls with structured logging.
Log file saved alongside the Excel output for audit trail.
"""

import logging
import os
from datetime import datetime

_logger = None
_log_path = None


def setup_logger(output_dir: str = ".") -> logging.Logger:
    global _logger, _log_path

    log_name = f"ledger_run_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"
    _log_path = os.path.join(output_dir, "logs", log_name)
    os.makedirs(os.path.dirname(_log_path), exist_ok=True)

    _logger = logging.getLogger("cryptoledger")
    _logger.setLevel(logging.DEBUG)
    _logger.handlers.clear()

    # Console handler — INFO and above, human-readable
    ch = logging.StreamHandler()
    ch.setLevel(logging.INFO)
    ch.setFormatter(logging.Formatter("%(message)s"))
    _logger.addHandler(ch)

    # File handler — DEBUG and above, full detail with timestamps
    fh = logging.FileHandler(_log_path, encoding="utf-8")
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(logging.Formatter(
        "%(asctime)s [%(levelname)-8s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S"
    ))
    _logger.addHandler(fh)

    _logger.info(f"  📋  Log file: {_log_path}")
    return _logger


def get_logger() -> logging.Logger:
    global _logger
    if _logger is None:
        _logger = setup_logger()
    return _logger


def get_log_path() -> str:
    return _log_path or ""


# Convenience wrappers matching original console helper style
def banner(version: str):
    log = get_logger()
    log.info("\n" + "═"*68)
    log.info(f"   🔗  CRYPTO WALLET LEDGER PRO  v{version}")
    log.info("   Accounting-Ready  •  Intra-Day USD  •  FIFO Gains  •  Classification")
    log.info("═"*68)


def section(t: str):
    get_logger().info(f"\n{'─'*68}\n  {t}\n{'─'*68}")


def ok(m: str):   get_logger().info(f"  ✅  {m}")
def warn(m: str): get_logger().warning(f"  ⚠️   {m}")
def err(m: str):  get_logger().error(f"  ❌  {m}")
def info(m: str): get_logger().info(f"  ℹ️   {m}")
def prog(m: str): get_logger().info(f"  ⏳  {m}")
def dbg(m: str):  get_logger().debug(f"  🔍  {m}")
