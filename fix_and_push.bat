@echo off
cd /d d:\libot
echo ===== Current Git Status =====
git status

echo.
echo ===== Adding changes =====
git add .gitignore tg_bot/config.env.example

echo.
echo ===== Committing changes =====
git commit -m "Remove sensitive files and add example config"

echo.
echo ===== Force pushing to remote =====
git push -u origin main --force

echo.
echo ===== Done =====
pause
