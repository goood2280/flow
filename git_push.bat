@echo off
chcp 65001 >nul
cd /d "D:\semi all\flow"

if exist ".git\index.lock" del ".git\index.lock"

git add -A
git status --short
git commit -m "feat: TEG extended check - prefix reorder, match_rule labels, purple checklist, direction split"
git push

echo.
echo Done!
pause >nul
