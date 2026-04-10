@echo off
REM Second Brain — thin wrapper for users who prefer CMD over PowerShell.
REM Usage: win\brain.cmd up | up-backup | down | index | backup-once | ...
setlocal
cd /d "%~dp0.."
if "%~1"=="" goto :help

if /I "%~1"=="up" docker compose up -d & goto :eof
if /I "%~1"=="up-backup" docker compose --profile backup up -d & goto :eof
if /I "%~1"=="down" docker compose down & goto :eof
if /I "%~1"=="down-backup" docker compose --profile backup down & goto :eof
if /I "%~1"=="restart" docker compose restart & goto :eof
if /I "%~1"=="index" docker compose --profile index run --rm indexer python3 indexer.py --full & goto :eof
if /I "%~1"=="backup-once" docker compose --profile backup run --rm backup python -m app snapshot-once & goto :eof
if /I "%~1"=="backup-retry" docker compose --profile backup run --rm backup python -m app retry & goto :eof
if /I "%~1"=="backup-retention" docker compose --profile backup run --rm backup python -m app retention & goto :eof
if /I "%~1"=="logs" docker compose logs -f & goto :eof
if /I "%~1"=="ps" docker compose ps & goto :eof
if /I "%~1"=="build" docker compose build --no-cache mcp-server watcher && docker compose up -d --force-recreate mcp-server watcher & goto :eof

:help
echo Usage: win\brain.cmd up ^| up-backup ^| down ^| index ^| backup-once ^| ...
echo Or use PowerShell: .\win\brain.ps1 help
exit /b 1
