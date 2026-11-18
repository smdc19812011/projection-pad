@echo off
setlocal EnableDelayedExpansion
title Projection Pad — Windows Build

echo.
echo  ================================================================
echo   Projection Pad  ^|  Windows .exe Builder
echo  ================================================================
echo.

REM ── Check Python ──────────────────────────────────────────────────
where python >nul 2>nul
if errorlevel 1 (
    echo  ERROR: Python not found in PATH.
    echo  Install Python 3.8+ from https://python.org
    echo  Make sure to check "Add Python to PATH" during install.
    pause & exit /b 1
)
for /f "tokens=2 delims= " %%v in ('python --version 2^>^&1') do set PYVER=%%v
echo  Python %PYVER% found.
echo.

REM ── Install / upgrade dependencies ────────────────────────────────
echo  Installing dependencies...
echo.
pip install --upgrade PyQt5 mss pywin32 pyinstaller
if errorlevel 1 (
    echo.
    echo  ERROR: pip install failed. Check your internet connection.
    pause & exit /b 1
)
echo.

REM ── Build choices ─────────────────────────────────────────────────
echo  Build type:
echo    [1] Single file  (ProjectionPad.exe — easier to distribute, slower first launch)
echo    [2] Folder       (dist\ProjectionPad\ — faster launch, zip before sharing)
echo.
set /p CHOICE=  Choose [1/2, default=1]:
if "%CHOICE%"=="2" goto BUILD_DIR
goto BUILD_ONE

:BUILD_ONE
echo.
echo  Building single-file exe...
pyinstaller ^
    --onefile ^
    --windowed ^
    --name "ProjectionPad" ^
    --hidden-import=PyQt5.sip ^
    --hidden-import=mss ^
    --hidden-import=mss.windows ^
    --hidden-import=win32gui ^
    --hidden-import=win32process ^
    --hidden-import=win32api ^
    --hidden-import=win32con ^
    --collect-submodules=mss ^
    projection_pad.py
goto DONE

:BUILD_DIR
echo.
echo  Building folder distribution...
pyinstaller ^
    --onedir ^
    --windowed ^
    --name "ProjectionPad" ^
    --hidden-import=PyQt5.sip ^
    --hidden-import=mss ^
    --hidden-import=mss.windows ^
    --hidden-import=win32gui ^
    --hidden-import=win32process ^
    --hidden-import=win32api ^
    --hidden-import=win32con ^
    --collect-submodules=mss ^
    projection_pad.py
goto DONE

:DONE
echo.
if exist "dist\ProjectionPad.exe" (
    for %%F in ("dist\ProjectionPad.exe") do set SIZE=%%~zF
    set /a SIZEMB=!SIZE! / 1048576
    echo  ================================================================
    echo   Build successful^^!
    echo   Output : dist\ProjectionPad.exe  (~!SIZEMB! MB^)
    echo  ================================================================
    echo.
    set /p OPEN=  Open dist\ folder now? [Y/n]:
    if /i not "!OPEN!"=="n" explorer dist
) else if exist "dist\ProjectionPad\ProjectionPad.exe" (
    echo  ================================================================
    echo   Build successful^^!
    echo   Output : dist\ProjectionPad\ProjectionPad.exe
    echo   Zip the dist\ProjectionPad\ folder to share it.
    echo  ================================================================
    echo.
    set /p OPEN=  Open dist\ folder now? [Y/n]:
    if /i not "!OPEN!"=="n" explorer dist\ProjectionPad
) else (
    echo  Build may have failed — check the output above for errors.
    echo  Common fixes:
    echo    - Run as Administrator
    echo    - Disable antivirus temporarily (it can block PyInstaller)
    echo    - Try: pip install --upgrade pyinstaller
)
echo.
pause
