from __future__ import annotations

import hashlib
import re
from pathlib import Path

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from .excel_parser import digits_only, normalize_text, parse_workbook
from .models import (
    Assignment,
    Building,
    Contact,
    ImportBatch,
    ImportFile,
    ImportIssue,
    ImportSheet,
    OrganizationUnit,
    Person,
    SourceRow,
    utcnow,
)


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def stage_file(db: Session, batch: ImportBatch, path: Path, original_name: str, size_bytes: int) -> ImportFile:
    digest = file_sha256(path)
    duplicate = db.scalar(
        select(ImportFile.id).where(ImportFile.batch_id == batch.id, ImportFile.sha256 == digest).limit(1)
    )
    if duplicate:
        raise ValueError(f"ไฟล์ {original_name} ซ้ำกับไฟล์อื่นในชุดนำเข้านี้")
    parsed_sheets = parse_workbook(path, original_name)
    import_file = ImportFile(
        batch_id=batch.id,
        original_name=original_name,
        stored_path=str(path),
        sha256=digest,
        size_bytes=size_bytes,
        sheet_count=len(parsed_sheets),
    )
    db.add(import_file)
    db.flush()
    for parsed_sheet in parsed_sheets:
        sheet = ImportSheet(
            file_id=import_file.id,
            name=parsed_sheet.name,
            detected_building=parsed_sheet.detected_building,
            detected_floor=parsed_sheet.detected_floor,
            nonempty_rows=parsed_sheet.nonempty_rows,
            employee_rows=parsed_sheet.employee_rows,
            issue_rows=parsed_sheet.issue_rows,
        )
        db.add(sheet)
        db.flush()
        for parsed_row in parsed_sheet.rows:
            source = SourceRow(
                sheet_id=sheet.id,
                row_number=parsed_row.row_number,
                classification=parsed_row.classification,
                raw_values=parsed_row.raw_values,
                parsed_data=parsed_row.parsed_data,
            )
            db.add(source)
            db.flush()
            for severity, code, message in parsed_row.issues:
                db.add(
                    ImportIssue(
                        batch_id=batch.id,
                        source_row_id=source.id,
                        severity=severity,
                        code=code,
                        message=message,
                    )
                )
    return import_file


def _get_building(db: Session, code: str | None) -> Building | None:
    code = (code or "").strip()
    if not code:
        return None
    building = db.scalar(select(Building).where(Building.code == code))
    if not building:
        building = Building(code=code, name=f"ตึก {code}")
        db.add(building)
        db.flush()
    return building


def _get_unit(db: Session, name: str | None, unit_type: str | None) -> OrganizationUnit | None:
    name = (name or "").strip()
    if not name:
        return None
    normalized = normalize_text(name)
    unit = db.scalar(
        select(OrganizationUnit).where(
            OrganizationUnit.normalized_name == normalized,
            OrganizationUnit.parent_id.is_(None),
        )
    )
    if not unit:
        unit = OrganizationUnit(name=name, normalized_name=normalized, unit_type=unit_type or "หน่วยงาน")
        db.add(unit)
        db.flush()
    return unit


def _identity_key(data: dict, scope_key: str, source_row_id: int) -> str:
    email = (data.get("email") or "").strip().lower()
    if email:
        return f"email:{email}"
    name = normalize_text(data.get("full_name"))
    phone = digits_only(data.get("phone")) or digits_only(data.get("extension"))
    if name and phone:
        return f"namephone:{name}|{phone}"
    return f"source:{scope_key}|{source_row_id}"


def _contact_values(data: dict):
    email = (data.get("email") or "").strip()
    phone = (data.get("phone") or "").strip()
    extension = (data.get("extension") or "").strip()
    if email:
        yield "email", email, email.lower()
    if phone:
        yield "phone", phone, digits_only(phone)
    if extension:
        yield "extension", extension, digits_only(extension)


def publish_batch(db: Session, batch: ImportBatch, user_id: int) -> None:
    if batch.status != "staged":
        raise ValueError("เผยแพร่ได้เฉพาะชุดข้อมูลที่อยู่ระหว่างตรวจสอบ")

    sheets = db.scalars(
        select(ImportSheet).join(ImportFile).where(ImportFile.batch_id == batch.id).order_by(ImportSheet.id)
    ).all()
    for sheet in sheets:
        building_code = sheet.override_building or sheet.detected_building or "ไม่ระบุ"
        floor = sheet.override_floor or sheet.detected_floor
        scope_key = f"{building_code}|{sheet.name}"
        db.execute(
            update(Assignment)
            .where(Assignment.scope_key == scope_key, Assignment.is_active.is_(True))
            .values(is_active=False, superseded_by_batch_id=batch.id)
        )
        db.execute(
            update(Contact)
            .where(Contact.scope_key == scope_key, Contact.is_active.is_(True))
            .values(is_active=False)
        )
        building = _get_building(db, building_code)
        rows = db.scalars(
            select(SourceRow).where(
                SourceRow.sheet_id == sheet.id,
                SourceRow.classification.in_(["employee", "employee_review"]),
            ).order_by(SourceRow.row_number)
        ).all()
        for row in rows:
            data = row.parsed_data or {}
            full_name = (data.get("full_name") or "").strip()
            if not full_name:
                continue
            email = (data.get("email") or "").strip().lower() or None
            identity = _identity_key(data, scope_key, row.id)
            person = db.scalar(select(Person).where(Person.identity_key == identity))
            if not person and email:
                person = db.scalar(select(Person).where(Person.email_normalized == email))
            if not person:
                person = Person(
                    identity_key=identity,
                    full_name=full_name,
                    normalized_name=normalize_text(full_name),
                    nickname=data.get("nickname"),
                    email_normalized=email,
                    needs_review=bool(data.get("needs_review")),
                )
                db.add(person)
                db.flush()
            else:
                if data.get("nickname") and not person.nickname:
                    person.nickname = data["nickname"]
                person.needs_review = person.needs_review or bool(data.get("needs_review"))

            unit_name = sheet.override_unit or data.get("organization") or sheet.name
            unit = _get_unit(db, unit_name, data.get("unit_type"))
            assignment = Assignment(
                person_id=person.id,
                building_id=building.id if building else None,
                organization_unit_id=unit.id if unit else None,
                position=data.get("position"),
                floor=floor,
                scope_key=scope_key,
                import_batch_id=batch.id,
                import_sheet_id=sheet.id,
                source_row_id=row.id,
                is_active=True,
            )
            db.add(assignment)
            db.flush()
            for contact_type, raw_value, normalized_value in _contact_values(data):
                db.add(
                    Contact(
                        person_id=person.id,
                        assignment_id=assignment.id,
                        contact_type=contact_type,
                        raw_value=raw_value,
                        normalized_value=normalized_value,
                        is_active=True,
                        import_batch_id=batch.id,
                        scope_key=scope_key,
                    )
                )
    batch.status = "published"
    batch.published_by_id = user_id
    batch.published_at = utcnow()
    # Re-evaluate the active directory whenever a new Excel snapshot is published.
    from .hr_service import recompute_employment_statuses

    recompute_employment_statuses(db)


def rollback_batch(db: Session, batch: ImportBatch, user_id: int) -> None:
    if batch.status != "published":
        raise ValueError("ย้อนกลับได้เฉพาะชุดข้อมูลที่เผยแพร่แล้ว")
    newer = db.scalar(
        select(Assignment.id).where(
            Assignment.import_batch_id == batch.id,
            Assignment.superseded_by_batch_id.is_not(None),
        ).limit(1)
    )
    if newer:
        raise ValueError("ชุดข้อมูลนี้ถูกแทนที่ด้วยรุ่นใหม่แล้ว ต้องย้อนกลับรุ่นใหม่ก่อน")

    assignment_ids = db.scalars(select(Assignment.id).where(Assignment.import_batch_id == batch.id)).all()
    if assignment_ids:
        db.execute(update(Assignment).where(Assignment.id.in_(assignment_ids)).values(is_active=False))
        db.execute(update(Contact).where(Contact.assignment_id.in_(assignment_ids)).values(is_active=False))

    restored_ids = db.scalars(
        select(Assignment.id).where(Assignment.superseded_by_batch_id == batch.id)
    ).all()
    if restored_ids:
        db.execute(
            update(Assignment)
            .where(Assignment.id.in_(restored_ids))
            .values(is_active=True, superseded_by_batch_id=None)
        )
        db.execute(update(Contact).where(Contact.assignment_id.in_(restored_ids)).values(is_active=True))

    batch.status = "rolled_back"
    batch.rolled_back_by_id = user_id
    batch.rolled_back_at = utcnow()
    from .hr_service import recompute_employment_statuses

    recompute_employment_statuses(db)
