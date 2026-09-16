$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
$CodexPro = Join-Path $Root ".runtime\codexpro"
$Launcher = Join-Path $CodexPro "scripts\codexpro.mjs"
$HttpBuild = Join-Path $CodexPro "dist\http.js"
$StdioBuild = Join-Path $CodexPro "dist\stdio.js"
if (-not (Test-Path $HttpBuild) -or -not (Test-Path $StdioBuild)) {
    throw "CodexPro is not prepared. Run scripts\setup-codexpro.ps1 first."
}
$ForwardedArgs = @($args)
$HasTranscriptOption = $false
foreach ($Argument in $ForwardedArgs) {
    if ($Argument -eq "--bash-transcript" -or $Argument -eq "--full-bash-transcript") {
        $HasTranscriptOption = $true
        break
    }
}
if (-not $HasTranscriptOption) {
    $Transcript = if ($env:WAS_CODEXPRO_BASH_TRANSCRIPT) { $env:WAS_CODEXPRO_BASH_TRANSCRIPT } else { "full" }
    if ($Transcript -notin @("compact", "full")) {
        throw "WAS_CODEXPRO_BASH_TRANSCRIPT must be compact or full."
    }
    $ForwardedArgs += @("--bash-transcript", $Transcript)
}
& node $Launcher @ForwardedArgs
exit $LASTEXITCODE
