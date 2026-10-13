# HROne Employee Attendance & Analytics API

Backend service for the HROne Employee Attendance & Analytics system built with FastAPI and MongoDB. It implements employee management, attendance tracking with strict business rules, concurrency safeguards, aggregation pipelines for real-time analytics, and diagnostic query plan inspection.

## Features
- **Strict Business Rules**: Implements rules R1–R10 including IST timezone handling, overnight shifts, second truncation, 10-minute grace period, half-up rounding, overtime calculation, half-day detection, and mid-month joiner working days.
- **Concurrency Safeguards**: Relies on MongoDB unique indexes (compound `{emp_code: 1, date: 1}`) and atomic operations to prevent duplicate punch-ins and handle concurrent regularizations.
- **Analytics Aggregations**:
  - Employee monthly summaries with attendance percentages and working day calculations.
  - Department summaries with zero-log headcount retention.
  - Late leaderboard utilizing `$setWindowFields` with `$rank` for standard competition ranking (1, 2, 2, 4).
  - Department trends utilizing `$densify` for calendar gap-filling and trailing 7-day moving averages.
- **Diagnostics**: Implements `/admin/explain/{endpoint}` supporting `attendance_list`, `employee_monthly`, `department_summary`, `late_leaderboard`, and `department_trend` with `executionStats` to inspect query execution plans and index usage.

## Prerequisites
- **Python**: 3.11 or newer
- **MongoDB**: 6.0 or newer (required for `$densify` and window operators)

## Configuration
The application reads configuration from environment variables (or an optional `.env` file via `python-dotenv`):
- `MONGO_URI`: MongoDB connection string (defaults to `mongodb://localhost:27017`)
- `MONGO_DB`: Target database name (defaults to `attendance_db`)

All required indexes on `employees` and `attendance_logs` are created automatically and idempotently during application startup (`lifespan`).

## Quick Start (Windows)
1. Ensure MongoDB 6.0+ is running locally on `mongodb://localhost:27017` (or set `MONGO_URI`).
2. Run `run.bat` (or double-click it in Windows Explorer):
   - Verifies the Python installation (warns if below 3.11).
   - Creates and activates the local virtual environment (`.venv`) if not already present.
   - Installs dependencies from `requirements.txt`.
   - Verifies MongoDB connectivity and reports service status.
   - Starts the FastAPI application at `http://127.0.0.1:8000`.
3. Open **`http://127.0.0.1:8000/docs`** for interactive Swagger UI documentation.
4. Press **`Ctrl+C`** in the terminal to stop the server at any time.

## Manual Setup

### Windows (PowerShell)
```powershell
# Create and activate virtual environment
python -m venv .venv
.\.venv\Scripts\Activate.ps1

# Install dependencies
pip install -r requirements.txt

# Start the application
uvicorn app.main:app --port 8000
```

### Linux / macOS (bash / zsh)
```bash
# Create and activate virtual environment
python3 -m venv .venv
source .venv/bin/activate

# Install dependencies
pip install -r requirements.txt

# Start the application
uvicorn app.main:app --port 8000
```

## Running Tests
Run the automated test suite using Python's built-in `unittest` runner:
```bash
python -m unittest tests.test_api
```
The test suite exercises root and health probes, employee creation and pagination, punch-in/out business rules, concurrent punch-in races, regularization audit history, monthly and department analytics pipelines, and `/admin/explain` index plan checks against an isolated temporary test database (`test_hrone_candidate_db`), which is automatically dropped after the tests finish.

## Missing / Incomplete Work
None. All required endpoints defined in `openapi.yaml`, business rules (R1–R10), indexing strategies, and analytics aggregation pipelines are implemented.
