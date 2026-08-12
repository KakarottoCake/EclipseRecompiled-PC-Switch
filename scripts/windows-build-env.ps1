$repoRoot = Split-Path -Parent $PSScriptRoot
$workspaceRoot = Split-Path -Parent $repoRoot

$script:VsDevShell = Join-Path ${env:ProgramFiles(x86)} `
    'Microsoft Visual Studio\2022\BuildTools\Common7\Tools\Launch-VsDevShell.ps1'
if (-not (Test-Path -LiteralPath $script:VsDevShell)) {
    throw 'Visual Studio 2022 C++ Build Tools are required.'
}
& $script:VsDevShell -Arch amd64 -HostArch amd64 -SkipAutomaticLocation

$script:NativeNinja = Get-Command ninja -All | Where-Object {
    $_.Source -notmatch '(?i)devkitPro[\\/]msys2'
} | Select-Object -First 1 -ExpandProperty Source
if (-not $script:NativeNinja) {
    throw 'A native Windows Ninja executable is required.'
}

$script:NativeGit = Get-Command git -All -ErrorAction SilentlyContinue |
    Where-Object { $_.Source -notmatch '(?i)devkitPro[\\/]msys2' } |
    Select-Object -First 1 -ExpandProperty Source
if (-not $script:NativeGit) {
    $gitCandidate = Join-Path $env:ProgramFiles 'Git\cmd\git.exe'
    if (Test-Path -LiteralPath $gitCandidate) {
        $script:NativeGit = $gitCandidate
    }
}
if (-not $script:NativeGit) {
    throw 'Git for Windows is required.'
}

$script:CMake = Get-Command cmake | Select-Object -ExpandProperty Source
$bundledCMake = Join-Path $repoRoot `
    'toolchains\python-cmake-3.31\cmake\data\bin\cmake.exe'
$workspaceBundledCMake = Join-Path $workspaceRoot `
    'toolchains\python-cmake-3.31\cmake\data\bin\cmake.exe'
foreach ($candidate in @($bundledCMake, $workspaceBundledCMake)) {
    if (Test-Path -LiteralPath $candidate) {
        $script:CMake = $candidate
        break
    }
}

$script:NativePython = Get-Command python -All -ErrorAction SilentlyContinue |
    Where-Object { $_.Source -notmatch '(?i)devkitPro[\\/]msys2' } |
    Select-Object -First 1 -ExpandProperty Source
$workspacePython = Join-Path $workspaceRoot `
    'toolchains\llvm-mingw-20260616-ucrt-x86_64\python\bin\python.exe'
if (-not $script:NativePython -and (Test-Path -LiteralPath $workspacePython)) {
    $script:NativePython = $workspacePython
}
if (-not $script:NativePython) {
    throw 'A native Windows Python 3 interpreter is required.'
}
