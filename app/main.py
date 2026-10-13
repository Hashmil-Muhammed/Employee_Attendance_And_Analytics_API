"""
Employee Attendance & Analytics API
"""
import os
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from decimal import ROUND_HALF_UP, Decimal
from typing import Optional

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request, Query
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, field_validator
from pymongo import ASCENDING, DESCENDING, MongoClient
from pymongo.errors import DuplicateKeyError

load_dotenv()

IST = timezone(timedelta(hours=5, minutes=30))

client = MongoClient(os.getenv("MONGO_URI", "mongodb://localhost:27017"), tz_aware=True)
db = client[os.getenv("MONGO_DB", "attendance_db")]


@asynccontextmanager
async def lifespan(app: FastAPI):
    db.employees.create_index([("emp_code", ASCENDING)], unique=True)
    db.employees.create_index([("department", ASCENDING), ("joined_on", ASCENDING)])
    db.employees.create_index([("joined_on", ASCENDING)])
    db.attendance_logs.create_index([("emp_code", ASCENDING), ("date", ASCENDING)], unique=True)
    db.attendance_logs.create_index([("date", DESCENDING), ("emp_code", ASCENDING)])
    yield


app = FastAPI(title="Employee Attendance & Analytics API", version="2.0.0", lifespan=lifespan)

@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    return JSONResponse(status_code=422, content={"detail": jsonable_encoder(exc.errors())})


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def now_utc_truncated() -> datetime:
    n = datetime.now(timezone.utc)
    return n.replace(microsecond=0)

def truncate_ms(ms: int) -> datetime:
    if not isinstance(ms, int):
        raise ValueError("Must be integer")
    if ms < 100000000000 or ms > 4102444800000:
        raise ValueError("Must be epoch milliseconds")
    return datetime.fromtimestamp(ms // 1000, tz=timezone.utc)

def to_epoch_ms(dt: Optional[datetime]) -> Optional[int]:
    if not dt:
        return None
    if isinstance(dt, (int, float)):
        return int(dt)
    if hasattr(dt, 'tzinfo') and dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return int(dt.timestamp() * 1000)

def compute_late_minutes(punch_in_utc: datetime, shift_start: str, attendance_date_str: Optional[str] = None, shift_end: Optional[str] = None) -> int:
    h, m = map(int, shift_start.split(":"))
    punch_in_ist = punch_in_utc.astimezone(IST)
    if attendance_date_str:
        att_date = datetime.fromisoformat(attendance_date_str).date()
    elif shift_end and shift_end <= shift_start:
        att_date_str = get_attendance_date(punch_in_utc, shift_start, shift_end)
        att_date = datetime.fromisoformat(att_date_str).date()
    else:
        att_date = punch_in_ist.date()
    shift_start_ist = datetime.combine(att_date, datetime.min.time(), tzinfo=IST).replace(hour=h, minute=m, second=0, microsecond=0)

    delta_seconds = (punch_in_ist - shift_start_ist).total_seconds()
    if delta_seconds > 600:
        return int(delta_seconds // 60)
    return 0

def get_attendance_date(punch_in_utc: datetime, shift_start: str, shift_end: str) -> str:
    punch_in_ist = punch_in_utc.astimezone(IST)
    date_ist = punch_in_ist.date()

    if shift_end <= shift_start:
        h_end, m_end = map(int, shift_end.split(":"))
        shift_end_time = punch_in_ist.replace(hour=h_end, minute=m_end, second=0, microsecond=0)
        if punch_in_ist < shift_end_time:
            date_ist -= timedelta(days=1)

    return date_ist.isoformat()

def compute_work_hours(punch_in_utc: datetime, punch_out_utc: datetime) -> float:
    seconds = (punch_out_utc - punch_in_utc).total_seconds()
    val = Decimal(str(seconds)) / Decimal("3600")
    return float(val.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))

def compute_overtime(punch_out_utc: datetime, shift_end: str, attendance_date_str: str, shift_start: str) -> int:
    h, m = map(int, shift_end.split(":"))
    att_date = datetime.fromisoformat(attendance_date_str).date()

    if shift_end <= shift_start:
        att_date += timedelta(days=1)

    end_ist = datetime.combine(att_date, datetime.min.time(), tzinfo=IST).replace(hour=h, minute=m)
    punch_out_ist = punch_out_utc.astimezone(IST)

    minutes = int((punch_out_ist - end_ist).total_seconds() // 60)
    return minutes if minutes >= 30 else 0

def compute_half_day(work_hours: float) -> bool:
    return work_hours < 4.50

def format_employee(doc: dict) -> dict:
    doc.pop("_id", None)
    doc["created_at"] = to_epoch_ms(doc.get("created_at"))
    return doc

def format_attendance(doc: dict) -> dict:
    doc.pop("_id", None)
    doc["punch_in"] = to_epoch_ms(doc.get("punch_in"))
    doc["punch_out"] = to_epoch_ms(doc.get("punch_out"))

    if "history" in doc:
        for entry in doc["history"]:
            entry["at"] = to_epoch_ms(entry.get("at"))
            if "changes" in entry:
                c = entry["changes"]
                if "punch_in" in c:
                    c["punch_in"]["from"] = to_epoch_ms(c["punch_in"].get("from"))
                    c["punch_in"]["to"] = to_epoch_ms(c["punch_in"].get("to"))
                if "punch_out" in c:
                    c["punch_out"]["from"] = to_epoch_ms(c["punch_out"].get("from"))
                    c["punch_out"]["to"] = to_epoch_ms(c["punch_out"].get("to"))
    else:
        doc["history"] = []

    if "half_day" not in doc:
        doc["half_day"] = False

    if "late_minutes" not in doc:
        doc["late_minutes"] = 0

    if "overtime_minutes" not in doc:
        doc["overtime_minutes"] = 0

    return doc

# --------------------------------------------------------------------------- #
# Models
# --------------------------------------------------------------------------- #
class EmployeeIn(BaseModel):
    emp_code: str = Field(pattern=r'^EMP\d{4,6}$')
    name: str = Field(min_length=1, max_length=100)
    email: str = Field(max_length=120, pattern=r'^[^@\s]+@[^@\s]+\.[^@\s]+$')
    department: str = Field(min_length=1, max_length=50)
    shift_start: str = Field(default="09:30", pattern=r'^([01]\d|2[0-3]):[0-5]\d$')
    shift_end: str = Field(default="18:30", pattern=r'^([01]\d|2[0-3]):[0-5]\d$')
    joined_on: str = Field(pattern=r'^\d{4}-(0[1-9]|1[0-2])-(0[1-9]|[12]\d|3[01])$')

    @field_validator('shift_end')
    def shift_times_must_differ(cls, v, info):
        if 'shift_start' in info.data and v == info.data['shift_start']:
            raise ValueError('shift_start must differ from shift_end')
        return v

class PunchInIn(BaseModel):
    emp_code: str
    punched_at: Optional[int] = None
    status: str = "PRESENT"

    @field_validator('punched_at', mode='before')
    def validate_punched_at(cls, v):
        if v is not None:
            if isinstance(v, bool) or not isinstance(v, int):
                raise ValueError("Must be integer epoch milliseconds")
            if v < 100000000000 or v > 4102444800000:
                raise ValueError("Must be epoch milliseconds")
        return v

class PunchOutIn(BaseModel):
    emp_code: str
    punched_at: Optional[int] = None

    @field_validator('punched_at', mode='before')
    def validate_punched_at(cls, v):
        if v is not None:
            if isinstance(v, bool) or not isinstance(v, int):
                raise ValueError("Must be integer epoch milliseconds")
            if v < 100000000000 or v > 4102444800000:
                raise ValueError("Must be epoch milliseconds")
        return v

class RegularizeIn(BaseModel):
    status: Optional[str] = Field(None, pattern=r'^(PRESENT|ABSENT|LEAVE|WFH|ON_DUTY)$')
    punch_in: Optional[int] = None
    punch_out: Optional[int] = None
    reason: str = Field(min_length=5, max_length=200)
    regularized_by: str = Field(min_length=1, max_length=50)

    @field_validator('punch_in', 'punch_out', mode='before')
    def validate_epoch_ms(cls, v):
        if v is not None:
            if isinstance(v, bool) or not isinstance(v, int):
                raise ValueError("Must be integer epoch milliseconds")
            if v < 100000000000 or v > 4102444800000:
                raise ValueError("Must be epoch milliseconds")
        return v

# --------------------------------------------------------------------------- #
# Endpoints
# --------------------------------------------------------------------------- #
@app.get("/")
def root():
    return {"message": "HROne Employee Attendance & Analytics API", "docs": "/docs", "health": "/health"}

@app.get("/health")
def health():
    try:
        db.command("ping")
        return {"status": "ok"}
    except Exception:
        raise HTTPException(status_code=503, detail="Database not ready")

@app.post("/employees", status_code=201)
def create_employee(body: EmployeeIn):
    doc = body.model_dump()
    doc["created_at"] = now_utc_truncated()
    try:
        db.employees.insert_one(doc)
    except DuplicateKeyError:
        raise HTTPException(409, "emp_code already exists")

    return format_employee(doc)

@app.get("/employees")
def list_employees(
    department: Optional[str] = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100)
):
    page_val = page if isinstance(page, int) else 1
    page_size_val = page_size if isinstance(page_size, int) else 20
    q = {}
    if department:
        q["department"] = department
    skip = (page_val - 1) * page_size_val
    total = db.employees.count_documents(q)
    cursor = db.employees.find(q).sort("emp_code", ASCENDING).skip(skip).limit(page_size_val)
    items = [format_employee(d) for d in cursor]
    return {"items": items, "total": total, "page": page_val, "page_size": page_size_val}

@app.post("/attendance/punch-in", status_code=201)
def punch_in(body: PunchInIn):
    if body.status not in ("PRESENT", "WFH", "ON_DUTY"):
        raise HTTPException(422, "Invalid status for punch-in")

    emp = db.employees.find_one({"emp_code": body.emp_code})
    if not emp:
        raise HTTPException(404, "unknown employee")

    ts = truncate_ms(body.punched_at) if body.punched_at else now_utc_truncated()
    d = get_attendance_date(ts, emp["shift_start"], emp["shift_end"])

    doc = {
        "emp_code": body.emp_code,
        "date": d,
        "status": body.status,
        "punch_in": ts,
        "punch_out": None,
        "work_hours": None,
        "late_minutes": compute_late_minutes(ts, emp["shift_start"], attendance_date_str=d, shift_end=emp["shift_end"]),
        "overtime_minutes": 0,
        "half_day": False,
        "history": [],
    }

    try:
        db.attendance_logs.insert_one(doc)
    except DuplicateKeyError:
        raise HTTPException(409, "already punched in for this date")

    return format_attendance(doc)

@app.post("/attendance/punch-out")
def punch_out(body: PunchOutIn):
    emp = db.employees.find_one({"emp_code": body.emp_code})
    if not emp:
        raise HTTPException(404, "unknown employee")

    ts = truncate_ms(body.punched_at) if body.punched_at else now_utc_truncated()

    record = db.attendance_logs.find_one(
        {"emp_code": body.emp_code, "punch_in": {"$lte": ts}},
        sort=[("punch_in", DESCENDING)]
    )

    if not record:
        raise HTTPException(404, "no punch-in found")

    if record.get("punch_out") is not None:
        raise HTTPException(409, "already punched out")

    punch_in_ts = record["punch_in"]
    if ts <= punch_in_ts or (ts - punch_in_ts).total_seconds() > 86400:
        raise HTTPException(422, "punched_at must be after punch_in and within 24 hours")

    work_hours = compute_work_hours(punch_in_ts, ts)
    overtime_mins = compute_overtime(ts, emp["shift_end"], record["date"], emp["shift_start"])
    half_day = compute_half_day(work_hours)

    res = db.attendance_logs.find_one_and_update(
        {"_id": record["_id"], "punch_out": None},
        {
            "$set": {
                "punch_out": ts,
                "work_hours": work_hours,
                "overtime_minutes": overtime_mins,
                "half_day": half_day
            }
        },
        return_document=True
    )

    if not res:
        raise HTTPException(409, "already punched out")

    return format_attendance(res)

@app.get("/attendance")
def list_attendance(
    emp_code: Optional[str] = None,
    date_from: Optional[str] = Query(None, pattern=r'^\d{4}-(0[1-9]|1[0-2])-(0[1-9]|[12]\d|3[01])$'),
    date_to: Optional[str] = Query(None, pattern=r'^\d{4}-(0[1-9]|1[0-2])-(0[1-9]|[12]\d|3[01])$'),
    status: Optional[str] = Query(None, pattern=r'^(PRESENT|ABSENT|LEAVE|WFH|ON_DUTY)$'),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
):
    if date_from and date_to and date_from > date_to:
        raise HTTPException(422, "date_from > date_to")

    q = {}
    if emp_code:
        q["emp_code"] = emp_code
    if date_from or date_to:
        q["date"] = {}
        if date_from:
            q["date"]["$gte"] = date_from
        if date_to:
            q["date"]["$lte"] = date_to
    if status:
        q["status"] = status

    page_val = page if isinstance(page, int) else 1
    page_size_val = page_size if isinstance(page_size, int) else 20
    skip = (page_val - 1) * page_size_val
    total = db.attendance_logs.count_documents(q)
    cursor = db.attendance_logs.find(q).sort([("date", DESCENDING), ("emp_code", ASCENDING)]).skip(skip).limit(page_size_val)
    items = [format_attendance(d) for d in cursor]

    return {"items": items, "total": total, "page": page_val, "page_size": page_size_val}

@app.patch("/attendance/{emp_code}/{date}")
def regularize_attendance(emp_code: str, date: str, body: RegularizeIn):
    emp = db.employees.find_one({"emp_code": emp_code})
    if not emp:
        raise HTTPException(404, "unknown employee")

    record = db.attendance_logs.find_one({"emp_code": emp_code, "date": date})
    if not record:
        raise HTTPException(404, "no record found")

    new_status = body.status if body.status else record.get("status")

    if new_status in ("ABSENT", "LEAVE"):
        if body.punch_in is not None or body.punch_out is not None:
            raise HTTPException(422, "punch_in/out not allowed for ABSENT/LEAVE")
        new_punch_in = None
        new_punch_out = None
    else:
        new_punch_in = truncate_ms(body.punch_in) if body.punch_in else record.get("punch_in")
        if not new_punch_in:
            raise HTTPException(422, "presence status requires punch_in")

        expected_date = get_attendance_date(new_punch_in, emp["shift_start"], emp["shift_end"])
        if expected_date != record["date"]:
            raise HTTPException(422, "punch_in must stay on the record's attendance date")

        new_punch_out = truncate_ms(body.punch_out) if body.punch_out else record.get("punch_out")

        if new_punch_out:
            if new_punch_out <= new_punch_in or (new_punch_out - new_punch_in).total_seconds() > 86400:
                raise HTTPException(422, "punch_out must be after punch_in and within 24 hours")

    changes = {}
    if new_status != record.get("status"):
        changes["status"] = {"from": record.get("status"), "to": new_status}

    if new_punch_in != record.get("punch_in"):
        changes["punch_in"] = {"from": record.get("punch_in"), "to": new_punch_in}

    if new_punch_out != record.get("punch_out"):
        changes["punch_out"] = {"from": record.get("punch_out"), "to": new_punch_out}

    new_late = 0
    new_overtime = 0
    new_work_hours = None
    new_half_day = False

    if new_status not in ("ABSENT", "LEAVE") and new_punch_in:
        new_late = compute_late_minutes(new_punch_in, emp["shift_start"], attendance_date_str=record["date"], shift_end=emp["shift_end"])
        if new_punch_out:
            new_work_hours = compute_work_hours(new_punch_in, new_punch_out)
            new_overtime = compute_overtime(new_punch_out, emp["shift_end"], record["date"], emp["shift_start"])
            new_half_day = compute_half_day(new_work_hours)

    if new_work_hours != record.get("work_hours"):
        changes["work_hours"] = {"from": record.get("work_hours"), "to": new_work_hours}
    if new_late != record.get("late_minutes", 0):
        changes["late_minutes"] = {"from": record.get("late_minutes", 0), "to": new_late}
    if new_overtime != record.get("overtime_minutes", 0):
        changes["overtime_minutes"] = {"from": record.get("overtime_minutes", 0), "to": new_overtime}
    if new_half_day != record.get("half_day", False):
        changes["half_day"] = {"from": record.get("half_day", False), "to": new_half_day}

    if not changes:
        raise HTTPException(422, "request changes nothing")

    history_entry = {
        "at": now_utc_truncated(),
        "by": body.regularized_by,
        "reason": body.reason,
        "changes": changes
    }

    update = {
        "$set": {
            "status": new_status,
            "punch_in": new_punch_in,
            "punch_out": new_punch_out,
            "work_hours": new_work_hours,
            "late_minutes": new_late,
            "overtime_minutes": new_overtime,
            "half_day": new_half_day
        },
        "$push": {"history": history_entry}
    }

    res = db.attendance_logs.find_one_and_update(
        {"_id": record["_id"]},
        update,
        return_document=True
    )

    return format_attendance(res)



# --------------------------------------------------------------------------- #
# Analytics Endpoints
# --------------------------------------------------------------------------- #
from fastapi import Query

def get_working_days(month: str, joined_on: str) -> int:
    y, m = map(int, month.split("-"))
    start_date = datetime(y, m, 1).date()
    if m == 12:
        end_date = datetime(y+1, 1, 1).date() - timedelta(days=1)
    else:
        end_date = datetime(y, m+1, 1).date() - timedelta(days=1)

    join_date = datetime.fromisoformat(joined_on).date()
    actual_start = max(start_date, join_date)
    if actual_start > end_date:
        return 0

    count = 0
    curr = actual_start
    while curr <= end_date:
        if curr.weekday() < 5:
            count += 1
        curr += timedelta(days=1)
    return count

def get_month_end_str(month: str) -> str:
    y, m = map(int, month.split("-"))
    if m == 12:
        end_date = datetime(y+1, 1, 1).date() - timedelta(days=1)
    else:
        end_date = datetime(y, m+1, 1).date() - timedelta(days=1)
    return end_date.isoformat()

def build_employee_monthly_pipeline(emp_code: str, month: str):
    return [
        {
            "$match": {
                "emp_code": emp_code,
                "date": {"$regex": f"^{month}"}
            }
        },
        {
            "$project": {
                "is_weekday": {
                    "$in": [{"$dayOfWeek": {"$dateFromString": {"dateString": "$date"}}}, [2, 3, 4, 5, 6]]
                },
                "status": 1,
                "late_minutes": 1,
                "overtime_minutes": 1,
                "half_day": 1
            }
        },
        {
            "$group": {
                "_id": None,
                "present_days": {
                    "$sum": {
                        "$cond": [
                            {"$and": [
                                {"$eq": ["$is_weekday", True]},
                                {"$in": ["$status", ["PRESENT", "WFH", "ON_DUTY"]]}
                            ]},
                            {"$cond": [{"$eq": ["$half_day", True]}, 0.5, 1.0]},
                            0
                        ]
                    }
                },
                "leave_days": {
                    "$sum": {"$cond": [{"$eq": ["$status", "LEAVE"]}, 1, 0]}
                },
                "late_count": {
                    "$sum": {"$cond": [{"$gt": ["$late_minutes", 0]}, 1, 0]}
                },
                "total_late_minutes": {
                    "$sum": "$late_minutes"
                },
                "total_overtime_minutes": {
                    "$sum": "$overtime_minutes"
                }
            }
        }
    ]

@app.get("/analytics/employees/{emp_code}/monthly")
def employee_monthly(emp_code: str, month: str = Query(pattern=r'^\d{4}-(0[1-9]|1[0-2])$')):
    emp = db.employees.find_one({"emp_code": emp_code})
    if not emp:
        raise HTTPException(404, "unknown employee")

    wd = get_working_days(month, emp["joined_on"])
    pipeline = build_employee_monthly_pipeline(emp_code, month)
    res = list(db.attendance_logs.aggregate(pipeline))

    if not res:
        agg = {
            "present_days": 0.0,
            "leave_days": 0,
            "late_count": 0,
            "total_late_minutes": 0,
            "total_overtime_minutes": 0
        }
    else:
        agg = res[0]

    att_pct = None
    if wd > 0:
        val = Decimal(str(agg["present_days"])) / Decimal(str(wd)) * Decimal("100")
        att_pct = float(val.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))

    return {
        "emp_code": emp_code,
        "month": month,
        "working_days": wd,
        "present_days": float(agg["present_days"]),
        "leave_days": agg["leave_days"],
        "late_count": agg["late_count"],
        "total_late_minutes": agg.get("total_late_minutes", 0),
        "total_overtime_minutes": agg.get("total_overtime_minutes", 0),
        "attendance_pct": att_pct
    }

def build_department_summary_pipeline(month: str, department: Optional[str] = None):
    end_date = get_month_end_str(month)
    match_stage = {"joined_on": {"$lte": end_date}}
    if department:
        match_stage["department"] = department

    return [
        {
            "$match": match_stage
        },
        {
            "$lookup": {
                "from": "attendance_logs",
                "let": {"emp": "$emp_code"},
                "pipeline": [
                    {
                        "$match": {
                            "$expr": {"$eq": ["$emp_code", "$$emp"]},
                            "date": {"$regex": f"^{month}"}
                        }
                    }
                ],
                "as": "logs"
            }
        },
        {
            "$unwind": {
                "path": "$logs",
                "preserveNullAndEmptyArrays": True
            }
        },
        {
            "$project": {
                "department": 1,
                "status": "$logs.status",
                "late_minutes": {"$ifNull": ["$logs.late_minutes", 0]},
                "work_hours": "$logs.work_hours",
                "half_day": {"$ifNull": ["$logs.half_day", False]},
                "has_log": {"$cond": [{"$gt": ["$logs.date", None]}, 1, 0]},
                "is_weekday": {
                    "$cond": [
                        {"$gt": ["$logs.date", None]},
                        {"$in": [{"$dayOfWeek": {"$dateFromString": {"dateString": "$logs.date"}}}, [2, 3, 4, 5, 6]]},
                        False
                    ]
                }
            }
        },
        {
            "$group": {
                "_id": "$department",
                "employees": {"$addToSet": "$_id"},
                "present_days": {
                    "$sum": {
                        "$cond": [
                            {"$and": [
                                {"$eq": ["$is_weekday", True]},
                                {"$in": ["$status", ["PRESENT", "WFH", "ON_DUTY"]]}
                            ]},
                            {"$cond": [{"$eq": ["$half_day", True]}, 0.5, 1.0]},
                            0
                        ]
                    }
                },
                "work_hours_sum": {
                    "$sum": {
                        "$cond": [
                            {"$and": [
                                {"$in": ["$status", ["PRESENT", "WFH", "ON_DUTY"]]},
                                {"$ne": ["$work_hours", None]}
                            ]},
                            "$work_hours",
                            0
                        ]
                    }
                },
                "work_hours_count": {
                    "$sum": {
                        "$cond": [
                            {"$and": [
                                {"$in": ["$status", ["PRESENT", "WFH", "ON_DUTY"]]},
                                {"$ne": ["$work_hours", None]}
                            ]},
                            1,
                            0
                        ]
                    }
                },
                "late_count": {
                    "$sum": {"$cond": [{"$gt": ["$late_minutes", 0]}, 1, 0]}
                },
                "total_late_minutes": {
                    "$sum": "$late_minutes"
                },
                "leave_count": {
                    "$sum": {"$cond": [{"$eq": ["$status", "LEAVE"]}, 1, 0]}
                },
                "on_duty_count": {
                    "$sum": {"$cond": [{"$eq": ["$status", "ON_DUTY"]}, 1, 0]}
                }
            }
        },
        {
            "$sort": {"_id": 1}
        }
    ]

@app.get("/analytics/departments/summary")
def department_summary(month: str = Query(pattern=r'^\d{4}-(0[1-9]|1[0-2])$'), department: Optional[str] = None):
    pipeline = build_department_summary_pipeline(month, department)
    cursor = db.employees.aggregate(pipeline)

    items = []
    for doc in cursor:
        avg_wh = None
        if doc["work_hours_count"] > 0:
            val = Decimal(str(doc["work_hours_sum"])) / Decimal(str(doc["work_hours_count"]))
            avg_wh = float(val.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))

        items.append({
            "department": doc["_id"],
            "headcount": len(doc["employees"]),
            "present_days": float(doc["present_days"]),
            "avg_work_hours": avg_wh,
            "late_count": doc["late_count"],
            "total_late_minutes": doc.get("total_late_minutes", 0),
            "leave_count": doc["leave_count"],
            "on_duty_count": doc["on_duty_count"]
        })

    return {"month": month, "items": items}

def build_late_leaderboard_pipeline(month: str, limit: int, department: Optional[str] = None):
    pipeline = [
        {
            "$match": {
                "date": {"$regex": f"^{month}"},
                "late_minutes": {"$gt": 0}
            }
        },
        {
            "$group": {
                "_id": "$emp_code",
                "total_late_minutes": {"$sum": "$late_minutes"},
                "late_count": {"$sum": 1}
            }
        },
        {
            "$lookup": {
                "from": "employees",
                "localField": "_id",
                "foreignField": "emp_code",
                "as": "emp"
            }
        },
        {
            "$unwind": "$emp"
        }
    ]

    if department:
        pipeline.append({
            "$match": {
                "emp.department": department
            }
        })

    pipeline.extend([
        {
            "$setWindowFields": {
                "sortBy": {"total_late_minutes": -1},
                "output": {
                    "rank": {
                        "$rank": {}
                    }
                }
            }
        },
        {
            "$match": {
                "rank": {"$lte": limit}
            }
        },
        {
            "$sort": {
                "total_late_minutes": -1,
                "_id": 1
            }
        }
    ])

    return pipeline

@app.get("/analytics/leaderboard/late")
def late_leaderboard(
    month: str = Query(pattern=r'^\d{4}-(0[1-9]|1[0-2])$'),
    limit: int = Query(10, ge=1, le=50),
    department: Optional[str] = None
):
    limit_val = limit if isinstance(limit, int) else 10
    pipeline = build_late_leaderboard_pipeline(month, limit_val, department)
    cursor = db.attendance_logs.aggregate(pipeline)

    items = []
    for doc in cursor:
        items.append({
            "rank": doc["rank"],
            "emp_code": doc["_id"],
            "name": doc["emp"]["name"],
            "department": doc["emp"]["department"],
            "total_late_minutes": doc.get("total_late_minutes", 0),
            "late_count": doc["late_count"]
        })

    return {"month": month, "items": items}

def build_department_trend_pipeline(department: str, from_date: str, to_date: str):
    from_date_dt = datetime.fromisoformat(from_date).replace(tzinfo=timezone.utc)
    to_date_dt = datetime.fromisoformat(to_date).replace(tzinfo=timezone.utc) + timedelta(days=1)

    return [
        {"$match": {"department": department}},
        {"$limit": 1},
        {"$project": {"_id": 0, "date_dt": {"$literal": from_date_dt}}},
        {
            "$densify": {
                "field": "date_dt",
                "range": {
                    "step": 1,
                    "unit": "day",
                    "bounds": [from_date_dt, to_date_dt]
                }
            }
        },
        {
            "$project": {
                "date_str": {"$dateToString": {"format": "%Y-%m-%d", "date": "$date_dt"}},
                "is_working_day": {
                    "$in": [{"$dayOfWeek": "$date_dt"}, [2, 3, 4, 5, 6]]
                }
            }
        },
        {
            "$lookup": {
                "from": "employees",
                "let": {"curr_date": "$date_str"},
                "pipeline": [
                    {
                        "$match": {
                            "department": department,
                            "$expr": {"$lte": ["$joined_on", "$$curr_date"]}
                        }
                    }
                ],
                "as": "active_emps"
            }
        },
        {
            "$addFields": {
                "headcount": {"$size": "$active_emps"},
                "emp_codes": {
                    "$map": {
                        "input": "$active_emps",
                        "as": "e",
                        "in": "$$e.emp_code"
                    }
                }
            }
        },
        {
            "$lookup": {
                "from": "attendance_logs",
                "let": {"curr_date": "$date_str", "emps": "$emp_codes"},
                "pipeline": [
                    {
                        "$match": {
                            "$expr": {
                                "$and": [
                                    {"$eq": ["$date", "$$curr_date"]},
                                    {"$in": ["$emp_code", "$$emps"]}
                                ]
                            }
                        }
                    }
                ],
                "as": "logs"
            }
        },
        {
            "$addFields": {
                "present_count": {
                    "$sum": {
                        "$map": {
                            "input": "$logs",
                            "as": "l",
                            "in": {
                                "$cond": [
                                    {"$in": ["$$l.status", ["PRESENT", "WFH", "ON_DUTY"]]},
                                    {"$cond": [{"$eq": ["$$l.half_day", True]}, 0.5, 1.0]},
                                    0
                                ]
                            }
                        }
                    }
                },
                "late_count": {
                    "$sum": {
                        "$map": {
                            "input": "$logs",
                            "as": "l",
                            "in": {
                                "$cond": [{"$gt": ["$$l.late_minutes", 0]}, 1, 0]
                            }
                        }
                    }
                }
            }
        },
        {
            "$addFields": {
                "attendance_rate_raw": {
                    "$cond": [
                        {"$and": [{"$eq": ["$is_working_day", True]}, {"$gt": ["$headcount", 0]}]},
                        {"$divide": ["$present_count", "$headcount"]},
                        None
                    ]
                }
            }
        },
        {
            "$setWindowFields": {
                "sortBy": {"date_str": 1},
                "output": {
                    "moving_avg_7d_raw": {
                        "$avg": "$attendance_rate_raw",
                        "window": {
                            "documents": [-6, 0]
                        }
                    }
                }
            }
        }
    ]

@app.get("/analytics/departments/{department}/trend")
def department_trend(
    department: str,
    from_: str = Query(..., alias="from", pattern=r'^\d{4}-(0[1-9]|1[0-2])-(0[1-9]|[12]\d|3[01])$'),
    to: str = Query(..., pattern=r'^\d{4}-(0[1-9]|1[0-2])-(0[1-9]|[12]\d|3[01])$')
):
    if to < from_:
        raise HTTPException(422, "to < from")

    d_from = datetime.fromisoformat(from_).date()
    d_to = datetime.fromisoformat(to).date()
    if (d_to - d_from).days > 92:
        raise HTTPException(422, "range exceeds 92 days")

    emp = db.employees.find_one({"department": department})
    if not emp:
        raise HTTPException(404, "unknown department")

    pipeline = build_department_trend_pipeline(department, from_, to)
    cursor = db.employees.aggregate(pipeline)

    items = []
    for doc in cursor:
        rate = None
        if doc.get("attendance_rate_raw") is not None:
            val = Decimal(str(doc["attendance_rate_raw"]))
            rate = float(val.quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP))

        mavg = None
        if doc.get("moving_avg_7d_raw") is not None:
            val = Decimal(str(doc["moving_avg_7d_raw"]))
            mavg = float(val.quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP))

        items.append({
            "date": doc["date_str"],
            "is_working_day": doc["is_working_day"],
            "headcount": doc["headcount"],
            "present_count": float(doc["present_count"]),
            "late_count": doc["late_count"],
            "attendance_rate": rate,
            "moving_avg_7d": mavg
        })

    return {"department": department, "items": items}

@app.get("/admin/explain/{endpoint}")
def explain_endpoint(
    endpoint: str,
    emp_code: Optional[str] = None,
    month: Optional[str] = Query(None, pattern=r'^\d{4}-(0[1-9]|1[0-2])$'),
    department: Optional[str] = None,
    limit: int = Query(10, ge=1, le=50),
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    status: Optional[str] = None,
    from_: Optional[str] = Query(None, alias="from"),
    to: Optional[str] = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
):
    if endpoint not in ["attendance_list", "employee_monthly", "department_summary", "late_leaderboard", "department_trend"]:
        raise HTTPException(422, "Invalid endpoint")

    page_val = page if isinstance(page, int) else 1
    page_size_val = page_size if isinstance(page_size, int) else 20
    limit_val = limit if isinstance(limit, int) else 10

    if endpoint == "attendance_list":
        q = {}
        if emp_code: q["emp_code"] = emp_code
        if date_from or date_to:
            q["date"] = {}
            if date_from: q["date"]["$gte"] = date_from
            if date_to: q["date"]["$lte"] = date_to
        if status: q["status"] = status
        skip = (page_val - 1) * page_size_val

        command = {
            "find": "attendance_logs",
            "filter": q,
            "sort": {"date": -1, "emp_code": 1},
            "skip": skip,
            "limit": page_size_val
        }
        res = db.command("explain", command, verbosity="executionStats")
        coll = "attendance_logs"

    elif endpoint == "employee_monthly":
        if not emp_code or not month: raise HTTPException(422, "Missing parameters")
        pipeline = build_employee_monthly_pipeline(emp_code, month)
        command = {"aggregate": "attendance_logs", "pipeline": pipeline, "cursor": {}}
        res = db.command("explain", command, verbosity="executionStats")
        coll = "attendance_logs"

    elif endpoint == "department_summary":
        if not month: raise HTTPException(422, "Missing parameters")
        pipeline = build_department_summary_pipeline(month, department)
        command = {"aggregate": "employees", "pipeline": pipeline, "cursor": {}}
        res = db.command("explain", command, verbosity="executionStats")
        coll = "employees"

    elif endpoint == "late_leaderboard":
        if not month: raise HTTPException(422, "Missing parameters")
        pipeline = build_late_leaderboard_pipeline(month, limit_val, department)
        command = {"aggregate": "attendance_logs", "pipeline": pipeline, "cursor": {}}
        res = db.command("explain", command, verbosity="executionStats")
        coll = "attendance_logs"

    elif endpoint == "department_trend":
        if not department or not from_ or not to: raise HTTPException(422, "Missing parameters")
        pipeline = build_department_trend_pipeline(department, from_, to)
        command = {"aggregate": "employees", "pipeline": pipeline, "cursor": {}}
        res = db.command("explain", command, verbosity="executionStats")
        coll = "employees"

    return {
        "endpoint": endpoint,
        "collection": coll,
        "explain": res
    }
