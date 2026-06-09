"""
config.py — Central configuration for CryptoLedger Pro v5.0
All constants, chain definitions, token maps, and policy settings live here.
"""

VERSION = "5.0"

# ── API endpoints ────────────────────────────────────────────
ETHERSCAN_V2  = "https://api.etherscan.io/v2/api"
# DefiLlama endpoints are defined in pricing/defillama.py

# ── Stablecoin policy ────────────────────────────────────────
# "fixed_1"     : always treat stablecoins as exactly $1.00
# "market_price": fetch actual market price from CoinGecko
STABLECOIN_POLICY = "fixed_1"   # default; can be overridden via CLI

STABLECOINS = {
    "USDT","USDC","DAI","BUSD","TUSD","USDP","FRAX",
    "LUSD","USDD","GUSD","CUSD","SUSD","USDN","USDX",
    "PYUSD","CRVUSD","FDUSD","USDM","USDE","USDS",
}

# ── Symbol → CoinGecko ID map ────────────────────────────────
# Used by DefiLlama as "coingecko:{id}" keys for native tokens
# (ETH, BTC, SOL, BNB, etc.) that have no contract address.
# ERC-20 tokens are resolved by contract address first.
COINGECKO_IDS = {
    "ETH":"ethereum","BTC":"bitcoin","BNB":"binancecoin",
    "MATIC":"matic-network","POL":"matic-network",
    "AVAX":"avalanche-2","FTM":"fantom","ARB":"arbitrum",
    "OP":"optimism","SOL":"solana","WETH":"weth",
    "WBTC":"wrapped-bitcoin","LINK":"chainlink","UNI":"uniswap",
    "AAVE":"aave","CRV":"curve-dao-token",
    "COMP":"compound-governance-token","MKR":"maker",
    "SNX":"synthetix-network-token","YFI":"yearn-finance",
    "SUSHI":"sushi","BAL":"balancer","1INCH":"1inch",
    "LDO":"lido-dao","RPL":"rocket-pool","RETH":"rocket-pool-eth",
    "STETH":"staked-ether","WSTETH":"wrapped-steth",
    "SHIB":"shiba-inu","PEPE":"pepe","DOGE":"dogecoin",
    "FLOKI":"floki","MEME":"memecoin",
    "SAND":"the-sandbox","MANA":"decentraland","AXS":"axie-infinity",
    "APE":"apecoin","BLUR":"blur",
    "GRT":"the-graph","BAT":"basic-attention-token",
    "ENJ":"enjincoin","CHZ":"chiliz",
    "RNDR":"render-token","FET":"fetch-ai","OCEAN":"ocean-protocol",
    "IMX":"immutable-x","LRC":"loopring","ZRX":"0x",
    "USDT":"tether","USDC":"usd-coin","DAI":"dai",
    "BUSD":"binance-usd","FRAX":"frax",
    "CVX":"convex-finance","PENDLE":"pendle",
    "EIGEN":"eigenlayer","ONDO":"ondo-finance",
    "WLD":"worldcoin-wld","CBETH":"coinbase-wrapped-staked-eth",
}

# ── Contract address → CoinGecko ID ─────────────────────────
# Used by DefiLlama as "coingecko:{id}" for known contracts.
# For unknown contracts DefiLlama resolves via "{chain}:{contract}"
# automatically — so this map is only needed for tokens where the
# coingecko: key is more reliable than the chain:contract key.
# Keys are lowercase contract addresses.
CONTRACT_TO_COINGECKO = {
    # Ethereum mainnet
    "0xdac17f958d2ee523a2206206994597c13d831ec7": "tether",
    "0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48": "usd-coin",
    "0x6b175474e89094c44da98b954eedeac495271d0f": "dai",
    "0x2260fac5e5542a773aa44fbcfedf7c193bc2c599": "wrapped-bitcoin",
    "0xc02aaa39b223fe8d0a0e5c4f27ead9083c756cc2": "weth",
    "0x514910771af9ca656af840dff83e8264ecf986ca": "chainlink",
    "0x1f9840a85d5af5bf1d1762f925bdaddc4201f984": "uniswap",
    "0x7fc66500c84a76ad7e9c93437bfc5ac33e2ddae9": "aave",
    "0xd533a949740bb3306d119cc777fa900ba034cd52": "curve-dao-token",
    "0xae7ab96520de3a18e5e111b5eaab095312d7fe84": "staked-ether",
    "0x5a98fcbea516cf06857215779fd812ca3bef1b32": "lido-dao",
    "0x95ad61b0a150d79219dcf64e1e6cc01f0b64c4ce": "shiba-inu",
    "0x6982508145454ce325ddbe47a25d4ec3d2311933": "pepe",
    "0x4d224452801aced8b2f0aebe155379bb5d594381": "apecoin",
    "0xc944e90c64b2c07662a292be6244bdf05cda44a7": "the-graph",
    "0x0d8775f648430679a709e98d2b0cb6250d2887ef": "basic-attention-token",
    "0xba100000625a3754423978a60c9317c58a424e3d": "balancer",
    "0x111111111117dc0aa78b770fa6a738034120c302": "1inch",
    "0xd533a949740bb3306d119cc777fa900ba034cd52": "curve-dao-token",
    "0x4e3fbd56cd56c3e72c1403e103b45db9da5b9d2b": "convex-finance",
    # Polygon
    "0x2791bca1f2de4661ed88a30c99a7a9449aa84174": "usd-coin",
    "0xc2132d05d31c914a87c6611c10748aeb04b58e8f": "tether",
    "0x8f3cf7ad23cd3cadbd9735aff958023239c6a063": "dai",
    # BSC
    "0x55d398326f99059ff775485246999027b3197955": "tether",
    "0x8ac76a51cc950d9822d68b83fe1ad97b32cd580d": "usd-coin",
    "0x1af3f329e8be154074d8769d1ffa4ee058b1dbc3": "dai",
}

# ── Known WETH/Wrapped contracts (wrap/unwrap = no disposal) ─
WRAP_CONTRACTS = {
    "0xc02aaa39b223fe8d0a0e5c4f27ead9083c756cc2",  # WETH Ethereum
    "0x4200000000000000000000000000000000000006",  # WETH Base/Optimism
    "0x82af49447d8a07e3bd95bd0d56f35241523fbab1",  # WETH Arbitrum
}

# ── Known bridge contracts (bridge = no disposal in most regimes) ─
BRIDGE_CONTRACTS = {
    "0x99c9fc46f92e8a1c0dec1b1747d010903e884be1",  # Optimism Bridge
    "0x4dbd4fc535ac27206064b68ffcf827b0a60bab3f",  # Arbitrum Inbox
    "0xa0c68c638235ee32657e8f720a23cec1bfc77c77",  # Polygon Bridge
    "0xeb466342c4d449bc9f53a865d5cb90586f405215",  # Axelar Gateway
    "0x3ee18b2214aff97000d974cf647e7c347e8fa585",  # Wormhole Token Bridge
}

# ── Native gas token per chain ───────────────────────────────
GAS_TOKEN = {
    "Ethereum":        "ETH",
    "BNB Smart Chain": "BNB",
    "Polygon":         "POL",
    "Avalanche":       "AVAX",
    "Fantom":          "FTM",
    "Arbitrum":        "ETH",
    "Optimism":        "ETH",
    "Base":            "ETH",
    "Bitcoin":         "BTC",
    "Solana":          "SOL",
}

# ── Chain definitions ────────────────────────────────────────
CHAINS = {
    "1":  {"name":"Ethereum",        "symbol":"ETH",  "type":"evm",     "chainid":1,
           "explorer":"https://etherscan.io"},
    "2":  {"name":"BNB Smart Chain", "symbol":"BNB",  "type":"evm",     "chainid":56,
           "explorer":"https://bscscan.com"},
    "3":  {"name":"Polygon",         "symbol":"POL",  "type":"evm",     "chainid":137,
           "explorer":"https://polygonscan.com"},
    "4":  {"name":"Avalanche",       "symbol":"AVAX", "type":"evm",     "chainid":43114,
           "explorer":"https://snowtrace.io"},
    "5":  {"name":"Fantom",          "symbol":"FTM",  "type":"evm",     "chainid":250,
           "explorer":"https://ftmscan.com"},
    "6":  {"name":"Arbitrum",        "symbol":"ETH",  "type":"evm",     "chainid":42161,
           "explorer":"https://arbiscan.io"},
    "7":  {"name":"Optimism",        "symbol":"ETH",  "type":"evm",     "chainid":10,
           "explorer":"https://optimistic.etherscan.io"},
    "8":  {"name":"Base",            "symbol":"ETH",  "type":"evm",     "chainid":8453,
           "explorer":"https://basescan.org"},
    "9":  {"name":"Bitcoin",         "symbol":"BTC",  "type":"bitcoin", "chainid":None,
           "explorer":"https://live.blockcypher.com/btc",
           "api_url":"https://api.blockcypher.com/v1/btc/main"},
    "10": {"name":"Solana",          "symbol":"SOL",  "type":"solana",  "chainid":None,
           "explorer":"https://solscan.io",
           "api_url":"https://api.mainnet-beta.solana.com"},
}

# ── Classification confidence thresholds ─────────────────────
CONFIDENCE_HIGH   = "High"
CONFIDENCE_MEDIUM = "Medium"
CONFIDENCE_LOW    = "Low"
