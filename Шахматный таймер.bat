@echo off
cd /d "%~dp0"
where pyw >nul 2>&1
if errorlevel 1 goto try_pythonw
start "" /D "%~dp0" pyw -3 "%~dp0chess_timer.py"
goto :eof

:try_pythonw
where pythonw >nul 2>&1
if errorlevel 1 goto fail
start "" /D "%~dp0" pythonw "%~dp0chess_timer.py"
goto :eof

:fail
echo Python 3 not found.
pause
