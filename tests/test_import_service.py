from pathlib import Path

from openpyxl import Workbook
import pytest
from sqlalchemy import select

from app.import_service import publish_batch, rollback_batch, stage_file
from app.models import Assignment, ImportBatch, Person, User


def make_book(path: Path, name: str, email: str, extension: str, building: str = "448"):
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = f"ฝ่ายทดสอบ ตึก{building}"
    sheet.append(["ชื่อ", "ชื่อเล่น", "ตำแหน่ง", "E-mail", "เบอร์ต่อ"])
    sheet.append([name, "ทดสอบ", "เจ้าหน้าที่", email, extension])
    workbook.save(path)


def test_publish_merges_email_and_keeps_multiple_assignments(db, tmp_path):
    user = User(username="admin", password_hash="x", role="admin")
    db.add(user)
    db.flush()
    batch = ImportBatch(created_by_id=user.id)
    db.add(batch)
    db.flush()
    first = tmp_path / "a.xlsx"
    second = tmp_path / "b.xlsx"
    make_book(first, "คุณสมชาย ทดสอบ", "same@example.com", "01001", "448")
    make_book(second, "คุณสมชาย ทดสอบ", "same@example.com", "02002", "522")
    stage_file(db, batch, first, first.name, first.stat().st_size)
    stage_file(db, batch, second, second.name, second.stat().st_size)
    publish_batch(db, batch, user.id)
    db.commit()

    assert len(db.scalars(select(Person)).all()) == 1
    assignments = db.scalars(select(Assignment).where(Assignment.is_active.is_(True))).all()
    assert len(assignments) == 2
    assert {item.building.code for item in assignments} == {"448", "522"}


def test_new_snapshot_replaces_scope_and_rollback_restores(db, tmp_path):
    user = User(username="admin", password_hash="x", role="admin")
    db.add(user)
    db.flush()
    path1 = tmp_path / "old.xlsx"
    make_book(path1, "คุณเดิม ระบบ", "old@example.com", "01001")
    old_batch = ImportBatch(created_by_id=user.id)
    db.add(old_batch)
    db.flush()
    stage_file(db, old_batch, path1, "สมุดโทรตึก448.xlsx", path1.stat().st_size)
    publish_batch(db, old_batch, user.id)
    db.commit()

    path2 = tmp_path / "new.xlsx"
    make_book(path2, "คุณใหม่ ระบบ", "new@example.com", "01002")
    new_batch = ImportBatch(created_by_id=user.id)
    db.add(new_batch)
    db.flush()
    stage_file(db, new_batch, path2, "สมุดโทรตึก448.xlsx", path2.stat().st_size)
    publish_batch(db, new_batch, user.id)
    db.commit()
    active_names = {item.person.full_name for item in db.scalars(select(Assignment).where(Assignment.is_active.is_(True))).all()}
    assert active_names == {"คุณใหม่ ระบบ"}

    rollback_batch(db, new_batch, user.id)
    db.commit()
    active_names = {item.person.full_name for item in db.scalars(select(Assignment).where(Assignment.is_active.is_(True))).all()}
    assert active_names == {"คุณเดิม ระบบ"}


def test_duplicate_file_in_same_batch_is_rejected(db, tmp_path):
    user = User(username="admin", password_hash="x", role="admin")
    db.add(user)
    db.flush()
    batch = ImportBatch(created_by_id=user.id)
    db.add(batch)
    db.flush()
    path = tmp_path / "duplicate.xlsx"
    make_book(path, "คุณทดสอบ ซ้ำ", "duplicate@example.com", "01001")
    stage_file(db, batch, path, "first.xlsx", path.stat().st_size)
    with pytest.raises(ValueError, match="ซ้ำ"):
        stage_file(db, batch, path, "second.xlsx", path.stat().st_size)
