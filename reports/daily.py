"""Daily CSV + XLSX. Contains no SQL — it formats rows from reports/query.py."""
import csv
import sqlite3
from pathlib import Path

from .query import sessions_for_date, employee_daily_totals

HEADERS = ["employee_id", "employee_name", "date", "out_at", "in_at",
           "duration_minutes", "purpose", "inferred_return", "out_clip", "in_clip"]


def write_daily_csv(conn: sqlite3.Connection, date: str, out_dir: Path) -> Path:
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"attendance_{date}.csv"
    with path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=HEADERS)
        w.writeheader()
        for r in sessions_for_date(conn, date):
            w.writerow({h: getattr(r, h) for h in HEADERS})
    return path


def write_daily_xlsx(conn: sqlite3.Connection, date: str, out_dir: Path) -> Path:
    from openpyxl import Workbook
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"attendance_{date}.xlsx"
    rows = sessions_for_date(conn, date)
    totals = employee_daily_totals(conn, date)

    wb = Workbook()
    ws = wb.active
    ws.title = "Trips"
    ws.append(HEADERS)
    for r in rows:
        ws.append([getattr(r, h) for h in HEADERS])
    for col, width in zip("ABCDEFGHIJ", (12, 22, 12, 21, 21, 17, 10, 16, 34, 34)):
        ws.column_dimensions[col].width = width

    s = wb.create_sheet("Summary")
    s.append(["employee_id", "trips", "total_minutes_out",
              "break_minutes", "delivery_minutes"])
    for t in totals:
        s.append([t["employee_id"], t["trips"], round(t["minutes"], 1),
                  round(t["break_minutes"], 1), round(t["delivery_minutes"], 1)])
    s.append([])
    s.append(["TOTAL", sum(t["trips"] for t in totals),
              round(sum(t["minutes"] for t in totals), 1),
              round(sum(t["break_minutes"] for t in totals), 1),
              round(sum(t["delivery_minutes"] for t in totals), 1)])
    wb.save(path)
    return path
