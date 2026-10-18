$ErrorActionPreference = 'Stop'
Set-Location (Split-Path -Parent $PSScriptRoot)

function Invoke-DockerChecked {
    param([string[]]$Arguments)
    & docker @Arguments
    if ($LASTEXITCODE -ne 0) { throw "Docker command failed (exit $LASTEXITCODE): $($Arguments -join ' ')" }
}

Invoke-DockerChecked -Arguments @('compose', 'config', '--quiet')
Invoke-DockerChecked -Arguments @('compose', 'build')
Invoke-DockerChecked -Arguments @('run', '--rm', '--init', 'xembra-cluster-dashboard:local', 'python', '-m', 'unittest', 'discover', '-s', 'tests', '-p', 'test_cluster_control.py', '-v')
Invoke-DockerChecked -Arguments @('run', '--rm', '--init', 'xembra-cluster-dashboard:local', 'python', '-m', 'unittest', 'discover', '-s', 'tests', '-p', 'test_evolution_state.py', '-v')

# Use an exclusively named temporary project/volume for destructive restart tests.
$checkProject = 'xembra-check-' + [guid]::NewGuid().ToString('N').Substring(0,12)
$oldPort = $env:XEMBRA_HOST_PORT
$checkListener = [System.Net.Sockets.TcpListener]::new([System.Net.IPAddress]::Loopback, 0)
$checkListener.Start()
$checkPort = $checkListener.LocalEndpoint.Port
$checkListener.Stop()
$env:XEMBRA_HOST_PORT = [string]$checkPort
$checkUrl = "http://127.0.0.1:$checkPort/api/status"
function Wait-ClusterState {
    param([scriptblock]$Predicate)
    $checkDeadline = [DateTime]::UtcNow.AddSeconds(90)
    while ([DateTime]::UtcNow -lt $checkDeadline) {
        try {
            $checkState = Invoke-RestMethod -Uri $checkUrl -TimeoutSec 3
            if (& $Predicate $checkState) { return $checkState }
        } catch { }
        Start-Sleep -Milliseconds 500
    }
    throw 'Container state did not reach the expected condition within 90 seconds.'
}
try {
    Invoke-DockerChecked -Arguments @('compose','-p',$checkProject,'up','-d','--no-build')
    $beforeRestart = Wait-ClusterState { param($s) $s.guardian_running -and $s.totals.completed -ge 2 }
    Write-Output "Real Linux evolution runs completed: $($beforeRestart.totals.completed)"
    Invoke-DockerChecked -Arguments @('compose','-p',$checkProject,'exec','-T','cluster','python','scripts/cluster_control.py','kill')
    $null = Wait-ClusterState { param($s) $s.settings.killed -eq 1 -and @($s.jobs | Where-Object state -eq 'running').Count -eq 0 }
    Invoke-DockerChecked -Arguments @('compose','-p',$checkProject,'restart','cluster')
    $afterRestart = Wait-ClusterState { param($s) $s.guardian_running -and $s.settings.killed -eq 1 }
    if ($afterRestart.totals.completed -lt $beforeRestart.totals.completed) { throw 'Persistent run history was lost after restart.' }
    Invoke-DockerChecked -Arguments @('compose','-p',$checkProject,'exec','-T','cluster','python','scripts/cluster_control.py','resume')
    $null = Wait-ClusterState { param($s) $s.settings.killed -eq 0 -and $s.totals.completed -gt $afterRestart.totals.completed }
    Write-Output 'Container restart, durable kill latch, resume, and persisted history verified.'
} finally {
    # This GUID project and its volume were created above solely for this test.
    & docker compose -p $checkProject down --volumes --remove-orphans
    $env:XEMBRA_HOST_PORT = $oldPort
}

Invoke-DockerChecked -Arguments @('compose','up','-d','--no-build')
$dashboardPort = if ($env:XEMBRA_HOST_PORT) { $env:XEMBRA_HOST_PORT } else { '8766' }
$checkUrl = "http://127.0.0.1:$dashboardPort/api/status"
$null = Wait-ClusterState { param($s) $s.guardian_running }
Write-Output "Docker dashboard running: http://127.0.0.1:$dashboardPort"
