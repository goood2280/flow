@echo off
cd /d "D:\semi all\flow"
echo Killing git processes...
taskkill /f /im git.exe 2>nul
taskkill /f /im git-remote-https.exe 2>nul
timeout /t 2 /nobreak >nul
echo Removing lock files...
del /f /q ".git\index.lock" 2>nul
del /f /q ".git\HEAD.lock" 2>nul
del /f /q ".git\objects\maintenance.lock" 2>nul
echo.
echo Adding setup.py...
git add setup.py
echo.
echo Committing...
git commit -m "fix: TEG 좌표 원점 불일치 수정 — cellAt y축 반전 (좌하단 원점, Chip_Radius 좌표계 일치)"
echo.
echo Pushing to GitHub...
git push origin main
echo.
echo === DONE ===
pause
