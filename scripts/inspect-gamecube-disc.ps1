[CmdletBinding()]
param(
    [Parameter(Mandatory = $true, Position = 0)]
    [ValidateScript({ Test-Path -LiteralPath $_ -PathType Leaf })]
    [string]$DiscImage,

    [switch]$Json,
    [switch]$IncludeDiscHash
)

$ErrorActionPreference = 'Stop'

function Read-U32BE {
    param(
        [Parameter(Mandatory = $true)]
        [System.IO.BinaryReader]$Reader,
        [Parameter(Mandatory = $true)]
        [long]$Offset
    )

    $Reader.BaseStream.Position = $Offset
    $bytes = $Reader.ReadBytes(4)
    if ($bytes.Length -ne 4) {
        throw "Unexpected end of disc image at 0x$($Offset.ToString('X'))."
    }

    return [uint32]([uint32]$bytes[0] -shl 24 -bor
        [uint32]$bytes[1] -shl 16 -bor
        [uint32]$bytes[2] -shl 8 -bor
        [uint32]$bytes[3])
}

function Read-U8 {
    param(
        [Parameter(Mandatory = $true)]
        [System.IO.BinaryReader]$Reader,
        [Parameter(Mandatory = $true)]
        [long]$Offset
    )

    $Reader.BaseStream.Position = $Offset
    return [int]$Reader.ReadByte()
}

function Read-U16BE {
    param(
        [Parameter(Mandatory = $true)]
        [System.IO.BinaryReader]$Reader,
        [Parameter(Mandatory = $true)]
        [long]$Offset
    )

    $Reader.BaseStream.Position = $Offset
    $bytes = $Reader.ReadBytes(2)
    if ($bytes.Length -ne 2) {
        throw "Unexpected end of disc image at 0x$($Offset.ToString('X'))."
    }
    return [uint16]([uint16]$bytes[0] -shl 8 -bor [uint16]$bytes[1])
}

function Read-Ascii {
    param(
        [Parameter(Mandatory = $true)]
        [System.IO.BinaryReader]$Reader,
        [Parameter(Mandatory = $true)]
        [long]$Offset,
        [Parameter(Mandatory = $true)]
        [int]$Length
    )

    $Reader.BaseStream.Position = $Offset
    return [Text.Encoding]::ASCII.GetString($Reader.ReadBytes($Length)).TrimEnd([char]0)
}

function Read-CString {
    param(
        [Parameter(Mandatory = $true)]
        [System.IO.BinaryReader]$Reader,
        [Parameter(Mandatory = $true)]
        [long]$Offset
    )

    $bytes = [Collections.Generic.List[byte]]::new()
    $Reader.BaseStream.Position = $Offset
    while ($Reader.BaseStream.Position -lt $Reader.BaseStream.Length) {
        $value = $Reader.ReadByte()
        if ($value -eq 0) { break }
        $bytes.Add($value)
    }
    return [Text.Encoding]::ASCII.GetString($bytes.ToArray())
}

function Get-StreamSha256 {
    param(
        [Parameter(Mandatory = $true)]
        [System.IO.Stream]$Stream,
        [Parameter(Mandatory = $true)]
        [long]$Offset,
        [Parameter(Mandatory = $true)]
        [long]$Length
    )

    $Stream.Position = $Offset
    $sha = [Security.Cryptography.IncrementalHash]::CreateHash(
        [Security.Cryptography.HashAlgorithmName]::SHA256)
    try {
        $buffer = [byte[]]::new(1MB)
        $remaining = $Length
        while ($remaining -gt 0) {
            $count = [int][Math]::Min($buffer.Length, $remaining)
            $read = $Stream.Read($buffer, 0, $count)
            if ($read -le 0) { throw 'Unexpected end of disc image while hashing.' }
            $sha.AppendData($buffer, 0, $read)
            $remaining -= $read
        }
        return [Convert]::ToHexString($sha.GetHashAndReset()).ToLowerInvariant()
    }
    finally {
        $sha.Dispose()
    }
}

function Read-KxeSummary {
    param(
        [Parameter(Mandatory = $true)]
        [System.IO.BinaryReader]$Reader,
        [Parameter(Mandatory = $true)]
        [string]$Path,
        [Parameter(Mandatory = $true)]
        [long]$FileOffset,
        [Parameter(Mandatory = $true)]
        [long]$FileSize
    )

    $magic = Read-Ascii -Reader $Reader -Offset $FileOffset -Length 4
    if ($magic -ne 'KXER') {
        return [pscustomobject]@{ Path = $Path; Error = "Unexpected magic: $magic" }
    }

    $sectionNames = @('Code', 'Data', 'Bss', 'Relocations', 'Imports', 'Exports')
    $sections = [ordered]@{}
    for ($index = 0; $index -lt $sectionNames.Count; $index++) {
        $sectionHeader = $FileOffset + 16 + 16L * $index
        $relativeOffset = [long](Read-U32BE -Reader $Reader -Offset $sectionHeader)
        $size = [long](Read-U32BE -Reader $Reader -Offset ($sectionHeader + 4))
        $sections[$sectionNames[$index]] = [pscustomobject]@{
            Offset = $relativeOffset
            Size = $size
            Crc32 = '0x' + (Read-U32BE -Reader $Reader -Offset ($sectionHeader + 8)).ToString('X8')
            Alignment = Read-U8 -Reader $Reader -Offset ($sectionHeader + 12)
            Flags = Read-U8 -Reader $Reader -Offset ($sectionHeader + 13)
        }
    }

    $relocationSection = $sections.Relocations
    $relocationCount = if ($relocationSection.Size -gt 0) {
        [int]($relocationSection.Size / 16)
    } else { 0 }
    $relocationTypes = [Collections.Generic.List[int]]::new()
    $externalImportIndexes = [Collections.Generic.List[uint32]]::new()
    for ($index = 0; $index -lt $relocationCount; $index++) {
        $relocationOffset = $FileOffset + $relocationSection.Offset + 16L * $index
        $relocationTypes.Add((Read-U8 -Reader $Reader -Offset $relocationOffset))
        $sourceSection = Read-U8 -Reader $Reader -Offset ($relocationOffset + 2)
        if ($sourceSection -eq 0xFF) {
            $externalImportIndexes.Add(
                (Read-U32BE -Reader $Reader -Offset ($relocationOffset + 8)))
        }
    }

    $importCount = if ($externalImportIndexes.Count -gt 0) {
        1 + [int](($externalImportIndexes | Measure-Object -Maximum).Maximum)
    } else { 0 }
    $imports = [Collections.Generic.List[object]]::new()
    for ($index = 0; $index -lt $importCount; $index++) {
        $stringRecord = $FileOffset + $sections.Imports.Offset + 12L * $index
        $stringOffset = [long](Read-U32BE -Reader $Reader -Offset $stringRecord)
        $stringLength = [int](Read-U32BE -Reader $Reader -Offset ($stringRecord + 4))
        $crc = Read-U32BE -Reader $Reader -Offset ($stringRecord + 8)
        if ($stringOffset + $stringLength -le $FileSize) {
            $imports.Add([pscustomobject]@{
                Name = Read-Ascii -Reader $Reader -Offset ($FileOffset + $stringOffset) -Length $stringLength
                Crc32 = '0x' + $crc.ToString('X8')
            })
        }
    }

    return [pscustomobject]@{
        Path = $Path
        FormatVersion = Read-U16BE -Reader $Reader -Offset ($FileOffset + 4)
        KernelVersion = Read-U16BE -Reader $Reader -Offset ($FileOffset + 6)
        DeclaredFileSize = Read-U32BE -Reader $Reader -Offset ($FileOffset + 8)
        EntryPointOffset = Read-U32BE -Reader $Reader -Offset ($FileOffset + 112)
        Sections = [pscustomobject]$sections
        RelocationCount = $relocationCount
        RelocationTypes = @($relocationTypes | Group-Object | Sort-Object Name |
            Select-Object @{Name='Type';Expression={[int]$_.Name}}, Count)
        Imports = $imports
    }
}

$resolvedImage = (Resolve-Path -LiteralPath $DiscImage).Path
$stream = [IO.File]::Open($resolvedImage, [IO.FileMode]::Open, [IO.FileAccess]::Read,
    [IO.FileShare]::Read)
$reader = [IO.BinaryReader]::new($stream, [Text.Encoding]::ASCII, $true)

try {
    $magic = Read-U32BE -Reader $reader -Offset 0x1C
    if ($magic.ToString('X8') -ne 'C2339F3D') {
        throw "Not a raw GameCube ISO/GCM image (magic was 0x$($magic.ToString('X8')))."
    }

    $dolOffset = [long](Read-U32BE -Reader $reader -Offset 0x420)
    $fstOffset = [long](Read-U32BE -Reader $reader -Offset 0x424)
    $fstSize = [long](Read-U32BE -Reader $reader -Offset 0x428)
    $entryCount = [int](Read-U32BE -Reader $reader -Offset ($fstOffset + 8))
    if ($entryCount -lt 1 -or $entryCount -gt 1000000) {
        throw "Implausible FST entry count: $entryCount"
    }
    if ($fstOffset + $fstSize -gt $stream.Length) {
        throw 'The filesystem table extends beyond the disc image.'
    }

    $dolSize = 0L
    foreach ($index in 0..6) {
        $sectionOffset = [long](Read-U32BE -Reader $reader -Offset ($dolOffset + $index * 4))
        $sectionSize = [long](Read-U32BE -Reader $reader -Offset ($dolOffset + 0x90 + $index * 4))
        $dolSize = [Math]::Max($dolSize, $sectionOffset + $sectionSize)
    }
    foreach ($index in 0..10) {
        $sectionOffset = [long](Read-U32BE -Reader $reader -Offset ($dolOffset + 0x1C + $index * 4))
        $sectionSize = [long](Read-U32BE -Reader $reader -Offset ($dolOffset + 0xAC + $index * 4))
        $dolSize = [Math]::Max($dolSize, $sectionOffset + $sectionSize)
    }
    if ($dolOffset + $dolSize -gt $stream.Length) {
        throw 'The main DOL extends beyond the disc image.'
    }

    $stringTable = $fstOffset + 12L * $entryCount
    $directories = [Collections.Generic.Stack[object]]::new()
    $directories.Push([pscustomobject]@{ Path = ''; End = $entryCount })
    $entries = [Collections.Generic.List[object]]::new()

    for ($index = 1; $index -lt $entryCount; $index++) {
        while ($directories.Count -gt 1 -and $index -ge $directories.Peek().End) {
            [void]$directories.Pop()
        }

        $entryOffset = $fstOffset + 12L * $index
        $typeAndName = Read-U32BE -Reader $reader -Offset $entryOffset
        $isDirectory = ($typeAndName -band 0xFF000000) -ne 0
        $nameOffset = $typeAndName -band 0x00FFFFFF
        $name = Read-CString -Reader $reader -Offset ($stringTable + $nameOffset)
        $parentPath = $directories.Peek().Path
        $path = if ($parentPath) { "$parentPath/$name" } else { $name }
        $second = [long](Read-U32BE -Reader $reader -Offset ($entryOffset + 4))
        $third = [long](Read-U32BE -Reader $reader -Offset ($entryOffset + 8))

        if ($isDirectory) {
            $entries.Add([pscustomobject]@{ Path = $path; Type = 'Directory'; Offset = $null; Size = $null })
            $directories.Push([pscustomobject]@{ Path = $path; End = [int]$third })
        }
        else {
            $entries.Add([pscustomobject]@{ Path = $path; Type = 'File'; Offset = $second; Size = $third })
        }
    }

    $files = @($entries | Where-Object Type -eq 'File')
    $interesting = @($files | Where-Object {
        $_.Path -match '(?i)\.(dol|elf|kxe|rel|rso|map)$' -or
        $_.Path -match '(?i)(kuribo|kamek|module|better.?sunshine)'
    })
    $kuriboModules = @($files | Where-Object Path -match '(?i)\.kxe$' |
        ForEach-Object {
            Read-KxeSummary -Reader $reader -Path $_.Path -FileOffset $_.Offset -FileSize $_.Size
        })
    $extensionSummary = @($files |
        Group-Object { [IO.Path]::GetExtension($_.Path).ToLowerInvariant() } |
        Sort-Object Count -Descending |
        Select-Object @{Name='Extension';Expression={ if ($_.Name) { $_.Name } else { '(none)' } }}, Count)

    $result = [ordered]@{
        Image = $resolvedImage
        ImageSize = $stream.Length
        GameId = Read-Ascii -Reader $reader -Offset 0 -Length 6
        DiscNumber = Read-U8 -Reader $reader -Offset 6
        Revision = Read-U8 -Reader $reader -Offset 7
        Title = Read-Ascii -Reader $reader -Offset 0x20 -Length 64
        DolOffset = $dolOffset
        DolSize = $dolSize
        DolSha256 = Get-StreamSha256 -Stream $stream -Offset $dolOffset -Length $dolSize
        FstOffset = $fstOffset
        FstSize = $fstSize
        DirectoryCount = @($entries | Where-Object Type -eq 'Directory').Count
        FileCount = $files.Count
        InterestingFiles = $interesting
        KuriboModules = $kuriboModules
        ExtensionSummary = $extensionSummary
    }
    if ($IncludeDiscHash) {
        $result.DiscSha256 = Get-StreamSha256 -Stream $stream -Offset 0 -Length $stream.Length
    }

    if ($Json) {
        [pscustomobject]$result | ConvertTo-Json -Depth 6
    }
    else {
        [pscustomobject]$result
    }
}
finally {
    $reader.Dispose()
    $stream.Dispose()
}
