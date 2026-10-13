"""
Employee Attendance & Analytics API - STARTER

Run:  uvicorn app.main:app --port 8000
Env:  MONGO_URI, MONGO_DB (a local .env is loaded for convenience)

This file was written quickly by a colleague who has left the company. The happy path
works, but nobody has reviewed it. Read PROBLEM_STATEMENT.docx for what is expected of you,
openapi.yaml for the contract and DATA_MODEL.md for what is stored in MongoDB.
"""

import calendar
from decimal import Decimal, ROUND_HALF_UP
import re
from pymongo.errors import DuplicateKeyError
from fastapi import HTTPException
import os
from pydantic import BaseModel, Field, StrictInt
from datetime import datetime,timedelta
from pydantic import BaseModel, field_validator, StrictInt
from datetime import datetime, timedelta, timezone
from typing import Optional
from decimal import Decimal, ROUND_HALF_UP
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel
from pymongo import MongoClient


load_dotenv()


client = MongoClient(os.getenv("MONGO_URI", "mongodb://localhost:27017"))
db = client[os.getenv("MONGO_DB", "attendance_db")]

IST = timezone(timedelta(hours=5, minutes=30))

app = FastAPI(
    title="Employee Attendance & Analytics API",
    version="2.0.0",
)


def create_indexes():
    db.employees.create_index("emp_code", unique=True)

    db.attendance_logs.create_index(
        [("emp_code", 1), ("date", 1)],
        unique=True,
    )

    db.employees.create_index(
        [("department", 1), ("emp_code", 1)]
    )
    db.employees.create_index("joined_on", name="joined_on_1")
    db.attendance_logs.create_index(
        [("date", -1), ("emp_code", 1)]
    )


@app.on_event("startup")
def startup_event():
    create_indexes()


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def compute_late_minutes(
    punch_in: datetime,
    shift_start: str,
    shift_date: str,
) -> int:
    h, m = map(int, shift_start.split(":"))
    work_date = datetime.fromisoformat(shift_date).date()

    start = datetime(
        work_date.year,
        work_date.month,
        work_date.day,
        h,
        m,
    )

    if punch_in.tzinfo is not None:
        punch_in = punch_in.astimezone(IST).replace(tzinfo=None)

    minutes_late = int((punch_in - start).total_seconds() // 60)

    return minutes_late if (punch_in - start).total_seconds() > 600 else 0

def compute_work_hours(punch_in: datetime, punch_out: datetime) -> float:
    seconds = Decimal(str((punch_out - punch_in).total_seconds()))
    hours = seconds / Decimal("3600")

    return float(
        hours.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    )


def compute_overtime(
    punch_out: datetime,
    shift_end: str,
    shift_date: str,
    overnight: bool = False,
) -> int:
    h, m = map(int, shift_end.split(":"))
    end_date = datetime.fromisoformat(shift_date).date()

    if overnight:
        from datetime import timedelta
        end_date += timedelta(days=1)

    shift_end_dt = datetime(
        end_date.year,
        end_date.month,
        end_date.day,
        h,
        m,
        tzinfo=IST,
    )

    if punch_out.tzinfo is None:
        punch_out = punch_out.replace(tzinfo=IST)

    minutes = int((punch_out - shift_end_dt).total_seconds() // 60)

    return minutes if minutes >= 30 else 0


# --------------------------------------------------------------------------- #
# Models
# --------------------------------------------------------------------------- #
class EmployeeIn(BaseModel):
    emp_code: str
    name: str
    email: str
    department: str
    shift_start: str = "09:30"
    shift_end: str = "18:30"
    joined_on: str

    @field_validator("emp_code")
    @classmethod
    def validate_emp_code(cls, value):
        if not re.fullmatch(r"EMP\d{4,6}", value):
            raise ValueError("emp_code must look like EMP0001")
        return value

    @field_validator("email")
    @classmethod
    def validate_email(cls, value):
        if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", value):
            raise ValueError("Invalid email address")
        return value

    @field_validator("joined_on")
    @classmethod
    def validate_joined_on(cls, value):
        try:
            datetime.strptime(value, "%Y-%m-%d")
        except ValueError:
            raise ValueError("joined_on must be YYYY-MM-DD")
        return value

    @field_validator("shift_start", "shift_end")
    @classmethod
    def validate_shift_time(cls, value):
        try:
            datetime.strptime(value, "%H:%M")
        except ValueError:
            raise ValueError("Shift time must be HH:MM")
        return value

    @field_validator("name", "department")
    @classmethod
    def validate_non_empty(cls, value):
        if not value.strip():
            raise ValueError("Field cannot be empty")
        return value

    @field_validator("shift_end")
    @classmethod
    def validate_shift_difference(cls, value, info):
        start = info.data.get("shift_start")
        if start == value:
            raise ValueError("Shift start and end cannot be equal")
        return value


# --------------------------------------------------------------------------- #
# Endpoints provided
# --------------------------------------------------------------------------- #
@app.get("/health")
def health():
    try:
        client.admin.command("ping")
        return {"status": "ok"}
    except Exception:
        raise HTTPException(
            status_code=503,
            detail="MongoDB is unavailable",
        )



from pymongo.errors import DuplicateKeyError

@app.post("/employees", status_code=201)
def create_employee(body: EmployeeIn):
    doc = body.model_dump()
    created_at = datetime.now(timezone.utc).replace(microsecond=0)
    doc["created_at"] = created_at

    try:
        db.employees.insert_one(doc)
    except DuplicateKeyError:
        raise HTTPException(
            status_code=409,
            detail="emp_code already exists",
        )

    # Return an API-safe response without MongoDB's internal _id.
    doc.pop("_id", None)
    doc["created_at"] = int(created_at.timestamp() * 1000)

    return doc



@app.get("/employees")
def list_employees(
    department: Optional[str] = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
):
    q = {}
    if department:
        q["department"] = department
    skip = (page-1) * page_size
    total = db.employees.count_documents(q)
    items = list(
    db.employees.find(q, {"_id": 0})
    .sort("emp_code", 1)
    .skip((page - 1) * page_size)
    .limit(page_size)
)
    return {"items": items, "total": total, "page": page, "page_size": page_size}


class PunchInIn(BaseModel):
    emp_code: str
    punched_at: Optional[StrictInt] = None
    status: str = "PRESENT"

@app.post("/attendance/punch-in", status_code=201)
def punch_in(body: PunchInIn):
    emp = db.employees.find_one({"emp_code": body.emp_code})

    if not emp:
        raise HTTPException(status_code=404, detail="Employee not found")

    if body.status not in {"PRESENT", "WFH", "ON_DUTY"}:
        raise HTTPException(
            status_code=422,
            detail="Status must be PRESENT, WFH, or ON_DUTY",
        )

    if body.punched_at is not None:
        if isinstance(body.punched_at, bool) or body.punched_at < 100000000000:
            raise HTTPException(
                status_code=422,
                detail="punched_at must be epoch milliseconds",
            )

        try:
            ts_utc = datetime.fromtimestamp(
                body.punched_at / 1000,
                tz=timezone.utc,
            ).replace(microsecond=0)
        except (ValueError, OverflowError, OSError):
            raise HTTPException(
                status_code=422,
                detail="Invalid punched_at timestamp",
            )
    else:
        ts_utc = datetime.now(timezone.utc).replace(microsecond=0)

    local_ts = ts_utc.astimezone(IST)
    shift_start = emp["shift_start"]
    shift_end = emp["shift_end"]

    start_minutes = int(shift_start[:2]) * 60 + int(shift_start[3:])
    end_minutes = int(shift_end[:2]) * 60 + int(shift_end[3:])
    overnight = end_minutes <= start_minutes

    attendance_date = local_ts.date()

    if overnight and (
        local_ts.hour * 60 + local_ts.minute < end_minutes
    ):
        attendance_date -= timedelta(days=1)

    date_str = attendance_date.isoformat()

    doc = {
        "emp_code": body.emp_code,
        "date": date_str,
        "status": body.status,
        "punch_in": ts_utc.replace(tzinfo=None),
        "punch_out": None,
        "work_hours": None,
        "late_minutes": compute_late_minutes(
            local_ts.replace(tzinfo=None),
            shift_start,
            date_str,
        ),
        "overtime_minutes": 0,
        "half_day": False,
        "history": [],
    }

    try:
        db.attendance_logs.insert_one(doc)
    except DuplicateKeyError:
        raise HTTPException(
            status_code=409,
            detail="Attendance already exists for this employee and date",
        )

    # Return timestamps in epoch milliseconds, not MongoDB's internal format.
    response = {
        "emp_code": doc["emp_code"],
        "date": doc["date"],
        "status": doc["status"],
        "punch_in": int(ts_utc.timestamp() * 1000),
        "punch_out": None,
        "work_hours": None,
        "late_minutes": doc["late_minutes"],
        "overtime_minutes": 0,
        "half_day": False,
        "history": [],
    }

    return response

class PunchOutIn(BaseModel):
    emp_code: str
    punched_at: Optional[StrictInt] = None


@app.post("/attendance/punch-out")
def punch_out(body: PunchOutIn):
    emp = db.employees.find_one({"emp_code": body.emp_code})

    if not emp:
        raise HTTPException(
            status_code=404,
            detail="Employee not found",
        )

    # Convert the supplied epoch milliseconds to UTC.
    if body.punched_at is not None:
        if (
            isinstance(body.punched_at, bool)
            or body.punched_at < 100000000000
            or body.punched_at > 4102444800000
        ):
            raise HTTPException(
                status_code=422,
                detail="punched_at must be epoch milliseconds",
            )

        try:
            punch_out_utc = datetime.fromtimestamp(
                body.punched_at / 1000,
                tz=timezone.utc,
            ).replace(microsecond=0)
        except (ValueError, OverflowError, OSError):
            raise HTTPException(
                status_code=422,
                detail="Invalid punched_at timestamp",
            )
    else:
        punch_out_utc = datetime.now(timezone.utc).replace(microsecond=0)

    # MongoDB stores naive UTC timestamps.
    punch_out_db = punch_out_utc.replace(tzinfo=None)

    # Find the most recent open punch-in before this punch-out.
    record = db.attendance_logs.find_one(
        {
            "emp_code": body.emp_code,
            "punch_in": {"$lte": punch_out_db},
            "punch_out": None,
            "status": {"$in": ["PRESENT", "WFH", "ON_DUTY"]},
        },
        sort=[("punch_in", -1)],
    )

    if not record:
        # Look for a previous punch-in, whether open or closed.
        existing_record = db.attendance_logs.find_one(
            {
                "emp_code": body.emp_code,
                "punch_in": {"$lte": punch_out_db},
                "status": {"$in": ["PRESENT", "WFH", "ON_DUTY"]},
            },
            sort=[("punch_in", -1)],
        )

        if existing_record:
            if existing_record.get("punch_out") is not None:
                raise HTTPException(
                    status_code=409,
                    detail="Attendance record was already punched out",
                )

        raise HTTPException(
            status_code=404,
            detail="No open punch-in record found",
        )

    punch_in_db = record["punch_in"]

    # Validate punch-out time.
    if punch_out_db <= punch_in_db:
        raise HTTPException(
            status_code=422,
            detail="Punch-out must be after punch-in",
        )

    if punch_out_db > punch_in_db + timedelta(hours=24):
        raise HTTPException(
            status_code=422,
            detail="Punch-out cannot be more than 24 hours after punch-in",
        )

    # Convert timestamps to IST for calculations.
    punch_in_ist = punch_in_db.replace(
        tzinfo=timezone.utc
    ).astimezone(IST)

    punch_out_ist = punch_out_utc.astimezone(IST)

    work_hours = compute_work_hours(
        punch_in_ist,
        punch_out_ist,
    )

    half_day = work_hours < 4.50

    shift_start = emp["shift_start"]
    shift_end = emp["shift_end"]

    start_minutes = (
        int(shift_start[:2]) * 60 + int(shift_start[3:])
    )
    end_minutes = (
        int(shift_end[:2]) * 60 + int(shift_end[3:])
    )

    overnight = end_minutes <= start_minutes

    overtime_minutes = compute_overtime(
        punch_out_ist.replace(tzinfo=None).replace(tzinfo=IST),
        shift_end,
        record["date"],
        overnight,
    )

    # Atomically close the record to prevent concurrent punch-outs.
    result = db.attendance_logs.update_one(
        {
            "_id": record["_id"],
            "punch_out": None,
        },
        {
            "$set": {
                "punch_out": punch_out_db,
                "work_hours": work_hours,
                "overtime_minutes": overtime_minutes,
                "half_day": half_day,
            }
        },
    )

    if result.modified_count == 0:
        raise HTTPException(
            status_code=409,
            detail="Attendance record was already punched out",
        )

    return {
        "emp_code": body.emp_code,
        "date": record["date"],
        "status": record["status"],
        "punch_in": int(
            punch_in_db.replace(tzinfo=timezone.utc).timestamp()
            * 1000
        ),
        "punch_out": int(punch_out_utc.timestamp() * 1000),
        "work_hours": work_hours,
        "late_minutes": record["late_minutes"],
        "overtime_minutes": overtime_minutes,
        "half_day": half_day,
    }



@app.get("/attendance")
def list_attendance(
    emp_code: Optional[str] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    status: Optional[str] = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
):
    q = {}

    if emp_code:
        q["emp_code"] = emp_code

    # Validate and apply date filters
    date_filter = {}

    if date_from:
        try:
            datetime.strptime(date_from, "%Y-%m-%d")
        except ValueError:
            raise HTTPException(422, "date_from must be YYYY-MM-DD")
        date_filter["$gte"] = date_from

    if date_to:
        try:
            datetime.strptime(date_to, "%Y-%m-%d")
        except ValueError:
            raise HTTPException(422, "date_to must be YYYY-MM-DD")
        date_filter["$lte"] = date_to

    if date_from and date_to and date_from > date_to:
        raise HTTPException(422, "date_from cannot be after date_to")

    if date_filter:
        q["date"] = date_filter

    # Validate status
    allowed_statuses = {
        "PRESENT", "WFH", "ON_DUTY", "ABSENT", "LEAVE"
    }

    if status:
        if status not in allowed_statuses:
            raise HTTPException(422, "Invalid attendance status")
        q["status"] = status

    # Database-side pagination
    total = db.attendance_logs.count_documents(q)

    page_docs = list(
        db.attendance_logs.find(q, {"_id": 0})
        .sort([("date", -1), ("emp_code", 1)])
        .skip((page - 1) * page_size)
        .limit(page_size)
    )

    # Convert stored UTC datetimes to epoch milliseconds
    for item in page_docs:
        for field in ("punch_in", "punch_out"):
            timestamp = item.get(field)

            if timestamp is not None:
                timestamp_utc = timestamp.replace(tzinfo=timezone.utc)
                item[field] = int(timestamp_utc.timestamp() * 1000)

    return {
        "items": page_docs,
        "total": total,
        "page": page,
        "page_size": page_size,
    }

class AttendanceCorrectionIn(BaseModel):
    status: Optional[str] = None
    punch_in: Optional[StrictInt] = None
    punch_out: Optional[StrictInt] = None
    reason: str = Field(..., min_length=5, max_length=200)
    regularized_by: str = Field(..., min_length=1, max_length=50)




@app.patch("/attendance/{emp_code}/{date}")
def correct_attendance(
    emp_code: str,
    date: str,
    body: AttendanceCorrectionIn,
):
    # 1. Validate date format
    try:
        parsed_date = datetime.strptime(date, "%Y-%m-%d")
        if parsed_date.strftime("%Y-%m-%d") != date:
            raise ValueError
    except ValueError:
        raise HTTPException(
            status_code=422,
            detail="date must be YYYY-MM-DD",
        )

    # 2. Find employee and attendance record
    employee = db.employees.find_one({"emp_code": emp_code})
    if not employee:
        raise HTTPException(
            status_code=404,
            detail="Employee not found",
        )

    record = db.attendance_logs.find_one({
        "emp_code": emp_code,
        "date": date,
    })

    if not record:
        raise HTTPException(
            status_code=404,
            detail="Attendance record not found",
        )

    # 3. Extract audit metadata separately from attendance fields
    changes = body.model_dump(exclude_unset=True)

    reason = changes.pop("reason", None)
    regularized_by = changes.pop("regularized_by", None)

    if not isinstance(reason, str) or not 5 <= len(reason.strip()) <= 200:
        raise HTTPException(
            status_code=422,
            detail="reason must be between 5 and 200 characters",
        )

    if (
        not isinstance(regularized_by, str)
        or not 1 <= len(regularized_by.strip()) <= 50
    ):
        raise HTTPException(
            status_code=422,
            detail="regularized_by must be between 1 and 50 characters",
        )

    if not changes:
        raise HTTPException(
            status_code=422,
            detail="Provide at least one attendance field to update",
        )

    allowed_fields = {"status", "punch_in", "punch_out"}

    if not set(changes).issubset(allowed_fields):
        raise HTTPException(
            status_code=422,
            detail="Only status, punch_in, and punch_out can be corrected",
        )

    # 4. Validate attendance status
    allowed_statuses = {
        "PRESENT", "WFH", "ON_DUTY", "ABSENT", "LEAVE"
    }

    if (
        "status" in changes
        and changes["status"] not in allowed_statuses
    ):
        raise HTTPException(
            status_code=422,
            detail="Invalid attendance status",
        )

    # 5. Convert supplied epoch milliseconds to naive UTC datetimes
    update_fields = {}

    for field in ("punch_in", "punch_out"):
        if field not in changes:
            continue

        value = changes[field]

        if value is None:
            update_fields[field] = None
            continue

        if (
            isinstance(value, bool)
            or not isinstance(value, int)
            or value < 100000000000
            or value > 4102444800000
        ):
            raise HTTPException(
                status_code=422,
                detail=f"{field} must be epoch milliseconds",
            )

        try:
            timestamp = datetime.fromtimestamp(
                value / 1000,
                tz=timezone.utc,
            ).replace(microsecond=0)
        except (ValueError, OverflowError, OSError):
            raise HTTPException(
                status_code=422,
                detail=f"Invalid {field} timestamp",
            )

        update_fields[field] = timestamp.replace(tzinfo=None)

    if "status" in changes:
        update_fields["status"] = changes["status"]

    # 6. Recalculate derived fields
    final_status = update_fields.get("status", record["status"])

    if final_status in {"ABSENT", "LEAVE"}:
        if "punch_in" in changes and changes["punch_in"] is not None:
            raise HTTPException(
                status_code=422,
                detail="ABSENT or LEAVE cannot have punch times",
            )

        if "punch_out" in changes and changes["punch_out"] is not None:
            raise HTTPException(
                status_code=422,
                detail="ABSENT or LEAVE cannot have punch times",
            )

        update_fields.update({
            "punch_in": None,
            "punch_out": None,
            "work_hours": None,
            "late_minutes": 0,
            "overtime_minutes": 0,
            "half_day": False,
        })

    else:
        punch_in = update_fields.get(
            "punch_in", record.get("punch_in")
        )
        punch_out = update_fields.get(
            "punch_out", record.get("punch_out")
        )

        if punch_in is None:
            raise HTTPException(
                status_code=422,
                detail="Presence statuses require punch_in",
            )

        # R1: punch-in must belong to this attendance date.
        punch_in_ist = punch_in.replace(
            tzinfo=timezone.utc
        ).astimezone(IST)

        shift_start = employee["shift_start"]
        shift_end = employee["shift_end"]

        start_minutes = (
            int(shift_start[:2]) * 60 + int(shift_start[3:])
        )
        end_minutes = (
            int(shift_end[:2]) * 60 + int(shift_end[3:])
        )
        overnight = end_minutes <= start_minutes

        local_punch_date = punch_in_ist.date()

        if overnight:
            shift_end_minutes = end_minutes
            local_punch_minutes = (
                punch_in_ist.hour * 60 + punch_in_ist.minute
            )

            if local_punch_minutes < shift_end_minutes:
                local_punch_date -= timedelta(days=1)

        if local_punch_date.isoformat() != date:
            raise HTTPException(
                status_code=422,
                detail="punch_in must belong to the attendance date",
            )

        if punch_out is not None:
            if punch_out <= punch_in:
                raise HTTPException(
                    status_code=422,
                    detail="punch_out must be after punch_in",
                )

            if punch_out > punch_in + timedelta(hours=24):
                raise HTTPException(
                    status_code=422,
                    detail="Punch duration cannot exceed 24 hours",
                )

            punch_out_ist = punch_out.replace(
                tzinfo=timezone.utc
            ).astimezone(IST)

            work_hours = compute_work_hours(
                punch_in.replace(tzinfo=timezone.utc),
                punch_out.replace(tzinfo=timezone.utc),
            )

            update_fields["work_hours"] = work_hours
            update_fields["half_day"] = work_hours < 4.50

            update_fields["late_minutes"] = compute_late_minutes(
                punch_in_ist,
                shift_start,
                date,
            )

            update_fields["overtime_minutes"] = compute_overtime(
                punch_out_ist,
                shift_end,
                date,
                overnight,
            )

        else:
            update_fields["work_hours"] = None
            update_fields["half_day"] = False
            update_fields["late_minutes"] = compute_late_minutes(
                punch_in_ist,
                shift_start,
                date,
            )
            update_fields["overtime_minutes"] = 0

    # 7. Keep only fields whose values actually changed.
    # Include derived fields if their values changed.
    previous = {}
    new_values = {}

    for field, new_value in update_fields.items():
        old_value = record.get(field)

        if old_value != new_value:
            previous[field] = old_value
            new_values[field] = new_value

    if not new_values:
        raise HTTPException(
            status_code=422,
            detail="No changes detected",
        )

    # 8. Create exactly one complete audit entry.
    history_entry = {
        "changed_at": datetime.now(timezone.utc).replace(tzinfo=None),
        "reason": reason.strip(),
        "regularized_by": regularized_by.strip(),
        "previous": previous,
        "new": new_values,
    }

    # 9. Update attendance and append history atomically.
    result = db.attendance_logs.update_one(
        {
            "_id": record["_id"],
            **{
                field: record.get(field)
                for field in previous
            },
        },
        {
            "$set": new_values,
            "$push": {"history": history_entry},
        },
    )

    if result.modified_count == 0:
        raise HTTPException(
            status_code=409,
            detail="Attendance record changed concurrently; retry the correction",
        )

    # 10. Return the updated record without MongoDB _id.
    updated = db.attendance_logs.find_one(
        {"_id": record["_id"]},
        {"_id": 0},
    )

    def timestamp_to_ms(value):
        if value is None:
            return None

        if isinstance(value, datetime):
            if value.tzinfo is None:
                value = value.replace(tzinfo=timezone.utc)

            return int(value.timestamp() * 1000)

        return value

    for field in ("punch_in", "punch_out"):
        updated[field] = timestamp_to_ms(updated.get(field))

    for entry in updated.get("history", []):
        entry["changed_at"] = timestamp_to_ms(
            entry.get("changed_at")
        )

        for section in ("previous", "new"):
            values = entry.get(section, {})

            for field in ("punch_in", "punch_out"):
                if field in values:
                    values[field] = timestamp_to_ms(values[field])

    return updated





# --------------------------------------------------------------------------- #
# TODO - the rest of the contract (see openapi.yaml):
#   POST  /attendance/punch-out
#   PATCH /attendance/{emp_code}/{date}
#   GET   /analytics/employees/{emp_code}/monthly
#   GET   /analytics/departments/summary
#   GET   /analytics/leaderboard/late
#   GET   /analytics/departments/{department}/trend
#   GET   /admin/explain/{endpoint}
# --------------------------------------------------------------------------- #
@app.get("/analytics/employees/{emp_code}/monthly")
def employee_monthly_analytics(
    emp_code: str,
    month: str = Query(..., description="Month in YYYY-MM format"),
):
    # Validate month
    try:
        month_date = datetime.strptime(month, "%Y-%m")
        if month_date.strftime("%Y-%m") != month:
            raise ValueError
    except ValueError:
        raise HTTPException(
            status_code=422,
            detail="month must be in YYYY-MM format"
        )

    # Find employee
    employee = db.employees.find_one({"emp_code": emp_code})

    if not employee:
        raise HTTPException(status_code=404, detail="Employee not found")

    year = month_date.year
    month_num = month_date.month
    last_day = calendar.monthrange(year, month_num)[1]

    month_start = datetime(year, month_num, 1).date()
    month_end = datetime(year, month_num, last_day).date()
    joined_on = datetime.strptime(
        employee["joined_on"], "%Y-%m-%d"
    ).date()

    # Count weekdays from the later of month start and joining date
    start_date = max(month_start, joined_on)
    working_days = 0

    if start_date <= month_end:
        current_date = start_date
        while current_date <= month_end:
            if current_date.weekday() < 5:
                working_days += 1
            current_date += timedelta(days=1)

    # Fetch this employee's records for the selected month
    records = list(
        db.attendance_logs.find(
            {
                "emp_code": emp_code,
                "date": {
                    "$gte": month_start.isoformat(),
                    "$lte": month_end.isoformat(),
                },
            },
            {"_id": 0},
        )
    )

    presence_statuses = {"PRESENT", "WFH", "ON_DUTY"}
    present_days = 0.0
    leave_days = 0
    late_count = 0
    total_late_minutes = 0
    total_overtime_minutes = 0

    for record in records:
        record_date = datetime.strptime(
            record["date"], "%Y-%m-%d"
        ).date()

        if record.get("status") == "LEAVE":
            if record_date.weekday() < 5:
                leave_days += 1

        if record.get("status") in presence_statuses:
            if record_date.weekday() < 5:
                present_days += (
                    0.5 if record.get("half_day", False) else 1.0
                )

        late_minutes = record.get("late_minutes", 0) or 0
        overtime_minutes = record.get("overtime_minutes", 0) or 0

        if late_minutes > 0:
            late_count += 1

        # Late and overtime totals include weekend records
        total_late_minutes += late_minutes
        total_overtime_minutes += overtime_minutes

    # Attendance percentage rounded HALF_UP to 2 decimals
    if working_days == 0:
        attendance_pct = None
    else:
        percentage = (
            Decimal(str(present_days))
            / Decimal(working_days)
            * Decimal("100")
        )
        attendance_pct = float(
            percentage.quantize(
                Decimal("0.01"),
                rounding=ROUND_HALF_UP,
            )
        )

    return {
        "emp_code": emp_code,
        "month": month,
        "working_days": working_days,
        "present_days": present_days,
        "leave_days": leave_days,
        "late_count": late_count,
        "total_late_minutes": total_late_minutes,
        "total_overtime_minutes": total_overtime_minutes,
        "attendance_pct": attendance_pct,
    }

@app.get("/analytics/departments/summary")
def department_summary(
    month: str = Query(
        ...,
        pattern=r"^\d{4}-(0[1-9]|1[0-2])$"
    ),
    department: str | None = Query(None)
):
    # Validate and calculate the month's date range
    try:
        month_start_date = datetime.strptime(month, "%Y-%m").date()
    except ValueError:
        raise HTTPException(
            status_code=422,
            detail="Month must be in YYYY-MM format"
        )

    month_end_date = (
        month_start_date.replace(day=28)
        + timedelta(days=4)
    )
    month_end_date = month_end_date.replace(day=1) - timedelta(days=1)

    month_start = month_start_date.isoformat()
    month_end = month_end_date.isoformat()

    presence_statuses = ["PRESENT", "WFH", "ON_DUTY"]
    employee_match = {"joined_on": {"$lte": month_end}}
    if department:
        employee_match["department"] = department

    pipeline = [
        # Include employees who joined on or before the month's end.
        # Employees without attendance records remain included.
        {
            "$match": employee_match
        },

        # Fetch attendance records for each employee for this month.
        {
            "$lookup": {
                "from": "attendance_logs",
                "let": {"employee_code": "$emp_code"},
                "pipeline": [
                    {
                        "$match": {
                            "$expr": {
                                "$and": [
                                    {
                                        "$eq": [
                                            "$emp_code",
                                            "$$employee_code"
                                        ]
                                    },
                                    {
                                        "$gte": [
                                            "$date",
                                            month_start
                                        ]
                                    },
                                    {
                                        "$lte": [
                                            "$date",
                                            month_end
                                        ]
                                    }
                                ]
                            }
                        }
                    }
                ],
                "as": "attendance"
            }
        },

        # Calculate monthly statistics for each employee.
        {
            "$project": {
                "department": 1,
                "employee_stats": {
                    "$reduce": {
                        "input": "$attendance",
                        "initialValue": {
                            "present_days": 0.0,
                            "leave_count": 0,
                            "on_duty_count": 0,
                            "late count":0.0,
                            "total_late_minutes": 0,
                            "work_hours_total": 0.0,
                            "work_hours_count": 0
                        },
                        "in": {
                            "$let": {
                                "vars": {
                                    "day_of_week": {
                                        "$dayOfWeek": {
                                            "$dateFromString": {
                                                "dateString": "$$this.date"
                                            }
                                        }
                                    },
                                    "late": {
                                        "$ifNull": [
                                            "$$this.late_minutes",
                                            0
                                        ]
                                    },
                                    "overtime": {
                                        "$ifNull": [
                                            "$$this.overtime_minutes",
                                            0
                                        ]
                                    },
                                    "hours": "$$this.work_hours",
                                    "status": "$$this.status"
                                },
                                "in": {
                                    "$let": {
                                        "vars": {
                                            # Monday=2, ..., Friday=6
                                            "is_weekday": {
                                                "$and": [
                                                    {
                                                        "$gte": [
                                                            "$$day_of_week",
                                                            2
                                                        ]
                                                    },
                                                    {
                                                        "$lte": [
                                                            "$$day_of_week",
                                                            6
                                                        ]
                                                    }
                                                ]
                                            }
                                        },
                                        "in": {
                                            "present_days": {
                                                "$add": [
                                                    "$$value.present_days",
                                                    {
                                                        "$cond": [
                                                            {
                                                                "$and": [
                                                                    "$$is_weekday",
                                                                    {
                                                                        "$in": [
                                                                            "$$status",
                                                                            presence_statuses
                                                                        ]
                                                                    }
                                                                ]
                                                            },
                                                            {
                                                                "$cond": [
                                                                    {
                                                                        "$eq": [
                                                                            "$$this.half_day",
                                                                            True
                                                                        ]
                                                                    },
                                                                    0.5,
                                                                    1.0
                                                                ]
                                                            },
                                                            0.0
                                                        ]
                                                    }
                                                ]
                                            },
                                            "leave_count": {
                                                "$add": [
                                                    "$$value.leave_count",
                                                    {
                                                        "$cond": [
                                                            {"$eq":["$$status","LEAVE"]},
                                                            1,
                                                            0
                                                        ]
                                                    }
                                                ]
                                            },
                                            "on_duty_count": {
                                                "$add": [
                                                    "$$value.on_duty_count",
                                                    {
                                                        "$cond": [
                                                            {"$eq": ["$$status", "ON_DUTY"]},
                                                            1,
                                                            0
                                                        ]
                                                    }
                                                ]
                                            },
                                            
                                            "total_late_minutes": {
                                                "$add": [
                                                    "$$value.total_late_minutes",
                                                    "$$late"
                                                ]
                                            },
                                            "total_overtime_minutes": {
                                                "$add": [
                                                    "$$value.total_overtime_minutes",
                                                    "$$overtime"
                                                ]
                                            },
                                            "work_hours_count": {
                                                "$add": [
                                                    "$$value.work_hours_count",
                                                    {
                                                        "$cond": [
                                                            {
                                                                "$and": [
                                                                    {"$in": ["$$status", presence_statuses]},
                                                                    {"$ne": ["$$hours", None]}
                                                                ]
                                                            },
                                                            1,
                                                            0
                                                        ]
                                                    }
                                                ]
                                            },
                                            "work_hours_total": {
                                                "$add": [
                                                    "$$value.work_hours_total",
                                                    {
                                                        "$cond": [
                                                            {
                                                                "$and": [
                                                                    {"$in": ["$$status", presence_statuses]},
                                                                    {"$ne": ["$$hours", None]}
                                                                ]
                                                            },
                                                            "$$hours",
                                                            0
                                                        ]
                                                    }
                                                ]
                                            },
                                        }
                                    }
                                }
                            }
                        }
                    }
                }
            }
        },

        # Aggregate employee statistics by department.
        {
            "$group": {
                "_id": "$department",
                "headcount": {"$sum": 1},
                "present_days": {
                    "$sum": "$employee_stats.present_days"
                },
                "leave_count": {
                    "$sum": "$employee_stats.leave_count"
                },
                "on_duty_count":{
                    "$sum": "$employee_stats.on_duty_count"
                },
                "late_count": {
                    "$sum": "$employee_stats.late_count"
                },
                "total_late_minutes": {
                    "$sum": "$employee_stats.total_late_minutes"
                },
                "total_overtime_minutes": {
                    "$sum": "$employee_stats.total_overtime_minutes"
                },
                "work_hours_total": {
                    "$sum": "$employee_stats.work_hours_total"
                },
                "work_hours_count": {
                    "$sum": "$employee_stats.work_hours_count"
                }
            }
        },

        # Format the final response.
        {
            "$project": {
                "_id": 0,
                "department": "$_id",
                "headcount": 1,
                "present_days": 1,
                "leave_count": 1,
                "on duty_count":1,
                "late_count": 1,
                "total_late_minutes": 1,
                "avg_work_hours": {
                    "$cond": [
                        {"$gt": ["$work_hours_count", 0]},
                        {
                            "$round": [
                                {
                                    "$divide": [
                                        "$work_hours_total",
                                        "$work_hours_count"
                                    ]
                                },
                                2
                            ]
                        },
                        None
                    ]
                }
            }
        },

        {"$sort": {"department": 1}}
    ]

    departments = list(db.employees.aggregate(pipeline))

    return {
        "month": month,
        "items": departments
    }



@app.get("/analytics/leaderboard/late")
def late_leaderboard(
    month: str = Query(
        ...,
        pattern=r"^\d{4}-(0[1-9]|1[0-2])$",
        description="Month in YYYY-MM format"
    ),
    limit: int = Query(10, ge=1, le=50),
    department: str | None = Query(None)
):
    # Validate month
    try:
        month_date = datetime.strptime(month, "%Y-%m")
        if month_date.strftime("%Y-%m") != month:
            raise ValueError
    except ValueError:
        raise HTTPException(
            status_code=422,
            detail="month must be in YYYY-MM format"
        )

    year = month_date.year
    month_num = month_date.month
    last_day = calendar.monthrange(year, month_num)[1]

    first_date = f"{year:04d}-{month_num:02d}-01"
    last_date = f"{year:04d}-{month_num:02d}-{last_day:02d}"

    pipeline = [
        # Match attendance records for the requested month.
        {
            "$match": {
                "date": {
                    "$gte": first_date,
                    "$lte": last_date
                },
                "late_minutes": {"$gt": 0}
            }
        },

        # Calculate late count and total late minutes per employee.
        {
            "$group": {
                "_id": "$emp_code",
                "late_count": {"$sum": 1},
                "total_late_minutes": {"$sum": "$late_minutes"}
            }
        },

        # Join employee information.
        {
            "$lookup": {
                "from": "employees",
                "localField": "_id",
                "foreignField": "emp_code",
                "as": "employee"
            }
        },
        {"$unwind": "$employee"},

        # Apply the optional department filter before ranking.
        *(
            [{"$match": {"employee.department": department}}]
            if department
            else []
        ),

        # Competition ranking: 1, 2, 2, 4.
        {
            "$setWindowFields": {
                "sortBy": {"total_late_minutes": -1},
                "output": {
                    "rank": {"$rank": {}}
                }
            }
        },

        # Keep all employees whose rank is within the limit.
        {"$match": {"rank": {"$lte": limit}}},

        # Return the required fields in a stable order.
        {
            "$project": {
                "_id": 0,
                "emp_code": "$_id",
                "name": "$employee.name",
                "department": "$employee.department",
                "late_count": 1,
                "total_late_minutes": 1,
                "rank": 1
            }
        },
        {"$sort": {"rank": 1, "emp_code": 1}}
    ]

    results = list(db.attendance_logs.aggregate(pipeline))

    return {
        "month": month,
        "items": results
    }


@app.get("/analytics/departments/{department}/trend")
def department_attendance_trend(
    department: str,
    date_from: str = Query(..., alias="from"),
    date_to: str = Query(..., alias="to"),
):
    # Validate dates
    try:
        start_date = datetime.strptime(date_from, "%Y-%m-%d").date()
        end_date = datetime.strptime(date_to, "%Y-%m-%d").date()

        if (
            start_date.isoformat() != date_from
            or end_date.isoformat() != date_to
        ):
            raise ValueError

    except ValueError:
        raise HTTPException(
            status_code=422,
            detail="Dates must use YYYY-MM-DD format",
        )

    if start_date > end_date:
        raise HTTPException(
            status_code=422,
            detail="'from' cannot be after 'to'",
        )

    if (end_date - start_date).days + 1 > 92:
        raise HTTPException(
            status_code=422,
            detail="Date range cannot exceed 92 days",
        )

    # Verify that the department exists.
    if not db.employees.find_one({"department": department}):
        raise HTTPException(
            status_code=404,
            detail="Department not found",
        )

    # Create a seed document for every requested date.
    seeds = []
    current_date = start_date

    while current_date <= end_date:
        seeds.append({
            "day": datetime.combine(
                current_date,
                datetime.min.time(),
            )
        })
        current_date += timedelta(days=1)

    pipeline = [
        {"$documents": seeds},

        {
            "$set": {
                "day_string": {
                    "$dateToString": {
                        "format": "%Y-%m-%d",
                        "date": "$day",
                        "timezone": "UTC",
                    }
                },
                "day_of_week": {"$dayOfWeek": "$day"},
            }
        },

        # Find employees who have joined by each date.
        {
            "$lookup": {
                "from": "employees",
                "let": {"selected_day": "$day_string"},
                "pipeline": [
                    {
                        "$match": {
                            "department": department,
                            "$expr": {
                                "$lte": [
                                    "$joined_on",
                                    "$$selected_day",
                                ]
                            },
                        }
                    },
                    {"$project": {"_id": 0, "emp_code": 1}},
                ],
                "as": "eligible_employees",
            }
        },

        # Aggregate daily attendance for eligible employees.
        {
            "$lookup": {
                "from": "attendance_logs",
                "let": {
                    "selected_day": "$day_string",
                    "employee_codes": "$eligible_employees.emp_code",
                },
                "pipeline": [
                    {
                        "$match": {
                            "$expr": {
                                "$and": [
                                    {"$eq": ["$date", "$$selected_day"]},
                                    {
                                        "$in": [
                                            "$emp_code",
                                            "$$employee_codes",
                                        ]
                                    },
                                ]
                            }
                        }
                    },
                    {
                        "$group": {
                            "_id": None,
                            "present_count": {
                                "$sum": {
                                    "$cond": [
                                        {
                                            "$in": [
                                                "$status",
                                                ["PRESENT", "WFH", "ON_DUTY"],
                                            ]
                                        },
                                        {
                                            "$cond": [
                                                {"$eq": ["$half_day", True]},
                                                0.5,
                                                1.0,
                                            ]
                                        },
                                        0.0,
                                    ]
                                }
                            },
                            "late_count": {
                                "$sum": {
                                    "$cond": [
                                        {
                                            "$gt": [
                                                {"$ifNull": ["$late_minutes", 0]},
                                                0,
                                            ]
                                        },
                                        1,
                                        0,
                                    ]
                                }
                            },
                        }
                    },
                ],
                "as": "daily_attendance",
            }
        },

        # Set headcount and daily metrics.
        {
            "$set": {
                "headcount": {"$size": "$eligible_employees"},
                "present_count": {
                    "$ifNull": [
                        {
                            "$arrayElemAt": [
                                "$daily_attendance.present_count",
                                0,
                            ]
                        },
                        0.0,
                    ]
                },
                "late_count": {
                    "$ifNull": [
                        {
                            "$arrayElemAt": [
                                "$daily_attendance.late_count",
                                0,
                            ]
                        },
                        0,
                    ]
                },
                "is_working_day": {
                    "$and": [
                        {"$gte": ["$day_of_week", 2]},
                        {"$lte": ["$day_of_week", 6]},
                    ]
                },
            }
        },

        # Attendance rate is a ratio, not a percentage.
        # It is null on weekends or when headcount is zero.
        {
            "$set": {
                "attendance_rate": {
                    "$cond": [
                        {
                            "$and": [
                                "$is_working_day",
                                {"$gt": ["$headcount", 0]},
                            ]
                        },
                        {
                            "$round": [
                                {
                                    "$divide": [
                                        "$present_count",
                                        "$headcount",
                                    ]
                                },
                                4,
                            ]
                        },
                        None,
                    ]
                }
            }
        },

        # Average non-null rates across this day and up to 6 previous
        # dates within the requested range.
        {
            "$setWindowFields": {
                "sortBy": {"day": 1},
                "output": {
                    "moving_avg_7d": {
                        "$avg": "$attendance_rate",
                        "window": {"documents": [-6, 0]},
                    }
                },
            }
        },

        {
            "$project": {
                "_id": 0,
                "date": "$day_string",
                "is_working_day": 1,
                "headcount": 1,
                "present_count": 1,
                "late_count": 1,
                "attendance_rate": 1,
                "moving_avg_7d": {
                    "$cond": [
                        {"$ne": ["$moving_avg_7d", None]},
                        {"$round": ["$moving_avg_7d", 4]},
                        None,
                    ]
                },
            }
        },

        {"$sort": {"date": 1}},
    ]

    try:
        result = db.command({
            "aggregate": 1,
            "pipeline": pipeline,
            "cursor": {},
        })
        series = result["cursor"]["firstBatch"]

    except Exception:
        raise HTTPException(
            status_code=500,
            detail="Department trend aggregation failed",
        )

    return {
        "department": department,
        "items": series,
    }




@app.get("/admin/explain/{endpoint}")
def admin_explain(
    endpoint: str,
    emp_code: str | None = None,
    month: str | None = None,
    department: str | None = None,
    limit: int = Query(10, ge=1, le=50),
    date_from: str | None = None,
    date_to: str | None = None,
    status: str | None = None,
    from_: str | None = Query(None, alias="from"),
    to: str | None = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
):
    allowed_endpoints = {
        "attendance_list",
        "employee_monthly",
        "department_summary",
        "late_leaderboard",
        "department_trend",
    }

    if endpoint not in allowed_endpoints:
        raise HTTPException(
            status_code=404,
            detail="Unknown endpoint name",
        )

    def explain_find(collection, filter_query, sort_query=None):
        operation = {
            "find": collection,
            "filter": filter_query,
            "skip": (page - 1) * page_size,
            "limit": page_size,
        }

        if sort_query:
            operation["sort"] = sort_query

        result = db.command({
            "explain": operation,
            "verbosity": "executionStats",
        })

        return {
            "endpoint": endpoint,
            "collection": collection,
            "explain": result,
        }

    def explain_aggregate(collection, pipeline):
        result = db.command({
            "explain": {
                "aggregate": collection,
                "pipeline": pipeline,
                "cursor": {},
            },
            "verbosity": "executionStats",
        })

        return {
            "endpoint": endpoint,
            "collection": collection,
            "explain": result,
        }

    # 1. Attendance listing
    if endpoint == "attendance_list":
        filters = {}

        if emp_code:
            filters["emp_code"] = emp_code

        if date_from or date_to:
            filters["date"] = {}

            if date_from:
                filters["date"]["$gte"] = date_from

            if date_to:
                filters["date"]["$lte"] = date_to

        if status:
            filters["status"] = status

        return explain_find(
            "attendance_logs",
            filters,
            {"date": -1, "emp_code": 1},
        )

    # 2. Employee monthly analytics
    elif endpoint == "employee_monthly":
        if not emp_code or not month:
            raise HTTPException(
                status_code=422,
                detail="emp_code and month are required",
            )

        try:
            month_start = datetime.strptime(
                month, "%Y-%m"
            ).date()

            if month_start.strftime("%Y-%m") != month:
                raise ValueError

        except ValueError:
            raise HTTPException(
                status_code=422,
                detail="month must be in YYYY-MM format",
            )

        month_end = (
            month_start.replace(day=28) + timedelta(days=4)
        ).replace(day=1) - timedelta(days=1)

        return explain_find(
            "attendance_logs",
            {
                "emp_code": emp_code,
                "date": {
                    "$gte": month_start.isoformat(),
                    "$lte": month_end.isoformat(),
                },
            },
            {"date": 1},
        )

    # 3. Department summary
    elif endpoint == "department_summary":
        if not month:
            raise HTTPException(
                status_code=422,
                detail="month is required",
            )

        try:
            month_start = datetime.strptime(
                month, "%Y-%m"
            ).date()

            if month_start.strftime("%Y-%m") != month:
                raise ValueError

        except ValueError:
            raise HTTPException(
                status_code=422,
                detail="month must be in YYYY-MM format",
            )

        month_end = (
            month_start.replace(day=28) + timedelta(days=4)
        ).replace(day=1) - timedelta(days=1)

        employee_filter = {
            "joined_on": {"$lte": month_end.isoformat()}
        }

        if department:
            employee_filter["department"] = department

        pipeline = [
            {"$match": employee_filter},
            {
                "$lookup": {
                    "from": "attendance_logs",
                    "let": {"code": "$emp_code"},
                    "pipeline": [
                        {
                            "$match": {
                                "$expr": {
                                    "$and": [
                                        {
                                            "$eq": [
                                                "$emp_code",
                                                "$$code",
                                            ]
                                        },
                                        {
                                            "$gte": [
                                                "$date",
                                                month_start.isoformat(),
                                            ]
                                        },
                                        {
                                            "$lte": [
                                                "$date",
                                                month_end.isoformat(),
                                            ]
                                        },
                                    ]
                                }
                            }
                        }
                    ],
                    "as": "monthly_logs",
                }
            },
        ]

        return explain_aggregate("employees", pipeline)

    # 4. Late leaderboard
    elif endpoint == "late_leaderboard":
        if not month:
            raise HTTPException(
                status_code=422,
                detail="month is required",
            )

        try:
            month_start = datetime.strptime(
                month, "%Y-%m"
            ).date()

            if month_start.strftime("%Y-%m") != month:
                raise ValueError

        except ValueError:
            raise HTTPException(
                status_code=422,
                detail="month must be in YYYY-MM format",
            )

        month_end = (
            month_start.replace(day=28) + timedelta(days=4)
        ).replace(day=1) - timedelta(days=1)

        pipeline = [
            {
                "$match": {
                    "date": {
                        "$gte": month_start.isoformat(),
                        "$lte": month_end.isoformat(),
                    },
                    "late_minutes": {"$gt": 0},
                }
            },
            {
                "$group": {
                    "_id": "$emp_code",
                    "total_late_minutes": {
                        "$sum": "$late_minutes"
                    },
                }
            },
            {"$sort": {"total_late_minutes": -1}},
            {"$limit": limit},
        ]

        if department:
            pipeline.insert(
                2,
                {
                    "$lookup": {
                        "from": "employees",
                        "localField": "_id",
                        "foreignField": "emp_code",
                        "as": "employee",
                    }
                },
            )
            pipeline.insert(3, {"$unwind": "$employee"})
            pipeline.insert(
                4,
                {"$match": {"employee.department": department}},
            )

        return explain_aggregate("attendance_logs", pipeline)

    # 5. Department trend
    elif endpoint == "department_trend":
        if not department or not from_ or not to:
            raise HTTPException(
                status_code=422,
                detail="department, from, and to are required",
            )

        try:
            start_date = datetime.strptime(
                from_, "%Y-%m-%d"
            ).date()
            end_date = datetime.strptime(
                to, "%Y-%m-%d"
            ).date()

            if (
                start_date.isoformat() != from_
                or end_date.isoformat() != to
                or end_date < start_date
                or (end_date - start_date).days + 1 > 92
            ):
                raise ValueError

        except ValueError:
            raise HTTPException(
                status_code=422,
                detail="Invalid date range; use YYYY-MM-DD and a maximum of 92 days",
            )

        pipeline = [
            {
                "$match": {
                    "date": {
                        "$gte": from_,
                        "$lte": to,
                    }
                }
            },
            {
                "$lookup": {
                    "from": "employees",
                    "localField": "emp_code",
                    "foreignField": "emp_code",
                    "as": "employee",
                }
            },
            {"$unwind": "$employee"},
            {"$match": {"employee.department": department}},
        ]

        return explain_aggregate("attendance_logs", pipeline)

