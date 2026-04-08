@echo off
REM ═══════════════════════════════════════════════════════════════════════════
REM  setup-piper.bat  — Download Piper TTS executable only (no model download)
REM  Place your .onnx model files manually into: piper\models\
REM ═══════════════════════════════════════════════════════════════════════════
echo.
echo  Piper TTS Setup
echo  ───────────────────────────────────────────────────────
echo  This will:
echo    1. Clean any previous piper\ folder
echo    2. Download Piper TTS executable  (Windows x64)
echo    3. Create piper\models\ folder for you to drop your model files
echo.
echo  NOTE: Model files (.onnx + .onnx.json) must be placed manually
echo        into the  piper\models\  folder after this script finishes.
echo.

REM ── 0. Clean previous piper folder ──────────────────────────────────────────
if exist "piper" (
    echo [0/2] Cleaning previous piper\ folder...
    rmdir /S /Q "piper"
    echo    Cleaned.
)

REM ── Create fresh directories ─────────────────────────────────────────────────
mkdir piper
mkdir piper\models
echo    Created piper\ and piper\models\

REM ── 1. Download Piper Windows binary ────────────────────────────────────────
echo.
echo [1/2] Downloading Piper TTS for Windows (x64)...
powershell -Command "Invoke-WebRequest -Uri 'https://github.com/rhasspy/piper/releases/download/2023.11.14-2/piper_windows_amd64.zip' -OutFile 'piper_win.zip' -UseBasicParsing"

if not exist "piper_win.zip" (
    echo ERROR: Download failed. Check your internet connection.
    pause
    exit /b 1
)

REM ── 2. Extract ───────────────────────────────────────────────────────────────
echo [2/2] Extracting...
powershell -Command "Expand-Archive -Path 'piper_win.zip' -DestinationPath 'piper_extract' -Force"

if exist "piper_extract\piper\piper.exe" (
    copy /Y "piper_extract\piper\piper.exe" "piper\piper.exe" >nul
    copy /Y "piper_extract\piper\*.dll"     "piper\"          2>nul
) else if exist "piper_extract\piper.exe" (
    copy /Y "piper_extract\piper.exe" "piper\piper.exe" >nul
    copy /Y "piper_extract\*.dll"     "piper\"           2>nul
) else (
    powershell -Command "Get-ChildItem -Recurse -Path 'piper_extract' -Filter 'piper.exe' | Select-Object -First 1 | Copy-Item -Destination 'piper\'"
    powershell -Command "Get-ChildItem -Recurse -Path 'piper_extract' -Filter '*.dll'    | ForEach-Object { Copy-Item $_.FullName -Destination 'piper\' }"
)

REM Cleanup temp files
del /Q piper_win.zip      2>nul
rmdir /S /Q piper_extract 2>nul

REM ── Result ───────────────────────────────────────────────────────────────────
echo.
if exist "piper\piper.exe" (
    echo  piper\piper.exe  ---- OK
) else (
    echo  piper\piper.exe  ---- NOT FOUND  ^(check errors above^)
    pause
    exit /b 1
)

echo.
echo  ───────────────────────────────────────────────────────
echo  NEXT STEP:  Drop your model files into  piper\models\
echo.
echo  Expected files per voice:
echo    piper\models\your-voice.onnx
echo    piper\models\your-voice.onnx.json
echo.
echo  Then update PIPER_MODEL in .env to match, e.g.:
echo    PIPER_MODEL=piper/models/your-voice.onnx
echo  ───────────────────────────────────────────────────────
echo.
pause
