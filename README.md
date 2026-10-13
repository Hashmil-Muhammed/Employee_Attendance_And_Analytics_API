# HROne Employee Attendance & Analytics API

This is the backend for the HROne Employee Attendance system built with FastAPI and MongoDB. It supports employee management, strict business rules for time tracking, concurrency-safe attendance logs, and complex aggregation pipelines for real-time analytics.

## Features
- **Strict Business Rules**: Half-up rounding, overtime tracking, late minute calculation, and grace periods (R1-R10).
- **Concurrency Safe**: Uses strict MongoDB unique indexes and atomic `$set`/`$push` updates for safe punch-ins and record regularization under load.
- **Analytics Aggregations**: Implements robust pipelines utilizing `$setWindowFields` for leaderboard tie-ranking (competition ranking) and `$densify` for gap-filling 7-day moving averages.
- **Diagnostics**: Includes an `/admin/explain` endpoint using PyMongo's `executionStats` for verifying IXSCAN usage on 100k+ documents.

## Quick Start (Windows)
1. Ensure MongoDB is running locally on `mongodb://localhost:27017` (or set `MONGO_URI`).
2. **Double-click `run.bat`** (or run `.\run.bat` from Command Prompt / PowerShell):
   - Automatically checks Python and displays an environment check warning if Python is below 3.11.
   - Creates and activates the local `.venv` environment automatically.
   - Installs all dependencies from `requirements.txt`.
   - Verifies MongoDB connectivity and displays instructions if the service is stopped.
   - Starts the FastAPI application at `http://127.0.0.1:8000`.
3. Open **`http://127.0.0.1:8000/docs`** in your browser to interact with all API endpoints via Swagger UI.
4. Press **`Ctrl+C`** in the terminal to stop the server at any time.

## Manual Setup (PowerShell / Linux / macOS)
```powershell
# Create and activate virtual environment
python -m venv .venv
.\.venv\Scripts\Activate.ps1

# Install dependencies
pip install -r requirements.txt

# Start the application (reads MONGO_URI / MONGO_DB from environment or defaults to localhost:27017 / attendance_db)
uvicorn app.main:app --port 8000
```
MongoDB **6.0 or newer** (tested on local MongoDB 6.0.29) is required. Ensure MongoDB is running locally on `mongodb://localhost:27017` or configure `MONGO_URI` and `MONGO_DB` in your environment.

## Running Tests
To run the automated verification test suite verifying all 12 endpoints, business rules (R1-R10), concurrency, and MongoDB index scan (`IXSCAN`) coverage against an isolated test database:
```powershell
python -m unittest tests.test_api
```

## Missing / Incomplete Work
None. All CRUD endpoints, analytical MongoDB pipelines, and diagnostic tests are completely implemented and conform exactly to `openapi.yaml`.
