from fastapi.testclient import TestClient

from app.auth import hash_password
from app.main import app, optional_integer
from app.models import Assignment, ImportBatch, ImportFile, ImportSheet, OrganizationUnit, Person, SourceRow, User


def add_directory_entry(db, user, name, email, unit_name, row_number):
    batch = db.query(ImportBatch).first()
    if not batch:
        batch = ImportBatch(created_by_id=user.id, status="published")
        db.add(batch)
        db.flush()
        import_file = ImportFile(
            batch_id=batch.id,
            original_name="test.xlsx",
            stored_path="test.xlsx",
            sha256="a" * 64,
            size_bytes=1,
            sheet_count=1,
        )
        db.add(import_file)
        db.flush()
        sheet = ImportSheet(file_id=import_file.id, name="ทดสอบ", nonempty_rows=2, employee_rows=2, issue_rows=0)
        db.add(sheet)
        db.flush()
    else:
        sheet = db.query(ImportSheet).first()
    source = SourceRow(
        sheet_id=sheet.id,
        row_number=row_number,
        classification="employee",
        raw_values=[name],
        parsed_data={"full_name": name, "email": email},
    )
    unit = OrganizationUnit(name=unit_name, normalized_name="".join(unit_name.lower().split()), unit_type="ฝ่าย")
    person = Person(
        identity_key=f"email:{email}",
        full_name=name,
        normalized_name="".join(name.lower().split()),
        email_normalized=email,
    )
    db.add_all([source, unit, person])
    db.flush()
    db.add(
        Assignment(
            person_id=person.id,
            organization_unit_id=unit.id,
            position="เจ้าหน้าที่",
            scope_key="448|ทดสอบ",
            import_batch_id=batch.id,
            import_sheet_id=sheet.id,
            source_row_id=source.id,
            is_active=True,
        )
    )
    db.flush()


def test_login_and_role_protection(db):
    assert optional_integer("") is None
    assert optional_integer("12") == 12
    db.add(User(username="viewer", password_hash=hash_password("password123"), role="user"))
    db.commit()
    with TestClient(app) as client:
        login_page = client.get("/login")
        csrf = login_page.text.split('name="csrf" value="', 1)[1].split('"', 1)[0]
        response = client.post(
            "/login",
            data={"username": "viewer", "password": "password123", "csrf": csrf},
            follow_redirects=False,
        )
        assert response.status_code == 303
        assert response.headers["location"] == "/search"
        response = client.get("/search")
        assert response.status_code == 200
        assert "ค้นหาข้อมูลพนักงาน" in response.text
        assert 'href="/search" aria-label="ล้างเงื่อนไขการค้นหาทั้งหมด"' in response.text
        response = client.get("/admin/imports")
        assert response.status_code == 403


def test_unauthenticated_search_redirects():
    with TestClient(app) as client:
        response = client.get("/search", follow_redirects=False)
        assert response.status_code == 303
        assert response.headers["location"] == "/login"


def test_unit_text_filter_supports_partial_match_and_legacy_id(db):
    admin = User(username="admin", password_hash=hash_password("password123"), role="admin")
    db.add(admin)
    db.flush()
    add_directory_entry(db, admin, "คุณสมชาย ทดสอบ", "somchai@example.com", "ฝ่ายเทคโนโลยีสารสนเทศ", 1)
    add_directory_entry(db, admin, "คุณสมหญิง ตัวอย่าง", "somying@example.com", "ฝ่ายบัญชี", 2)
    db.commit()
    accounting = db.query(OrganizationUnit).filter(OrganizationUnit.name == "ฝ่ายบัญชี").one()

    with TestClient(app) as client:
        login_page = client.get("/login")
        csrf = login_page.text.split('name="csrf" value="', 1)[1].split('"', 1)[0]
        client.post(
            "/login",
            data={"username": "admin", "password": "password123", "csrf": csrf},
            follow_redirects=False,
        )
        response = client.get(f"/search?unit=เทคโนโลยี&unit_id={accounting.id}")
        assert response.status_code == 200
        assert "คุณสมชาย ทดสอบ" in response.text
        assert "คุณสมหญิง ตัวอย่าง" not in response.text
        assert 'name="unit" value="เทคโนโลยี"' in response.text

        response = client.get("/search?unit=%25")
        assert "คุณสมชาย ทดสอบ" not in response.text
        assert "คุณสมหญิง ตัวอย่าง" not in response.text
