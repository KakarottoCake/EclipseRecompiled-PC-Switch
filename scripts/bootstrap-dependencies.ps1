[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$gitBashCandidates = @(
    (Join-Path $env:ProgramFiles 'Git\bin\bash.exe'),
    (Join-Path $env:ProgramFiles 'Git\usr\bin\bash.exe')
)
$gitBash = $gitBashCandidates | Where-Object { Test-Path -LiteralPath $_ } |
    Select-Object -First 1
if (-not $gitBash) {
    throw 'Git for Windows with bash.exe is required.'
}

# SPIRV-Cross contains legitimate test paths longer than Win32 Git's legacy
# default. Scope long-path handling to this bootstrap instead of modifying the
# user's global Git configuration.
$previousGlobalConfig = $env:GIT_CONFIG_GLOBAL
$previousPath = $env:PATH
$env:GIT_CONFIG_GLOBAL = Join-Path $PSScriptRoot 'windows.gitconfig'
$env:PATH = "$(Split-Path -Parent $gitBash);$env:PATH"
try {
    & $gitBash (Join-Path $PSScriptRoot 'bootstrap-dependencies.sh')
    if ($LASTEXITCODE -ne 0) {
        throw "Dependency bootstrap failed with exit code $LASTEXITCODE."
    }
}
finally {
    $env:PATH = $previousPath
    if ($null -eq $previousGlobalConfig) {
        Remove-Item Env:GIT_CONFIG_GLOBAL -ErrorAction SilentlyContinue
    }
    else {
        $env:GIT_CONFIG_GLOBAL = $previousGlobalConfig
    }
}
