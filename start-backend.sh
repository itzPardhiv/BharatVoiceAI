#!/usr/bin/env bash
set -e

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$DIR/backend"

echo "=============================================="
echo " Starting BharatVoice AI Backend (FastAPI)"
echo "=============================================="

if [ -f ".venv/bin/activate" ]; then
    source .venv/bin/activate
else
    if ! python3 -c "import fastapi, uvicorn" &> /dev/null; then
        echo "Creating virtual environment..."
        python3 -m venv .venv
        source .venv/bin/activate
        echo "Installing dependencies..."
        pip install -r requirements.txt
    fi
fi

echo ""
echo "Starting FastAPI server at http://127.0.0.1:8000"
echo "Interactive API docs at http://127.0.0.1:8000/docs"
echo ""
python3 -m uvicorn app.main:app --reload --port 8000
