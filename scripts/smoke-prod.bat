@echo off
:: Build + start the production Dockerfiles locally and verify both
:: /health endpoints respond. Always tears the stack down.
::
:: Run from repo root:  scripts\smoke-prod.bat

setlocal
set COMPOSE_FILE=docker-compose.smoke.yml

if not exist "%COMPOSE_FILE%" (
    echo ERROR: %COMPOSE_FILE% not found ^(run from repo root^)
    exit /b 1
)

echo --- Building production Dockerfiles ---
docker compose -f "%COMPOSE_FILE%" build || goto :fail

echo.
echo --- Starting stack ^(waiting for healthchecks^) ---
docker compose -f "%COMPOSE_FILE%" up -d --wait || goto :fail

echo.
echo --- Pinging backend ---
curl -fsS http://localhost:8000/health || goto :fail
echo.

echo --- Pinging frontend ---
curl -fsS http://localhost:8080/health || goto :fail
echo.

echo.
echo OK -- production stack starts and responds.
goto :cleanup

:fail
echo.
echo FAIL -- see logs above. Dumping container logs:
docker compose -f "%COMPOSE_FILE%" logs
set EXITCODE=1
goto :cleanup

:cleanup
echo.
echo --- Tearing down ---
docker compose -f "%COMPOSE_FILE%" down -v --remove-orphans >nul 2>&1
exit /b %EXITCODE%
