@echo off
cd /d "D:\semi all\flow"
del /f ".git\index.lock" 2>nul
del /f ".git\HEAD.lock" 2>nul

echo === Git Add ===
git add -A

echo === Git Status ===
git status --short

echo === Git Commit ===
git commit -m "fix: [TEST_POINT] origin top-left parsing + WfSvg highlight"

echo === Git Push ===
git push origin main

echo.
echo === DONE ===
pause
