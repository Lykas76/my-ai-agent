param([ValidateSet("api","scheduler","telegram")][string]$Component = "api")
$ErrorActionPreference = "Stop"
Set-Location (Split-Path -Parent $PSScriptRoot)
$modules = @{api="api.server"; scheduler="scheduler.worker"; telegram="clients.telegram"}
python -B -m $modules[$Component]
exit $LASTEXITCODE
