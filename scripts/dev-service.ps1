param(
    [Parameter(Mandatory = $true)]
    [ValidateSet("start", "stop", "restart", "status")]
    [string]$Action
)

$ErrorActionPreference = "Stop"

# Single canonical local runtime:
#   Docker Compose project "srm_fieldinspect"  ->  db + backend  (root compose + dev overlay)
#   Windows host                                ->  frontend (Vite, :5173)
#
# The backend NO LONGER runs as a Windows Python process. It runs in Docker and
# is the only thing serving http://localhost:8000.

$Root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$PidDir = Join-Path $Root ".service-pids"
$LogDir = Join-Path $Root ".service-logs"
$FrontendDir = Join-Path $Root "frontend"
$BackendEnvFile = Join-Path $Root "backend\.env"
$ComposeRoot = Join-Path $Root "docker-compose.yml"
$ComposeDev = Join-Path $Root "docker-compose.dev.yml"
$ComposeProject = "srm_fieldinspect"
$DbVolume = "srm-fieldinspect-db-data"
$DbContainer = "srm-fieldinspect-db"
$BackendContainer = "srm-fieldinspect-backend"

function Ensure-Dirs {
    New-Item -ItemType Directory -Force -Path $PidDir | Out-Null
    New-Item -ItemType Directory -Force -Path $LogDir | Out-Null
}

function Get-PidPath([string]$Name) {
    return Join-Path $PidDir "$Name.pid"
}

function Get-ServicePid([string]$Name) {
    $path = Get-PidPath $Name
    if (-not (Test-Path $path)) {
        return $null
    }

    $raw = (Get-Content -LiteralPath $path -ErrorAction SilentlyContinue | Select-Object -First 1)
    if (-not $raw) {
        return $null
    }

    $pidValue = 0
    if ([int]::TryParse($raw.Trim(), [ref]$pidValue)) {
        return $pidValue
    }
    return $null
}

function Test-ServiceRunning([string]$Name) {
    $pidValue = Get-ServicePid $Name
    if ($null -eq $pidValue) {
        return $false
    }
    return $null -ne (Get-Process -Id $pidValue -ErrorAction SilentlyContinue)
}

function Get-ListeningPid([int]$Port) {
    try {
        $conn = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue |
            Select-Object -First 1
        if ($null -ne $conn) {
            return [int]$conn.OwningProcess
        }
    } catch {
        return $null
    }
    return $null
}

# Canonical Compose invocation — the ONE command used everywhere. Absolute
# paths + Push-Location keep the compose file's relative mounts (./backend,
# ./docker/init-db.sql) resolving against the repo root regardless of caller CWD.
function Invoke-Compose([string[]]$ComposeArgs) {
    if (-not (Test-Path -LiteralPath $BackendEnvFile)) {
        throw "Missing backend/.env. Copy backend/.env.example and configure local DB settings first."
    }

    Push-Location $Root
    try {
        & docker compose `
            --env-file $BackendEnvFile `
            -p $ComposeProject `
            -f $ComposeRoot `
            -f $ComposeDev `
            @ComposeArgs

        if ($LASTEXITCODE -ne 0) {
            throw "Docker Compose failed with exit code $LASTEXITCODE."
        }
    } finally {
        Pop-Location
    }
}

function Assert-DockerReady {
    docker info *> $null
    if ($LASTEXITCODE -ne 0) {
        throw "Docker is not available. Start Docker Desktop and try again."
    }
}

# NEVER auto-create the DB volume. The system already holds data; an empty
# volume here would silently start a blank database. If the volume is missing,
# stop and let a human restore from backup instead.
function Assert-DbVolume {
    docker volume inspect $DbVolume *> $null
    if ($LASTEXITCODE -ne 0) {
        throw "Database volume '$DbVolume' was not found. Refusing to auto-create an empty volume (existing data would appear lost). Restore from backup or check Docker Desktop, then retry."
    }
}

function Wait-ContainerHealthy([string]$Name, [int]$TimeoutSec = 60) {
    $deadline = (Get-Date).AddSeconds($TimeoutSec)
    do {
        $health = docker inspect `
            --format "{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}" `
            $Name 2>$null
        if ($health -eq "healthy") {
            return
        }
        Start-Sleep -Seconds 2
    } while ((Get-Date) -lt $deadline)

    throw "$Name did not become healthy within $TimeoutSec seconds."
}

function Wait-BackendHttp([int]$TimeoutSec = 60) {
    $deadline = (Get-Date).AddSeconds($TimeoutSec)
    do {
        try {
            $resp = Invoke-WebRequest -UseBasicParsing -Uri "http://localhost:8000/health" -TimeoutSec 5
            if ($resp.StatusCode -eq 200) {
                return
            }
        } catch {
            # not ready yet
        }
        Start-Sleep -Seconds 2
    } while ((Get-Date) -lt $deadline)

    throw "Backend http://localhost:8000/health did not return 200 within $TimeoutSec seconds."
}

function Start-DockerStack {
    Assert-DockerReady
    Assert-DbVolume

    Write-Host "Starting Docker db + backend (project $ComposeProject)..."
    Invoke-Compose @("up", "-d", "db", "backend")

    Write-Host "Waiting for database health..."
    Wait-ContainerHealthy $DbContainer 60
    Write-Host "database: running (healthy)"

    Write-Host "Waiting for backend /health..."
    Wait-BackendHttp 60
    Write-Host "backend: running (healthy) http://localhost:8000"
}

function Stop-DockerStack {
    # Plain stop only — never remove containers, volumes, or networks here.
    Write-Host "Stopping Docker backend + db..."
    Invoke-Compose @("stop", "backend", "db")
}

function Start-OneService(
    [string]$Name,
    [string]$WorkingDirectory,
    [string]$Command,
    [int]$Port
) {
    Ensure-Dirs

    if (Test-ServiceRunning $Name) {
        Write-Host "$Name is already running (PID $(Get-ServicePid $Name))."
        return
    }

    $listenerPid = Get-ListeningPid $Port
    if ($null -ne $listenerPid) {
        Write-Host "$Name port $Port is already in use by PID $listenerPid (leaving it as-is)."
        return
    }

    $stdout = Join-Path $LogDir "$Name.out.log"
    $stderr = Join-Path $LogDir "$Name.err.log"

    $process = Start-Process `
        -FilePath "cmd.exe" `
        -ArgumentList "/c", $Command `
        -WorkingDirectory $WorkingDirectory `
        -WindowStyle Hidden `
        -RedirectStandardOutput $stdout `
        -RedirectStandardError $stderr `
        -PassThru

    Set-Content -LiteralPath (Get-PidPath $Name) -Value $process.Id -Encoding ASCII
    Write-Host "Started $Name (PID $($process.Id)). Logs: $stdout / $stderr"
}

function Stop-OneService([string]$Name) {
    $pidValue = Get-ServicePid $Name
    $pidPath = Get-PidPath $Name

    if ($null -eq $pidValue) {
        Write-Host "$Name is not tracked."
        return
    }

    $process = Get-Process -Id $pidValue -ErrorAction SilentlyContinue
    if ($null -eq $process) {
        Write-Host "$Name PID $pidValue is not running."
        Remove-Item -LiteralPath $pidPath -ErrorAction SilentlyContinue
        return
    }

    Stop-Process -Id $pidValue -Force
    Remove-Item -LiteralPath $pidPath -ErrorAction SilentlyContinue
    Write-Host "Stopped $Name (PID $pidValue)."
}

function Get-ContainerState([string]$Name) {
    $state = docker inspect `
        --format "{{.State.Status}}{{if .State.Health}} ({{.State.Health.Status}}){{end}}" `
        $Name 2>$null
    if ($LASTEXITCODE -eq 0 -and $state) {
        return $state
    }
    return "stopped"
}

function Show-Status {
    Write-Host "compose project: $ComposeProject"
    Write-Host "database (Docker $DbContainer :5432): $(Get-ContainerState $DbContainer)"
    Write-Host "backend  (Docker $BackendContainer :8000): $(Get-ContainerState $BackendContainer)"

    $name = "frontend"
    $port = 5173
    if (Test-ServiceRunning $name) {
        Write-Host "frontend (Windows :$port): running (PID $(Get-ServicePid $name))"
    } else {
        $listenerPid = Get-ListeningPid $port
        if ($null -ne $listenerPid) {
            Write-Host "frontend (Windows :$port): active but not tracked (PID $listenerPid)"
        } else {
            Write-Host "frontend (Windows :$port): stopped"
        }
    }

    Write-Host ""
    Write-Host "Frontend URL: http://localhost:5173/"
    Write-Host "Backend URL:  http://localhost:8000/"
    Write-Host "Logs:         $LogDir"
}

function Start-Services {
    Start-DockerStack

    Start-OneService `
        -Name "frontend" `
        -WorkingDirectory $FrontendDir `
        -Command "npm.cmd run dev" `
        -Port 5173

    Write-Host ""
    Show-Status
}

function Stop-Services {
    Stop-OneService "frontend"
    Stop-DockerStack
}

switch ($Action) {
    "start" {
        Start-Services
    }
    "stop" {
        Stop-Services
    }
    "restart" {
        Stop-Services
        Start-Sleep -Seconds 2
        Start-Services
    }
    "status" {
        Show-Status
    }
}
