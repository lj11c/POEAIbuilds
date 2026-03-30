@echo off
echo === POE AI Build Generator ===
echo.

REM Try py launcher first, then python
where py >nul 2>&1
if %errorlevel% == 0 (
    set PYTHON=py
    goto :found
)
where python >nul 2>&1
if %errorlevel% == 0 (
    set PYTHON=python
    goto :found
)
echo ERROR: Python not found!
echo Please install Python from https://www.python.org/downloads/
echo Make sure to check "Add Python to PATH" during installation.
pause
exit /b 1

:found
echo Using: %PYTHON%
%PYTHON% --version
echo.
echo Installing dependencies...
%PYTHON% -m pip install -r requirements.txt
echo.
echo Starting server... Open http://localhost:8000 in your browser
echo Press Ctrl+C to stop.
echo.
%PYTHON% -m uvicorn app:app --reload --port 8000
pause
