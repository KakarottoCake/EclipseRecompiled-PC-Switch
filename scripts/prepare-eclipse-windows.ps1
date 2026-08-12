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
. (Join-Path $PSScriptRoot 'windows-build-env.ps1')

$expectedDiscSha256 = 'd47de756d57511c427c1d500858f7eee5bbcb1a8e5e40c97758d18cb2f2bed6f'
$expectedDolSha256 = '5a146d7d8b2c8244a6188beb1f7c9b738b13897eb0cdacc02283a8a810cac134'
$inspection = & (Join-Path $PSScriptRoot 'inspect-gamecube-disc.ps1') `
    -DiscImage $DiscImage -IncludeDiscHash -Json | ConvertFrom-Json
if ($inspection.GameId -ne 'GMSE04' -or $inspection.DiscSha256 -ne $expectedDiscSha256 -or
    $inspection.DolSha256 -ne $expectedDolSha256) {
    throw 'Unsupported Eclipse image. This branch currently accepts exact Eclipse 1.1.0 GMSE04 only.'
}

& (Join-Path $PSScriptRoot 'bootstrap-dependencies.ps1')
$dolRecompSource = Join-Path $root 'ref\ModernGekko\vendor\dolphin\DolRecomp'
$dolRecompBuild = Join-Path $root 'out\dolrecomp-windows'
& $script:CMake -S $dolRecompSource -B $dolRecompBuild -G Ninja `
    -DCMAKE_BUILD_TYPE=Release "-DCMAKE_MAKE_PROGRAM=$script:NativeNinja"
if ($LASTEXITCODE -ne 0) { throw 'DolRecomp configuration failed.' }
& $script:CMake --build $dolRecompBuild --config Release -j $Jobs
if ($LASTEXITCODE -ne 0) { throw 'DolRecomp build failed.' }

$gameDir = Join-Path $root 'ref\Eclipse-GMSE04'
$marker = Join-Path $gameDir '.eclipse-source-sha256'
if (Test-Path -LiteralPath $gameDir) {
    if (-not (Test-Path -LiteralPath $marker) -or
        (Get-Content -Raw $marker).Trim() -ne $expectedDiscSha256) {
        # Accept the first extraction made by the same exact validated image.
        $existingDol = Join-Path $gameDir 'sys\main.dol'
        if (-not (Test-Path -LiteralPath $existingDol) -or
            (Get-FileHash -Algorithm SHA256 -LiteralPath $existingDol).Hash.ToLowerInvariant() -ne
                $expectedDolSha256) {
            throw "Existing private extraction is not the supported image: $gameDir"
        }
        Set-Content -LiteralPath $marker -Value $expectedDiscSha256 -Encoding ascii
    }
}
else {
    $staging = "$gameDir.importing.$PID"
    $resolvedRef = [IO.Path]::GetFullPath((Join-Path $root 'ref'))
    $resolvedStaging = [IO.Path]::GetFullPath($staging)
    if (-not $resolvedStaging.StartsWith(
            $resolvedRef + [IO.Path]::DirectorySeparatorChar,
            [StringComparison]::OrdinalIgnoreCase)) {
        throw "Refusing to use a staging path outside the private ref directory: $resolvedStaging"
    }
    try {
        & (Join-Path $dolRecompBuild 'dolrecomp.exe') extract `
            (Resolve-Path -LiteralPath $DiscImage).Path $staging
        if ($LASTEXITCODE -ne 0) { throw 'Eclipse extraction failed.' }
        Set-Content -LiteralPath (Join-Path $staging '.eclipse-source-sha256') `
            -Value $expectedDiscSha256 -Encoding ascii
        Move-Item -LiteralPath $staging -Destination $gameDir
    }
    finally {
        if (Test-Path -LiteralPath $staging) {
            Remove-Item -LiteralPath $staging -Recurse -Force
        }
    }
}

$generatedRoot = Join-Path $root 'ref\Eclipse-generated'
$generated = Join-Path $generatedRoot 'generated'
$map = Join-Path $gameDir 'files\marioUS.MAP'
& (Join-Path $dolRecompBuild 'dolrecomp.exe') --gamecube --map $map `
    "-j$Jobs" (Join-Path $gameDir 'sys\main.dol') $generatedRoot
if ($LASTEXITCODE -ne 0) { throw 'Eclipse recompilation failed.' }
Copy-Item -LiteralPath (Join-Path $gameDir 'sys\main.dol') `
    -Destination (Join-Path $generated 'main.dol') -Force

$moduleSource = Join-Path $root 'ref\ModernGekko\vendor\dolphin\module-template'
$moduleBuild = Join-Path $root 'out\eclipse-module-windows'
& $script:CMake -S $moduleSource -B $moduleBuild -G Ninja `
    -DCMAKE_BUILD_TYPE=Release "-DCMAKE_MAKE_PROGRAM=$script:NativeNinja" `
    "-DPython3_EXECUTABLE=$script:NativePython" -DGAME_ID=GMSE04 `
    "-DGENERATED_DIR=$generated" `
    "-DGXRUNTIME_DIR=$(Join-Path $root 'ref\ModernGekko\vendor\dolphin\GXRuntime')" `
    "-DCHASSIS_ABI_DIR=$(Join-Path $root 'ref\ModernGekko\vendor\dolphin\Source\Core\Core\PowerPC\StaticRecomp')"
if ($LASTEXITCODE -ne 0) { throw 'Eclipse module configuration failed.' }
& $script:CMake --build $moduleBuild --config Release -j $Jobs
if ($LASTEXITCODE -ne 0) { throw 'Eclipse module build failed.' }

$module = Join-Path $moduleBuild 'gGMSE04_recomp.dll'
if (-not (Test-Path -LiteralPath $module)) {
    throw "Expected module was not produced: $module"
}
Write-Host "Prepared private Eclipse data at $gameDir"
Write-Host "Built native Eclipse module at $module"
