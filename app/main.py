from __future__ import annotations

import math
import secrets
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import exists, func, or_, select
from sqlalchemy.orm import Session, joinedload
from starlette.middleware.sessions import SessionMiddleware

from .auth import get_current_user, hash_password, require_admin, verify_password
from .config import BASE_DIR, settings
from .db import SessionLocal, get_db
from .hr_service import (
    HRUpdateError,
    RESIGNED,
    UNCHECKED,
    VALID_EMPLOYMENT_STATUSES,
    WORKING,
    current_hr_run,
    refresh_hr_data,
)
from .import_service import publish_batch, rollback_batch, stage_file
from .models import (
    Assignment,
    Building,
    Contact,
    HRUpdateRecord,
    HRUpdateRun,
    ImportBatch,
    ImportFile,
    ImportIssue,
    ImportSheet,
    OrganizationUnit,
    Person,
    SourceRow,
    User,
)


templates = Jinja2Templates(directory=str(BASE_DIR / "app" / "templates"))


def flash(request: Request, message: str, category: str = "success") -> None:
    request.session["flash"] = {"message": message, "category": category}


def template_context(request: Request, **kwargs):
    csrf_token = request.session.setdefault("csrf", secrets.token_urlsafe(24))
    return {
        "request": request,
        "flash": request.session.pop("flash", None),
        "csrf_token": csrf_token,
        **kwargs,
    }


def validate_csrf(request: Request, token: str) -> None:
    expected = request.session.get("csrf", "")
    if not expected or not secrets.compare_digest(expected, token or ""):
        raise HTTPException(status_code=403, detail="แบบฟอร์มหมดอายุ กรุณาลองใหม่")


def optional_integer(value: str) -> int | None:
    return int(value) if value.isdigit() else None


def contains_pattern(value: str) -> str:
    escaped = value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


@asynccontextmanager
async def lifespan(_: FastAPI):
    settings.upload_dir.mkdir(parents=True, exist_ok=True)
    if settings.admin_password:
        with SessionLocal() as db:
            admin = db.scalar(select(User).where(User.username == settings.admin_username))
            if not admin:
                db.add(
                    User(
                        username=settings.admin_username,
                        password_hash=hash_password(settings.admin_password),
                        role="admin",
                    )
                )
                db.commit()
    yield


app = FastAPI(title=settings.app_name, lifespan=lifespan)
app.add_middleware(
    SessionMiddleware,
    secret_key=settings.secret_key,
    same_site="lax",
    https_only=settings.session_https_only,
    max_age=8 * 60 * 60,
)
app.mount("/static", StaticFiles(directory=str(BASE_DIR / "app" / "static")), name="static")


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/")
def home():
    return RedirectResponse("/search", status_code=303)


@app.get("/login", response_class=HTMLResponse)
def login_page(request: Request):
    return templates.TemplateResponse(
        request=request,
        name="login.html",
        context=template_context(request, title="เข้าสู่ระบบ"),
    )


@app.post("/login")
def login(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    csrf: str = Form(""),
    db: Session = Depends(get_db),
):
    validate_csrf(request, csrf)
    user = db.scalar(select(User).where(User.username == username.strip()))
    if not user or not user.is_active or not verify_password(password, user.password_hash):
        flash(request, "ชื่อผู้ใช้หรือรหัสผ่านไม่ถูกต้อง", "danger")
        return RedirectResponse("/login", status_code=303)
    request.session.clear()
    request.session["user_id"] = user.id
    request.session["csrf"] = secrets.token_urlsafe(24)
    return RedirectResponse("/search", status_code=303)


@app.post("/logout")
def logout(request: Request, csrf: str = Form("")):
    validate_csrf(request, csrf)
    request.session.clear()
    return RedirectResponse("/login", status_code=303)


@app.get("/search", response_class=HTMLResponse)
def search(
    request: Request,
    q: str = "",
    building_id: str = "",
    unit_id: str = "",
    unit: str = "",
    floor: str = "",
    status: str = "",
    page: int = 1,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    page = max(page, 1)
    page_size = 25
    selected_building_id = optional_integer(building_id)
    selected_unit_id = optional_integer(unit_id)
    unit_text = " ".join(unit.split())
    selected_status = status if status in VALID_EMPLOYMENT_STATUSES else ""
    active_assignment = select(Assignment.id).where(
        Assignment.person_id == Person.id,
        Assignment.is_active.is_(True),
    )
    query = select(Person).where(exists(active_assignment))
    normalized_q = "".join(q.lower().split())
    digits_q = "".join(ch for ch in q if ch.isdigit())
    if q.strip():
        text_pattern = f"%{q.strip()}%"
        normalized_pattern = f"%{normalized_q}%"
        assignment_match = exists(
            select(Assignment.id)
            .outerjoin(Building, Assignment.building_id == Building.id)
            .outerjoin(OrganizationUnit, Assignment.organization_unit_id == OrganizationUnit.id)
            .where(
                Assignment.person_id == Person.id,
                Assignment.is_active.is_(True),
                or_(
                    Assignment.position.ilike(text_pattern),
                    Assignment.floor.ilike(text_pattern),
                    Building.name.ilike(text_pattern),
                    Building.code.ilike(text_pattern),
                    OrganizationUnit.name.ilike(text_pattern),
                ),
            )
        )
        contact_pattern = f"%{digits_q or q.strip().lower()}%"
        contact_match = exists(
            select(Contact.id).where(
                Contact.person_id == Person.id,
                Contact.is_active.is_(True),
                Contact.normalized_value.ilike(contact_pattern),
            )
        )
        query = query.where(
            or_(
                Person.full_name.ilike(text_pattern),
                Person.normalized_name.ilike(normalized_pattern),
                Person.nickname.ilike(text_pattern),
                Person.email_normalized.ilike(f"%{q.strip().lower()}%"),
                assignment_match,
                contact_match,
            )
        )
    if selected_building_id:
        query = query.where(
            exists(
                select(Assignment.id).where(
                    Assignment.person_id == Person.id,
                    Assignment.is_active.is_(True),
                    Assignment.building_id == selected_building_id,
                )
            )
        )
    if unit_text:
        query = query.where(
            exists(
                select(Assignment.id)
                .join(OrganizationUnit, Assignment.organization_unit_id == OrganizationUnit.id)
                .where(
                    Assignment.person_id == Person.id,
                    Assignment.is_active.is_(True),
                    OrganizationUnit.name.ilike(contains_pattern(unit_text), escape="\\"),
                )
            )
        )
    elif selected_unit_id:
        query = query.where(
            exists(
                select(Assignment.id).where(
                    Assignment.person_id == Person.id,
                    Assignment.is_active.is_(True),
                    Assignment.organization_unit_id == selected_unit_id,
                )
            )
        )
    if floor.strip():
        query = query.where(
            exists(
                select(Assignment.id).where(
                    Assignment.person_id == Person.id,
                    Assignment.is_active.is_(True),
                    Assignment.floor.ilike(f"%{floor.strip()}%"),
                )
            )
        )
    if selected_status == UNCHECKED:
        query = query.where(Person.employment_status.is_(None))
    elif selected_status:
        query = query.where(Person.employment_status == selected_status)

    total = db.scalar(select(func.count()).select_from(query.subquery())) or 0
    people = db.scalars(
        query.options(
            joinedload(Person.assignments).joinedload(Assignment.building),
            joinedload(Person.assignments).joinedload(Assignment.organization_unit),
            joinedload(Person.contacts),
        )
        .order_by(Person.full_name)
        .offset((page - 1) * page_size)
        .limit(page_size)
    ).unique().all()
    buildings = db.scalars(select(Building).order_by(Building.code)).all()
    units = db.scalars(select(OrganizationUnit).order_by(OrganizationUnit.name)).all()
    return templates.TemplateResponse(
        request=request,
        name="search.html",
        context=template_context(
            request,
            title="ค้นหาพนักงาน",
            user=user,
            people=people,
            buildings=buildings,
            units=units,
            q=q,
            building_id=selected_building_id,
            unit_id=selected_unit_id,
            unit=unit_text,
            floor=floor,
            status=selected_status,
            employment_statuses=(WORKING, RESIGNED, UNCHECKED),
            page=page,
            pages=max(1, math.ceil(total / page_size)),
            total=total,
        ),
    )


@app.get("/admin/hr-updates", response_class=HTMLResponse)
def hr_updates_page(
    request: Request,
    page: int = 1,
    db: Session = Depends(get_db),
    user: User = Depends(require_admin),
):
    page = max(page, 1)
    page_size = 50
    active_run = current_hr_run(db)
    total_records = active_run.parsed_count if active_run and active_run.parsed_count else 0
    records = []
    if active_run:
        records = db.scalars(
            select(HRUpdateRecord)
            .where(HRUpdateRecord.run_id == active_run.id)
            .order_by(HRUpdateRecord.source_order)
            .offset((page - 1) * page_size)
            .limit(page_size)
        ).all()
    runs = db.scalars(
        select(HRUpdateRun)
        .options(joinedload(HRUpdateRun.created_by))
        .order_by(HRUpdateRun.id.desc())
        .limit(50)
    ).all()
    return templates.TemplateResponse(
        request=request,
        name="hr_updates.html",
        context=template_context(
            request,
            title="Update Data from HR",
            user=user,
            current_run=active_run,
            records=records,
            runs=runs,
            page=page,
            pages=max(1, math.ceil(total_records / page_size)),
            total_records=total_records,
        ),
    )


@app.post("/admin/hr-updates/refresh")
def refresh_hr_update(
    request: Request,
    csrf: str = Form(""),
    db: Session = Depends(get_db),
    user: User = Depends(require_admin),
):
    validate_csrf(request, csrf)
    try:
        run = refresh_hr_data(db, user.id)
        flash(
            request,
            f"อัปเดต HR สำเร็จ {run.parsed_count or 0:,} รายการ: ทำงาน {run.working_count or 0:,} ราย ลาออก {run.resigned_count or 0:,} ราย",
        )
    except HRUpdateError as exc:
        flash(request, str(exc), "danger")
    return RedirectResponse("/admin/hr-updates", status_code=303)


@app.get("/people/{person_id}", response_class=HTMLResponse)
def person_detail(
    person_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    person = db.scalar(
        select(Person)
        .where(Person.id == person_id)
        .options(
            joinedload(Person.assignments).joinedload(Assignment.building),
            joinedload(Person.assignments).joinedload(Assignment.organization_unit),
            joinedload(Person.assignments).joinedload(Assignment.contacts),
        )
    )
    if not person:
        raise HTTPException(404, "ไม่พบข้อมูลพนักงาน")
    active_assignments = [item for item in person.assignments if item.is_active]
    return templates.TemplateResponse(
        request=request,
        name="person.html",
        context=template_context(
            request, title=person.full_name, user=user, person=person, assignments=active_assignments
        ),
    )


@app.get("/admin/imports", response_class=HTMLResponse)
def imports_page(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_admin),
):
    batches = db.scalars(select(ImportBatch).order_by(ImportBatch.id.desc()).limit(100)).all()
    return templates.TemplateResponse(
        request=request,
        name="imports.html",
        context=template_context(request, title="นำเข้าข้อมูล", user=user, batches=batches),
    )


@app.post("/admin/imports")
async def upload_imports(
    request: Request,
    files: list[UploadFile] = File(...),
    csrf: str = Form(""),
    db: Session = Depends(get_db),
    user: User = Depends(require_admin),
):
    validate_csrf(request, csrf)
    if not files:
        flash(request, "กรุณาเลือกไฟล์", "danger")
        return RedirectResponse("/admin/imports", status_code=303)
    if any(not (item.filename or "").lower().endswith(".xlsx") for item in files):
        flash(request, "รองรับเฉพาะไฟล์ .xlsx", "danger")
        return RedirectResponse("/admin/imports", status_code=303)

    batch = ImportBatch(created_by_id=user.id, status="staged")
    db.add(batch)
    db.flush()
    batch_dir = settings.upload_dir / str(batch.id)
    batch_dir.mkdir(parents=True, exist_ok=True)
    try:
        for item in files:
            safe_name = f"{secrets.token_hex(8)}.xlsx"
            target = batch_dir / safe_name
            size = 0
            with target.open("wb") as output:
                while chunk := await item.read(1024 * 1024):
                    size += len(chunk)
                    if size > settings.max_upload_bytes:
                        raise ValueError(f"ไฟล์ {item.filename} มีขนาดเกินกำหนด 25 MB")
                    output.write(chunk)
            stage_file(db, batch, target, item.filename or safe_name, size)
        db.commit()
    except Exception as exc:
        db.rollback()
        flash(request, f"นำเข้าไฟล์ไม่สำเร็จ: {exc}", "danger")
        return RedirectResponse("/admin/imports", status_code=303)
    flash(request, "อ่านไฟล์ครบแล้ว กรุณาตรวจสอบก่อนเผยแพร่")
    return RedirectResponse(f"/admin/imports/{batch.id}/preview", status_code=303)


@app.get("/admin/imports/{batch_id}/preview", response_class=HTMLResponse)
def import_preview(
    batch_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_admin),
):
    batch = db.scalar(select(ImportBatch).where(ImportBatch.id == batch_id))
    if not batch:
        raise HTTPException(404, "ไม่พบชุดนำเข้า")
    sheets = db.scalars(
        select(ImportSheet).join(ImportFile).where(ImportFile.batch_id == batch.id).order_by(ImportFile.id, ImportSheet.id)
    ).all()
    issues = db.scalars(
        select(ImportIssue)
        .where(ImportIssue.batch_id == batch.id)
        .options(joinedload(ImportIssue.source_row).joinedload(SourceRow.sheet))
        .order_by(ImportIssue.id)
        .limit(200)
    ).all()
    samples = db.scalars(
        select(SourceRow)
        .join(ImportSheet)
        .join(ImportFile)
        .where(
            ImportFile.batch_id == batch.id,
            SourceRow.classification.in_(["employee", "employee_review"]),
        )
        .order_by(SourceRow.id)
        .limit(100)
    ).all()
    return templates.TemplateResponse(
        request=request,
        name="preview.html",
        context=template_context(
            request,
            title=f"ตรวจสอบชุดนำเข้า #{batch.id}",
            user=user,
            batch=batch,
            sheets=sheets,
            issues=issues,
            samples=samples,
        ),
    )


@app.post("/admin/imports/{batch_id}/sheets/{sheet_id}")
def update_sheet(
    batch_id: int,
    sheet_id: int,
    request: Request,
    building: str = Form(""),
    floor: str = Form(""),
    unit: str = Form(""),
    csrf: str = Form(""),
    db: Session = Depends(get_db),
    _: User = Depends(require_admin),
):
    validate_csrf(request, csrf)
    sheet = db.scalar(
        select(ImportSheet).join(ImportFile).where(
            ImportSheet.id == sheet_id,
            ImportFile.batch_id == batch_id,
        )
    )
    if not sheet:
        raise HTTPException(404, "ไม่พบชีต")
    sheet.override_building = building.strip() or None
    sheet.override_floor = floor.strip() or None
    sheet.override_unit = unit.strip() or None
    db.commit()
    flash(request, f"บันทึกการตั้งค่าชีต {sheet.name} แล้ว")
    return RedirectResponse(f"/admin/imports/{batch_id}/preview", status_code=303)


@app.post("/admin/imports/{batch_id}/publish")
def publish_import(
    batch_id: int,
    request: Request,
    csrf: str = Form(""),
    db: Session = Depends(get_db),
    user: User = Depends(require_admin),
):
    validate_csrf(request, csrf)
    batch = db.get(ImportBatch, batch_id)
    if not batch:
        raise HTTPException(404, "ไม่พบชุดนำเข้า")
    try:
        publish_batch(db, batch, user.id)
        db.commit()
        flash(request, "เผยแพร่ข้อมูลเรียบร้อยแล้ว")
    except Exception as exc:
        db.rollback()
        flash(request, f"เผยแพร่ไม่สำเร็จ: {exc}", "danger")
        return RedirectResponse(f"/admin/imports/{batch_id}/preview", status_code=303)
    return RedirectResponse("/search", status_code=303)


@app.post("/admin/imports/{batch_id}/rollback")
def rollback_import(
    batch_id: int,
    request: Request,
    csrf: str = Form(""),
    db: Session = Depends(get_db),
    user: User = Depends(require_admin),
):
    validate_csrf(request, csrf)
    batch = db.get(ImportBatch, batch_id)
    if not batch:
        raise HTTPException(404, "ไม่พบชุดนำเข้า")
    try:
        rollback_batch(db, batch, user.id)
        db.commit()
        flash(request, "ย้อนกลับชุดข้อมูลเรียบร้อยแล้ว")
    except Exception as exc:
        db.rollback()
        flash(request, f"ย้อนกลับไม่สำเร็จ: {exc}", "danger")
    return RedirectResponse("/admin/imports", status_code=303)


@app.get("/admin/users", response_class=HTMLResponse)
def users_page(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_admin),
):
    users = db.scalars(select(User).order_by(User.username)).all()
    return templates.TemplateResponse(
        request=request,
        name="users.html",
        context=template_context(request, title="ผู้ใช้งาน", user=user, users=users),
    )


@app.post("/admin/users")
def create_user(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    role: str = Form("user"),
    csrf: str = Form(""),
    db: Session = Depends(get_db),
    _: User = Depends(require_admin),
):
    validate_csrf(request, csrf)
    username = username.strip()
    if len(username) < 3 or len(password) < 8 or role not in {"admin", "user"}:
        flash(request, "ชื่อผู้ใช้ต้องมีอย่างน้อย 3 ตัว และรหัสผ่านอย่างน้อย 8 ตัว", "danger")
    elif db.scalar(select(User).where(User.username == username)):
        flash(request, "ชื่อผู้ใช้นี้มีอยู่แล้ว", "danger")
    else:
        db.add(User(username=username, password_hash=hash_password(password), role=role))
        db.commit()
        flash(request, "เพิ่มผู้ใช้เรียบร้อยแล้ว")
    return RedirectResponse("/admin/users", status_code=303)


@app.post("/admin/users/{user_id}/toggle")
def toggle_user(
    user_id: int,
    request: Request,
    csrf: str = Form(""),
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
):
    validate_csrf(request, csrf)
    target = db.get(User, user_id)
    if not target:
        raise HTTPException(404, "ไม่พบผู้ใช้")
    if target.id == admin.id:
        flash(request, "ไม่สามารถปิดบัญชีที่กำลังใช้งานอยู่", "danger")
    else:
        target.is_active = not target.is_active
        db.commit()
        flash(request, "อัปเดตสถานะผู้ใช้แล้ว")
    return RedirectResponse("/admin/users", status_code=303)
