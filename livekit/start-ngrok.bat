@echo off
REM ═══════════════════════════════════════════════════════════════════════════
REM  start-ngrok.bat  — Start ngrok tunnel on your static domain
REM  Static domain: bairnly-unvamped-billye.ngrok-free.dev
REM ═══════════════════════════════════════════════════════════════════════════
echo.
echo  Starting ngrok tunnel...
echo  Domain: bairnly-unvamped-billye.ngrok-free.dev
echo  Backend must be running on port 8000
echo.
echo  Backend URL: https://bairnly-unvamped-billye.ngrok-free.dev
echo  Call Test:   https://bairnly-unvamped-billye.ngrok-free.dev/call-test
echo.
ngrok http 8000 --domain=bairnly-unvamped-billye.ngrok-free.dev
