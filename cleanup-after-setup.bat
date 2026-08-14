@echo off
chcp 65001 >nul
pushd "%~dp0"
python scripts\cleanup-after-setup.py %*
set RC=%ERRORLEVEL%
popd
exit /b %RC%
