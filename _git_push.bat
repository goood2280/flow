@echo off
cd /d "D:\semi all\flow"
del /f ".git\index.lock" 2>nul
del /f ".git\HEAD.lock" 2>nul
echo Current commit:
git log --oneline -1
echo.
echo Pushing to GitHub...
git push origin main
echo.
echo === DONE ===
pause
