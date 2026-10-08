import pytest
from sqlalchemy import select

from app.hr_service import (
    HRUpdateError,
    RESIGNED,
    WORKING,
    normalize_person_name,
    parse_hr_contacts,
    refresh_hr_data,
)
from app.models import Assignment, HRUpdateRun, ImportBatch, ImportFile, ImportSheet, Person, SourceRow, User


def sample_html(count=2):
    cards = """
    <div class="col-12 col-sm-6 col-md-4 d-flex align-items-stretch flex-column">
      <div class="card"><div class="card-header"><div class="row"><div class="col-11">ฝ่ายไอที</div></div></div>
      <div class="card-body"><h2 class="lead"><b>นาย สมชาย ทดสอบ (ชาย)</b></h2><ul>
      <li><span>รหัส</span> | BK001</li><li><span>ตำแหน่ง</span> | เจ้าหน้าที่</li>
      <li><span>อีเมล</span> | somchai@example.com</li><li><span>มือถือ</span> | 081-234-5678</li><li><span>โทร</span> | 10234</li>
      </ul></div></div>
    </div>
    <div class="col-12 col-sm-6 col-md-4 d-flex align-items-stretch flex-column">
      <div class="card"><div class="card-header"><div class="row"><div class="col-11">ฝ่ายบัญชี</div></div></div>
      <div class="card-body"><h2 class="lead"><b>นางสาว สมหญิง ตัวอย่าง</b></h2><ul>
      <li><span>รหัส</span> | BK002</li><li><span>ตำแหน่ง</span> | นักบัญชี</li>
      <li><span>อีเมล</span> | </li><li><span>มือถือ</span> | </li><li><span>โทร</span> | </li>
      </ul></div></div>
    </div>
    """
    return f"<html><h5 id='card-contact-results'>Results : {count}</h5>{cards}</html>"


def add_active_person(db, user, name, row_number):
    batch = db.scalar(select(ImportBatch))
    if not batch:
        batch = ImportBatch(created_by_id=user.id, status="published")
        db.add(batch)
        db.flush()
        imported = ImportFile(batch_id=batch.id, original_name="test.xlsx", stored_path="test.xlsx", sha256="f" * 64, size_bytes=1, sheet_count=1)
        db.add(imported)
        db.flush()
        sheet = ImportSheet(file_id=imported.id, name="Test", nonempty_rows=1, employee_rows=1, issue_rows=0)
        db.add(sheet)
        db.flush()
    else:
        sheet = db.scalar(select(ImportSheet))
    source = SourceRow(sheet_id=sheet.id, row_number=row_number, classification="employee", raw_values=[name], parsed_data={"full_name": name})
    person = Person(identity_key=f"test:{row_number}", full_name=name, normalized_name=name.replace(" ", "").lower())
    db.add_all([source, person])
    db.flush()
    db.add(Assignment(person_id=person.id, scope_key=f"test:{row_number}", import_batch_id=batch.id, import_sheet_id=sheet.id, source_row_id=source.id, is_active=True))
    db.flush()
    return person


def test_name_normalization_removes_title_nickname_spacing_and_punctuation():
    assert normalize_person_name("นาย สมชาย ทดสอบ (ชาย)") == normalize_person_name("สมชาย-ทดสอบ")
    assert normalize_person_name("Ms. Jane DOE") == normalize_person_name("jane doe")
    assert normalize_person_name("น.ส.หญิง วราภรณ์ กระวินทร์ (รักษ์)") == normalize_person_name("คุณวราภรณ์ กระวินทร์")
    assert normalize_person_name("จ.ส.ต.หญิง วราภรณ์ กระวินทร์ (รักษ์)") == normalize_person_name("วราภรณ์ กระวินทร์")


def test_parser_reads_all_fields_and_allows_empty_contacts():
    reported, records = parse_hr_contacts(sample_html())
    assert reported == 2
    assert records[0]["employee_code"] == "BK001"
    assert records[0]["mobile"] == "081-234-5678"
    assert records[1]["email"] == ""
    assert records[1]["office_phone"] == ""


def test_parser_rejects_count_mismatch():
    with pytest.raises(HRUpdateError, match="จำนวนข้อมูลไม่ตรงกัน"):
        parse_hr_contacts(sample_html(count=3))


def test_refresh_updates_status_and_failure_preserves_current_snapshot(db):
    user = User(username="admin", password_hash="x", role="admin")
    db.add(user)
    db.flush()
    working = add_active_person(db, user, "สมชาย ทดสอบ", 1)
    resigned = add_active_person(db, user, "นาย ไม่อยู่ แล้ว", 2)
    db.commit()

    run = refresh_hr_data(db, user.id, fetcher=lambda: sample_html())
    db.refresh(working)
    db.refresh(resigned)
    assert run.status == "completed"
    assert run.parsed_count == 2
    assert working.employment_status == WORKING
    assert resigned.employment_status == RESIGNED

    with pytest.raises(HRUpdateError):
        refresh_hr_data(db, user.id, fetcher=lambda: sample_html(count=99))
    db.refresh(working)
    assert working.employment_status == WORKING
    current = db.scalar(select(HRUpdateRun).where(HRUpdateRun.is_current.is_(True)))
    assert current.id == run.id
    assert db.scalar(select(HRUpdateRun).where(HRUpdateRun.status == "failed")) is not None
