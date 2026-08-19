param(
    [Parameter(Mandatory = $true)]
    [ValidateSet("start", "stop", "restart", "status")]
    [string]$Action,

    # Round 8-22C - test/dry-run hook only. When set, dot-sourcing this file
    # (". .\dev-service.ps1 -Action status -DotSource") defines every
    # function below without executing the real start/stop/restart/status
    # dispatch at the bottom, so a test script can call the functions
    # directly against temporary ports/processes. -Action is still required
    # (ValidateSet) but its value is never used when -DotSource is set.
    # None of start-service.bat/stop-service.bat/restart-service.bat/
    # status-service.bat ever pass this - production behavior is unchanged.
    [switch]$DotSource
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

# Round 8-22C - the frontend is the only Windows-host (non-Docker) process
# this script manages, identified by its listening port PLUS an ownership
# check (see Test-IsOwnedListener) so this repo's dev server is never
# confused with any other program/project that happens to be on the port.
$FrontendPort = 5173
$FrontendOwnerPattern = "vite"

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

function Get-ProcessCommandLine([int]$ProcessId) {
    try {
        $ci = Get-CimInstance -ClassName Win32_Process -Filter "ProcessId=$ProcessId" -ErrorAction SilentlyContinue
        if ($null -ne $ci) {
            return $ci.CommandLine
        }
    } catch {
        return $null
    }
    return $null
}

# Round 8-22C item B - ownership guard. A PID must never be adopted, tracked,
# or stopped as "this repo's $Name process" on the strength of the port
# number alone (any unrelated program could be squatting on it) or the
# process name alone (taskkill /IM node.exe-style matching would hit every
# Node process on the machine, including other projects' dev servers). The
# process's own command line must prove BOTH that it is the expected kind of
# process (OwnerPattern, e.g. "vite") AND that it was launched from/for THIS
# repo's working directory - Vite's actual listener is a node.exe running
# "<WorkingDirectory>\node_modules\vite\bin\vite.js" (or equivalent), so that
# absolute path is always present in a genuine match.
#
# Round 8-22C.1 - this is a pure "is this process one of ours?" test on any
# PID (it is also applied to the TRACKED pid, which may not be listening at
# all). Passing it never implies the process is serving $Port - only
# Get-ServiceState's Listener/Verified fields establish that.
function Test-IsOwnedListener([int]$ProcessId, [string]$WorkingDirectory, [string]$OwnerPattern) {
    if ($null -eq $ProcessId -or $ProcessId -le 0) {
        return $false
    }
    if (-not (Get-Process -Id $ProcessId -ErrorAction SilentlyContinue)) {
        return $false
    }
    $cmdLine = Get-ProcessCommandLine $ProcessId
    if ([string]::IsNullOrEmpty($cmdLine)) {
        return $false
    }
    $hasWorkingDir = $cmdLine.IndexOf($WorkingDirectory, [System.StringComparison]::OrdinalIgnoreCase) -ge 0
    $hasPattern = $cmdLine -match [regex]::Escape($OwnerPattern)
    return $hasWorkingDir -and $hasPattern
}

function Wait-PortFree([int]$Port, [int]$TimeoutSec) {
    $deadline = (Get-Date).AddSeconds($TimeoutSec)
    do {
        if ($null -eq (Get-ListeningPid $Port)) {
            return $true
        }
        Start-Sleep -Milliseconds 500
    } while ((Get-Date) -lt $deadline)
    return $false
}

# Round 8-22C - the single source of truth for "what is really going on"
# with a Windows-host service, used identically by start/stop/status so the
# three can never disagree with each other.
#
# Round 8-22C.1 - THE LISTENER IS THE SOURCE OF TRUTH. Verified is derived
# ONLY from the process actually LISTENing on $Port, never from the tracked
# PID. The previous version preferred an alive+owned tracked PID, so a stale
# tracked PID that merely still passed the ownership check could be reported
# as Verified even when a completely different process held the port - which
# both mis-classified the state AND suppressed Mismatch (Mismatch was derived
# from Verified being null). A service IS the thing serving its port; a PID
# file is only bookkeeping about it, and bookkeeping never outranks reality.
#
#   Tracked           - PID in the .pid file (or $null if none/unparseable)
#   TrackedAlive      - $true if that PID is a running process right now
#   TrackedOwned      - $true if that PID is alive AND passes the ownership
#                       check (i.e. it is genuinely one of ours)
#   Listener          - PID actually LISTENing on $Port right now (or $null)
#   ListenerOwned     - $true if that listener passes the ownership check
#   TrackedIsListener - $true if the tracked PID IS the current listener
#                       (the only state in which tracking is correct)
#   TrackedStray      - $true if the tracked PID is alive but is NOT the
#                       listener: stale/stray bookkeeping, never "ready"
#   Verified          - the listener PID, and ONLY when ListenerOwned;
#                       $null otherwise. The one PID that may be treated as
#                       "this repo's $Name process" for start/stop purposes.
#   Mismatch          - $true whenever something IS listening on $Port but
#                       ownership does not check out. Depends solely on the
#                       listener - no tracked PID can ever clear it. Never
#                       touch a Mismatch listener.
function Get-ServiceState([string]$Name, [string]$WorkingDirectory, [int]$Port, [string]$OwnerPattern) {
    $tracked = Get-ServicePid $Name
    $trackedAlive = ($null -ne $tracked) -and ($null -ne (Get-Process -Id $tracked -ErrorAction SilentlyContinue))
    $trackedOwned = $trackedAlive -and (Test-IsOwnedListener $tracked $WorkingDirectory $OwnerPattern)

    $listener = Get-ListeningPid $Port
    $listenerOwned = ($null -ne $listener) -and (Test-IsOwnedListener $listener $WorkingDirectory $OwnerPattern)

    $verified = $null
    if ($listenerOwned) {
        $verified = $listener
    }
    $mismatch = ($null -ne $listener) -and (-not $listenerOwned)
    $trackedIsListener = ($null -ne $tracked) -and ($null -ne $listener) -and ($tracked -eq $listener)
    $trackedStray = $trackedAlive -and (-not $trackedIsListener)

    return [PSCustomObject]@{
        Tracked           = $tracked
        TrackedAlive      = [bool]$trackedAlive
        TrackedOwned      = [bool]$trackedOwned
        Listener          = $listener
        ListenerOwned     = [bool]$listenerOwned
        TrackedIsListener = [bool]$trackedIsListener
        TrackedStray      = [bool]$trackedStray
        Verified          = $verified
        Mismatch          = $mismatch
    }
}

# Canonical Compose invocation - the ONE command used everywhere. Absolute
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

# Round 8-22C item D - Restart/Start must confirm the NEW frontend process is
# actually serving HTTP, not just that a port opened (a process can bind a
# socket before Vite has finished its own startup work).
function Wait-FrontendHttp([int]$TimeoutSec = 60) {
    $deadline = (Get-Date).AddSeconds($TimeoutSec)
    do {
        try {
            $resp = Invoke-WebRequest -UseBasicParsing -Uri "http://localhost:$FrontendPort/" -TimeoutSec 5
            if ($resp.StatusCode -eq 200) {
                return
            }
        } catch {
            # not ready yet
        }
        Start-Sleep -Seconds 1
    } while ((Get-Date) -lt $deadline)

    throw "Frontend http://localhost:$FrontendPort/ did not return 200 within $TimeoutSec seconds."
}

# Round 8-22C item E - a single, non-blocking probe for `status` (read-only:
# one quick request, never a retry loop, never starts/stops anything).
function Test-HttpOk([string]$Uri, [int]$TimeoutSec = 3) {
    try {
        $resp = Invoke-WebRequest -UseBasicParsing -Uri $Uri -TimeoutSec $TimeoutSec
        if ($resp.StatusCode -eq 200) {
            return "ready (HTTP 200)"
        }
        return "responding (HTTP $($resp.StatusCode))"
    } catch {
        return "not responding"
    }
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
    # Plain stop only - never remove containers, volumes, or networks here.
    Write-Host "Stopping Docker backend + db..."
    Invoke-Compose @("stop", "backend", "db")
}

function Start-OneService(
    [string]$Name,
    [string]$WorkingDirectory,
    [string]$Command,
    [int]$Port,
    [string]$OwnerPattern,
    [int]$StartTimeoutSec = 60
) {
    Ensure-Dirs
    $pidPath = Get-PidPath $Name
    $state = Get-ServiceState $Name $WorkingDirectory $Port $OwnerPattern

    # Round 8-22C item D / 8-22C.1 item 4 - already running: an owned
    # LISTENER on $Port (whether or not it is the tracked PID) is the only
    # thing that counts as "already running". Adopt it by writing its real
    # listener PID to the PID file, rather than launching a second instance
    # that would just fail to bind the same port. An alive+owned tracked PID
    # that is NOT the listener can never reach this branch (see
    # Get-ServiceState: Verified comes from the listener only).
    if ($null -ne $state.Verified) {
        Set-Content -LiteralPath $pidPath -Value $state.Verified -Encoding ASCII
        if ($state.TrackedIsListener) {
            Write-Host "$Name is already running (PID $($state.Verified))."
        } else {
            Write-Host "$Name port $Port is already serving this repo's dev server (PID $($state.Verified)) - adopted, not restarted."
        }
        if ($state.TrackedStray) {
            Write-Host "  note: previously tracked PID $($state.Tracked) is still running but is NOT serving port $Port - tracking now points at the real listener. Stop that process manually if it is not wanted."
        }
        return
    }

    # Round 8-22C item B / 8-22C.1 item 2 - the port is taken by something we
    # could NOT verify as ours. Fail loudly with the PID rather than silently
    # leaving it "as-is" (the pre-8-22C behavior) or, worse, trying to start
    # over it. A tracked PID of our own can never suppress this.
    if ($state.Mismatch) {
        throw "$Name port $Port is already in use by PID $($state.Listener), which does NOT look like this repo's $Name process (command line does not reference $WorkingDirectory / '$OwnerPattern'). Refusing to start a second instance or touch that process - free port $Port manually and retry."
    }

    # Round 8-22C.1 item 3 - nothing is listening on $Port. A tracked PID may
    # still be alive (e.g. a Vite that fell back to another port), but it is
    # NOT this service: say so explicitly and never report it as ready. It is
    # left running untouched - only its stale tracking is cleared, since the
    # port is free and a fresh instance is about to take it.
    if ($state.TrackedStray) {
        Write-Host "$Name tracked PID $($state.Tracked) is running but is NOT listening on port $Port (stale/stray tracking) - it is NOT this service. Leaving that process untouched and starting a new $Name."
    }
    Remove-Item -LiteralPath $pidPath -ErrorAction SilentlyContinue

    $stdout = Join-Path $LogDir "$Name.out.log"
    $stderr = Join-Path $LogDir "$Name.err.log"

    $launcher = Start-Process `
        -FilePath "cmd.exe" `
        -ArgumentList "/c", $Command `
        -WorkingDirectory $WorkingDirectory `
        -WindowStyle Hidden `
        -RedirectStandardOutput $stdout `
        -RedirectStandardError $stderr `
        -PassThru

    Write-Host "Launching $Name (launcher PID $($launcher.Id)) - waiting for port $Port..."

    # Round 8-22C item A - the launcher (cmd.exe) is NEVER what ends up
    # listening on $Port: npm/Vite spawn their own child node.exe for that.
    # Poll for the real listener instead of trusting $launcher.Id, bounded
    # by $StartTimeoutSec. If the launcher itself dies first (e.g. npm not
    # found, a syntax error), fail immediately and clearly rather than
    # waiting out the full timeout - and report only the LOG PATHS, never
    # their contents (a crash trace could contain env values).
    $deadline = (Get-Date).AddSeconds($StartTimeoutSec)
    $listenerPid = $null
    while ((Get-Date) -lt $deadline) {
        $candidate = Get-ListeningPid $Port
        if ($null -ne $candidate) {
            $listenerPid = $candidate
            break
        }
        if ($null -eq (Get-Process -Id $launcher.Id -ErrorAction SilentlyContinue)) {
            throw "$Name launcher (PID $($launcher.Id)) exited before port $Port opened. See logs: $stdout / $stderr"
        }
        Start-Sleep -Milliseconds 500
    }

    if ($null -eq $listenerPid) {
        throw "$Name did not open port $Port within $StartTimeoutSec seconds. See logs: $stdout / $stderr"
    }

    if (-not (Test-IsOwnedListener $listenerPid $WorkingDirectory $OwnerPattern)) {
        throw "$Name port $Port opened (PID $listenerPid) but that process does not look like this repo's $Name (command line does not reference $WorkingDirectory / '$OwnerPattern'). Refusing to track it. See logs: $stdout / $stderr"
    }

    Set-Content -LiteralPath $pidPath -Value $listenerPid -Encoding ASCII
    Write-Host "Started $Name (PID $listenerPid, launcher PID $($launcher.Id)). Logs: $stdout / $stderr"
}

function Stop-OneService(
    [string]$Name,
    [string]$WorkingDirectory,
    [int]$Port,
    [string]$OwnerPattern,
    [int]$StopTimeoutSec = 30
) {
    $pidPath = Get-PidPath $Name
    $state = Get-ServiceState $Name $WorkingDirectory $Port $OwnerPattern

    # Round 8-22C item B - something is listening on $Port but it is proven
    # NOT to be this repo's process. Never kill it, never guess by name. A
    # tracked PID of our own does not change that (8-22C.1 item 2): the
    # listener still belongs to someone else, so there is nothing here for
    # this script to stop.
    if ($state.Mismatch) {
        Write-Host "${Name}: port $Port is in use by PID $($state.Listener), which does NOT look like this repo's $Name process - leaving it untouched. Nothing to stop."
        if ($state.TrackedStray) {
            Write-Host "  note: tracked PID $($state.Tracked) is still running but is NOT the listener on port $Port - left untouched; stop it manually if it is not wanted."
        }
        Remove-Item -LiteralPath $pidPath -ErrorAction SilentlyContinue
        return
    }

    # Round 8-22C.1 item 3 - no owned listener on $Port. Whatever the PID
    # file says, this service is not serving. A still-alive tracked PID is
    # a stray (e.g. a Vite that fell back to a different port) and is
    # deliberately NOT killed: it is not the process this command targets,
    # and killing a same-repo dev server on another port would be a
    # surprising, unrequested side effect. It is reported instead, and only
    # the stale bookkeeping is cleared.
    if ($null -eq $state.Verified) {
        if ($state.TrackedStray) {
            Write-Host "$Name is not listening on port $Port. Tracked PID $($state.Tracked) is still running but is NOT this service (stale/stray tracking) - left untouched; stop it manually if it is not wanted."
        } else {
            Write-Host "$Name is not running."
        }
        Remove-Item -LiteralPath $pidPath -ErrorAction SilentlyContinue
        return
    }

    # Round 8-22C.1 - the ONLY process this command ever stops: the owned
    # listener on $Port.
    $targetPid = $state.Verified
    if (-not $state.TrackedIsListener) {
        # Round 8-22C item C - the PID file was stale, missing, or pointing
        # at a different process, but this repo's own process is still the
        # real listener on $Port. Stop THAT one instead of reporting "not
        # running" and leaving it orphaned.
        Write-Host "$Name PID file did not point at the listener - port $Port is held by this repo's process (PID $targetPid); stopping it."
        if ($state.TrackedStray) {
            Write-Host "  note: tracked PID $($state.Tracked) is a different, still-running process - left untouched; stop it manually if it is not wanted."
        }
    }

    Stop-Process -Id $targetPid -Force
    Remove-Item -LiteralPath $pidPath -ErrorAction SilentlyContinue

    # Round 8-22C item C - never report success until the port is verifiably
    # free; a signalled-but-not-yet-exited process (or an orphaned sibling
    # still holding the socket) must fail loudly instead.
    if (-not (Wait-PortFree $Port $StopTimeoutSec)) {
        throw "$Name (PID $targetPid) was signalled to stop, but port $Port is still in use after $StopTimeoutSec seconds. NOT reporting success - check for a stuck process on port $Port."
    }
    Write-Host "Stopped $Name (PID $targetPid) - port $Port is free."
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
    # Read-only (item E): every call below is a query (Get-*, Test-*, one
    # non-retrying HTTP probe) - nothing here starts, stops, or writes a PID
    # file.
    Write-Host "compose project: $ComposeProject"
    Write-Host "database (Docker $DbContainer :5432): $(Get-ContainerState $DbContainer)"
    Write-Host "backend  (Docker $BackendContainer :8000): $(Get-ContainerState $BackendContainer)"

    # Round 8-22C.1 - the port's listener decides the headline state; the
    # tracked PID only ever decides whether tracking is CORRECT. A stray
    # tracked PID is always reported on its own line so it can never be
    # mistaken for the running service.
    $state = Get-ServiceState "frontend" $FrontendDir $FrontendPort $FrontendOwnerPattern
    if ($null -ne $state.Verified) {
        if ($state.TrackedIsListener) {
            Write-Host "frontend (Windows :$FrontendPort): running, tracked (PID $($state.Verified))"
        } else {
            Write-Host "frontend (Windows :$FrontendPort): running (PID $($state.Verified)) but NOT tracked correctly - run start to adopt it"
        }
    } elseif ($state.Mismatch) {
        Write-Host "frontend (Windows :$FrontendPort): port in use by a DIFFERENT process (PID $($state.Listener)) - not this repo's frontend"
    } else {
        Write-Host "frontend (Windows :$FrontendPort): stopped (nothing listening on :$FrontendPort)"
    }
    if ($state.TrackedStray) {
        $ownedNote = if ($state.TrackedOwned) { "one of this repo's processes" } else { "not recognizable as this repo's process" }
        Write-Host "  tracked PID $($state.Tracked) is running but is NOT the listener on :$FrontendPort (stale/stray tracking; $ownedNote)"
    }

    Write-Host ""
    Write-Host "Frontend HTTP: $(Test-HttpOk "http://localhost:$FrontendPort/")"
    Write-Host "Backend  HTTP: $(Test-HttpOk 'http://localhost:8000/health')"

    Write-Host ""
    Write-Host "Frontend URL: http://localhost:$FrontendPort/"
    Write-Host "Backend URL:  http://localhost:8000/"
    Write-Host "Logs:         $LogDir"
}

function Start-Services {
    Start-DockerStack

    Start-OneService `
        -Name "frontend" `
        -WorkingDirectory $FrontendDir `
        -Command "npm.cmd run dev" `
        -Port $FrontendPort `
        -OwnerPattern $FrontendOwnerPattern

    # Round 8-22C.1 item 4 - readiness ONLY. Start-OneService has already
    # proven ownership of the listener PID (Test-IsOwnedListener) before we
    # get here; an HTTP 200 says something answers on the port and says
    # NOTHING about whose process it is, so it must never be treated as
    # evidence of ownership.
    Write-Host "Waiting for frontend http://localhost:$FrontendPort/ ..."
    Wait-FrontendHttp 60
    Write-Host "frontend: running (ready) http://localhost:$FrontendPort/"

    Write-Host ""
    Show-Status
}

function Stop-Services {
    Stop-OneService -Name "frontend" -WorkingDirectory $FrontendDir -Port $FrontendPort -OwnerPattern $FrontendOwnerPattern
    Stop-DockerStack
}

if (-not $DotSource) {
    switch ($Action) {
        "start" {
            Start-Services
        }
        "stop" {
            Stop-Services
        }
        "restart" {
            Stop-Services
            # Round 8-22C item D - no more blind Start-Sleep here:
            # Stop-Services (via Stop-OneService's Wait-PortFree) already
            # blocks until port $FrontendPort is verifiably free before
            # returning, so a fixed guess-and-hope delay is no longer
            # needed to avoid the old "port still in use, old Frontend
            # keeps running" restart bug.
            Start-Services
        }
        "status" {
            Show-Status
        }
    }
}
