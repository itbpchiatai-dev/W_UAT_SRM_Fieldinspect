@echo off
chcp 65001 >nul
pushd "%~dp0"
echo.
echo ================================================
echo   Web App Standard — Project Setup
echo ================================================
echo.

:: Check Python
python --version >nul 2>&1
if errorlevel 1 (
    echo [ERROR] ไม่พบ Python
    echo.
    echo ดาวน์โหลดและติดตั้งก่อน:
    echo https://www.python.org/downloads/
    echo.
    echo หมายเหตุ: ตอนติดตั้งให้ติ๊ก "Add Python to PATH" ด้วย
    echo.
    pause
    popd
    exit /b 1
)

:: Check Python version >= 3.12
for /f "tokens=2" %%v in ('python --version 2^>^&1') do set PYVER=%%v
echo Python %PYVER% พบแล้ว
:: Reject < 3.12 (scaffold uses py3.12 syntax + requires-python = ">=3.12")
python scripts\check_python.py >nul 2>&1
if errorlevel 1 (
    echo.
    echo [ERROR] Python %PYVER% เก่าเกินไป — ต้องผ่าน 3.12+
    echo.
    echo ดาวน์โหลดและติดตั้งใหม่: https://www.python.org/downloads/
    echo.
    pause
    popd
    exit /b 1
)

:: Check Docker CLI (binary). setup.py needs it to query containers; even
:: if Docker Engine is down setup.py will route to native-pg-on-5432 — so
:: only the CLI is a hard requirement here. Engine state is checked by
:: setup.py with a soft warning so users with native Postgres still work.
docker --version >nul 2>&1
if errorlevel 1 (
    echo.
    echo [ERROR] ไม่พบ Docker Desktop
    echo.
    echo ดาวน์โหลดและติดตั้งก่อน:
    echo https://www.docker.com/products/docker-desktop/
    echo.
    pause
    popd
    exit /b 1
)

:: Check Node.js
node --version >nul 2>&1
if errorlevel 1 (
    echo.
    echo [ERROR] ไม่พบ Node.js
    echo.
    echo ดาวน์โหลดและติดตั้งก่อน:
    echo https://nodejs.org/  ^(เลือก LTS^)
    echo.
    pause
    popd
    exit /b 1
)

echo.
echo ทุกอย่างพร้อม — เริ่ม setup...
echo.

python scripts\setup.py
popd
