from __future__ import annotations

import re
import unicodedata
from collections import Counter
from html.parser import HTMLParser
from urllib.parse import urljoin

import httpx
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from .config import settings
from .models import Assignment, HRUpdateRecord, HRUpdateRun, Person, utcnow


WORKING = "ทำงาน"
RESIGNED = "ลาออก"
UNCHECKED = "ยังไม่ตรวจสอบ"
VALID_EMPLOYMENT_STATUSES = {WORKING, RESIGNED, UNCHECKED}

HONORIFIC_RE = re.compile(
    r"^(?:(?:ว่าที่\s*)?(?:ร\.?ต\.?|ด\.?ช\.?|ด\.?ญ\.?|น\.?ส\.?)|ดร\.?|คุณ|นาย|นางสาว|นาง|mr\.?|mrs\.?|ms\.?)\s*",
    re.IGNORECASE,
)
TRAILING_NICKNAME_RE = re.compile(r"\s*[\(（][^\)）]*[\)）]\s*$")


class HRUpdateError(RuntimeError):
    pass


class _ContactPageParser(HTMLParser):
    required_card_classes = {
        "col-12", "col-sm-6", "col-md-4", "d-flex", "align-items-stretch", "flex-column"
    }

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.cards: list[dict[str, object]] = []
        self.card_depth = 0
        self.current: dict[str, object] | None = None
        self.capture: str | None = None
        self.capture_tag: str | None = None
        self.capture_depth = 0
        self.buffer: list[str] = []
        self.in_h2 = 0

    def handle_starttag(self, tag: str, attrs):
        attr_map = dict(attrs)
        classes = set(attr_map.get("class", "").split())
        if tag == "div" and not self.card_depth and self.required_card_classes.issubset(classes):
            self.card_depth = 1
            self.current = {"department": "", "name": "", "values": []}
            return
        if not self.card_depth:
            return
        if tag == "div":
            self.card_depth += 1
            if "col-11" in classes and not self.current["department"]:
                self._start_capture("department", tag)
        elif tag == "h2":
            self.in_h2 += 1
        elif tag == "b" and self.in_h2 and not self.current["name"]:
            self._start_capture("name", tag)
        elif tag == "li":
            self._start_capture("value", tag)
        if self.capture:
            self.capture_depth += 1

    def handle_endtag(self, tag: str):
        if not self.card_depth:
            return
        if self.capture:
            self.capture_depth -= 1
            if tag == self.capture_tag and self.capture_depth <= 0:
                text = " ".join("".join(self.buffer).split())
                if self.capture == "value":
                    self.current["values"].append(text.split("|", 1)[1].strip() if "|" in text else text)
                else:
                    self.current[self.capture] = text
                self.capture = None
                self.capture_tag = None
                self.buffer = []
                self.capture_depth = 0
        if tag == "h2" and self.in_h2:
            self.in_h2 -= 1
        if tag == "div":
            if self.card_depth == 1:
                self.cards.append(self.current)
                self.current = None
            self.card_depth -= 1

    def handle_data(self, data: str):
        if self.capture:
            self.buffer.append(data)

    def _start_capture(self, field: str, tag: str):
        if self.capture:
            return
        self.capture = field
        self.capture_tag = tag
        self.capture_depth = 0
        self.buffer = []


def normalize_person_name(value: str | None) -> str:
    text = unicodedata.normalize("NFKC", value or "").strip().casefold()
    text = TRAILING_NICKNAME_RE.sub("", text)
    previous = None
    while text and text != previous:
        previous = text
        text = HONORIFIC_RE.sub("", text).strip()
    return re.sub(r"[^\wก-๙]+|_", "", text, flags=re.UNICODE)


def parse_hr_contacts(html: str) -> tuple[int, list[dict[str, str]]]:
    count_match = re.search(r"Results\s*:\s*([0-9,]+)", html, re.IGNORECASE)
    if not count_match:
        raise HRUpdateError("ไม่พบจำนวน Results ในหน้า HR")
    reported_count = int(count_match.group(1).replace(",", ""))
    if reported_count <= 0:
        raise HRUpdateError("หน้า HR รายงานจำนวนข้อมูลเป็นศูนย์")

    parser = _ContactPageParser()
    parser.feed(html)
    records: list[dict[str, str]] = []
    for order, card in enumerate(parser.cards, start=1):
        values = list(card["values"])
        values.extend([""] * max(0, 5 - len(values)))
        full_name = str(card["name"])
        normalized_name = normalize_person_name(full_name)
        if not full_name or not normalized_name:
            raise HRUpdateError(f"ข้อมูล HR ลำดับที่ {order} ไม่มีชื่อที่ใช้งานได้")
        records.append(
            {
                "source_order": order,
                "employee_code": values[0],
                "full_name": full_name,
                "normalized_name": normalized_name,
                "department": str(card["department"]),
                "position": values[1],
                "email": values[2],
                "mobile": values[3],
                "office_phone": values[4],
            }
        )
    if reported_count != len(records):
        raise HRUpdateError(
            f"จำนวนข้อมูลไม่ตรงกัน: หน้า HR รายงาน {reported_count:,} รายการ แต่ระบบอ่านได้ {len(records):,} รายการ"
        )
    return reported_count, records


def fetch_hr_contacts_html() -> str:
    try:
        with httpx.Client(follow_redirects=True, timeout=settings.hr_contacts_timeout) as client:
            response = client.get(settings.hr_contacts_url)
            response.raise_for_status()
            if re.search(r"Results\s*:\s*[0-9,]+", response.text, re.IGNORECASE):
                return response.text
            # The contacts page loads the complete result list through this form endpoint.
            data_url = urljoin(str(response.url), "contact.php")
            data_response = client.post(data_url, data={"search": ""})
            data_response.raise_for_status()
            return data_response.text
    except httpx.HTTPError as exc:
        raise HRUpdateError(f"เชื่อมต่อเว็บไซต์ HR ไม่สำเร็จ: {exc}") from exc


def current_hr_run(db: Session) -> HRUpdateRun | None:
    return db.scalar(
        select(HRUpdateRun)
        .where(HRUpdateRun.status == "completed", HRUpdateRun.is_current.is_(True))
        .order_by(HRUpdateRun.id.desc())
    )


def recompute_employment_statuses(db: Session, run: HRUpdateRun | None = None) -> tuple[int, int]:
    run = run or current_hr_run(db)
    if not run:
        return 0, 0
    hr_names = set(
        db.scalars(
            select(HRUpdateRecord.normalized_name).where(HRUpdateRecord.run_id == run.id)
        ).all()
    )
    people = db.scalars(
        select(Person)
        .where(
            Person.id.in_(
                select(Assignment.person_id).where(Assignment.is_active.is_(True)).distinct()
            )
        )
        .order_by(Person.id)
    ).all()
    checked_at = utcnow()
    working_count = 0
    resigned_count = 0
    for person in people:
        matched = normalize_person_name(person.full_name) in hr_names
        person.employment_status = WORKING if matched else RESIGNED
        person.employment_status_updated_at = checked_at
        person.hr_update_run_id = run.id
        if matched:
            working_count += 1
        else:
            resigned_count += 1
    return working_count, resigned_count


def refresh_hr_data(db: Session, user_id: int, fetcher=None) -> HRUpdateRun:
    run = HRUpdateRun(
        status="running",
        source_url=settings.hr_contacts_url,
        created_by_id=user_id,
    )
    db.add(run)
    db.commit()
    db.refresh(run)

    try:
        html = (fetcher or fetch_hr_contacts_html)()
        reported_count, records = parse_hr_contacts(html)
        db.execute(update(HRUpdateRun).where(HRUpdateRun.is_current.is_(True)).values(is_current=False))
        for record in records:
            db.add(HRUpdateRecord(run_id=run.id, **record))
        db.flush()
        run = db.get(HRUpdateRun, run.id)
        run.reported_count = reported_count
        run.parsed_count = len(records)
        name_counts = Counter(item["normalized_name"] for item in records)
        run.duplicate_name_count = sum(1 for count in name_counts.values() if count > 1)
        run.status = "completed"
        run.is_current = True
        run.completed_at = utcnow()
        run.working_count, run.resigned_count = recompute_employment_statuses(db, run)
        db.commit()
        db.refresh(run)
        return run
    except Exception as exc:
        db.rollback()
        failed_run = db.get(HRUpdateRun, run.id)
        failed_run.status = "failed"
        failed_run.is_current = False
        failed_run.error_message = str(exc)[:2000]
        failed_run.completed_at = utcnow()
        db.commit()
        if isinstance(exc, HRUpdateError):
            raise
        raise HRUpdateError(f"อัปเดตข้อมูล HR ไม่สำเร็จ: {exc}") from exc
