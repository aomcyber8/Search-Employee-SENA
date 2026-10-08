from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Integer, JSON, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class User(Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(80), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    role: Mapped[str] = mapped_column(String(20), default="user", index=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class HRUpdateRun(Base):
    __tablename__ = "hr_update_runs"
    id: Mapped[int] = mapped_column(primary_key=True)
    status: Mapped[str] = mapped_column(String(30), default="running", index=True)
    source_url: Mapped[str] = mapped_column(String(700))
    reported_count: Mapped[int | None] = mapped_column(Integer)
    parsed_count: Mapped[int | None] = mapped_column(Integer)
    working_count: Mapped[int | None] = mapped_column(Integer)
    resigned_count: Mapped[int | None] = mapped_column(Integer)
    duplicate_name_count: Mapped[int | None] = mapped_column(Integer)
    error_message: Mapped[str | None] = mapped_column(Text)
    is_current: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_by_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    created_by: Mapped[User] = relationship()
    records: Mapped[list[HRUpdateRecord]] = relationship(
        back_populates="run", cascade="all, delete-orphan"
    )


class HRUpdateRecord(Base):
    __tablename__ = "hr_update_records"
    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[int] = mapped_column(
        ForeignKey("hr_update_runs.id", ondelete="CASCADE"), index=True
    )
    source_order: Mapped[int] = mapped_column(Integer)
    employee_code: Mapped[str | None] = mapped_column(String(80), index=True)
    full_name: Mapped[str] = mapped_column(String(255), index=True)
    normalized_name: Mapped[str] = mapped_column(String(255), index=True)
    department: Mapped[str | None] = mapped_column(String(500), index=True)
    position: Mapped[str | None] = mapped_column(String(255))
    email: Mapped[str | None] = mapped_column(String(255), index=True)
    mobile: Mapped[str | None] = mapped_column(String(255))
    office_phone: Mapped[str | None] = mapped_column(String(255))
    run: Mapped[HRUpdateRun] = relationship(back_populates="records")
    __table_args__ = (UniqueConstraint("run_id", "source_order", name="uq_hr_run_source_order"),)


class Building(Base):
    __tablename__ = "buildings"
    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(40), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(120))


class OrganizationUnit(Base):
    __tablename__ = "organization_units"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(255), index=True)
    normalized_name: Mapped[str] = mapped_column(String(255), index=True)
    unit_type: Mapped[str] = mapped_column(String(40), default="หน่วยงาน")
    parent_id: Mapped[int | None] = mapped_column(ForeignKey("organization_units.id"))
    parent: Mapped[OrganizationUnit | None] = relationship(remote_side="OrganizationUnit.id")
    __table_args__ = (UniqueConstraint("normalized_name", "parent_id", name="uq_org_unit_parent"),)


class Person(Base):
    __tablename__ = "people"
    id: Mapped[int] = mapped_column(primary_key=True)
    identity_key: Mapped[str] = mapped_column(String(500), unique=True, index=True)
    full_name: Mapped[str] = mapped_column(String(255), index=True)
    normalized_name: Mapped[str] = mapped_column(String(255), index=True)
    nickname: Mapped[str | None] = mapped_column(String(120), index=True)
    email_normalized: Mapped[str | None] = mapped_column(String(255), index=True)
    needs_review: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    employment_status: Mapped[str | None] = mapped_column(String(30), index=True)
    employment_status_updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    hr_update_run_id: Mapped[int | None] = mapped_column(
        ForeignKey("hr_update_runs.id"), index=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    assignments: Mapped[list[Assignment]] = relationship(back_populates="person")
    contacts: Mapped[list[Contact]] = relationship(back_populates="person")


class ImportBatch(Base):
    __tablename__ = "import_batches"
    id: Mapped[int] = mapped_column(primary_key=True)
    status: Mapped[str] = mapped_column(String(30), default="staged", index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    rolled_back_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_by_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    published_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    rolled_back_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    note: Mapped[str | None] = mapped_column(Text)
    files: Mapped[list[ImportFile]] = relationship(back_populates="batch", cascade="all, delete-orphan")


class ImportFile(Base):
    __tablename__ = "import_files"
    id: Mapped[int] = mapped_column(primary_key=True)
    batch_id: Mapped[int] = mapped_column(ForeignKey("import_batches.id", ondelete="CASCADE"), index=True)
    original_name: Mapped[str] = mapped_column(String(255))
    stored_path: Mapped[str] = mapped_column(String(700))
    sha256: Mapped[str] = mapped_column(String(64), index=True)
    size_bytes: Mapped[int] = mapped_column(Integer)
    sheet_count: Mapped[int] = mapped_column(Integer, default=0)
    batch: Mapped[ImportBatch] = relationship(back_populates="files")
    sheets: Mapped[list[ImportSheet]] = relationship(back_populates="file", cascade="all, delete-orphan")


class ImportSheet(Base):
    __tablename__ = "import_sheets"
    id: Mapped[int] = mapped_column(primary_key=True)
    file_id: Mapped[int] = mapped_column(ForeignKey("import_files.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(255))
    detected_building: Mapped[str | None] = mapped_column(String(40))
    detected_floor: Mapped[str | None] = mapped_column(String(40))
    override_building: Mapped[str | None] = mapped_column(String(40))
    override_floor: Mapped[str | None] = mapped_column(String(40))
    override_unit: Mapped[str | None] = mapped_column(String(255))
    nonempty_rows: Mapped[int] = mapped_column(Integer, default=0)
    employee_rows: Mapped[int] = mapped_column(Integer, default=0)
    issue_rows: Mapped[int] = mapped_column(Integer, default=0)
    file: Mapped[ImportFile] = relationship(back_populates="sheets")
    rows: Mapped[list[SourceRow]] = relationship(back_populates="sheet", cascade="all, delete-orphan")


class SourceRow(Base):
    __tablename__ = "source_rows"
    id: Mapped[int] = mapped_column(primary_key=True)
    sheet_id: Mapped[int] = mapped_column(ForeignKey("import_sheets.id", ondelete="CASCADE"), index=True)
    row_number: Mapped[int] = mapped_column(Integer)
    classification: Mapped[str] = mapped_column(String(40), index=True)
    raw_values: Mapped[list] = mapped_column(JSON)
    parsed_data: Mapped[dict | None] = mapped_column(JSON)
    sheet: Mapped[ImportSheet] = relationship(back_populates="rows")
    issues: Mapped[list[ImportIssue]] = relationship(back_populates="source_row")
    __table_args__ = (UniqueConstraint("sheet_id", "row_number", name="uq_source_sheet_row"),)


class ImportIssue(Base):
    __tablename__ = "import_issues"
    id: Mapped[int] = mapped_column(primary_key=True)
    batch_id: Mapped[int] = mapped_column(ForeignKey("import_batches.id", ondelete="CASCADE"), index=True)
    source_row_id: Mapped[int | None] = mapped_column(ForeignKey("source_rows.id", ondelete="CASCADE"))
    severity: Mapped[str] = mapped_column(String(20), default="warning")
    code: Mapped[str] = mapped_column(String(80))
    message: Mapped[str] = mapped_column(Text)
    resolved: Mapped[bool] = mapped_column(Boolean, default=False)
    source_row: Mapped[SourceRow | None] = relationship(back_populates="issues")


class Assignment(Base):
    __tablename__ = "assignments"
    id: Mapped[int] = mapped_column(primary_key=True)
    person_id: Mapped[int] = mapped_column(ForeignKey("people.id"), index=True)
    building_id: Mapped[int | None] = mapped_column(ForeignKey("buildings.id"), index=True)
    organization_unit_id: Mapped[int | None] = mapped_column(ForeignKey("organization_units.id"), index=True)
    position: Mapped[str | None] = mapped_column(String(255), index=True)
    floor: Mapped[str | None] = mapped_column(String(40), index=True)
    scope_key: Mapped[str] = mapped_column(String(600), index=True)
    import_batch_id: Mapped[int] = mapped_column(ForeignKey("import_batches.id"), index=True)
    import_sheet_id: Mapped[int] = mapped_column(ForeignKey("import_sheets.id"), index=True)
    source_row_id: Mapped[int] = mapped_column(ForeignKey("source_rows.id"))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    superseded_by_batch_id: Mapped[int | None] = mapped_column(ForeignKey("import_batches.id"), index=True)
    person: Mapped[Person] = relationship(back_populates="assignments")
    building: Mapped[Building | None] = relationship()
    organization_unit: Mapped[OrganizationUnit | None] = relationship()
    contacts: Mapped[list[Contact]] = relationship(back_populates="assignment")


class Contact(Base):
    __tablename__ = "contacts"
    id: Mapped[int] = mapped_column(primary_key=True)
    person_id: Mapped[int] = mapped_column(ForeignKey("people.id"), index=True)
    assignment_id: Mapped[int] = mapped_column(ForeignKey("assignments.id", ondelete="CASCADE"), index=True)
    contact_type: Mapped[str] = mapped_column(String(30), index=True)
    raw_value: Mapped[str] = mapped_column(String(255))
    normalized_value: Mapped[str] = mapped_column(String(255), index=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    import_batch_id: Mapped[int] = mapped_column(ForeignKey("import_batches.id"), index=True)
    scope_key: Mapped[str] = mapped_column(String(600), index=True)
    person: Mapped[Person] = relationship(back_populates="contacts")
    assignment: Mapped[Assignment] = relationship(back_populates="contacts")


Index("ix_people_name_trgm_placeholder", Person.normalized_name)
