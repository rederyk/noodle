@echo off
REM noodle — one-click launcher for Windows. Double-click this file.
REM It checks Docker Desktop, builds + starts the container, then opens
REM your browser at the node editor.
setlocal
cd /d "%~dp0"

where docker >nul 2>&1
if errorlevel 1 (
  echo.
  echo Docker is not installed.
  echo Install Docker Desktop for Windows, then run this file again:
  echo   https://docs.docker.com/desktop/install/windows-install/
  echo.
  pause
  exit /b 1
)

docker info >nul 2>&1
if errorlevel 1 (
  echo.
  echo Docker Desktop is installed but not running.
  echo Open Docker Desktop, wait until it says "Running", then run this again.
  echo.
  pause
  exit /b 1
)

echo.
echo Building and starting noodle (first run downloads ~1 GB, be patient)...
docker compose up -d --build
if errorlevel 1 (
  echo.
  echo Something went wrong. Check the messages above.
  pause
  exit /b 1
)

echo.
echo Waiting for the app to come up...
for /l %%i in (1,1,60) do (
  curl -fsS http://localhost:8090/health >nul 2>&1 && goto :ready
  timeout /t 2 >nul
)
:ready

echo.
echo noodle is running at http://localhost:8090/nodes
echo Stop it later with:  docker compose down
start "" http://localhost:8090/nodes
echo.
pause
