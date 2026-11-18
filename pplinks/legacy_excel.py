"""Excel writer extracted from supplied notebook cell 4; no notebook execution."""
import logging
from datetime import datetime
from typing import List, Dict
import openpyxl
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from openpyxl.utils import get_column_letter
log=logging.getLogger(__name__)

def write_excel(records: List[Dict], output_file: str) -> None:
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Facebook Reels"
 
    hdr_font = Font(name="Arial", bold=True,  color="FFFFFF", size=11)
    hdr_fill = PatternFill("solid", start_color="1877F2")
    err_fill = PatternFill("solid", start_color="FFD7D7")
    alt_fill = PatternFill("solid", start_color="EEF3FF")
    center   = Alignment(horizontal="center", vertical="center", wrap_text=True)
    left     = Alignment(horizontal="left",   vertical="center", wrap_text=True)
    border   = Border(
        left=Side(style="thin"), right=Side(style="thin"),
        top=Side(style="thin"),  bottom=Side(style="thin"),
    )
 
    headers    = ["#", "Original URL", "Final URL", "Title", "Author",
                  "Description", "Views", "Likes", "Duration",
                  "Posted Date", "Thumbnail URL", "Status", "Error"]
    col_widths = [4,   42,             42,           42,       20,
                  55,           10,      10,      12,
                  16,            42,             10,       35]
    center_cols = {1, 7, 8, 9, 12}
 
    ws.append(headers)
    ws.row_dimensions[1].height = 28
    for ci, (h, w) in enumerate(zip(headers, col_widths), 1):
        cell           = ws.cell(1, ci)
        cell.value     = h
        cell.font      = hdr_font
        cell.fill      = hdr_fill
        cell.alignment = center
        cell.border    = border
        ws.column_dimensions[get_column_letter(ci)].width = w
 
    for ri, rec in enumerate(records, 1):
        values = [
            ri,
            rec.get("url",         ""),
            rec.get("final_url",   ""),
            rec.get("title",       ""),
            rec.get("author",      ""),
            rec.get("description", ""),
            rec.get("views",       ""),
            rec.get("likes",       ""),
            rec.get("duration",    ""),
            rec.get("posted_date", ""),
            rec.get("thumbnail",   ""),
            rec.get("status",      ""),
            rec.get("error",       ""),
        ]
        er = ri + 1
        ws.append(values)
        ws.row_dimensions[er].height = 38
 
        is_err  = rec.get("status") == "ERROR"
        fill    = err_fill if is_err else (alt_fill if ri % 2 == 0 else None)
        txt_col = "AA0000" if is_err else "111111"
 
        for ci in range(1, len(values) + 1):
            cell           = ws.cell(er, ci)
            cell.font      = Font(name="Arial", size=10, color=txt_col)
            cell.alignment = center if ci in center_cols else left
            cell.border    = border
            if fill:
                cell.fill = fill
 
        for ci, key in [(2, "url"), (3, "final_url")]:
            link = rec.get(key, "")
            if link:
                cell           = ws.cell(er, ci)
                cell.hyperlink = link
                cell.font      = Font(name="Arial", size=10,
                                      color="1155CC", underline="single")
 
    ws.freeze_panes = "A2"
 
    ws2   = wb.create_sheet("Summary")
    total = len(records)
    ok    = sum(1 for r in records if r.get("status") == "OK")
    for ri2, (label, value) in enumerate([
        ("Generated",   datetime.now().strftime("%Y-%m-%d %H:%M")),
        ("Output File", str(output_file) if isinstance(output_file, (str, bytes)) else "pplinks-master.xlsx"),
        ("", ""),
        ("Total Links", total),
        ("Scraped OK",  ok),
        ("Errors",      total - ok),
    ], 1):
        ws2.cell(ri2, 1, label).font = Font(name="Arial", bold=True,  size=10)
        ws2.cell(ri2, 2, value).font = Font(name="Arial", bold=False, size=10)
    ws2.column_dimensions["A"].width = 20
    ws2.column_dimensions["B"].width = 28
 
    wb.save(output_file)
    log.info("Saved -> %s  (%d total | %d OK | %d errors)",
             output_file, total, ok, total - ok)
