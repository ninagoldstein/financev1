from openpyxl import Workbook
from openpyxl.styles import Font


def export_xlsx(
    ticker: str,
    headers: list[str],
    rows: list[list[str]],
    missing: list[tuple[str, str]],
    out_path,
) -> None:
    wb = Workbook()
    ws = wb.active
    ws.title = "Financials"

    ws.append(headers)
    for cell in ws[1]:
        cell.font = Font(bold=True)
    ws.freeze_panes = "A2"

    for row in rows:
        ws.append(row)

    ws.append([])
    ws.append([])
    ws.append(["Missing Data"])
    for metric, period in missing:
        ws.append([metric, period])

    wb.save(out_path)
