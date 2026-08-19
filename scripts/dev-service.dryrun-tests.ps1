# Round 8-22C / 8-22C.1 - focused dry-run tests for scripts/dev-service.ps1's
# Windows-host frontend PID tracking, ownership guard, and start/stop
# correctness. Exercises the REAL functions (dot-sourced from dev-service.ps1
# itself via its -DotSource hook - see that file's param block) against
# TEMPORARY ports and TEMPORARY stub processes only.
#
# NEVER modifies:
#   - anything on port 5173 (the real frontend port)
#   - .service-pids/frontend.pid (the real tracked PID file)
#   - the real npm/vite dev server, if one happens to be running
#
# The ONE place the real frontend.pid / :5173 are involved at all is the
# Show-Status read-only test below, which exists precisely to PROVE that
# status does not write to them: it snapshots the real PID file, runs
# Show-Status, and asserts the file is byte- and timestamp-identical
# afterwards. Reading is what status does; writing is what it must never do.
#
# Not Pester (not a dependency anywhere in this repo - AGENTS.md "no new
# dependency") - a plain, self-contained script. Run directly:
#   powershell -NoProfile -ExecutionPolicy Bypass -File scripts\dev-service.dryrun-tests.ps1
# Exits non-zero (and lists every failed assertion) on any failure.

$ErrorActionPreference = "Stop"
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$DevServicePath = Join-Path $ScriptDir "dev-service.ps1"

# Loads every function from dev-service.ps1 WITHOUT running its real
# start/stop/restart/status dispatch.
. $DevServicePath -Action "status" -DotSource

$script:Failures = @()
$script:Passed = 0

function Assert([bool]$Condition, [string]$Message) {
    if ($Condition) {
        $script:Passed++
    } else {
        $script:Failures += $Message
        Write-Host "FAIL: $Message" -ForegroundColor Red
    }
}

function Get-FreeTestPort {
    for ($i = 0; $i -lt 20; $i++) {
        $candidate = Get-Random -Minimum 40000 -Maximum 49000
        if ($null -eq (Get-ListeningPid $candidate)) {
            return $candidate
        }
    }
    throw "Could not find a free test port after 20 attempts."
}

# A real process with a fully-controlled command line (its own script FILE
# PATH, which appears verbatim in its OS command line) - stands in for "a
# Vite dev server for this repo" without spawning npm/Vite at all.
#   -Dir       reuse an existing directory, so two stubs can both satisfy the
#              SAME WorkingDirectory ownership check (needed to model
#              "tracked owned process A vs owned listener process B").
#   -NoListen  alive and ownership-passing, but binds NO port - models a
#              stale/stray tracked PID.
function New-FakeStub([string]$Label, [string]$Dir = "", [switch]$NoListen) {
    if ([string]::IsNullOrEmpty($Dir)) {
        $Dir = Join-Path $env:TEMP "devservice-8-22c-$([guid]::NewGuid().ToString('N'))"
    }
    New-Item -ItemType Directory -Path $Dir -Force | Out-Null

    $port = $null
    $scriptPath = Join-Path $Dir "$Label-$([guid]::NewGuid().ToString('N').Substring(0, 6)).ps1"
    if ($NoListen) {
        "Start-Sleep -Seconds 120" | Set-Content -LiteralPath $scriptPath -Encoding ASCII
    } else {
        $port = Get-FreeTestPort
        @"
`$l = [System.Net.Sockets.TcpListener]::new([System.Net.IPAddress]::Loopback, $port)
`$l.Start()
Start-Sleep -Seconds 120
`$l.Stop()
"@ | Set-Content -LiteralPath $scriptPath -Encoding ASCII
    }

    $proc = Start-Process -FilePath "powershell.exe" `
        -ArgumentList @("-NoProfile", "-WindowStyle", "Hidden", "-File", $scriptPath) `
        -WorkingDirectory $Dir `
        -PassThru

    if ($NoListen) {
        # Give the process a moment to actually exist/report a command line.
        $deadline = (Get-Date).AddSeconds(15)
        while ((Get-Date) -lt $deadline -and [string]::IsNullOrEmpty((Get-ProcessCommandLine $proc.Id))) {
            Start-Sleep -Milliseconds 200
        }
    } else {
        $deadline = (Get-Date).AddSeconds(15)
        while ((Get-Date) -lt $deadline -and $null -eq (Get-ListeningPid $port)) {
            Start-Sleep -Milliseconds 200
        }
    }

    return [PSCustomObject]@{ Dir = $Dir; Port = $port; Proc = $proc; ScriptPath = $scriptPath }
}

function Remove-FakeStub($Stub) {
    Stop-Process -Id $Stub.Proc.Id -Force -ErrorAction SilentlyContinue
    Remove-Item -LiteralPath $Stub.ScriptPath -Force -ErrorAction SilentlyContinue
}

function Remove-StubDir($Stub) {
    Remove-Item -LiteralPath $Stub.Dir -Recurse -Force -ErrorAction SilentlyContinue
}

$TestPidName = "test-frontend-8-22c-$([guid]::NewGuid().ToString('N').Substring(0, 8))"
$TestPidPath = Get-PidPath $TestPidName

try {
    # ================= Test-IsOwnedListener (pure ownership logic) ==========
    $ownCmdLine = Get-ProcessCommandLine $PID
    Assert (-not [string]::IsNullOrEmpty($ownCmdLine)) `
        "Get-ProcessCommandLine returns a non-empty command line for the current process"
    Assert (Test-IsOwnedListener $PID $ScriptDir "dev-service") `
        "Test-IsOwnedListener True when both WorkingDirectory and OwnerPattern substrings are present"
    Assert (-not (Test-IsOwnedListener $PID "C:\definitely-not-a-real-path-xyz" "dev-service")) `
        "Test-IsOwnedListener False when WorkingDirectory does not match"
    Assert (-not (Test-IsOwnedListener $PID $ScriptDir "totally-unrelated-pattern-xyz")) `
        "Test-IsOwnedListener False when OwnerPattern does not match"
    Assert (-not (Test-IsOwnedListener 999999 $ScriptDir "dev-service")) `
        "Test-IsOwnedListener False for a PID that does not exist"

    # ================= tracked == owned listener (the healthy state) ========
    $stub = New-FakeStub "fakevite"
    try {
        Assert ($null -ne (Get-ListeningPid $stub.Port)) "fake vite stub actually opened its test port"
        Assert (Test-IsOwnedListener $stub.Proc.Id $stub.Dir "fakevite") `
            "Test-IsOwnedListener True for the real fake-vite stub process"

        Set-Content -LiteralPath $TestPidPath -Value $stub.Proc.Id -Encoding ASCII
        $healthy = Get-ServiceState $TestPidName $stub.Dir $stub.Port "fakevite"
        Assert ($healthy.Verified -eq $stub.Proc.Id) "tracked == owned listener -> Verified is that PID"
        Assert ($healthy.TrackedIsListener) "tracked == owned listener -> TrackedIsListener true"
        Assert (-not $healthy.TrackedStray) "tracked == owned listener -> TrackedStray false"
        Assert (-not $healthy.Mismatch) "tracked == owned listener -> Mismatch false"

        # ---- untracked but owned listener -> adopted by Start ----
        Remove-Item -LiteralPath $TestPidPath -ErrorAction SilentlyContinue
        $state = Get-ServiceState $TestPidName $stub.Dir $stub.Port "fakevite"
        Assert ($null -eq $state.Tracked) "Get-ServiceState: no PID file yet -> Tracked is null"
        Assert ($state.Verified -eq $stub.Proc.Id) "Get-ServiceState: untracked-but-owned listener is Verified"
        Assert (-not $state.Mismatch) "Get-ServiceState: owned listener is not a Mismatch"

        Start-OneService -Name $TestPidName -WorkingDirectory $stub.Dir -Command "echo unused" `
            -Port $stub.Port -OwnerPattern "fakevite"
        Assert ((Get-ServicePid $TestPidName) -eq $stub.Proc.Id) `
            "Start-OneService adopted the real listener PID into the PID file"

        # ---- stale/dead tracked PID + owned listener -> listener wins ----
        Set-Content -LiteralPath $TestPidPath -Value 999999 -Encoding ASCII
        $staleState = Get-ServiceState $TestPidName $stub.Dir $stub.Port "fakevite"
        Assert (-not $staleState.TrackedAlive) "stale PID (999999) correctly seen as not alive"
        Assert ($staleState.Verified -eq $stub.Proc.Id) `
            "stale/dead tracked + owned listener -> Verified is the listener"
        Assert (-not $staleState.TrackedIsListener) "stale tracked PID is not flagged as the listener"

        Stop-OneService -Name $TestPidName -WorkingDirectory $stub.Dir -Port $stub.Port `
            -OwnerPattern "fakevite" -StopTimeoutSec 15
        Assert ($null -eq (Get-ListeningPid $stub.Port)) "Stop-OneService actually freed the test port"
        Assert (-not (Test-Path $TestPidPath)) "Stop-OneService removed the (stale) PID file"
        Assert ($null -eq (Get-Process -Id $stub.Proc.Id -ErrorAction SilentlyContinue)) `
            "Stop-OneService actually terminated the real listener process"
    } finally {
        Remove-FakeStub $stub
        Remove-StubDir $stub
    }

    # ====== 8-22C.1: tracked alive+owned but NOTHING is listening ==========
    $stray = New-FakeStub "fakevite" -NoListen
    try {
        $freePort = Get-FreeTestPort
        Set-Content -LiteralPath $TestPidPath -Value $stray.Proc.Id -Encoding ASCII
        $s = Get-ServiceState $TestPidName $stray.Dir $freePort "fakevite"

        Assert ($s.TrackedAlive) "stray: tracked PID is alive"
        Assert ($s.TrackedOwned) "stray: tracked PID passes the ownership check"
        Assert ($null -eq $s.Listener) "stray: nothing is listening on the port"
        Assert ($null -eq $s.Verified) `
            "8-22C.1 CORE: tracked alive+owned but NOT the listener is NEVER Verified"
        Assert ($s.TrackedStray) "stray: TrackedStray is true"
        Assert (-not $s.Mismatch) "stray: no listener at all means no Mismatch"

        # Stop must not kill a process that is not the listener on this port.
        Stop-OneService -Name $TestPidName -WorkingDirectory $stray.Dir -Port $freePort `
            -OwnerPattern "fakevite" -StopTimeoutSec 5
        Assert ($null -ne (Get-Process -Id $stray.Proc.Id -ErrorAction SilentlyContinue)) `
            "Stop-OneService never kills an alive+owned tracked PID that is not the listener"
        Assert (-not (Test-Path $TestPidPath)) "Stop-OneService cleared the stray PID file"
    } finally {
        Remove-FakeStub $stray
        Remove-StubDir $stray
    }

    # === 8-22C.1: tracked alive+owned, but an UNRELATED process holds port ==
    $strayB = New-FakeStub "fakevite" -NoListen
    $other = New-FakeStub "unrelated-app"
    try {
        Set-Content -LiteralPath $TestPidPath -Value $strayB.Proc.Id -Encoding ASCII
        # OwnerPattern "fakevite" deliberately does NOT match the unrelated
        # stub, and its dir differs from $strayB.Dir.
        $m = Get-ServiceState $TestPidName $strayB.Dir $other.Port "fakevite"

        Assert ($m.TrackedOwned) "mismatch case: the tracked PID itself is still alive+owned"
        Assert ($m.Mismatch) `
            "8-22C.1 CORE: an alive+owned tracked PID can NEVER clear Mismatch on a foreign listener"
        Assert ($null -eq $m.Verified) "mismatch case: Verified stays null"
        Assert ($m.Listener -eq $other.Proc.Id) "mismatch case: Listener is the unrelated process"

        $threw = $false
        try {
            Start-OneService -Name $TestPidName -WorkingDirectory $strayB.Dir -Command "echo unused" `
                -Port $other.Port -OwnerPattern "fakevite"
        } catch {
            $threw = $true
        }
        Assert $threw "Start-OneService throws (never adopts/launches) when the port is owned by an unverified process"
        Assert ($null -ne (Get-Process -Id $other.Proc.Id -ErrorAction SilentlyContinue)) `
            "Start-OneService left the unrelated listener completely untouched"
        Assert ($null -ne (Get-Process -Id $strayB.Proc.Id -ErrorAction SilentlyContinue)) `
            "Start-OneService left the stray tracked process untouched"

        Stop-OneService -Name $TestPidName -WorkingDirectory $strayB.Dir -Port $other.Port `
            -OwnerPattern "fakevite" -StopTimeoutSec 5
        Assert ($null -ne (Get-Process -Id $other.Proc.Id -ErrorAction SilentlyContinue)) `
            "Stop-OneService never kills a listener it could not verify as ours"
        Assert ($null -ne (Get-Process -Id $strayB.Proc.Id -ErrorAction SilentlyContinue)) `
            "Stop-OneService never kills the stray tracked process either"
        Assert ($null -ne (Get-ListeningPid $other.Port)) `
            "The unrelated process is still listening after Start and Stop both refused it"
    } finally {
        Remove-FakeStub $other
        Remove-StubDir $other
        Remove-FakeStub $strayB
        Remove-StubDir $strayB
    }

    # ==== 8-22C.1: tracked owned process A vs owned LISTENER process B ======
    # Both stubs live in the SAME directory, so BOTH pass the ownership check
    # against the same WorkingDirectory - the only thing separating them is
    # which one actually holds the port.
    $sharedDir = Join-Path $env:TEMP "devservice-8-22c-shared-$([guid]::NewGuid().ToString('N'))"
    $procA = New-FakeStub "fakevite-a" -Dir $sharedDir -NoListen
    $procB = New-FakeStub "fakevite-b" -Dir $sharedDir
    try {
        Set-Content -LiteralPath $TestPidPath -Value $procA.Proc.Id -Encoding ASCII
        $ab = Get-ServiceState $TestPidName $sharedDir $procB.Port "fakevite"

        Assert ($ab.TrackedOwned) "A/B: tracked process A is alive and passes ownership"
        Assert ($ab.ListenerOwned) "A/B: listener process B also passes ownership"
        Assert ($ab.Verified -eq $procB.Proc.Id) `
            "8-22C.1 CORE: with two owned processes, Verified is the LISTENER (B), not the tracked one (A)"
        Assert ($ab.Verified -ne $procA.Proc.Id) "A/B: the tracked non-listener A is never Verified"
        Assert ($ab.TrackedStray) "A/B: tracked A is flagged as a stray"

        # Start adopts B's real listener PID (never A's).
        Start-OneService -Name $TestPidName -WorkingDirectory $sharedDir -Command "echo unused" `
            -Port $procB.Port -OwnerPattern "fakevite"
        Assert ((Get-ServicePid $TestPidName) -eq $procB.Proc.Id) `
            "Start-OneService writes the LISTENER PID (B) to the PID file, not the tracked PID (A)"
        Assert ($null -ne (Get-Process -Id $procA.Proc.Id -ErrorAction SilentlyContinue)) `
            "Start-OneService left the stray owned process A running"

        # Stop stops only B; A survives untouched.
        Stop-OneService -Name $TestPidName -WorkingDirectory $sharedDir -Port $procB.Port `
            -OwnerPattern "fakevite" -StopTimeoutSec 15
        Assert ($null -eq (Get-Process -Id $procB.Proc.Id -ErrorAction SilentlyContinue)) `
            "Stop-OneService stopped the listener process B"
        Assert ($null -ne (Get-Process -Id $procA.Proc.Id -ErrorAction SilentlyContinue)) `
            "Stop-OneService did NOT touch the other owned process A"
        Assert ($null -eq (Get-ListeningPid $procB.Port)) "Stop-OneService freed B's port"
    } finally {
        Remove-FakeStub $procB
        Remove-FakeStub $procA
        Remove-Item -LiteralPath $sharedDir -Recurse -Force -ErrorAction SilentlyContinue
    }

    # ================= Wait-PortFree timeout behavior ======================
    $busy = New-FakeStub "still-busy"
    try {
        Assert (-not (Wait-PortFree $busy.Port 2)) `
            "Wait-PortFree correctly times out (returns false) while the port is still held"
    } finally {
        Remove-FakeStub $busy
        Remove-StubDir $busy
    }

    # ================= status is strictly read-only =========================
    # Get-ServiceState must never create tracking for a name that has none.
    $unseenName = "test-never-tracked-$([guid]::NewGuid().ToString('N').Substring(0, 8))"
    $unseenPath = Get-PidPath $unseenName
    Get-ServiceState $unseenName $ScriptDir (Get-FreeTestPort) "fakevite" | Out-Null
    Assert (-not (Test-Path $unseenPath)) "Get-ServiceState never creates a PID file as a side effect"

    # Show-Status against the REAL frontend must not modify the REAL PID file.
    $realPidPath = Get-PidPath "frontend"
    $existedBefore = Test-Path $realPidPath
    $contentBefore = if ($existedBefore) { Get-Content -LiteralPath $realPidPath -Raw } else { $null }
    $stampBefore = if ($existedBefore) { (Get-Item -LiteralPath $realPidPath).LastWriteTimeUtc } else { $null }

    Show-Status 6>$null | Out-Null

    $existedAfter = Test-Path $realPidPath
    Assert ($existedBefore -eq $existedAfter) "Show-Status did not create or delete the real frontend PID file"
    if ($existedBefore -and $existedAfter) {
        $contentAfter = Get-Content -LiteralPath $realPidPath -Raw
        $stampAfter = (Get-Item -LiteralPath $realPidPath).LastWriteTimeUtc
        Assert ($contentBefore -eq $contentAfter) "Show-Status did not change the real frontend PID file's contents"
        Assert ($stampBefore -eq $stampAfter) "Show-Status did not write to the real frontend PID file (mtime unchanged)"
    }
} finally {
    Remove-Item -LiteralPath $TestPidPath -ErrorAction SilentlyContinue
}

Write-Host ""
if ($Failures.Count -eq 0) {
    Write-Host "ALL $Passed ASSERTIONS PASSED" -ForegroundColor Green
    exit 0
} else {
    Write-Host "$($Failures.Count) FAILURE(S) out of $($Passed + $Failures.Count) assertions:" -ForegroundColor Red
    $Failures | ForEach-Object { Write-Host "  - $_" -ForegroundColor Red }
    exit 1
}
