@echo off
title BharatVoice AI - Frontend Server
cd /d "%~dp0frontend"
if not exist node_modules (
  echo Installing frontend dependencies...
  npm install
)
echo Starting BharatVoice AI frontend at http://localhost:5173
npm run dev
pause
