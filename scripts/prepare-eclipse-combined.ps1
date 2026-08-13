[CmdletBinding()]
param(
    [ValidateRange(1, 64)]
    [int]$Jobs = 8
)

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
. (Join-Path $PSScriptRoot 'windows-build-env.ps1')

$game = Join-Path $root 'ref\Eclipse-GMSE04'
$mainDol = Join-Path $game 'sys\main.dol'
$map = Join-Path $game 'files\marioUS.MAP'
$mods = Join-Path $game 'files\Kuribo!\Mods'
$bse = Join-Path $mods 'BetterSunshineEngine.kxe'
$moveset = Join-Path $mods 'BetterSunshineMoveset.kxe'
$mirror = Join-Path $mods 'MirrorMode.kxe'
$eclipse = Join-Path $mods 'SuperMarioEclipse.kxe'
$wordPatches = Join-Path $root 'tools\kxe\eclipse_pc_runtime_patches.json'
$dolRecomp = Join-Path $root 'out\dolrecomp-windows\dolrecomp.exe'
$expectedDolSha256 = '5a146d7d8b2c8244a6188beb1f7c9b738b13897eb0cdacc02283a8a810cac134'

foreach ($required in @($mainDol, $map, $bse, $moveset, $mirror, $eclipse, $wordPatches, $dolRecomp)) {
    if (-not (Test-Path -LiteralPath $required -PathType Leaf)) {
        throw "Missing prerequisite: $required. Run prepare-eclipse-windows.ps1 first."
    }
}
if ((Get-FileHash -Algorithm SHA256 -LiteralPath $mainDol).Hash.ToLowerInvariant() -ne
    $expectedDolSha256) {
    throw 'The private extraction no longer contains the exact supported Eclipse main.dol.'
}

$output = Join-Path $root 'out\eclipse-combined'
$combinedDol = Join-Path $output 'main.dol'
$generatedRoot = Join-Path $output 'recompiled'
$generated = Join-Path $generatedRoot 'generated'
$moduleSource = Join-Path $root 'ref\ModernGekko\vendor\dolphin\module-template'
$moduleBuild = Join-Path $root 'out\eclipse-combined-module-windows'
$mutableChunks = '0x8163C000,0x81674000,0x816AC000'

Push-Location $root
try {
    & $script:NativePython -m tools.kxe.combine_dol `
        $mainDol $bse $combinedDol `
        --base 0x81600000 --trampoline 0x817F0000 `
        --loader-hook 0x802A744C --loader-resume 0x802A7450 `
        --loader-init 0x802C0F8C `
        --lifecycle-hook 0x802A746C --lifecycle-resume 0x802A7470 `
        --word-patches $wordPatches `
        --module "$moveset@0x81673000" `
        --module "$mirror@0x8167A000" `
        --module "$eclipse@0x8167D000"
    if ($LASTEXITCODE -ne 0) { throw 'Combined Eclipse/BSE DOL generation failed.' }

    & $dolRecomp --gamecube --cpu gekko --map $map "-j$Jobs" `
        $combinedDol $generatedRoot
    if ($LASTEXITCODE -ne 0) { throw 'Combined Eclipse/BSE recompilation failed.' }
    Copy-Item -LiteralPath $combinedDol -Destination (Join-Path $generated 'main.dol') -Force

    & $script:CMake -S $moduleSource -B $moduleBuild -G Ninja `
        -DCMAKE_BUILD_TYPE=Release "-DCMAKE_MAKE_PROGRAM=$script:NativeNinja" `
        "-DPython3_EXECUTABLE=$script:NativePython" -DGAME_ID=GMSE04 `
        "-DGENERATED_DIR=$generated" `
        "-DGXRUNTIME_DIR=$(Join-Path $root 'ref\ModernGekko\vendor\dolphin\GXRuntime')" `
        "-DCHASSIS_ABI_DIR=$(Join-Path $root 'ref\ModernGekko\vendor\dolphin\Source\Core\Core\PowerPC\StaticRecomp')" `
        "-DRECOMPCORE_MUTABLE_CHUNKS=$mutableChunks"
    if ($LASTEXITCODE -ne 0) { throw 'Combined module configuration failed.' }
    & $script:CMake --build $moduleBuild --config Release -j $Jobs
    if ($LASTEXITCODE -ne 0) { throw 'Combined module build failed.' }

    $module = Join-Path $moduleBuild 'gGMSE04_recomp.dll'
    $inspector = Join-Path $root 'out\moderngekko-windows\moderngekko-module-info.exe'
    if (-not (Test-Path -LiteralPath $module -PathType Leaf)) {
        throw "Expected combined module was not produced: $module"
    }
    if (Test-Path -LiteralPath $inspector -PathType Leaf) {
        & $inspector $module
        if ($LASTEXITCODE -ne 0) { throw 'Combined module failed ABI inspection.' }
    }

    # Keep the exact validated extraction untouched. The diagnostic runtime
    # root substitutes only sys/main.dol and junctions to the private files.
    $runtimeGame = Join-Path $root 'out\eclipse-combined-game'
    $runtimeSys = Join-Path $runtimeGame 'sys'
    New-Item -ItemType Directory -Force -Path $runtimeSys | Out-Null
    Get-ChildItem -LiteralPath (Join-Path $game 'sys') -File |
        Where-Object Name -ne 'main.dol' |
        Copy-Item -Destination $runtimeSys -Force
    Copy-Item -LiteralPath $combinedDol -Destination (Join-Path $runtimeSys 'main.dol') -Force

    $runtimeFiles = Join-Path $runtimeGame 'files'
    if (-not (Test-Path -LiteralPath $runtimeFiles)) {
        New-Item -ItemType Junction -Path $runtimeFiles `
            -Target (Join-Path $game 'files') | Out-Null
    }
    elseif ((Get-Item -LiteralPath $runtimeFiles).LinkType -ne 'Junction') {
        throw "Refusing to replace the existing non-junction path: $runtimeFiles"
    }
}
finally {
    Pop-Location
}

Write-Host 'Combined Eclipse + all four Kuribo modules prototype built.'
Write-Host "Game root: $(Join-Path $root 'out\eclipse-combined-game')"
Write-Host "Module: $(Join-Path $moduleBuild 'gGMSE04_recomp.dll')"
Write-Warning 'The module is a bring-up prototype. Menus, gameplay, saving, and shutdown are not yet accepted.'
