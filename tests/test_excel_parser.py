from pathlib import Path

import pytest
from openpyxl import Workbook

from app.excel_parser import parse_workbook


SAMPLE_448 = Path(r"D:/OneDrive/Desktop/เบอร์โทรตึก448 01.10.69(1).xlsx")
SAMPLE_522 = Path(r"D:/OneDrive/Desktop/เบอร์โทรตึก522 01.10.69(3).xlsx")


def test_parser_reads_repeated_headers_and_free_text(tmp_path):
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "ฝ่ายไอที ชั้น5 ตึก448"
    sheet.append(["ฝ่ายไอที"])
    sheet.append(["ชื่อ", "ชื่อเล่น", "ตำแหน่ง", "E-mail", "เบอร์ต่อ"])
    sheet.append(["คุณทดสอบ ระบบ", "เทส", "เจ้าหน้าที่", "test@example.com", "01001"])
    sheet.append(["ทีมสนับสนุน"])
    sheet.append(["ชื่อ", "ชื่อเล่น", "ตำแหน่ง", "E-mail", "เบอร์ต่อ"])
    sheet.append(["คุณตัวอย่าง ข้อมูล", "อย่าง", "ผู้จัดการ", "sample@example.com", "01002"])
    sheet.append(["คุณแม่บ้าน ทดลอง โทร 081-234-5678"])
    path = tmp_path / "สมุดโทรตึก448.xlsx"
    workbook.save(path)

    parsed = parse_workbook(path)
    assert len(parsed) == 1
    assert parsed[0].detected_building == "448"
    assert parsed[0].detected_floor == "5"
    assert parsed[0].employee_rows == 3
    assert sum(row.classification == "header" for row in parsed[0].rows) == 2
    assert any(row.classification == "employee_review" for row in parsed[0].rows)


@pytest.mark.skipif(not (SAMPLE_448.exists() and SAMPLE_522.exists()), reason="sample workbooks not attached")
def test_real_workbooks_cover_all_39_sheets_and_ignore_formatting_tail():
    first = parse_workbook(SAMPLE_448)
    second = parse_workbook(SAMPLE_522)
    assert len(first) == 24
    assert len(second) == 15
    assert sum(sheet.nonempty_rows for sheet in first + second) == 1021
    admin_524 = next(sheet for sheet in first if sheet.name == "ฝ่ายธุรการตึก524")
    assert admin_524.detected_building == "524"
    it_sheet = next(sheet for sheet in first if sheet.name == "ไอที")
    assert it_sheet.nonempty_rows == 40
    assert it_sheet.employee_rows > 20
