@echo off
title BharatVoice AI - Backend Server
cd /d "%~dp0backend"

echo ==============================================
echo  Starting BharatVoice AI Backend (FastAPI)
echo ==============================================

if exist ".venv\Scripts\activate.bat" (
    call .venv\Scripts\activate.bat
) else (
    python -c "import fastapi, uvicorn" 2>nul
    if errorlevel 1 (
        echo Creating virtual environment...
        python -m venv .venv
        call .venv\Scripts\activate.bat
        echo Installing dependencies...
        pip install -r requirements.txt
    )
)

echo.
echo Starting FastAPI server at http://127.0.0.1:8000
echo Interactive API docs at http://127.0.0.1:8000/docs
echo.
python -m uvicorn app.main:app --reload --port 8000

pause
