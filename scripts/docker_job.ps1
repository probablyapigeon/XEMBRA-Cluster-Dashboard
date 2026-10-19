param([switch]$ResumeAutomaticCircuit, [ValidateSet('learning','xc')][string]$Workload='learning')
$ErrorActionPreference = 'Stop'
$dockerJobRoot = Split-Path -Parent $PSScriptRoot
$dockerJobDirectory = Join-Path $dockerJobRoot 'logs\docker-build'
New-Item -ItemType Directory -Path $dockerJobDirectory -Force | Out-Null
$dockerJobResult = [ordered]@{
    status = 'running'
    pid = $PID
    started = [DateTime]::UtcNow.ToString('o')
    completed = $null
    error = $null
}
$dockerJobResult | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $dockerJobDirectory 'result.json') -Encoding UTF8
$dockerJobExit = 0
try {
    & (Join-Path $PSScriptRoot 'docker_verify.ps1') -ResumeAutomaticCircuit:$ResumeAutomaticCircuit -Workload $Workload
    $dockerJobResult.status = 'success'
} catch {
    $dockerJobResult.status = 'failed'
    $dockerJobResult.error = $_.Exception.Message
    Write-Error -Message $_.Exception.Message -ErrorAction Continue
    $dockerJobExit = 1
} finally {
    $dockerJobResult.completed = [DateTime]::UtcNow.ToString('o')
    $dockerJobResult | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $dockerJobDirectory 'result.json') -Encoding UTF8
}
exit $dockerJobExit
