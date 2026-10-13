@echo off
setlocal enabledelayedexpansion

:: 1. Switch to the script directory
cd /d "%~dp0"
title HROne Employee Attendance ^& Analytics API

echo ======================================================================
echo       HROne Employee Attendance ^& Analytics API - Startup Script
echo ======================================================================
echo.

:: 2. Check Python installation
where python >nul 2>nul
if %errorlevel% neq 0 (
    echo [ERROR] Python is not installed or not found in system PATH.
    echo Please install Python 3.11+ from https://www.python.org/
    echo.
    pause
    exit /b 1
)

for /f "tokens=*" %%i in ('python --version 2^>^&1') do set "PY_VER=%%i"
echo [OK] Found Python: %PY_VER%

:: Check for Python 3.11+ requirement and display warning if 3.10 or below
python -c "import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)" >nul 2>nul
if %errorlevel% neq 0 (
    echo [WARNING] The assignment specifies Python 3.11+.
    echo           Currently running on %PY_VER%.
    echo           Continuing with the available interpreter...
)
echo.

:: 3. Create .venv if not exists
if not exist ".venv\Scripts\activate.bat" (
    echo [INFO] Creating virtual environment .venv...
    python -m venv .venv
    if %errorlevel% neq 0 (
        echo [ERROR] Failed to create virtual environment.
        echo.
        pause
        exit /b 1
    )
    echo [OK] Virtual environment created.
)

:: 4. Activate .venv
call ".venv\Scripts\activate.bat"
if %errorlevel% neq 0 (
    echo [ERROR] Failed to activate virtual environment.
    echo.
    pause
    exit /b 1
)
echo [OK] Virtual environment activated.
echo.

:: 5. Install dependencies from requirements.txt
echo [INFO] Checking dependencies from requirements.txt...
python -m pip install -r requirements.txt
if %errorlevel% neq 0 (
    echo [ERROR] Failed to install dependencies.
    echo.
    pause
    exit /b 1
)
echo [OK] Dependencies ready.
echo.

:: 6. Check MongoDB connectivity
echo [INFO] Verifying MongoDB connectivity at mongodb://localhost:27017...
python -c "import sys, os; from pymongo import MongoClient; uri = os.getenv('MONGO_URI', 'mongodb://localhost:27017'); client = MongoClient(uri, serverSelectionTimeoutMS=2500); client.admin.command('ping')" >nul 2>nul
if %errorlevel% neq 0 (
    echo.
    echo ======================================================================
    echo [ERROR] Cannot connect to MongoDB at mongodb://localhost:27017!
    echo ======================================================================
    echo MongoDB is required to start the application.
    echo.
    echo How to start MongoDB on Windows:
    echo   1. Open an Administrator Command Prompt and run:
    echo        net start MongoDB
    echo   2. Or press Win + R, type 'services.msc', find 'MongoDB Server',
    echo      and click 'Start the service'.
    echo   3. If using Docker, run:
    echo        docker run -d --name hrone-mongo -p 27017:27017 mongo:7
    echo ======================================================================
    echo.
    pause
    exit /b 1
)
echo [OK] MongoDB is running and responsive.
echo.

:: 7. Start FastAPI server
echo ======================================================================
echo Starting FastAPI application...
echo   - Local URL:   http://127.0.0.1:8000
echo   - Swagger UI:  http://127.0.0.1:8000/docs
echo   - Health:      http://127.0.0.1:8000/health
echo.
echo Press Ctrl+C to stop the server at any time.
echo ======================================================================
echo.

python -m uvicorn app.main:app --host 127.0.0.1 --port 8000

echo.
echo [INFO] Server stopped.
pause
