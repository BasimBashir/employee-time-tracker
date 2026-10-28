"""Monthly per-employee PDF. No SQL here — formatting only."""
import calendar
import sqlite3
from pathlib import Path

from .query import sessions_for_employee, employees


def write_monthly_pdf(conn: sqlite3.Connection, year_month: str, out_dir: Path) -> Path:
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.units import mm
    from reportlab.pdfgen import canvas

    year, month = (int(x) for x in year_month.split("-"))
    last_day = calendar.monthrange(year, month)[1]
    start, end = f"{year_month}-01", f"{year_month}-{last_day:02d}"

    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"summary_{year_month}.pdf"
    c = canvas.Canvas(str(path), pagesize=A4)
    w, h = A4
    y = h - 20 * mm

    def line(txt, dy=6 * mm, bold=False):
        nonlocal y
        if y < 20 * mm:
            c.showPage()
            y = h - 20 * mm
        c.setFont("Helvetica-Bold" if bold else "Helvetica", 11 if bold else 9)
        c.drawString(20 * mm, y, txt)
        y -= dy

    line(f"Employee out-of-work summary — {year_month}", 10 * mm, bold=True)
    line("")
    grand = 0.0
    for emp in employees(conn):
        rows = sessions_for_employee(conn, emp["id"], start, end)
        total = sum(r.duration_minutes or 0 for r in rows)
        grand += total
        line(f"{emp['name']}  ({emp['employee_code'] or '-'})", 5 * mm, bold=True)
        line(f"  trips: {len(rows)}   total out: {total:.1f} minutes")
        for r in rows:
            flag = " inferred" if r.inferred_return else ""
            line(f"    {r.date}  {r.out_at[11:16]} - {(r.in_at or '??')[11:16]}  "
                 f"{r.duration_minutes or 0:.0f} min  {r.purpose}{flag}")
        line("")
    line(f"Roster total: {grand:.1f} minutes", 8 * mm, bold=True)
    c.save()
    return path
