<#
.SYNOPSIS
  Second Brain — Docker Compose helpers (Windows). Same idea as Makefile on macOS/Linux.

.DESCRIPTION
  Run from repo root or any folder: scripts resolve the project root automatically.
  Docker Compose reads `.env` from the project root when you `Set-Location` there.

.EXAMPLE
  .\win\brain.ps1 up
  .\win\brain.ps1 up-backup
  .\win\brain.ps1 index
  .\win\brain.ps1 backup-once
#>
param(
    [Parameter(Position = 0)]
    [ValidateSet("help", "up", "down", "restart", "up-backup", "down-backup", "index", "backup-once", "backup-retry", "backup-retention", "logs", "ps", "build")]
    [string]$Command = "help"
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

function Invoke-Compose {
    param([string[]]$Args)
    & docker compose @Args
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
}

switch ($Command) {
    "help" {
        Write-Host @"
Second Brain (Windows)

  .\win\brain.ps1 up           Start stack (core services)
  .\win\brain.ps1 up-backup    Start stack + backup (set BRAIN_* in .env or use .brain-local/ defaults)
  .\win\brain.ps1 down         Stop stack
  .\win\brain.ps1 down-backup  Stop including backup container
  .\win\brain.ps1 restart      Restart stack
  .\win\brain.ps1 index        Full vault reindex (Chroma)
  .\win\brain.ps1 backup-once  One full snapshot (optional: set BACKUP args in .env)
  .\win\brain.ps1 backup-retry Retry failed iCloud copies
  .\win\brain.ps1 backup-retention  Run retention cleanup
  .\win\brain.ps1 logs         docker compose logs -f
  .\win\brain.ps1 ps           docker compose ps
  .\win\brain.ps1 build        Rebuild mcp-server + watcher

Copy .env.example to .env and set at least VAULT_PATH.
For backup, also set BRAIN_SNAPSHOT_LOCAL_DIR, BRAIN_ICLOUD_BACKUP_DIR, BRAIN_STATE_DIR.
"@
    }
    "up" {
        Invoke-Compose @("up", "-d")
    }
    "up-backup" {
        Invoke-Compose @("--profile", "backup", "up", "-d")
    }
    "down" {
        Invoke-Compose @("down")
    }
    "down-backup" {
        Invoke-Compose @("--profile", "backup", "down")
    }
    "restart" {
        Invoke-Compose @("restart")
    }
    "index" {
        Invoke-Compose @("--profile", "index", "run", "--rm", "indexer", "python3", "indexer.py", "--full")
    }
    "backup-once" {
        Invoke-Compose @("--profile", "backup", "run", "--rm", "backup", "python", "-m", "app", "snapshot-once")
    }
    "backup-retry" {
        Invoke-Compose @("--profile", "backup", "run", "--rm", "backup", "python", "-m", "app", "retry")
    }
    "backup-retention" {
        Invoke-Compose @("--profile", "backup", "run", "--rm", "backup", "python", "-m", "app", "retention")
    }
    "logs" {
        Invoke-Compose @("logs", "-f")
    }
    "ps" {
        Invoke-Compose @("ps")
    }
    "build" {
        Invoke-Compose @("build", "--no-cache", "mcp-server", "watcher")
        Invoke-Compose @("up", "-d", "--force-recreate", "mcp-server", "watcher")
    }
}
