@echo off
setlocal
cd /d "%~dp0"

where uv >nul 2>nul
if errorlevel 1 (
    echo [ERROR] uv was not found. Install uv first: https://docs.astral.sh/uv/getting-started/installation/
    pause
    exit /b 1
)

echo Syncing Python dependencies...
uv sync
if errorlevel 1 (
    echo [ERROR] Dependency installation failed.
    pause
    exit /b 1
)

echo Starting Game Control MCP server...
echo This server uses MCP stdio. For Codex use, add it to config.toml so Codex launches it.
uv run python -m game_control.server
set "SERVER_EXIT_CODE=%errorlevel%"

if not "%SERVER_EXIT_CODE%"=="0" (
    echo [ERROR] Server exited with code %SERVER_EXIT_CODE%.
    pause
)

exit /b %SERVER_EXIT_CODE%
