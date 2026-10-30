"""Excel export module for uni-test-sentinel."""

import io
from typing import Any

from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

from src.grading import calculate_percentage, format_score


def generate_excel_report(
    sessions: list[dict[str, Any]],
    group_filter: str | None = None,
) -> io.BytesIO:
    """Generates a styled Excel workbook (.xlsx) with student exam results.

    Columns: [№ п/п, Дата, Группа, ФИО, Баллы, Процент, Оценка]
    """
    wb = Workbook()
    ws = wb.active
    ws.title = "Ведомость тестирования"

    # Ensure grid lines are visible
    ws.views.sheetView[0].showGridLines = True

    # Palette
    HEADER_FILL = PatternFill(
        start_color="1E3A8A", end_color="1E3A8A", fill_type="solid"
    )  # Deep Navy
    HEADER_FONT = Font(name="Calibri", size=11, bold=True, color="FFFFFF")
    TITLE_FONT = Font(name="Calibri", size=14, bold=True, color="1E3A8A")
    META_FONT = Font(name="Calibri", size=10, italic=True, color="4B5563")
    DATA_FONT = Font(name="Calibri", size=10)
    BOLD_FONT = Font(name="Calibri", size=10, bold=True)

    # Borders
    thin_border_side = Side(border_style="thin", color="D1D5DB")
    cell_border = Border(
        left=thin_border_side,
        right=thin_border_side,
        top=thin_border_side,
        bottom=thin_border_side,
    )
    header_border = Border(
        left=Side(border_style="thin", color="1E3A8A"),
        right=Side(border_style="thin", color="1E3A8A"),
        top=Side(border_style="thin", color="1E3A8A"),
        bottom=Side(border_style="medium", color="1E3A8A"),
    )

    # Grade color fills (soft pastel)
    GRADE_FILLS = {
        5: PatternFill(start_color="DCFCE7", end_color="DCFCE7", fill_type="solid"),  # Soft Green
        4: PatternFill(start_color="DBEAFE", end_color="DBEAFE", fill_type="solid"),  # Soft Blue
        3: PatternFill(start_color="FEF9C3", end_color="FEF9C3", fill_type="solid"),  # Soft Yellow
        2: PatternFill(start_color="FEE2E2", end_color="FEE2E2", fill_type="solid"),  # Soft Red
    }

    # Title block
    ws.merge_cells("A1:G1")
    title_cell = ws["A1"]
    title_cell.value = "ВЕДОМОСТЬ РЕЗУЛЬТАТОВ ТЕСТИРОВАНИЯ: ИНФОРМАЦИОННЫЕ ТЕХНОЛОГИИ"
    title_cell.font = TITLE_FONT
    title_cell.alignment = Alignment(horizontal="center", vertical="center")
    ws.row_dimensions[1].height = 26

    # Meta block
    ws.merge_cells("A2:G2")
    meta_cell = ws["A2"]
    filter_text = f"Фильтр: {group_filter}" if group_filter else "Все группы"
    meta_cell.value = (
        f"Количество участников: {len(sessions)} | {filter_text} | Система: uni-test-sentinel"
    )
    meta_cell.font = META_FONT
    meta_cell.alignment = Alignment(horizontal="center", vertical="center")
    ws.row_dimensions[2].height = 18

    # Empty row 3
    ws.row_dimensions[3].height = 8

    # Table headers (Row 4)
    headers = ["№ п/п", "Дата", "Группа", "ФИО", "Баллы", "Процент", "Оценка"]
    ws.row_dimensions[4].height = 24

    for col_idx, header_title in enumerate(headers, start=1):
        cell = ws.cell(row=4, column=col_idx)
        cell.value = header_title
        cell.font = HEADER_FONT
        cell.fill = HEADER_FILL
        cell.border = header_border
        cell.alignment = Alignment(horizontal="center", vertical="center")

    # Data rows
    row_num = 5
    for idx, s in enumerate(sessions, start=1):
        ws.row_dimensions[row_num].height = 20
        score = float(s.get("score", 0.0))
        total = float(s.get("total", 10.0))
        grade = s.get("grade", 2)
        pct = (
            s.get("percentage")
            if s.get("percentage") is not None
            else calculate_percentage(score, total)
        )

        row_values = [
            (idx, Alignment(horizontal="center", vertical="center"), DATA_FONT),
            (
                s.get("test_date", ""),
                Alignment(horizontal="center", vertical="center"),
                DATA_FONT,
            ),
            (
                s.get("group_name", ""),
                Alignment(horizontal="center", vertical="center"),
                DATA_FONT,
            ),
            (
                s.get("fio", ""),
                Alignment(horizontal="left", vertical="center"),
                BOLD_FONT,
            ),
            (
                format_score(score, total),
                Alignment(horizontal="center", vertical="center"),
                DATA_FONT,
            ),
            (
                f"{pct}%",
                Alignment(horizontal="center", vertical="center"),
                DATA_FONT,
            ),
            (
                grade,
                Alignment(horizontal="center", vertical="center"),
                BOLD_FONT,
            ),
        ]

        for col_idx, (val, align, font_style) in enumerate(row_values, start=1):
            cell = ws.cell(row=row_num, column=col_idx)
            cell.value = val
            cell.alignment = align
            cell.font = font_style
            cell.border = cell_border

            # Highlight grade column
            if col_idx == 7 and grade in GRADE_FILLS:
                cell.fill = GRADE_FILLS[grade]

        row_num += 1

    # Auto-fit column widths with padding
    for col in ws.columns:
        col_letter = get_column_letter(col[0].column)
        max_len = 0
        for cell in col:
            # Skip merged title rows
            if cell.row in (1, 2, 3):
                continue
            val_str = str(cell.value or "")
            max_len = max(max_len, len(val_str))
        ws.column_dimensions[col_letter].width = max(max_len + 4, 12)

    # Extra padding for FIO
    ws.column_dimensions["D"].width = max(ws.column_dimensions["D"].width, 32)

    stream = io.BytesIO()
    wb.save(stream)
    stream.seek(0)
    return stream
