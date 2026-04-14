@echo off
title APIhdy Supreme V5.0.0
color 0B
cd /d %~dp0

echo ========================================================
echo        FOX DI CLOUD SUPREME V5.0.0
echo ========================================================
echo.
echo Launching native proxy server...

:: Powershell execution
powershell -NoProfile -ExecutionPolicy Bypass -File "./server.ps1"

if %ERRORLEVEL% NEQ 0 (
    echo.
    echo [ERROR] Fail to start server.
    pause
)
