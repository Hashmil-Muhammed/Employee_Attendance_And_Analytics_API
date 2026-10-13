"""
Automated Compatibility & Verification Test Suite for HROne Employee Attendance & Analytics API
Runs with Python 3.11.9 and MongoDB 6.0.29 on localhost:27017.
Uses a dedicated test database (test_hrone_candidate_db) to avoid touching production/dev data.
"""
import os
import sys
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone, timedelta

# Ensure project root is in sys.path
PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

import pymongo
from fastapi import HTTPException
from pydantic import ValidationError

# Set environment to test database before importing main
TEST_DB_NAME = "test_hrone_candidate_db"
os.environ["MONGO_DB"] = TEST_DB_NAME
os.environ["MONGO_URI"] = os.getenv("MONGO_URI", "mongodb://localhost:27017")

import app.main as main
from app.main import (
    app,
    db,
    client,
    IST,
    EmployeeIn,
    PunchInIn,
    PunchOutIn,
    RegularizeIn,
    root,
    health,
    create_employee,
    list_employees,
    punch_in,
    punch_out,
    list_attendance,
    regularize_attendance,
    employee_monthly,
    department_summary,
    late_leaderboard,
    department_trend,
    explain_endpoint,
    compute_late_minutes,
    compute_overtime,
    compute_work_hours,
    compute_half_day,
    truncate_ms,
    to_epoch_ms,
    get_attendance_date,
)

class TestAttendanceAPI(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Verify MongoDB connectivity and server version
        server_info = client.server_info()
        cls.mongo_version = server_info.get("version", "")
        print(f"\n[SETUP] Connected to MongoDB version: {cls.mongo_version}")
        assert cls.mongo_version.startswith("6.0"), f"Expected MongoDB 6.0.x, got {cls.mongo_version}"

        # Clean up test database
        client.drop_database(TEST_DB_NAME)

        # Create indexes as lifespan does
        db.employees.create_index([("emp_code", pymongo.ASCENDING)], unique=True)
        db.employees.create_index([("department", pymongo.ASCENDING), ("joined_on", pymongo.ASCENDING)])
        db.employees.create_index([("joined_on", pymongo.ASCENDING)])
        db.attendance_logs.create_index([("emp_code", pymongo.ASCENDING), ("date", pymongo.ASCENDING)], unique=True)
        db.attendance_logs.create_index([("date", pymongo.DESCENDING), ("emp_code", pymongo.ASCENDING)])

    @classmethod
    def tearDownClass(cls):
        # Drop dedicated test database after tests complete
        client.drop_database(TEST_DB_NAME)
        print(f"[TEARDOWN] Dropped test database '{TEST_DB_NAME}'")

    def setUp(self):
        # Clean collections before each test
        db.employees.delete_many({})
        db.attendance_logs.delete_many({})

    # ----------------------------------------------------------------------- #
    # 1. Health and Root Endpoints
    # ----------------------------------------------------------------------- #
    def test_01_root_endpoint(self):
        res = root()
        self.assertIn("message", res)
        self.assertEqual(res["health"], "/health")

    def test_02_health_endpoint(self):
        res = health()
        self.assertEqual(res, {"status": "ok"})

    # ----------------------------------------------------------------------- #
    # 2. Employee Creation and Listing
    # ----------------------------------------------------------------------- #
    def test_03_create_employee_success(self):
        payload = EmployeeIn(
            emp_code="EMP0001",
            name="Asha Rao",
            email="asha@example.com",
            department="Engineering",
            shift_start="09:30",
            shift_end="18:30",
            joined_on="2026-01-05"
        )
        res = create_employee(payload)
        self.assertEqual(res["emp_code"], "EMP0001")
        self.assertNotIn("_id", res)
        self.assertIsInstance(res["created_at"], int)

    def test_04_create_employee_duplicate_conflict(self):
        payload = EmployeeIn(
            emp_code="EMP0001",
            name="Asha Rao",
            email="asha@example.com",
            department="Engineering",
            joined_on="2026-01-05"
        )
        create_employee(payload)
        with self.assertRaises(HTTPException) as ctx:
            create_employee(payload)
        self.assertEqual(ctx.exception.status_code, 409)

    def test_05_create_employee_same_shift_validation(self):
        with self.assertRaises(ValidationError):
            EmployeeIn(
                emp_code="EMP0002",
                name="Bad Shift",
                email="bad@example.com",
                department="Engineering",
                shift_start="09:30",
                shift_end="09:30",
                joined_on="2026-01-05"
            )

    def test_06_list_employees_pagination_and_filter(self):
        for i in range(1, 26):
            dept = "Engineering" if i <= 15 else "Product"
            create_employee(EmployeeIn(
                emp_code=f"EMP{i:04d}",
                name=f"Emp {i}",
                email=f"emp{i}@example.com",
                department=dept,
                joined_on="2026-01-01"
            ))

        # Default page 1 (20 items)
        page1 = list_employees(department=None, page=1, page_size=20)
        self.assertEqual(len(page1["items"]), 20)
        self.assertEqual(page1["total"], 25)

        # Department filter
        eng_page = list_employees(department="Engineering", page=1, page_size=10)
        self.assertEqual(len(eng_page["items"]), 10)
        self.assertEqual(eng_page["total"], 15)

    # ----------------------------------------------------------------------- #
    # 3. Attendance Punch-In (R1, R2, Concurrency)
    # ----------------------------------------------------------------------- #
    def test_07_punch_in_and_grace_period_rules(self):
        create_employee(EmployeeIn(
            emp_code="EMP0001",
            name="Asha",
            email="a@example.com",
            department="Eng",
            shift_start="09:30",
            shift_end="18:30",
            joined_on="2026-01-01"
        ))

        # Test R2: Shift 09:30 IST
        # 09:40:00 (10 min exactly) -> 0 late minutes
        dt_094000 = datetime(2026, 7, 6, 9, 40, 0, tzinfo=IST)
        ms_094000 = int(dt_094000.timestamp() * 1000)
        res1 = punch_in(PunchInIn(emp_code="EMP0001", punched_at=ms_094000))
        self.assertEqual(res1["late_minutes"], 0)
        self.assertEqual(res1["date"], "2026-07-06")

        # Create another employee to test 09:40:01
        create_employee(EmployeeIn(
            emp_code="EMP0002",
            name="Bob",
            email="b@example.com",
            department="Eng",
            shift_start="09:30",
            shift_end="18:30",
            joined_on="2026-01-01"
        ))
        # 09:40:01 (> 10:00 grace) -> 10 minutes late (floor since shift start)
        dt_094001 = datetime(2026, 7, 6, 9, 40, 1, tzinfo=IST)
        ms_094001 = int(dt_094001.timestamp() * 1000)
        res2 = punch_in(PunchInIn(emp_code="EMP0002", punched_at=ms_094001))
        self.assertEqual(res2["late_minutes"], 10)

        # Create another employee to test 10:15:59
        create_employee(EmployeeIn(
            emp_code="EMP0003",
            name="Charlie",
            email="c@example.com",
            department="Eng",
            shift_start="09:30",
            shift_end="18:30",
            joined_on="2026-01-01"
        ))
        dt_101559 = datetime(2026, 7, 6, 10, 15, 59, tzinfo=IST)
        ms_101559 = int(dt_101559.timestamp() * 1000)
        res3 = punch_in(PunchInIn(emp_code="EMP0003", punched_at=ms_101559))
        self.assertEqual(res3["late_minutes"], 45)

    def test_08_punch_in_overnight_shift(self):
        # Overnight shift 22:00 to 06:00
        create_employee(EmployeeIn(
            emp_code="EMP0005",
            name="Night Shift",
            email="night@example.com",
            department="Support",
            shift_start="22:00",
            shift_end="06:00",
            joined_on="2026-01-01"
        ))

        # Punch in at 01:00 on July 7th IST (< 06:00 shift end) -> belongs to July 6th!
        dt_0100 = datetime(2026, 7, 7, 1, 0, 0, tzinfo=IST)
        ms_0100 = int(dt_0100.timestamp() * 1000)
        res = punch_in(PunchInIn(emp_code="EMP0005", punched_at=ms_0100))
        self.assertEqual(res["date"], "2026-07-06")
        # 3 hours late = 180 minutes
        self.assertEqual(res["late_minutes"], 180)

    def test_09_punch_in_concurrency_race(self):
        create_employee(EmployeeIn(
            emp_code="EMP0001",
            name="Asha",
            email="a@example.com",
            department="Eng",
            joined_on="2026-01-01"
        ))
        dt = datetime(2026, 7, 6, 9, 30, 0, tzinfo=IST)
        ms = int(dt.timestamp() * 1000)

        results = []
        errors = []

        def do_punch():
            try:
                r = punch_in(PunchInIn(emp_code="EMP0001", punched_at=ms))
                results.append(r)
            except HTTPException as e:
                errors.append(e.status_code)

        with ThreadPoolExecutor(max_workers=5) as ex:
            futures = [ex.submit(do_punch) for _ in range(5)]
            for f in futures:
                f.result()

        # Exactly 1 success (201) and four 409 conflicts
        self.assertEqual(len(results), 1)
        self.assertEqual(len(errors), 4)
        self.assertTrue(all(code == 409 for code in errors))

    # ----------------------------------------------------------------------- #
    # 4. Attendance Punch-Out (R3, R4, R5)
    # ----------------------------------------------------------------------- #
    def test_10_punch_out_work_hours_half_day_and_overtime(self):
        create_employee(EmployeeIn(
            emp_code="EMP0001",
            name="Asha",
            email="a@example.com",
            department="Eng",
            shift_start="09:30",
            shift_end="18:30",
            joined_on="2026-01-01"
        ))
        # Punch in at 09:28 IST (03:58 UTC)
        in_dt = datetime(2026, 7, 6, 9, 28, 0, tzinfo=IST)
        punch_in(PunchInIn(emp_code="EMP0001", punched_at=int(in_dt.timestamp() * 1000)))

        # Punch out at 18:35 IST (9h 7m = 32820s = 9.1166... hrs -> rounded half-up: 9.12)
        # Shift end is 18:30. Overtime is 5 min (< 30 min) -> 0 overtime
        out_dt = datetime(2026, 7, 6, 18, 35, 0, tzinfo=IST)
        res = punch_out(PunchOutIn(emp_code="EMP0001", punched_at=int(out_dt.timestamp() * 1000)))

        self.assertEqual(res["work_hours"], 9.12)
        self.assertFalse(res["half_day"])
        self.assertEqual(res["overtime_minutes"], 0)

        # Attempting second punch out -> 409
        with self.assertRaises(HTTPException) as ctx:
            punch_out(PunchOutIn(emp_code="EMP0001", punched_at=int(out_dt.timestamp() * 1000)))
        self.assertEqual(ctx.exception.status_code, 409)

    def test_11_punch_out_overtime_and_half_day(self):
        create_employee(EmployeeIn(
            emp_code="EMP0002",
            name="Half Day Emp",
            email="h@example.com",
            department="Eng",
            shift_start="09:30",
            shift_end="18:30",
            joined_on="2026-01-01"
        ))
        # Punch in 09:30, out 13:00 (3.5 hours -> half_day = True)
        in_dt = datetime(2026, 7, 6, 9, 30, 0, tzinfo=IST)
        out_dt = datetime(2026, 7, 6, 13, 0, 0, tzinfo=IST)
        punch_in(PunchInIn(emp_code="EMP0002", punched_at=int(in_dt.timestamp() * 1000)))
        res = punch_out(PunchOutIn(emp_code="EMP0002", punched_at=int(out_dt.timestamp() * 1000)))

        self.assertEqual(res["work_hours"], 3.50)
        self.assertTrue(res["half_day"])

    # ----------------------------------------------------------------------- #
    # 5. Regularization (PATCH) and History
    # ----------------------------------------------------------------------- #
    def test_12_regularize_record_and_history(self):
        create_employee(EmployeeIn(
            emp_code="EMP0001",
            name="Asha",
            email="a@example.com",
            department="Eng",
            shift_start="09:30",
            shift_end="18:30",
            joined_on="2026-01-01"
        ))
        in_dt = datetime(2026, 7, 6, 10, 5, 0, tzinfo=IST) # late 35 min
        out_dt = datetime(2026, 7, 6, 19, 10, 0, tzinfo=IST)
        punch_in(PunchInIn(emp_code="EMP0001", punched_at=int(in_dt.timestamp() * 1000)))
        punch_out(PunchOutIn(emp_code="EMP0001", punched_at=int(out_dt.timestamp() * 1000)))

        # Correct punch_in to 09:28 IST
        corr_in = datetime(2026, 7, 6, 9, 28, 0, tzinfo=IST)
        patch_payload = RegularizeIn(
            punch_in=int(corr_in.timestamp() * 1000),
            reason="Biometric glitch at entry gate",
            regularized_by="hr.admin"
        )
        res = regularize_attendance("EMP0001", "2026-07-06", patch_payload)

        self.assertEqual(res["late_minutes"], 0)
        self.assertEqual(len(res["history"]), 1)
        h = res["history"][0]
        self.assertEqual(h["by"], "hr.admin")
        self.assertIn("punch_in", h["changes"])
        self.assertIn("late_minutes", h["changes"])
        self.assertEqual(h["changes"]["late_minutes"]["from"], 35)
        self.assertEqual(h["changes"]["late_minutes"]["to"], 0)

        # No-op patch raises 422
        with self.assertRaises(HTTPException) as ctx:
            regularize_attendance("EMP0001", "2026-07-06", patch_payload)
        self.assertEqual(ctx.exception.status_code, 422)

    # ----------------------------------------------------------------------- #
    # 6. Analytics: Employee Monthly
    # ----------------------------------------------------------------------- #
    def test_13_employee_monthly_analytics(self):
        # Asha joined 2026-01-05
        create_employee(EmployeeIn(
            emp_code="EMP0001",
            name="Asha",
            email="a@example.com",
            department="Eng",
            joined_on="2026-01-05"
        ))
        # July 2026 has 23 working days (Mon-Fri)
        # Add 1 full day present (July 6, Mon)
        in_dt = datetime(2026, 7, 6, 9, 30, 0, tzinfo=IST)
        out_dt = datetime(2026, 7, 6, 18, 30, 0, tzinfo=IST)
        punch_in(PunchInIn(emp_code="EMP0001", punched_at=int(in_dt.timestamp() * 1000)))
        punch_out(PunchOutIn(emp_code="EMP0001", punched_at=int(out_dt.timestamp() * 1000)))

        res = employee_monthly("EMP0001", "2026-07")
        self.assertEqual(res["emp_code"], "EMP0001")
        self.assertEqual(res["working_days"], 23)
        self.assertEqual(res["present_days"], 1.0)
        # 1 / 23 * 100 = 4.3478... -> 4.35
        self.assertEqual(res["attendance_pct"], 4.35)

    # ----------------------------------------------------------------------- #
    # 7. Analytics: Department Summary
    # ----------------------------------------------------------------------- #
    def test_14_department_summary_headcount_and_aggregates(self):
        # 2 employees in Engineering: Asha (1 log) and Bob (0 logs)
        create_employee(EmployeeIn(
            emp_code="EMP0001",
            name="Asha",
            email="a@example.com",
            department="Engineering",
            joined_on="2026-01-01"
        ))
        create_employee(EmployeeIn(
            emp_code="EMP0002",
            name="Bob",
            email="b@example.com",
            department="Engineering",
            joined_on="2026-01-01"
        ))
        # Charlie in Marketing joined in August -> excluded from July headcount
        create_employee(EmployeeIn(
            emp_code="EMP0003",
            name="Charlie",
            email="c@example.com",
            department="Marketing",
            joined_on="2026-08-01"
        ))

        in_dt = datetime(2026, 7, 6, 9, 30, 0, tzinfo=IST)
        out_dt = datetime(2026, 7, 6, 18, 30, 0, tzinfo=IST)
        punch_in(PunchInIn(emp_code="EMP0001", punched_at=int(in_dt.timestamp() * 1000)))
        punch_out(PunchOutIn(emp_code="EMP0001", punched_at=int(out_dt.timestamp() * 1000)))

        res = department_summary("2026-07")
        self.assertEqual(len(res["items"]), 1)
        eng = res["items"][0]
        self.assertEqual(eng["department"], "Engineering")
        self.assertEqual(eng["headcount"], 2) # includes Bob with 0 logs!
        self.assertEqual(eng["present_days"], 1.0)
        self.assertEqual(eng["avg_work_hours"], 9.0)

    # ----------------------------------------------------------------------- #
    # 8. Analytics: Late Leaderboard (Competition Ranking & Ties)
    # ----------------------------------------------------------------------- #
    def test_15_late_leaderboard_competition_ranking(self):
        create_employee(EmployeeIn(emp_code="EMP0002", name="Bob", email="b@example.com", department="Eng", joined_on="2026-01-01"))
        create_employee(EmployeeIn(emp_code="EMP0001", name="Alice", email="a@example.com", department="Eng", joined_on="2026-01-01"))
        create_employee(EmployeeIn(emp_code="EMP0003", name="Charlie", email="c@example.com", department="Eng", joined_on="2026-01-01"))

        # Both EMP0001 and EMP0002 have 30 late minutes (tie for rank 1)
        # EMP0003 has 15 late minutes (rank 3)
        db.attendance_logs.insert_many([
            {"emp_code": "EMP0001", "date": "2026-07-06", "late_minutes": 30, "status": "PRESENT"},
            {"emp_code": "EMP0002", "date": "2026-07-06", "late_minutes": 30, "status": "PRESENT"},
            {"emp_code": "EMP0003", "date": "2026-07-06", "late_minutes": 15, "status": "PRESENT"},
        ])

        # limit=1: both tied employees at rank 1 must be returned
        res1 = late_leaderboard(month="2026-07", limit=1)
        self.assertEqual(len(res1["items"]), 2)
        self.assertEqual(res1["items"][0]["rank"], 1)
        self.assertEqual(res1["items"][0]["emp_code"], "EMP0001") # tie order by emp_code asc
        self.assertEqual(res1["items"][1]["rank"], 1)
        self.assertEqual(res1["items"][1]["emp_code"], "EMP0002")

        # limit=3: rank 2 is skipped, EMP0003 has rank 3
        res3 = late_leaderboard(month="2026-07", limit=3)
        self.assertEqual(len(res3["items"]), 3)
        self.assertEqual(res3["items"][2]["rank"], 3)
        self.assertEqual(res3["items"][2]["emp_code"], "EMP0003")

    # ----------------------------------------------------------------------- #
    # 9. Analytics: Department Trend ($densify & 7d moving avg)
    # ----------------------------------------------------------------------- #
    def test_16_department_trend_gap_filling(self):
        create_employee(EmployeeIn(
            emp_code="EMP0001",
            name="Asha",
            email="a@example.com",
            department="Engineering",
            joined_on="2026-07-01"
        ))

        # Query range July 1 to July 5 (5 days: Wed, Thu, Fri, Sat, Sun)
        res = department_trend(department="Engineering", from_="2026-07-01", to="2026-07-05")
        items = res["items"]
        self.assertEqual(len(items), 5) # Exactly one row per day via $densify!
        dates = [d["date"] for d in items]
        self.assertEqual(dates, ["2026-07-01", "2026-07-02", "2026-07-03", "2026-07-04", "2026-07-05"])

        # Check working day flags: Wed, Thu, Fri are True; Sat, Sun are False
        self.assertTrue(items[0]["is_working_day"])
        self.assertTrue(items[1]["is_working_day"])
        self.assertTrue(items[2]["is_working_day"])
        self.assertFalse(items[3]["is_working_day"])
        self.assertFalse(items[4]["is_working_day"])

    # ----------------------------------------------------------------------- #
    # 10. Admin Explain: IXSCAN and No COLLSCAN
    # ----------------------------------------------------------------------- #
    def test_17_explain_all_endpoints_use_ixscan(self):
        create_employee(EmployeeIn(
            emp_code="EMP0001",
            name="Asha",
            email="a@example.com",
            department="Engineering",
            joined_on="2026-01-01"
        ))
        db.attendance_logs.insert_one({
            "emp_code": "EMP0001",
            "date": "2026-07-06",
            "late_minutes": 10,
            "status": "PRESENT"
        })

        def inspect_plan(explain_doc):
            stages = []
            def walk(d):
                if not isinstance(d, dict): return
                if "stage" in d: stages.append(d["stage"])
                for v in d.values():
                    if isinstance(v, dict): walk(v)
                    elif isinstance(v, list):
                        for item in v: walk(item)
            walk(explain_doc)
            return stages

        endpoints_to_test = [
            ("attendance_list", {"date_from": "2026-07-01", "date_to": "2026-07-10"}),
            ("employee_monthly", {"emp_code": "EMP0001", "month": "2026-07"}),
            ("department_summary", {"month": "2026-07", "department": "Engineering"}),
            ("late_leaderboard", {"month": "2026-07", "limit": 10}),
            ("department_trend", {"department": "Engineering", "from_": "2026-07-01", "to": "2026-07-05"}),
        ]

        for ep_name, kwargs in endpoints_to_test:
            out = explain_endpoint(ep_name, **kwargs)
            self.assertIn("explain", out)
            stages = inspect_plan(out["explain"])
            self.assertIn("IXSCAN", stages, f"Endpoint {ep_name} did not use IXSCAN. Stages: {stages}")
            self.assertNotIn("COLLSCAN", stages, f"Endpoint {ep_name} used COLLSCAN! Stages: {stages}")

if __name__ == "__main__":
    unittest.main()
