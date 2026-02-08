@echo off
title FC Bot – Railway Auto Deploy

cd /d "C:\Users\Cookiechan\OneDrive\Desktop\FC Bot" || (
  echo ❌ Failed to access project folder.
  pause
  exit /b 1
)

echo.
echo 🚀 Deploying FC Bot to Railway...
echo.

railway up --service splendid-wholeness

echo.
if errorlevel 1 (
  echo ❌ Deployment failed. Check the output above.
) else (
  echo ✅ Deployment completed successfully.
)

echo.
pause
cmd /k
