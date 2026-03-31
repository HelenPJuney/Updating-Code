@echo off
title LiveKit Stack Launcher
cd /d "%~dp0"

echo.
echo ============================================================
echo   LiveKit Stack  +  ngrok  +  Call Test
echo ============================================================
echo.

:: ── Step 1: Check Docker ──────────────────────────────────────────────────────
echo [1/4] Checking Docker...
:wait_docker
docker info >nul 2>&1
if %errorlevel% neq 0 (
    echo      Docker not ready, waiting...
    ping -n 6 127.0.0.1 >nul
    goto wait_docker
)
echo      Docker ready.

:: ── Step 2: Start LiveKit + Kafka ─────────────────────────────────────────────
echo.
echo [2/4] Starting LiveKit + Kafka containers...
docker stop asterisk >nul 2>&1
docker rm -f asterisk >nul 2>&1
docker rm -f livekit-sip >nul 2>&1
docker compose up -d kafka livekit kafka-setup
echo      Containers started.

:: Wait for Kafka
set k=0
:wait_kafka
docker exec kafka rpk cluster health >nul 2>&1
if %errorlevel% neq 0 (
    set /a k+=1
    if %k% lss 20 (
        ping -n 4 127.0.0.1 >nul
        goto wait_kafka
    )
    echo      Kafka not ready - continuing anyway.
) else (
    echo      Kafka healthy.
)

:: ── Step 3: Start FastAPI backend (activate venv first) ──────────────────────
echo.
echo [3/4] Starting FastAPI backend...
if exist "%~dp0venv\Scripts\activate.bat" (
    start "FastAPI Backend" cmd /k "cd /d %~dp0 && call venv\Scripts\activate.bat && python main.py"
) else (
    echo      WARNING: venv not found, using system Python
    start "FastAPI Backend" cmd /k "cd /d %~dp0 && python main.py"
)
ping -n 4 127.0.0.1 >nul

:: ── Step 4: Start ngrok tunnel ───────────────────────────────────────────────
echo.
echo [4/4] Starting ngrok tunnel...
start "ngrok" cmd /k "ngrok http 8000 --domain=bairnly-unvamped-billye.ngrok-free.dev"
ping -n 3 127.0.0.1 >nul

:: ── Done ─────────────────────────────────────────────────────────────────────
echo.
echo ============================================================
echo   All services launched!
echo.
echo   Call Test  :  https://bairnly-unvamped-billye.ngrok-free.dev/call-test
echo   Backend    :  http://localhost:8000
echo   API Docs   :  http://localhost:8000/docs
echo   LiveKit    :  wss://sch-natyyy4y.livekit.cloud
echo ============================================================
echo.
echo   Open the Call Test URL in 2 browser tabs:
echo     Tab 1 = Caller tab  ^>  click "Call Helen"
echo     Tab 2 = Receiver tab  ^>  click "Go Online" then "Pick up"
echo.
echo   To stop containers:  docker compose down
echo.
pause
