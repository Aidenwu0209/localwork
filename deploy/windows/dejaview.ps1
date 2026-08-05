[CmdletBinding()]
param(
    [Parameter(Position = 0)]
    [ValidateSet('doctor', 'tunnel-up', 'tunnel-status', 'tunnel-down', 'product-up', 'product-status', 'product-down', 'capture')]
    [string]$Command = 'doctor'
)

$ErrorActionPreference = 'Stop'
$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
$RuntimeDir = if ($env:DEJAVIEW_RUNTIME_DIR) {
    [IO.Path]::GetFullPath($env:DEJAVIEW_RUNTIME_DIR)
} else {
    Join-Path $env:LOCALAPPDATA 'DejaView\runtime'
}
$UserSshConfig = [Environment]::GetEnvironmentVariable('DEJAVIEW_SSH_CONFIG', 'User')
$SshConfig = if ($env:DEJAVIEW_SSH_CONFIG) {
    [IO.Path]::GetFullPath($env:DEJAVIEW_SSH_CONFIG)
} elseif ($UserSshConfig) {
    [IO.Path]::GetFullPath($UserSshConfig)
} else {
    Join-Path $env:USERPROFILE '.ssh\config'
}
$ServicePorts = [ordered]@{ ocrd = 8006; memoryd = 8090; agentd = 8101 }
$DataCompose = Join-Path $RepoRoot 'deploy\mac\compose.data.yml'
$HonchoCompose = Join-Path $RepoRoot 'deploy\mac\compose.honcho.yml'

function Ensure-RuntimeDirectory {
    New-Item -ItemType Directory -Path $RuntimeDir -Force | Out-Null
}

function Get-RecordPath([string]$Name) {
    Join-Path $RuntimeDir "$Name.json"
}

function Get-LogPath([string]$Name) {
    Join-Path $RuntimeDir "$Name.log"
}

function Import-DotEnv([string]$Path) {
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) { return }
    foreach ($line in [IO.File]::ReadLines($Path)) {
        if ($line -notmatch '^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)=(.*)$') { continue }
        $name = $Matches[1]
        if ([Environment]::GetEnvironmentVariable($name, 'Process')) { continue }
        $value = $Matches[2].Trim()
        if ($value.Length -ge 2 -and (($value[0] -eq '"' -and $value[-1] -eq '"') -or ($value[0] -eq "'" -and $value[-1] -eq "'"))) {
            $value = $value.Substring(1, $value.Length - 2)
        }
        [Environment]::SetEnvironmentVariable($name, $value, 'Process')
    }
}

function Get-CimProcess([int]$ProcessId) {
    Get-CimInstance Win32_Process -Filter "ProcessId=$ProcessId" -ErrorAction SilentlyContinue
}

function Get-CreationStamp($Process) {
    $created = $Process.CreationDate
    if ($created -is [DateTime]) { return $created.ToUniversalTime().ToString('o') }
    ([Management.ManagementDateTimeConverter]::ToDateTime([string]$created)).ToUniversalTime().ToString('o')
}

function Save-ProcessRecord([string]$Name, [int]$ProcessId, [string]$Kind) {
    $process = Get-CimProcess $ProcessId
    if (-not $process) { throw "could not inspect $Name process $ProcessId" }
    [ordered]@{
        pid = $ProcessId
        kind = $Kind
        created = Get-CreationStamp $process
        executable = $process.ExecutablePath
        command_line = $process.CommandLine
    } | ConvertTo-Json | Set-Content -LiteralPath (Get-RecordPath $Name) -Encoding UTF8
}

function Read-ProcessRecord([string]$Name) {
    $path = Get-RecordPath $Name
    if (-not (Test-Path -LiteralPath $path -PathType Leaf)) { return $null }
    try { Get-Content -LiteralPath $path -Raw -Encoding UTF8 | ConvertFrom-Json } catch { return $null }
}

function Test-OwnedProcess([string]$Name, [string]$Kind) {
    $record = Read-ProcessRecord $Name
    if (-not $record -or $record.kind -ne $Kind -or $record.pid -notmatch '^\d+$') { return $false }
    $process = Get-CimProcess ([int]$record.pid)
    if (-not $process) { return $false }
    $created = Get-CreationStamp $process
    return $created -eq $record.created -and
        $process.ExecutablePath -eq $record.executable -and
        $process.CommandLine -eq $record.command_line
}

function Test-DescendantOrSelf([int]$Candidate, [int]$Owner) {
    for ($hops = 0; $hops -lt 32 -and $Candidate -gt 0; $hops++) {
        if ($Candidate -eq $Owner) { return $true }
        $process = Get-CimProcess $Candidate
        if (-not $process -or $process.ParentProcessId -eq $Candidate) { return $false }
        $Candidate = [int]$process.ParentProcessId
    }
    return $false
}

function Get-Listener([int]$Port) {
    @(Get-NetTCPConnection -State Listen -LocalPort $Port -ErrorAction SilentlyContinue)
}

function Test-OwnedListener([string]$Name, [string]$Kind, [int]$Port) {
    if (-not (Test-OwnedProcess $Name $Kind)) { return $false }
    $record = Read-ProcessRecord $Name
    $listeners = @(Get-Listener $Port)
    if ($listeners.Count -ne 1) { return $false }
    Test-DescendantOrSelf ([int]$listeners[0].OwningProcess) ([int]$record.pid)
}

function Assert-PortAvailableOrOwned([string]$Name, [string]$Kind, [int]$Port) {
    $listeners = @(Get-Listener $Port)
    if ($listeners.Count -eq 0) { return }
    if (Test-OwnedListener $Name $Kind $Port) { return }
    throw "port $Port is occupied by an unowned process; refusing to adopt or stop it"
}

function Invoke-Json([string]$Uri, [int]$Timeout = 2) {
    try { Invoke-RestMethod -Uri $Uri -TimeoutSec $Timeout -Method Get } catch { return $null }
}

function Test-ServiceHealth([string]$Service, [int]$Port) {
    $body = Invoke-Json "http://127.0.0.1:$Port/health"
    if (-not $body -or $body.status -ne 'ok') { return $false }
    if ($Service -eq 'ocrd') { return [bool]$body.backend }
    return $body.service -eq $Service
}

function Get-ModelsUri([string]$Base) {
    $trimmed = $Base.TrimEnd('/')
    if ($trimmed.EndsWith('/v1')) { $trimmed = $trimmed.Substring(0, $trimmed.Length - 3) }
    "$trimmed/v1/models"
}

function Test-Gateway([string]$Base, [string]$RequiredModel = '') {
    $body = Invoke-Json (Get-ModelsUri $Base)
    if (-not $body -or -not $body.data) { return $false }
    if (-not $RequiredModel) { return @($body.data).Count -gt 0 }
    return @($body.data | Where-Object { $_.id -eq $RequiredModel -and $_.owned_by }).Count -gt 0
}

function Wait-Condition([scriptblock]$Probe, [int]$Seconds = 90) {
    $deadline = [DateTime]::UtcNow.AddSeconds($Seconds)
    do {
        if (& $Probe) { return $true }
        Start-Sleep -Milliseconds 500
    } while ([DateTime]::UtcNow -lt $deadline)
    return $false
}

function Stop-OwnedProcess([string]$Name, [string]$Kind) {
    $path = Get-RecordPath $Name
    if (-not (Test-Path -LiteralPath $path)) { return }
    if (-not (Test-OwnedProcess $Name $Kind)) {
        $record = Read-ProcessRecord $Name
        if ($record -and (Get-CimProcess ([int]$record.pid))) {
            throw "refusing to stop unowned PID $($record.pid) from $path"
        }
        Remove-Item -LiteralPath $path -Force
        return
    }
    $record = Read-ProcessRecord $Name
    Stop-Process -Id ([int]$record.pid) -ErrorAction SilentlyContinue
    try { Wait-Process -Id ([int]$record.pid) -Timeout 10 -ErrorAction Stop } catch {
        if (Test-OwnedProcess $Name $Kind) { Stop-Process -Id ([int]$record.pid) -Force }
    }
    if (Get-CimProcess ([int]$record.pid)) { throw "$Name did not stop" }
    Remove-Item -LiteralPath $path -Force
    Write-Output "stopped $Name"
}

function Resolve-SshArguments {
    $args = @()
    if ($SshConfig -and (Test-Path -LiteralPath $SshConfig -PathType Leaf)) { $args += @('-F', $SshConfig) }
    $args
}

function Test-SshAlias {
    $ssh = Get-Command ssh -ErrorAction SilentlyContinue
    if (-not $ssh) { return $false }
    $args = @(Resolve-SshArguments) + @('-G', 'radeon-cloud')
    $output = & $ssh.Source @args 2>$null
    return $LASTEXITCODE -eq 0 -and ($output -match '^hostname\s+(?!radeon-cloud$).+')
}

function Start-Tunnel {
    Ensure-RuntimeDirectory
    Assert-PortAvailableOrOwned 'radeon-tunnel' 'ssh-tunnel' 14000
    if (Test-OwnedListener 'radeon-tunnel' 'ssh-tunnel' 14000) {
        Write-Output 'Radeon tunnel already managed and ready'
        return
    }
    if (-not (Test-SshAlias)) { throw 'SSH alias radeon-cloud is not configured' }
    $ssh = Get-Command ssh -ErrorAction Stop
    $args = @(Resolve-SshArguments) + @(
        '-o', 'BatchMode=yes', '-o', 'ExitOnForwardFailure=yes', '-N',
        '-L', '127.0.0.1:14000:127.0.0.1:4000', 'radeon-cloud'
    )
    $stdout = Get-LogPath 'radeon-tunnel'
    $stderr = Get-LogPath 'radeon-tunnel-error'
    $process = Start-Process -FilePath $ssh.Source -ArgumentList $args -RedirectStandardOutput $stdout -RedirectStandardError $stderr -WindowStyle Hidden -PassThru
    Start-Sleep -Milliseconds 150
    if ($process.HasExited) { throw "Radeon SSH tunnel exited before binding; inspect $stderr" }
    try { Save-ProcessRecord 'radeon-tunnel' $process.Id 'ssh-tunnel' } catch {
        if ($process.HasExited) { throw "Radeon SSH tunnel exited before binding; inspect $stderr" }
        Stop-Process -Id $process.Id -Force -ErrorAction SilentlyContinue
        throw
    }
    if (-not (Wait-Condition { Test-OwnedListener 'radeon-tunnel' 'ssh-tunnel' 14000 } 15)) {
        Stop-OwnedProcess 'radeon-tunnel' 'ssh-tunnel'
        throw 'Radeon SSH tunnel failed to bind 127.0.0.1:14000'
    }
    Write-Output 'Radeon tunnel managed and ready'
}

function Get-ComposeIds([string]$File) {
    $output = & docker compose -f $File ps -q 2>$null
    if ($LASTEXITCODE -ne 0) { throw "could not inspect compose file $File" }
    @($output | Where-Object { $_ })
}

function Start-Compose([string]$Name, [string]$File) {
    $existing = @(Get-ComposeIds $File)
    & docker compose -f $File up -d --wait
    if ($LASTEXITCODE -ne 0) { throw "$Name compose failed to start" }
    if ($existing.Count -eq 0) { Set-Content -LiteralPath (Join-Path $RuntimeDir "$Name.compose-owned") -Value 'owned' -Encoding ASCII }
}

function Stop-OwnedCompose([string]$Name, [string]$File) {
    $marker = Join-Path $RuntimeDir "$Name.compose-owned"
    if (-not (Test-Path -LiteralPath $marker)) { return }
    & docker compose -f $File down
    if ($LASTEXITCODE -ne 0) { throw "$Name compose failed to stop" }
    Remove-Item -LiteralPath $marker -Force
}

function Test-ComposeReady([string]$File) {
    try { return @(Get-ComposeIds $File).Count -gt 0 } catch { return $false }
}

function Start-ServiceProcess([string]$Service, [int]$Port) {
    Assert-PortAvailableOrOwned $Service 'service' $Port
    if ((Test-OwnedListener $Service 'service' $Port) -and (Test-ServiceHealth $Service $Port)) {
        Write-Output "$Service already managed and ready"
        return
    }
    $recordPath = Get-RecordPath $Service
    if (Test-Path -LiteralPath $recordPath) {
        $oldRecord = Read-ProcessRecord $Service
        if ($oldRecord -and (Get-CimProcess ([int]$oldRecord.pid))) {
            throw "refusing to overwrite a live unowned process record for $Service"
        }
        Remove-Item -LiteralPath $recordPath -Force
    }
    $uv = Get-Command uv -ErrorAction Stop
    $project = Join-Path $RepoRoot "services\$Service"
    $log = Get-LogPath $Service
    $errorLog = Get-LogPath "$Service-error"
    $process = Start-Process -FilePath $uv.Source -ArgumentList @('run', '--project', $project, 'python', '-m', $Service) -RedirectStandardOutput $log -RedirectStandardError $errorLog -WindowStyle Hidden -PassThru
    Save-ProcessRecord $Service $process.Id 'service'
    if (-not (Wait-Condition { (Test-ServiceHealth $Service $Port) -and (Test-OwnedListener $Service 'service' $Port) } 90)) {
        Stop-OwnedProcess $Service 'service'
        throw "$Service failed readiness; inspect $errorLog"
    }
    Write-Output "$Service managed and ready on 127.0.0.1:$Port"
}

function Invoke-Doctor {
    $failures = 0
    foreach ($name in @('git', 'uv', 'python', 'node', 'ssh')) {
        if (Get-Command $name -ErrorAction SilentlyContinue) { Write-Output "OK    $name available" }
        else { Write-Output "FAIL  $name missing"; $failures++ }
    }
    if (Get-Command docker -ErrorAction SilentlyContinue) {
        & docker info *> $null
        if ($LASTEXITCODE -eq 0) { Write-Output 'OK    Docker engine reachable' }
        else { Write-Output 'FAIL  Docker engine unavailable'; $failures++ }
        & docker compose version *> $null
        if ($LASTEXITCODE -eq 0) { Write-Output 'OK    Docker Compose available' }
        else { Write-Output 'FAIL  Docker Compose missing'; $failures++ }
    } else { Write-Output 'FAIL  Docker CLI missing'; $failures++ }
    if (Test-SshAlias) { Write-Output 'OK    SSH alias radeon-cloud configured' }
    else { Write-Output 'FAIL  SSH alias radeon-cloud missing'; $failures++ }
    if (Test-Path -LiteralPath (Join-Path $RepoRoot '.env')) { Write-Output 'OK    local .env present' }
    else { Write-Output 'WARN  local .env absent' }
    if (Test-Path -LiteralPath (Join-Path $RepoRoot 'deploy\mac\honcho.env')) { Write-Output 'OK    local Honcho configuration present' }
    else { Write-Output 'WARN  local Honcho configuration absent' }
    $env:PYTHONPATH = Join-Path $RepoRoot 'clients\capture\src'
    & uv run --project (Join-Path $RepoRoot 'clients\capture') python -c 'import capture.windows; assert capture.windows.list_windows()'
    if ($LASTEXITCODE -eq 0) { Write-Output 'OK    Windows capture backend available' }
    else { Write-Output 'FAIL  Windows capture backend unavailable'; $failures++ }
    if ($failures) { throw "doctor found $failures blocking prerequisite issue(s)" }
    Write-Output 'READY: Windows prerequisites verified'
}

function Get-ProductStatus {
    $failures = 0
    $script:ProductReady = $false
    foreach ($entry in $ServicePorts.GetEnumerator()) {
        if ((Test-OwnedListener $entry.Key 'service' $entry.Value) -and (Test-ServiceHealth $entry.Key $entry.Value)) {
            Write-Output "$($entry.Key): managed and ready"
        } else { Write-Output "$($entry.Key): down, unowned, or unhealthy"; $failures++ }
    }
    $localGateway = if ($env:LOCAL_GATEWAY_URL) { $env:LOCAL_GATEWAY_URL } else { 'http://127.0.0.1:4000/v1' }
    if (Test-Gateway $localGateway 'sentinel') { Write-Output 'privacy gateway: sentinel ready' }
    else { Write-Output 'privacy gateway: sentinel missing'; $failures++ }
    if (Test-OwnedListener 'radeon-tunnel' 'ssh-tunnel' 14000) { Write-Output 'Radeon tunnel: managed and ready' }
    else { Write-Output 'Radeon tunnel: down or unowned'; $failures++ }
    if (Test-ComposeReady $DataCompose) { Write-Output 'data compose: running' }
    else { Write-Output 'data compose: missing'; $failures++ }
    if (Test-ComposeReady $HonchoCompose) { Write-Output 'Honcho compose: running' }
    else { Write-Output 'Honcho compose: missing'; $failures++ }
    if ($failures) { Write-Output 'NOT_READY: Windows product runtime contracts failed'; return }
    Write-Output 'READY: Windows product runtime contracts verified'
    $script:ProductReady = $true
}

function Start-Product {
    Ensure-RuntimeDirectory
    Import-DotEnv (Join-Path $RepoRoot '.env')
    Import-DotEnv (Join-Path $RepoRoot 'deploy\mac\honcho.env')
    foreach ($entry in $ServicePorts.GetEnumerator()) { Assert-PortAvailableOrOwned $entry.Key 'service' $entry.Value }
    $localGateway = if ($env:LOCAL_GATEWAY_URL) { $env:LOCAL_GATEWAY_URL } else { 'http://127.0.0.1:4000/v1' }
    if (-not (Test-Gateway $localGateway 'sentinel')) { throw 'local privacy gateway lacks the sentinel role; raw frames must never use Radeon' }
    $tunnelWasOwned = Test-OwnedListener 'radeon-tunnel' 'ssh-tunnel' 14000
    Start-Tunnel
    $tunnelStarted = -not $tunnelWasOwned
    $env:GATEWAY_URL = if ($env:GATEWAY_URL) { $env:GATEWAY_URL } else { 'http://127.0.0.1:14000/v1' }
    $env:RADEON_GATEWAY_URL = if ($env:RADEON_GATEWAY_URL) { $env:RADEON_GATEWAY_URL } else { $env:GATEWAY_URL }
    $env:LOCAL_GATEWAY_URL = $localGateway
    $env:SENTINEL_GATEWAY_URL = $localGateway
    try {
        if (-not (Test-Gateway $env:RADEON_GATEWAY_URL)) { throw 'Radeon gateway is unavailable through the managed tunnel' }
        Start-Compose 'data' $DataCompose
        Start-Compose 'honcho' $HonchoCompose
        foreach ($entry in $ServicePorts.GetEnumerator()) { Start-ServiceProcess $entry.Key $entry.Value }
        Get-ProductStatus
        if (-not $script:ProductReady) { throw 'final Windows product readiness failed' }
    } catch {
        foreach ($name in @('agentd', 'memoryd', 'ocrd')) { try { Stop-OwnedProcess $name 'service' } catch {} }
        try { Stop-OwnedCompose 'honcho' $HonchoCompose } catch {}
        try { Stop-OwnedCompose 'data' $DataCompose } catch {}
        if ($tunnelStarted) { try { Stop-OwnedProcess 'radeon-tunnel' 'ssh-tunnel' } catch {} }
        throw
    }
    Write-Output 'DejaView product ready: http://127.0.0.1:8101/'
}

function Stop-Product {
    foreach ($name in @('agentd', 'memoryd', 'ocrd')) { Stop-OwnedProcess $name 'service' }
    Stop-OwnedCompose 'honcho' $HonchoCompose
    Stop-OwnedCompose 'data' $DataCompose
}

function Start-Capture {
    if (-not (Test-ServiceHealth 'memoryd' 8090)) { throw 'memoryd is not ready on 127.0.0.1:8090' }
    $env:PYTHONPATH = Join-Path $RepoRoot 'clients\capture\src'
    & uv run --project (Join-Path $RepoRoot 'clients\capture') python -m capture
    if ($LASTEXITCODE -ne 0) { throw "capture exited with code $LASTEXITCODE" }
}

Ensure-RuntimeDirectory
switch ($Command) {
    'doctor' { Invoke-Doctor }
    'tunnel-up' { Start-Tunnel }
    'tunnel-status' {
        if (Test-OwnedListener 'radeon-tunnel' 'ssh-tunnel' 14000) { 'READY: Radeon tunnel managed'; exit 0 }
        'NOT_READY: Radeon tunnel down or unowned'; exit 1
    }
    'tunnel-down' { Stop-OwnedProcess 'radeon-tunnel' 'ssh-tunnel' }
    'product-up' { Start-Product }
    'product-status' { Get-ProductStatus; if (-not $script:ProductReady) { exit 1 } }
    'product-down' { Stop-Product }
    'capture' { Start-Capture }
}
