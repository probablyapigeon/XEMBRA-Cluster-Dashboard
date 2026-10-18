param(
    [Parameter(Mandatory=$true)][string]$Coordinator,
    [Parameter(Mandatory=$true)][string]$Node,
    [Parameter(Mandatory=$true)][string]$TokenFile,
    [string]$Destination = (Split-Path -Parent $PSScriptRoot),
    [string]$CaFile
)
$ErrorActionPreference = 'Stop'
# Run on the intended remote Windows machine, including over an existing SSH session.
if (-not (Test-Path -LiteralPath (Join-Path $Destination 'scripts\network_cluster.py'))) {
    if (Test-Path -LiteralPath $Destination) { throw 'Destination exists but is not a Git checkout.' }
    & git clone https://github.com/probablyapigeon/XEMBRA-Cluster-Dashboard.git $Destination
    if ($LASTEXITCODE -ne 0) { throw 'Git clone failed.' }
}
if (-not (Test-Path -LiteralPath (Join-Path $Destination 'scripts\network_cluster.py'))) {
    throw 'This checkout does not contain the network feature. Transfer the updated source before provisioning.'
}
$nodePython = Join-Path $Destination '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $nodePython)) {
    & py -3.14 -m venv (Join-Path $Destination '.venv')
    if ($LASTEXITCODE -ne 0) { throw 'Creating virtual environment failed.' }
}
& $nodePython -m pip install -r (Join-Path $Destination 'requirements.txt')
if ($LASTEXITCODE -ne 0) { throw 'Installing dependencies failed.' }
$nodeArguments = @((Join-Path $Destination 'scripts\network_cluster.py'), 'watchdog', '--node', $Node,
    '--url', $Coordinator, '--token-file', (Resolve-Path -LiteralPath $TokenFile).Path,
    '--state', (Join-Path $Destination "logs\nodes\$Node"))
if ($CaFile) { $nodeArguments += @('--ca', (Resolve-Path -LiteralPath $CaFile).Path) }
& $nodePython @nodeArguments
if ($LASTEXITCODE -ne 0) { throw 'Node agent exited with an error.' }
