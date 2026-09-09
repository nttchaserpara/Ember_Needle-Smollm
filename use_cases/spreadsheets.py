"""Create XLSX files for Excel and compatible desktop applications."""

import os
import re
from io import BytesIO
from pathlib import Path


def create_spreadsheet(path: str, rows: list, sheet_name: str = "Sheet1",
                       open_after: bool = True) -> str:
    """Create a new workbook. Strings are literal text, never implicit formulas."""
    from openpyxl import Workbook
    from openpyxl.styles import Alignment

    output = Path(path).expanduser().resolve()
    if output.suffix.lower() != ".xlsx":
        raise ValueError("Use an .xlsx filename for Excel spreadsheets")
    if not isinstance(rows, list) or not rows or any(not isinstance(row, list) for row in rows):
        raise ValueError("rows must be a non-empty list of rows, e.g. [['Item', 'Qty'], ['Milk', 2]]")
    if len(rows) > 1048576 or any(len(row) > 16384 for row in rows):
        raise ValueError("Data exceeds Excel's row or column limit")
    if (not sheet_name or len(sheet_name) > 31 or re.search(r'[\\/*?:\[\]]', sheet_name)
            or sheet_name.startswith("'") or sheet_name.endswith("'")):
        raise ValueError("Sheet name must be 1-31 characters without \\ / * ? : [ ] or boundary apostrophes")

    book = Workbook()
    sheet = book.active
    sheet.title = sheet_name
    try:
        for row_number, row in enumerate(rows, 1):
            for column, value in enumerate(row, 1):
                if value is not None and not isinstance(value, (str, int, float, bool)):
                    raise ValueError("Cells must contain text, numbers, booleans, or null")
                if isinstance(value, str) and len(value) > 32767:
                    raise ValueError("Cell text exceeds Excel's 32767-character limit")
                cell = sheet.cell(row_number, column, value)
                if isinstance(value, str):
                    cell.data_type = "s"
                    cell.alignment = Alignment(wrap_text=True, vertical="top")
        buffer = BytesIO()
        book.save(buffer)
        output.parent.mkdir(parents=True, exist_ok=True)
        # Exclusive creation preserves an existing workbook and user edits.
        with output.open("xb") as stream:
            stream.write(buffer.getvalue())
    finally:
        book.close()

    result = f"Spreadsheet saved: {output} ({len(rows)} rows)"
    if open_after:
        if os.name != "nt":
            return result + "\nOpen this file in your spreadsheet application."
        try:
            os.startfile(str(output))
        except OSError as exc:
            return result + f"\nCould not open the spreadsheet application: {exc}"
        result += "\nSent to the default spreadsheet application."
    return result
