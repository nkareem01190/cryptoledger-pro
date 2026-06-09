"""
reports/excel.py — Excel workbook builder for CryptoLedger Pro v5.0

New sheets vs v4.0:
  Cover         — wallet info, price policy, classification guide
  Summary       — KPIs, asset flow, year & chain breakdowns
  Capital Gains — FIFO disposals with cost basis, gain/loss, term
  Classification— every tx with its class, treatment, confidence, review flag
  Reconciliation— ledger balance vs on-chain balance per token
  Year sheets   — ledger (accounting cols visible; detail cols hidden)
  Data Quality  — all warnings grouped by severity and category
"""

import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from datetime import datetime
from collections import defaultdict
from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from openpyxl.utils import get_column_letter
import quality_log
from config import VERSION, STABLECOIN_POLICY
from logger import get_logger, ok

# ── Palette ──────────────────────────────────────────────────
C_NAVY   = "1A2E4A"; C_BLUE   = "2E75B6"; C_LBLUE  = "D6E4F0"
C_GREEN  = "1E7B4B"; C_LGREEN = "E2F4EB"; C_DGREEN = "0F4D2E"
C_RED    = "A32D2D"; C_LRED   = "FDECEA"; C_DRED   = "6B1414"
C_AMBER  = "7B5500"; C_LAMBER = "FFF3CD"; C_GOLD   = "B8860B"
C_GRAY   = "F5F5F5"; C_LGRAY  = "E8E8E8"; C_DGRAY  = "555555"
C_WHITE  = "FFFFFF"; C_BLACK  = "000000"; C_PURPLE = "4B0082"
C_LPUR   = "EEE8FF"

def _font(name="Arial",size=10,bold=False,color=C_BLACK,italic=False):
    return Font(name=name,size=size,bold=bold,color=color,italic=italic)
def _fill(h): return PatternFill("solid",start_color=h,fgColor=h)
def _border(color=C_LGRAY,style="thin"):
    s=Side(border_style=style,color=color)
    return Border(left=s,right=s,top=s,bottom=s)
def _align(h="left",v="center",wrap=False):
    return Alignment(horizontal=h,vertical=v,wrap_text=wrap)

STYLES = {
    "hdr_navy":  {"font":_font(bold=True,color=C_WHITE,size=10),"fill":_fill(C_NAVY),"border":_border(),"align":_align("center")},
    "hdr_blue":  {"font":_font(bold=True,color=C_WHITE,size=10),"fill":_fill(C_BLUE),"border":_border(),"align":_align("center")},
    "hdr_green": {"font":_font(bold=True,color=C_WHITE,size=10),"fill":_fill(C_GREEN),"border":_border(),"align":_align("center")},
    "hdr_red":   {"font":_font(bold=True,color=C_WHITE,size=10),"fill":_fill(C_RED),"border":_border(),"align":_align("center")},
    "hdr_amber": {"font":_font(bold=True,color=C_WHITE,size=10),"fill":_fill(C_AMBER),"border":_border(),"align":_align("center")},
    "hdr_purple":{"font":_font(bold=True,color=C_WHITE,size=10),"fill":_fill(C_PURPLE),"border":_border(),"align":_align("center")},
    "lbl":       {"font":_font(bold=True,color=C_DGRAY,size=9),"fill":_fill(C_GRAY),"border":_border(C_LGRAY),"align":_align("left")},
    "val":       {"font":_font(color=C_BLACK,size=10),"fill":_fill(C_WHITE),"border":_border(C_LGRAY),"align":_align("left")},
    "debit_row": {"font":_font(color=C_BLACK,size=9),"fill":_fill(C_LRED),"border":_border(C_LGRAY),"align":_align("left")},
    "credit_row":{"font":_font(color=C_BLACK,size=9),"fill":_fill(C_LGREEN),"border":_border(C_LGRAY),"align":_align("left")},
    "plain_row": {"font":_font(color=C_BLACK,size=9),"fill":_fill(C_WHITE),"border":_border(C_LGRAY),"align":_align("left")},
    "alt_row":   {"font":_font(color=C_BLACK,size=9),"fill":_fill(C_GRAY),"border":_border(C_LGRAY),"align":_align("left")},
    "unk_row":   {"font":_font(color=C_DGRAY,size=9,italic=True),"fill":_fill(C_LAMBER),"border":_border(C_LGRAY),"align":_align("left")},
    "warn_row":  {"font":_font(color=C_AMBER,size=9),"fill":_fill(C_LAMBER),"border":_border(C_LGRAY),"align":_align("left")},
    "err_row":   {"font":_font(color=C_DRED,size=9),"fill":_fill(C_LRED),"border":_border(C_LGRAY),"align":_align("left")},
    "review_row":{"font":_font(color=C_PURPLE,size=9),"fill":_fill(C_LPUR),"border":_border(C_LGRAY),"align":_align("left")},
    "tot_debit": {"font":_font(bold=True,color=C_DRED,size=10),"fill":_fill(C_LRED),"border":_border(C_RED),"align":_align("right")},
    "tot_credit":{"font":_font(bold=True,color=C_DGREEN,size=10),"fill":_fill(C_LGREEN),"border":_border(C_GREEN),"align":_align("right")},
    "tot_lbl":   {"font":_font(bold=True,color=C_BLACK,size=10),"fill":_fill(C_LBLUE),"border":_border(C_BLUE),"align":_align("center")},
    "tot_net":   {"font":_font(bold=True,color=C_GOLD,size=10),"fill":_fill(C_LBLUE),"border":_border(C_BLUE),"align":_align("right")},
}

FMT_TOK="##,##0.00000000"; FMT_USD="$#,##0.00"; FMT_USD6="$#,##0.000000"
FMT_NAT="##,##0.00000000"; FMT_INT="#,##0"

def _apply(cell,sk,fmt=None,al=None):
    s=STYLES[sk]; cell.font=s["font"]; cell.fill=s["fill"]
    cell.border=s["border"]; cell.alignment=al or s["align"]
    if fmt: cell.number_format=fmt

def _w(ws,r,c,v,sk,fmt=None,al=None):
    cell=ws.cell(row=r,column=c,value=v); _apply(cell,sk,fmt,al); return cell

def _mw(ws,r,c1,c2,v,sk,fmt=None,h=None):
    ws.merge_cells(start_row=r,start_column=c1,end_row=r,end_column=c2)
    cell=ws.cell(row=r,column=c1,value=v); _apply(cell,sk,fmt)
    if h: ws.row_dimensions[r].height=h


# ════════════════════════════════════════════════════════════
#  COVER SHEET
# ════════════════════════════════════════════════════════════
def build_cover(wb, wallet, chains_used, total_txs, date_range,
                generated_at, owned_wallets):
    ws = wb.create_sheet("Cover", 0)
    ws.sheet_view.showGridLines = False
    for i,w in enumerate([3,30,45,18,3],1):
        ws.column_dimensions[get_column_letter(i)].width = w

    r = 2
    ws.merge_cells(f"B{r}:D{r}")
    c=ws.cell(row=r,column=2,value=f"CRYPTO WALLET LEDGER PRO  v{VERSION}")
    c.font=_font("Arial",22,True,C_NAVY); c.alignment=_align("center")
    ws.row_dimensions[r].height=34

    r+=1; ws.merge_cells(f"B{r}:D{r}")
    c=ws.cell(row=r,column=2,
              value="Professional Crypto Accounting · FIFO Capital Gains · Full Classification")
    c.font=_font(size=11,color=C_DGRAY,italic=True); c.alignment=_align("center")
    ws.row_dimensions[r].height=20

    r+=1; ws.merge_cells(f"B{r}:D{r}")
    c=ws.cell(row=r,column=2,value=f"Generated: {generated_at}")
    c.font=_font(size=9,color=C_DGRAY); c.alignment=_align("center")
    ws.row_dimensions[r].height=16

    r+=2
    info_rows = [
        ("Wallet Address",       wallet),
        ("Owned Wallets",        ", ".join(owned_wallets) if owned_wallets else "Not specified"),
        ("Blockchain(s)",        ", ".join(chains_used)),
        ("Total Transactions",   f"{total_txs:,}"),
        ("Date Range",           date_range),
        ("Price Method",         "CoinGecko hourly candles — intra-day accurate"),
        ("Stablecoin Policy",    STABLECOIN_POLICY.replace("_"," ").title()),
        ("Gain Method",          "FIFO (First-In First-Out)"),
        ("Report Version",       f"v{VERSION}"),
    ]
    for lbl,val in info_rows:
        _w(ws,r,2,lbl,"lbl"); _w(ws,r,3,val,"val"); _w(ws,r,4,"","val")
        ws.row_dimensions[r].height=18; r+=1

    r+=1; _mw(ws,r,2,4,"WORKBOOK STRUCTURE","hdr_navy",h=18); r+=1
    sheets = [
        ("Cover (this sheet)",  "Wallet info, policy, column key, colour code"),
        ("Summary",             "KPIs, asset flow table, year & chain breakdown"),
        ("Capital Gains",       "FIFO disposals — proceeds, cost basis, gain/loss, term"),
        ("Classification",      "Every tx with type, treatment, confidence, review flag"),
        ("Reconciliation",      "Ledger balance vs on-chain balance per token"),
        ("2021, 2022 … (year)", "Full transaction ledger — one tab per year"),
        ("Data Quality",        "All warnings: missing prices, FIFO gaps, duplicates"),
    ]
    for lbl,desc in sheets:
        _w(ws,r,2,lbl,"lbl"); _w(ws,r,3,desc,"val"); _w(ws,r,4,"","val")
        ws.row_dimensions[r].height=16; r+=1

    r+=1; _mw(ws,r,2,4,"ACCOUNTING COLUMN REFERENCE","hdr_blue",h=18); r+=1
    cols = [
        ("Date (UTC)",       "Transaction timestamp in UTC"),
        ("Type",             "Raw tx type from blockchain API"),
        ("Token/Asset",      "Token symbol (ETH, USDC, LINK …)"),
        ("Debit (tokens)",   "Amount that LEFT your wallet"),
        ("Credit (tokens)",  "Amount that ENTERED your wallet"),
        ("USD Price",        "Hourly CoinGecko price at transaction time"),
        ("Debit (USD)",      "Debit × USD Price"),
        ("Credit (USD)",     "Credit × USD Price"),
        ("Gas Fee (USD)",    "Gas × native-token historical price"),
        ("Status",           "Success / Failed"),
        ("Tx Class",         "Accounting classification (Swap, Receive, Send …)"),
        ("Treatment",        "Accounting treatment applied"),
        ("Confidence",       "High / Medium / Low — classifier confidence"),
        ("Review Required",  "Yes = needs accountant review"),
    ]
    for col,desc in cols:
        _w(ws,r,2,col,"lbl"); _w(ws,r,3,desc,"val"); _w(ws,r,4,"","val")
        ws.row_dimensions[r].height=16; r+=1

    r+=1; _mw(ws,r,2,4,"ROW COLOUR CODE","hdr_blue",h=18); r+=1
    colours = [
        ("tot_debit",  "RED",    "Debit — funds LEFT wallet"),
        ("tot_credit", "GREEN",  "Credit — funds ENTERED wallet"),
        ("unk_row",    "AMBER",  "Unknown direction — review required"),
        ("review_row", "PURPLE", "Classified but needs accountant review"),
    ]
    for style,label,desc in colours:
        _w(ws,r,2,label,style,al=_align("center"))
        _w(ws,r,3,desc,style); _w(ws,r,4,"",style)
        ws.row_dimensions[r].height=16; r+=1

    r+=1; _mw(ws,r,2,4,"IMPORTANT DISCLAIMERS","hdr_red",h=18); r+=1
    disclaimers = [
        "This report is ACCOUNTING-READY, not audit-grade. Do not rely on it for tax filing without professional review.",
        "Stablecoin prices are " + ("fixed at $1.00 (policy: fixed_1)." if STABLECOIN_POLICY=="fixed_1" else "fetched at market rate (policy: market_price)."),
        "Self-transfers between owned wallets are excluded from FIFO — verify owned_wallets list is complete.",
        "Bridge/wrap transactions are flagged for manual review — treatment depends on jurisdiction.",
        "FIFO gaps (UNKNOWN cost basis) must be resolved before using Capital Gains for tax purposes.",
    ]
    for disc in disclaimers:
        ws.merge_cells(f"B{r}:D{r}")
        c=ws.cell(row=r,column=2,value=f"⚠ {disc}")
        c.font=_font(size=9,color=C_RED,italic=True); c.alignment=_align("left",wrap=True)
        ws.row_dimensions[r].height=28; r+=1


# ════════════════════════════════════════════════════════════
#  SUMMARY SHEET
# ════════════════════════════════════════════════════════════
def build_summary(wb, all_txs, wallet, chains_used):
    ws = wb.create_sheet("Summary", 1)
    ws.sheet_view.showGridLines = False
    ws.freeze_panes = "A3"
    for i,w in enumerate([3,26,18,18,18,18,18,3],1):
        ws.column_dimensions[get_column_letter(i)].width = w

    r=1; _mw(ws,r,2,7,f"ACCOUNTING SUMMARY — v{VERSION}","hdr_navy",h=24); r+=1
    _mw(ws,r,2,7,wallet,"lbl",h=16); r+=2

    def _s(f): return sum(tx.get(f) or 0 for tx in all_txs
                          if isinstance(tx.get(f),(int,float)))

    cr=_s("Credit (USD)"); db=_s("Debit (USD)")
    fu=_s("Gas Fee (USD)"); net=cr-db
    years=sorted(set(tx["Year"] for tx in all_txs)) if all_txs else [0]
    unk=sum(1 for tx in all_txs if tx.get("tx_class")=="Unknown")
    rev=sum(1 for tx in all_txs if tx.get("review_required"))
    no_p=sum(1 for tx in all_txs if tx.get("USD Price") is None
             and tx.get("Token/Asset","") not in {"USDT","USDC","DAI","BUSD"})

    kpis=[
        ("Total Transactions",   f"{len(all_txs):,}"),
        ("Needs Review",         f"{rev:,}  (purple rows — see Classification sheet)"),
        ("Years Active",         f"{min(years)} – {max(years)}" if years else "—"),
        ("No USD Price",         f"{no_p:,}  txs (see Data Quality tab)"),
        ("Total Credits (USD)",  f"${cr:,.2f}"),
        ("Total Debits (USD)",   f"${db:,.2f}"),
        ("Net Position (USD)",   f"${net:,.2f}"),
        ("Gas Fees (USD)",       f"${fu:,.2f}"),
        ("Unknown Class",        f"{unk:,}  txs"),
        ("Stablecoin Policy",    STABLECOIN_POLICY.replace("_"," ").title()),
    ]
    for i in range(0,len(kpis),2):
        l1,v1=kpis[i]; l2,v2=kpis[i+1] if i+1<len(kpis) else ("","")
        _w(ws,r,2,l1,"lbl"); _w(ws,r,3,v1,"val")
        _w(ws,r,4,l2,"lbl"); _w(ws,r,5,v2,"val")
        _w(ws,r,6,"","val"); _w(ws,r,7,"","val")
        ws.row_dimensions[r].height=18; r+=1

    r+=1
    # Asset flow
    _mw(ws,r,2,8,"ASSET FLOW BY TOKEN","hdr_blue",h=20); r+=1
    for ci,h in enumerate(["Token","In (tokens)","Out (tokens)","Net (tokens)",
                            "In (USD)","Out (USD)","Net (USD)"],2):
        _w(ws,r,ci,h,"hdr_blue")
    ws.row_dimensions[r].height=16
    ws.column_dimensions[get_column_letter(7)].width=18
    ws.column_dimensions[get_column_letter(8)].width=18
    r+=1

    flow=defaultdict(lambda:{"in_t":0.,"out_t":0.,"in_u":0.,"out_u":0.})
    for tx in all_txs:
        sym=tx.get("Token/Asset","").strip()
        if not sym: continue
        cr2=tx.get("Credit"); cu=tx.get("Credit (USD)")
        db2=tx.get("Debit");  du=tx.get("Debit (USD)")
        if cr2: flow[sym]["in_t"]  += float(cr2)
        if cu:  flow[sym]["in_u"]  += float(cu)
        if db2: flow[sym]["out_t"] += float(db2)
        if du:  flow[sym]["out_u"] += float(du)

    rows=sorted(flow.items(),key=lambda x:abs(x[1]["in_u"]-x[1]["out_u"]),reverse=True)
    for ai,(sym,f) in enumerate(rows):
        nt=f["in_t"]-f["out_t"]; nu=f["in_u"]-f["out_u"]
        st="plain_row" if ai%2==0 else "alt_row"
        _w(ws,r,2,sym,st)
        _w(ws,r,3,f["in_t"],st,FMT_TOK,_align("right"))
        _w(ws,r,4,f["out_t"],st,FMT_TOK,_align("right"))
        _w(ws,r,5,nt,"tot_credit" if nt>=0 else "tot_debit",FMT_TOK)
        _w(ws,r,6,f["in_u"],st,FMT_USD,_align("right"))
        _w(ws,r,7,f["out_u"],st,FMT_USD,_align("right"))
        _w(ws,r,8,nu,"tot_credit" if nu>=0 else "tot_debit",FMT_USD)
        ws.row_dimensions[r].height=16; r+=1

    r+=1
    # Classification breakdown
    _mw(ws,r,2,8,"TRANSACTION CLASSIFICATION SUMMARY","hdr_purple",h=20); r+=1
    for ci,h in enumerate(["Class","Count","Review Required","% of Total"],2):
        _w(ws,r,ci,h,"hdr_purple")
    ws.row_dimensions[r].height=16; r+=1

    cls_counts=defaultdict(lambda:{"n":0,"rev":0})
    for tx in all_txs:
        c=tx.get("tx_class","Unknown")
        cls_counts[c]["n"]+=1
        if tx.get("review_required"): cls_counts[c]["rev"]+=1

    total_n=len(all_txs) or 1
    for ai,(cls,d) in enumerate(sorted(cls_counts.items(),key=lambda x:-x[1]["n"])):
        st="plain_row" if ai%2==0 else "alt_row"
        pct=round(d["n"]/total_n*100,1)
        _w(ws,r,2,cls,st); _w(ws,r,3,d["n"],st,FMT_INT,_align("right"))
        _w(ws,r,4,d["rev"],st,FMT_INT,_align("right"))
        _w(ws,r,5,f"{pct}%",st,None,_align("right"))
        ws.row_dimensions[r].height=16; r+=1

    r+=1
    # Year breakdown
    _mw(ws,r,2,8,"BREAKDOWN BY YEAR","hdr_blue",h=20); r+=1
    for ci,h in enumerate(["Year","Txs","Debits (USD)","Credits (USD)",
                            "Net (USD)","Gas (USD)"],2):
        _w(ws,r,ci,h,"hdr_navy"); ws.row_dimensions[r].height=16
    r+=1

    yd=defaultdict(lambda:{"n":0,"db":0.,"cr":0.,"fu":0.})
    for tx in all_txs:
        yr=tx["Year"]; yd[yr]["n"]+=1
        yd[yr]["db"]+=tx.get("Debit (USD)") or 0 if isinstance(tx.get("Debit (USD)"),(int,float)) else 0
        yd[yr]["cr"]+=tx.get("Credit (USD)") or 0 if isinstance(tx.get("Credit (USD)"),(int,float)) else 0
        yd[yr]["fu"]+=tx.get("Gas Fee (USD)") or 0 if isinstance(tx.get("Gas Fee (USD)"),(int,float)) else 0

    for yi,yr in enumerate(sorted(yd)):
        d=yd[yr]; st="plain_row" if yi%2==0 else "alt_row"; ny=d["cr"]-d["db"]
        _w(ws,r,2,yr,st); _w(ws,r,3,d["n"],st,FMT_INT,_align("right"))
        _w(ws,r,4,round(d["db"],2),st,FMT_USD,_align("right"))
        _w(ws,r,5,round(d["cr"],2),st,FMT_USD,_align("right"))
        _w(ws,r,6,round(ny,2),"tot_credit" if ny>=0 else "tot_debit",FMT_USD)
        _w(ws,r,7,round(d["fu"],2),st,FMT_USD,_align("right"))
        ws.row_dimensions[r].height=16; r+=1


# ════════════════════════════════════════════════════════════
#  CAPITAL GAINS SHEET
# ════════════════════════════════════════════════════════════
def build_gains_sheet(wb, disposals: list):
    ws = wb.create_sheet("Capital Gains", 2)
    ws.sheet_view.showGridLines = False; ws.freeze_panes = "A4"
    widths=[3,12,14,14,16,16,14,16,16,14,14,46,3]
    for i,w in enumerate(widths,1):
        ws.column_dimensions[get_column_letter(i)].width=w

    r=1; _mw(ws,r,2,12,"CAPITAL GAINS — FIFO METHOD","hdr_navy",h=24); r+=1
    _mw(ws,r,2,12,
        "Each row = one disposal lot.  Long-term ≥ 365 days.  "
        "UNKNOWN cost basis = no matching acquisition lot — resolve before tax filing.",
        "lbl",h=20); r+=1

    hdrs=["Token","Disposal Date","Type","Qty Disposed","Proceeds (USD)",
          "Acq. Date","Acq. Source","Cost Basis (USD)","Gain/Loss (USD)",
          "Holding Days","Term","Tx Hash (Proof)"]
    for ci,h in enumerate(hdrs,2): _w(ws,r,ci,h,"hdr_blue")
    ws.auto_filter.ref=f"B{r}:{get_column_letter(len(hdrs)+1)}{r}"
    ws.row_dimensions[r].height=16; r+=1

    total_gl=0.0
    for di,d in enumerate(disposals):
        unk=d["Acq. Date"]=="UNKNOWN"
        gl=d.get("Gain/Loss (USD)")
        if isinstance(gl,(int,float)): total_gl+=gl
        if unk:                base="unk_row"
        elif (gl or 0)>=0:    base="credit_row" if di%2==0 else "plain_row"
        else:                  base="debit_row"
        gl_st=("tot_credit" if (gl or 0)>=0 else "tot_debit") if not unk else "unk_row"

        _w(ws,r,2,d["Token"],base)
        _w(ws,r,3,d["Disposal Date"],base)
        _w(ws,r,4,d["Disposal Type"],base)
        _w(ws,r,5,d["Qty Disposed"],base,FMT_TOK,_align("right"))
        _w(ws,r,6,d["Proceeds (USD)"],base,FMT_USD,_align("right"))
        _w(ws,r,7,d["Acq. Date"],base)
        _w(ws,r,8,d.get("Acq. Source",""),base)
        _w(ws,r,9,d["Cost Basis (USD)"],base,FMT_USD,_align("right"))
        _w(ws,r,10,gl,gl_st,FMT_USD)
        _w(ws,r,11,d["Holding Days"],base,FMT_INT,_align("right"))
        _w(ws,r,12,d["Term"],base,None,_align("center"))
        # Full Tx Hash as clickable hyperlink to block explorer
        tx_hash  = d.get("Tx Hash","")
        explorer = d.get("Explorer Link","")
        hcell    = _w(ws,r,13,tx_hash,base)
        if tx_hash and explorer:
            hcell.hyperlink = explorer
            hcell.font = Font(name="Arial",size=9,color="0563C1",underline="single")
        ws.row_dimensions[r].height=15; r+=1

    _mw(ws,r,2,9,f"NET REALIZED GAIN/LOSS — {len(disposals):,} disposal events","tot_lbl",h=20)
    ns="tot_credit" if total_gl>=0 else "tot_debit"
    _w(ws,r,10,round(total_gl,2),ns,FMT_USD)
    for ci in range(11,14):
        c=ws.cell(row=r,column=ci); c.fill=_fill(C_LBLUE); c.border=_border(C_BLUE)
    ws.row_dimensions[r].height=20


# ════════════════════════════════════════════════════════════
#  CLASSIFICATION SHEET
# ════════════════════════════════════════════════════════════
def build_classification_sheet(wb, all_txs: list):
    ws = wb.create_sheet("Classification", 3)
    ws.sheet_view.showGridLines = False; ws.freeze_panes = "A3"
    widths=[20,14,12,18,18,18,14,50,46,3]
    for i,w in enumerate(widths,1):
        ws.column_dimensions[get_column_letter(i)].width=w

    r=1; _mw(ws,r,1,9,"TRANSACTION CLASSIFICATION — ALL TRANSACTIONS","hdr_purple",h=22); r+=1
    hdrs=["Date (UTC)","Token","Tx Class","Accounting Treatment",
          "FIFO Action","Confidence","Review?","Classification Notes","Tx Hash (Proof)"]
    for ci,h in enumerate(hdrs,1): _w(ws,r,ci,h,"hdr_purple")
    ws.auto_filter.ref=f"A{r}:{get_column_letter(len(hdrs))}{r}"
    ws.row_dimensions[r].height=16; r+=1

    for ri,tx in enumerate(all_txs):
        rev=tx.get("review_required",False)
        cls=tx.get("tx_class","Unknown")
        if cls=="Unknown":           base="unk_row"
        elif rev:                    base="review_row"
        elif ri%2==0:                base="plain_row"
        else:                        base="alt_row"

        _w(ws,r,1,tx.get("Date (UTC)",""),base)
        _w(ws,r,2,tx.get("Token/Asset",""),base)
        _w(ws,r,3,cls,base)
        _w(ws,r,4,tx.get("accounting_treatment",""),base)
        _w(ws,r,5,tx.get("fifo_action",""),base)
        _w(ws,r,6,tx.get("confidence",""),base)
        _w(ws,r,7,"YES" if rev else "no",
           "warn_row" if rev else base,None,_align("center"))
        _w(ws,r,8,tx.get("classification_notes",""),base,None,_align("left",wrap=True))
        # Full Tx Hash as clickable hyperlink
        tx_hash  = tx.get("Tx Hash","")
        explorer = tx.get("Explorer Link","")
        hcell    = _w(ws,r,9,tx_hash,base)
        if tx_hash and explorer:
            hcell.hyperlink = explorer
            hcell.font = Font(name="Arial",size=9,color="0563C1",underline="single")
        ws.row_dimensions[r].height=18; r+=1


# ════════════════════════════════════════════════════════════
#  RECONCILIATION SHEET
# ════════════════════════════════════════════════════════════
def build_reconciliation_sheet(wb, all_txs: list,
                                on_chain_balances: dict):
    """
    Compares calculated net token balance (from ledger)
    with on-chain balance (from API).
    Flags discrepancies > 0.001 tokens.
    """
    ws = wb.create_sheet("Reconciliation", 4)
    ws.sheet_view.showGridLines = False
    for i,w in enumerate([3,18,18,18,18,42,3],1):
        ws.column_dimensions[get_column_letter(i)].width=w

    r=1; _mw(ws,r,2,6,"RECONCILIATION — LEDGER vs ON-CHAIN BALANCE","hdr_navy",h=24); r+=1
    _mw(ws,r,2,6,
        "Compares this ledger's calculated net position with current on-chain balance. "
        "Discrepancies indicate missing transactions, spam tokens, or data gaps.",
        "lbl",h=24); r+=2

    hdrs=["Token","Ledger Balance","On-Chain Balance","Difference","Status"]
    for ci,h in enumerate(hdrs,2): _w(ws,r,ci,h,"hdr_navy")
    ws.row_dimensions[r].height=16; r+=1

    # Calculate ledger net balances
    ledger_bal=defaultdict(float)
    for tx in all_txs:
        sym=tx.get("Token/Asset","").strip()
        if not sym: continue
        cr=tx.get("Credit"); db=tx.get("Debit")
        if cr: ledger_bal[sym]+=float(cr)
        if db: ledger_bal[sym]-=float(db)

    all_tokens=set(ledger_bal.keys()) | set(on_chain_balances.keys())
    issues=0
    for ai,sym in enumerate(sorted(all_tokens)):
        led=round(ledger_bal.get(sym,0.0),8)
        onc=on_chain_balances.get(sym)
        if onc is None:
            diff=None; status="On-chain balance unknown"
            st="alt_row" if ai%2==0 else "plain_row"
        else:
            onc=round(float(onc),8)
            diff=round(onc-led,8)
            if abs(diff)<0.001:
                status="✅  Reconciled"; st="credit_row"
            else:
                status=f"⚠  Discrepancy — {diff:+.8f} tokens missing/extra"
                st="warn_row"; issues+=1

        _w(ws,r,2,sym,st)
        _w(ws,r,3,led,st,FMT_TOK,_align("right"))
        _w(ws,r,4,onc if onc is not None else "—",st,
           FMT_TOK if onc is not None else None,_align("right"))
        _w(ws,r,5,diff if diff is not None else "—",
           "tot_debit" if (diff or 0)!=0 else st,
           FMT_TOK if diff is not None else None,_align("right"))
        _w(ws,r,6,status,st)
        ws.row_dimensions[r].height=16; r+=1

    r+=1
    summary=("✅  All tokens reconciled." if issues==0
             else f"⚠  {issues} token(s) with discrepancies — review Data Quality sheet.")
    _mw(ws,r,2,6,summary,"hdr_green" if issues==0 else "hdr_amber",h=20)


# ════════════════════════════════════════════════════════════
#  YEAR SHEETS
# ════════════════════════════════════════════════════════════
LEDGER_ACCT_COLS=[
    ("Date (UTC)",    "Date (UTC)",         20,"left",  None),
    ("Type",          "Type",               14,"left",  None),
    ("Token/Asset",   "Token/Asset",        13,"left",  None),
    ("Debit",         "Debit (tokens)",     16,"right", FMT_TOK),
    ("Credit",        "Credit (tokens)",    16,"right", FMT_TOK),
    ("USD Price",     "USD Price",          14,"right", FMT_USD6),
    ("Debit (USD)",   "Debit (USD)",        15,"right", FMT_USD),
    ("Credit (USD)",  "Credit (USD)",       15,"right", FMT_USD),
    ("Gas Fee (USD)", "Gas Fee (USD)",      15,"right", FMT_USD),
    ("tx_class",      "Tx Class",           18,"left",  None),
    ("confidence",    "Confidence",         10,"center",None),
    ("review_required","Review?",            9,"center",None),
    ("Status",        "Status",             10,"center",None),
]
LEDGER_ACCT_EXTRA_COLS=[
    # Tx Hash is always visible — it is the audit proof for each row
    ("Tx Hash",          "Tx Hash (Proof)",   46,"left", None),
]
LEDGER_DETAIL_COLS=[
    # These remain hidden by default; right-click column header to unhide
    ("From",             "From Wallet",       44,"left", None),
    ("To",               "To Wallet",         44,"left", None),
    ("Gas Fee (native)", "Gas Fee (native)",  16,"right",FMT_NAT),
    ("Contract",         "Contract Addr",     44,"left", None),
    ("Explorer Link",    "Explorer Link",     72,"left", None),
]

def build_year_sheet(wb, year: int, txs: list):
    ws=wb.create_sheet(str(year))
    ws.freeze_panes="A3"; ws.sheet_view.showGridLines=False

    # Acct cols + Tx Hash (always visible) + detail cols (hidden)
    visible_cols = LEDGER_ACCT_COLS + LEDGER_ACCT_EXTRA_COLS
    all_cols     = visible_cols + LEDGER_DETAIL_COLS
    n_visible    = len(visible_cols)
    n_total      = len(all_cols)
    last         = get_column_letter(n_total)

    for ci,(_,_,width,_,_) in enumerate(all_cols,1):
        ws.column_dimensions[get_column_letter(ci)].width=width
    for ci in range(n_visible+1, n_total+1):
        ws.column_dimensions[get_column_letter(ci)].hidden=True

    ws.merge_cells(f"A1:{last}1")
    t=ws.cell(row=1,column=1,
              value=(f"Transaction Ledger — {year}  ({len(txs):,} transactions)  "
                     f"[Detail cols hidden — right-click header to unhide]"))
    t.font=_font(bold=True,color=C_WHITE,size=11)
    t.fill=_fill(C_NAVY); t.alignment=_align("center")
    ws.row_dimensions[1].height=22

    for ci,(_,hdr,_,_,_) in enumerate(LEDGER_ACCT_COLS,1):
        c=ws.cell(row=2,column=ci,value=hdr)
        c.font=_font(bold=True,color=C_WHITE,size=10)
        c.fill=_fill(C_NAVY); c.border=_border(); c.alignment=_align("center")
    # Tx Hash header — distinct teal colour to signal audit column
    for ci,(_,hdr,_,_,_) in enumerate(LEDGER_ACCT_EXTRA_COLS, len(LEDGER_ACCT_COLS)+1):
        c=ws.cell(row=2,column=ci,value=hdr)
        c.font=_font(bold=True,color=C_WHITE,size=10)
        c.fill=_fill("1E7B4B"); c.border=_border(); c.alignment=_align("center")
    for ci,(_,hdr,_,_,_) in enumerate(LEDGER_DETAIL_COLS, n_visible+1):
        c=ws.cell(row=2,column=ci,value=hdr)
        c.font=_font(bold=True,color=C_WHITE,size=10)
        c.fill=_fill(C_BLUE); c.border=_border(); c.alignment=_align("center")
    ws.row_dimensions[2].height=18
    ws.auto_filter.ref=f"A2:{get_column_letter(len(LEDGER_ACCT_COLS))}2"

    for ri,tx in enumerate(txs):
        rn=ri+3
        dir_=tx.get("Direction","")
        cls=tx.get("tx_class","")
        rev=tx.get("review_required",False)
        if dir_=="debit":        base="debit_row"
        elif dir_=="credit":     base="credit_row"
        elif dir_=="unknown" or cls=="Unknown": base="unk_row"
        elif rev:                base="review_row"
        elif ri%2==0:            base="plain_row"
        else:                    base="alt_row"

        for ci,(field,_,_,al,fmt) in enumerate(all_cols,1):
            raw=tx.get(field); val=None if raw=="" else raw
            if field=="review_required": val="YES" if raw else "no"
            c=ws.cell(row=rn,column=ci,value=val)
            c.font=STYLES[base]["font"]; c.fill=STYLES[base]["fill"]
            c.border=_border(C_LGRAY); c.alignment=_align(al)
            if fmt and val is not None: c.number_format=fmt
            # Tx Hash — render as clickable hyperlink
            if field=="Tx Hash" and val:
                explorer = tx.get("Explorer Link","")
                if explorer:
                    c.hyperlink = explorer
                    c.font = Font(name="Arial",size=9,color="0563C1",underline="single")
        ws.row_dimensions[rn].height=15

    tot=len(txs)+3
    ws.merge_cells(f"A{tot}:C{tot}")
    c=ws.cell(row=tot,column=1,value=f"TOTALS — {len(txs):,} transactions")
    c.font=_font(bold=True,color=C_NAVY,size=10)
    c.fill=_fill(C_LBLUE); c.border=_border(C_BLUE); c.alignment=_align("center")
    sum_fields={"Debit","Credit","Debit (USD)","Credit (USD)","Gas Fee (USD)"}
    for ci,(field,_,_,_,fmt) in enumerate(all_cols,1):
        c=ws.cell(row=tot,column=ci)
        c.fill=_fill(C_LBLUE); c.border=_border(C_BLUE)
        if field in sum_fields:
            ltr=get_column_letter(ci)
            c.value=f"=SUMIF({ltr}3:{ltr}{tot-1},\">0\")"
            c.number_format=fmt or FMT_USD
            c.font=_font(bold=True,color=C_NAVY,size=10)
            c.alignment=_align("right")
    ws.row_dimensions[tot].height=18


# ════════════════════════════════════════════════════════════
#  DATA QUALITY SHEET
# ════════════════════════════════════════════════════════════
def build_quality_sheet(wb, issues: list):
    ws=wb.create_sheet("Data Quality")
    ws.sheet_view.showGridLines=False
    for i,w in enumerate([3,24,16,62,46,3],1):
        ws.column_dimensions[get_column_letter(i)].width=w

    r=1; _mw(ws,r,2,5,"DATA QUALITY REPORT","hdr_navy",h=22); r+=1
    _mw(ws,r,2,5,
        "All warnings and data gaps found during this run. "
        "Resolve Error-severity items before using for tax purposes.",
        "lbl",h=20); r+=2

    if not issues:
        _mw(ws,r,2,5,
            "✅  No issues found — all transactions classified and priced successfully.",
            "val",h=20); return

    from collections import OrderedDict
    cat_order=["FIFO Gap","FIFO No Proceeds","FIFO No Cost Basis",
               "No USD Price","Unclassified Tx","Solana Truncated",
               "Duplicate Removed","Other"]
    sev_style={"Error":"hdr_red","Warning":"hdr_amber","Info":"hdr_blue"}
    row_style={"Error":"err_row","Warning":"warn_row","Info":"plain_row"}

    groups=OrderedDict()
    for cat in cat_order: groups[cat]=[]
    for issue in issues:
        cat=issue.category if issue.category in groups else "Other"
        groups[cat].append(issue)

    for cat,items in groups.items():
        if not items: continue
        sev=items[0].severity if items else "Warning"
        hdr_st=sev_style.get(sev,"hdr_blue")
        row_st=row_style.get(sev,"plain_row")
        _mw(ws,r,2,5,
            f"{cat}  ({len(items):,} instance{'s' if len(items)!=1 else ''})  "
            f"[{sev}]",hdr_st,h=18); r+=1
        for issue in items:
            _w(ws,r,2,issue.category,row_st)
            _w(ws,r,3,issue.severity,row_st,None,_align("center"))
            _w(ws,r,4,issue.detail,row_st)
            # Tx Hash with explorer link where available
            tx_hash = issue.tx_hash or ""
            hcell   = _w(ws,r,5,tx_hash,row_st)
            if tx_hash:
                hcell.font = Font(name="Arial",size=9,color="0563C1",underline="single")
            ws.row_dimensions[r].height=15; r+=1
        r+=1


# ════════════════════════════════════════════════════════════
#  ORCHESTRATOR
# ════════════════════════════════════════════════════════════
def export_excel(all_txs: list, disposals: list,
                 wallet: str, chains_used: list,
                 owned_wallets: list,
                 on_chain_balances: dict,
                 output_path: str):
    log=get_logger()
    log.info("\n  Building Excel workbook …")

    by_year=defaultdict(list)
    for tx in all_txs: by_year[tx["Year"]].append(tx)
    years=sorted(by_year.keys())
    date_rng=f"{min(years)} – {max(years)}" if years else "—"
    gen_at=datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")

    wb=Workbook()
    if "Sheet" in wb.sheetnames: del wb["Sheet"]

    build_cover(wb,wallet,chains_used,len(all_txs),date_rng,gen_at,owned_wallets)
    ok("Cover sheet built")

    build_summary(wb,all_txs,wallet,chains_used)
    ok("Summary sheet built")

    build_gains_sheet(wb,disposals)
    ok(f"Capital Gains sheet built — {len(disposals):,} disposal events")

    build_classification_sheet(wb,all_txs)
    ok("Classification sheet built")

    build_reconciliation_sheet(wb,all_txs,on_chain_balances)
    ok("Reconciliation sheet built")

    for yr in years:
        build_year_sheet(wb,yr,by_year[yr])
        ok(f"Year {yr}: {len(by_year[yr]):,} rows written")

    build_quality_sheet(wb,quality_log.all_issues())
    ok(f"Data Quality sheet built — {len(quality_log.all_issues()):,} entries")

    wb.save(output_path)
    ok(f"Saved → {output_path}")
