[CmdletBinding()]
param(
    [Parameter(Mandatory = $true, Position = 0)]
    [ValidateScript({ Test-Path -LiteralPath $_ -PathType Leaf })]
    [string]$DiscImage,
    [ValidateRange(1, 64)]
    [int]$Jobs = 8
)

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot

& (Join-Path $PSScriptRoot 'prepare-eclipse-windows.ps1') `
    -DiscImage $DiscImage -Jobs $Jobs

. (Join-Path $PSScriptRoot 'windows-build-env.ps1')
$source = Join-Path $root 'ref\ModernGekko'
$build = Join-Path $root 'out\moderngekko-windows'

& $script:CMake -S $source -B $build -G Ninja `
    -DCMAKE_BUILD_TYPE=Release `
    "-DCMAKE_MAKE_PROGRAM=$script:NativeNinja" `
    "-DGIT_EXECUTABLE=$script:NativeGit" `
    -DUSE_SYSTEM_LIBS=OFF -DENABLE_QT=OFF -DENABLE_TESTS=OFF `
    -DMODERNGEKKO_BUILD_FULL_RUNTIME=ON `
    -DMODERNGEKKO_ENABLE_DOLPHIN_RUNTIME=ON `
    -DMODERNGEKKO_ENABLE_DYNAMIC_MODULES=ON `
    -DMODERNGEKKO_ENABLE_IMGUI_LAUNCHER=ON `
    -DMODERNGEKKO_ENABLE_DISC_TOOL=OFF `
    -DMODERNGEKKO_GAMECUBE_CONTROLLERS=ON `
    -DMODERNGEKKO_PORTABLE_DEFAULT_GAME=ON `
    "-DMODERNGEKKO_FRONTEND_NAME=Eclipse Recompiled" `
    -DMODERNGEKKO_LAUNCHER_OUTPUT_NAME=EclipseRecompiled `
    -DMODERNGEKKO_RUNNER_OUTPUT_NAME=EclipseRecompiled-run `
    -DMODERNGEKKO_USER_DIRECTORY_NAME=EclipseRecompiled `
    -DMODERNGEKKO_REQUIRED_DISC_ID=GMSE04 `
    "-DMODERNGEKKO_DEFAULT_WINDOW_TITLE=Super Mario Eclipse"
if ($LASTEXITCODE -ne 0) { throw 'ModernGekko configuration failed.' }

& $script:CMake --build $build --config Release -j $Jobs --target `
    moderngekko-run moderngekko-launcher moderngekko-port moderngekko-module-info
if ($LASTEXITCODE -ne 0) { throw 'ModernGekko Windows host build failed.' }

$module = Join-Path $root 'out\eclipse-module-windows\gGMSE04_recomp.dll'
$moduleInfo = Join-Path $build 'moderngekko-module-info.exe'
& $moduleInfo $module
if ($LASTEXITCODE -ne 0) { throw 'The Eclipse module failed ABI validation.' }

$runner = Join-Path $build 'EclipseRecompiled-run.exe'
$game = Join-Path $root 'ref\Eclipse-GMSE04'
Write-Host ''
Write-Host 'Windows recompilation checkpoint is ready.'
Write-Host "Runner: $runner"
Write-Host "Module: $module"
Write-Host 'KXE integration is not complete, so this is not a playable release.'
Write-Host 'Diagnostic launch command:'
Write-Host "& `"$runner`" --game `"$game`" --module `"$module`""
