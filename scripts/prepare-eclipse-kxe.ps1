[CmdletBinding()]
param(
    [ValidatePattern('^0x[0-9a-fA-F]+$')]
    [string]$BetterSunshineEngineBase = '0x81700000',
    [ValidateRange(1, 64)]
    [int]$Jobs = 8,
    [switch]$BuildNativeModule
)

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
. (Join-Path $PSScriptRoot 'windows-build-env.ps1')

$gameDir = Join-Path $root 'ref\Eclipse-GMSE04'
$modsDir = Join-Path $gameDir 'files\Kuribo!\Mods'
$dolRecomp = Join-Path $root 'out\dolrecomp-windows\dolrecomp.exe'
$outputRoot = Join-Path $root 'out\eclipse-kxe'
$generatedRoot = Join-Path $outputRoot 'recompiled'
$generated = Join-Path $generatedRoot 'generated'
$bse = Join-Path $modsDir 'BetterSunshineEngine.kxe'
$bseDol = Join-Path $outputRoot 'BetterSunshineEngine.dol'

foreach ($required in @(
    $dolRecomp,
    $bse,
    (Join-Path $modsDir 'BetterSunshineMoveset.kxe'),
    (Join-Path $modsDir 'MirrorMode.kxe'),
    (Join-Path $modsDir 'SuperMarioEclipse.kxe')
)) {
    if (-not (Test-Path -LiteralPath $required -PathType Leaf)) {
        throw "Missing prerequisite: $required. Run prepare-eclipse-windows.ps1 first."
    }
}

New-Item -ItemType Directory -Force -Path $outputRoot | Out-Null
Push-Location $root
try {
    $modulePaths = Get-ChildItem -LiteralPath $modsDir -Filter '*.kxe' |
        Sort-Object Name | Select-Object -ExpandProperty FullName
    $audit = & $script:NativePython -m tools.kxe.inspect --json @modulePaths
    if ($LASTEXITCODE -ne 0) { throw 'KXE validation failed.' }
    Set-Content -LiteralPath (Join-Path $outputRoot 'audit.json') `
        -Value $audit -Encoding utf8

    & $script:NativePython -m tools.kxe.to_dol $bse $bseDol `
        --base $BetterSunshineEngineBase
    if ($LASTEXITCODE -ne 0) { throw 'Better Sunshine Engine relocation failed.' }

    & $dolRecomp --gamecube --cpu gekko "-j$Jobs" $bseDol $generatedRoot
    if ($LASTEXITCODE -ne 0) { throw 'Better Sunshine Engine recompilation failed.' }
    Copy-Item -LiteralPath $bseDol -Destination (Join-Path $generated 'main.dol') -Force

    if ($BuildNativeModule) {
        $moduleSource = Join-Path $root 'ref\ModernGekko\vendor\dolphin\module-template'
        $moduleBuild = Join-Path $root 'out\eclipse-bse-module-windows'
        & $script:CMake -S $moduleSource -B $moduleBuild -G Ninja `
            -DCMAKE_BUILD_TYPE=Release "-DCMAKE_MAKE_PROGRAM=$script:NativeNinja" `
            "-DPython3_EXECUTABLE=$script:NativePython" -DGAME_ID=BSE000 `
            "-DGENERATED_DIR=$generated" `
            "-DGXRUNTIME_DIR=$(Join-Path $root 'ref\ModernGekko\vendor\dolphin\GXRuntime')" `
            "-DCHASSIS_ABI_DIR=$(Join-Path $root 'ref\ModernGekko\vendor\dolphin\Source\Core\Core\PowerPC\StaticRecomp')"
        if ($LASTEXITCODE -ne 0) { throw 'Native BSE module configuration failed.' }
        & $script:CMake --build $moduleBuild --config Release -j $Jobs
        if ($LASTEXITCODE -ne 0) { throw 'Native BSE module build failed.' }

        $nativeModule = Join-Path $moduleBuild 'gBSE000_recomp.dll'
        if (-not (Test-Path -LiteralPath $nativeModule -PathType Leaf)) {
            throw "Expected native BSE module was not produced: $nativeModule"
        }
        $moduleInspector = Join-Path $root 'out\moderngekko-windows\moderngekko-module-info.exe'
        if (Test-Path -LiteralPath $moduleInspector -PathType Leaf) {
            & $moduleInspector $nativeModule
            if ($LASTEXITCODE -ne 0) { throw 'Native BSE module failed ABI inspection.' }
        }
        Write-Host "Built native BSE validation module: $nativeModule"
    }
}
finally {
    Pop-Location
}

Write-Host "Validated Eclipse KXE modules: $(Join-Path $outputRoot 'audit.json')"
Write-Host "Generated native BSE sources: $generated"
Write-Warning "$BetterSunshineEngineBase is an experimental conversion address; runtime ownership is not proven."
