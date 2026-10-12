# HROne Employee Attendance & Analytics API

This is the backend for the HROne Employee Attendance system built with FastAPI and MongoDB. It supports employee management, strict business rules for time tracking, concurrency-safe attendance logs, and complex aggregation pipelines for real-time analytics.

## Features
- **Strict Business Rules**: Half-up rounding, overtime tracking, late minute calculation, and grace periods (R1-R10).
- **Concurrency Safe**: Uses strict MongoDB unique indexes and atomic `$set`/`$push` updates for safe punch-ins and record regularization under load.
- **Analytics Aggregations**: Implements robust pipelines utilizing `$setWindowFields` for leaderboard tie-ranking (competition ranking) and `$densify` for gap-filling 7-day moving averages.
- **Diagnostics**: Includes an `/admin/explain` endpoint using PyMongo's `executionStats` for verifying IXSCAN usage on 100k+ documents.

## Local setup
```powershell
# Create and activate virtual environment
python -m venv .venv
.\.venv\Scripts\Activate.ps1

# Install dependencies
pip install -r requirements.txt

# Start the application (reads MONGO_URI / MONGO_DB from environment or defaults to localhost:27017 / attendance_db)
uvicorn app.main:app --port 8000 --reload
```
MongoDB **6.0 or newer** is required (the grader uses 7.0). MongoDB Atlas free tier (M0) works perfectly. Ensure you have the `MONGO_URI` and `MONGO_DB` configured.

## Missing / Incomplete Work
None. All CRUD endpoints, analytical MongoDB pipelines, and diagnostic tests are completely implemented and conform exactly to `openapi.yaml`.
