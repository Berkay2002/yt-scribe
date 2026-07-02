$ErrorActionPreference = "Stop"

$Repo = Split-Path -Parent $MyInvocation.MyCommand.Path
$Python = (Get-Command python -ErrorAction Stop).Source
$Bin = Join-Path $HOME ".local\bin"
New-Item -ItemType Directory -Force -Path $Bin | Out-Null

$Ps1 = Join-Path $Bin "yt-scribe.ps1"
$Cmd = Join-Path $Bin "yt-scribe.cmd"
$Script = Join-Path $Repo "yt_scribe.py"

@"
& "$Python" "$Script" @args
"@ | Set-Content -Encoding UTF8 -Path $Ps1

@"
@"$Python" "$Script" %*
"@ | Set-Content -Encoding ASCII -Path $Cmd

Write-Host "Installed yt-scribe wrappers:"
Write-Host "  $Ps1"
Write-Host "  $Cmd"
