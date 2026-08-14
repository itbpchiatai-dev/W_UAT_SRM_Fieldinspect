@echo off
chcp 65001 >nul
pushd "%~dp0"
echo.
echo ================================================
echo   Web App Standard — Existing Database Setup
echo ================================================
echo.
echo โหมดนี้จะเชื่อมต่อ Database เดิม
echo - ไม่สร้าง user หรือ database
echo - ไม่รัน migration หรือ seed อัตโนมัติ
echo - ไม่แก้ schema เดิม
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

:: Existing DB mode does not require Docker.
:: Node.js is still required for the generated frontend.
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
echo ทุกอย่างพร้อม — เริ่ม setup สำหรับ Database เดิม...
echo.

python scripts\setup.py --database-mode existing
popd
