# REVIEW.md

List every defect you found in the starter `app/main.py` (helpers and endpoints). For each one:

| # | Where (function / line) | What is wrong | How you'd notice it (test, input, or symptom) | How you fixed it |
|---|---|---|---|---|
| 1 | `health` | Always returns `{"status": "ok"}` without checking MongoDB connection. | If MongoDB is stopped, `/health` still returns 200 instead of 503. | Added `db.command("ping")` inside a try-except block, raising 503 on failure. |
| 2 | `create_employee` | Uses check-then-insert (`find_one` then `insert_one`) for uniqueness. | Two simultaneous requests with the same `emp_code` will create duplicate records. | Created a unique index on `emp_code` at startup and rely on catching `DuplicateKeyError` to return 409. |
| 3 | `create_employee`, `punch_in` | Uses `datetime.now()` for new timestamps. | Timestamps are naive (local) instead of UTC, and they include microseconds which violates R1. | Created `now_utc_truncated()` helper returning timezone-aware UTC datetimes with `microsecond=0`. |
| 4 | `list_employees` | `total` is computed via `count_documents({})`, ignoring the `department` query filter. | Calling with `?department=Engineering` filters the page items but the total count stays artificially high (counts all). | Passed the query dictionary `q` into `count_documents(q)`. |
| 5 | `punch_in` | Uses check-then-insert for the `(emp_code, date)` uniqueness constraint. | Two simultaneous punch-ins for the same employee/date create duplicate records. | Created a compound unique index on `[("emp_code", 1), ("date", 1)]` and catch `DuplicateKeyError` to return 409. |
| 6 | `punch_in` | Assigns the attendance `date` using just the `punch_in` timestamp's date. | For an overnight shift (e.g., 22:00-06:00), a punch-in at 05:00 on the 7th incorrectly assigns `date="2026-07-07"` instead of the 6th. | Added `get_attendance_date` helper to check overnight shifts and shift the calendar date back 1 day if needed. |
| 7 | `compute_late_minutes` | Mixes naive datetimes with local server times via `.replace()` without standardizing to IST. | Incorrect late minute calculation when server time differs from IST. Also misapplied the strict 10-minute grace period. | Converted UTC instant strictly to IST, subtracted shift start in IST, and checked `total_seconds() > 600`. |
| 8 | `compute_work_hours` | Uses Python's built-in `round(..., 2)`. | Python uses "banker's rounding" (round to even), which violates R4's explicit half-up requirement. | Replaced with `decimal.Decimal` and `quantize` using `ROUND_HALF_UP`. |
| 9 | `list_attendance` | Fetches the entire result set into memory (`list(...)`) before sorting and paginating in Python. | High memory usage (OOM) and massive latency on the grader's 100k-record collection. | Chained `.sort()`, `.skip()`, and `.limit()` on the MongoDB cursor directly. |

Also note anything you looked at and decided was **not** a defect, and why.
- **Handling of absent/leave records:** The starter doesn't populate `ABSENT`/`LEAVE` defaults in `punch_in`, but this isn't a defect because `punch_in` only allows `PRESENT`, `WFH`, and `ON_DUTY`. `ABSENT` and `LEAVE` are only created by seeded back-office data or corrections.
- **Default value for `shift_end` (18:30):** This is valid since the OpenAPI schema allows `09:30` and `18:30` as default values for shift start and end.
