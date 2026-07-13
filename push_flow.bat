@echo off
cd /d "%~dp0"
echo Pushing flow to origin/main...
git push origin main
echo.
git log --oneline -1
pause
