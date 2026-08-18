[CmdletBinding()]
param(
    [string] $Fixture = (Join-Path $PSScriptRoot 'fixtures\labor_template.hwp')
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$converter = Join-Path $PSScriptRoot '..\scripts\convert_hwp_hancom.ps1'
$temporaryRoot = [System.IO.Path]::GetFullPath([System.IO.Path]::GetTempPath())
$testDirectory = Join-Path $temporaryRoot (
    'hwpx-skill-hancom-test-' + [Guid]::NewGuid().ToString('N')
)
[void] (New-Item -ItemType Directory -Path $testDirectory)

function Assert-ZipHeader {
    param(
        [Parameter(Mandatory)]
        [string] $Path
    )

    $stream = [System.IO.File]::OpenRead($Path)
    try {
        if ($stream.Length -le 4 -or $stream.ReadByte() -ne 0x50 -or $stream.ReadByte() -ne 0x4B) {
            throw "Expected a ZIP-based HWPX output: $Path"
        }
    }
    finally {
        $stream.Dispose()
    }
}

try {
    if (-not (Test-Path -LiteralPath $Fixture)) {
        throw "HWP fixture was not found: $Fixture"
    }

    $firstRun = @(& $converter -InputPath $Fixture -OutputDirectory $testDirectory)
    $output = Join-Path $testDirectory 'labor_template.hwpx'
    if ($firstRun.Count -ne 1 -or -not (Test-Path -LiteralPath $output)) {
        throw 'The first Hancom conversion did not create exactly one output.'
    }
    Assert-ZipHeader -Path $output

    $hwpxRejected = $false
    try {
        [void] @(& $converter -InputPath $output)
    }
    catch {
        $hwpxRejected = $_.Exception.Message -like 'Only .hwp files are accepted:*'
    }
    if (-not $hwpxRejected) {
        throw 'A .hwpx input was not rejected by the exact extension gate.'
    }

    [System.IO.File]::WriteAllBytes($output, [byte[]] (1, 2, 3, 4))
    $secondRun = @(& $converter -InputPath $Fixture -OutputDirectory $testDirectory -Overwrite)
    if ($secondRun.Count -ne 1) {
        throw 'The overwrite conversion did not report exactly one output.'
    }
    Assert-ZipHeader -Path $output

    $temporaryOutputs = @(Get-ChildItem -LiteralPath $testDirectory -File |
        Where-Object { $_.Name -ne 'labor_template.hwpx' })
    if ($temporaryOutputs.Count -ne 0) {
        throw "Temporary HWPX files were left behind: $($temporaryOutputs.FullName -join ', ')"
    }

    'test_saved_hwpx_is_closed_before_atomic_publish: PASS'
}
finally {
    $resolvedTestDirectory = [System.IO.Path]::GetFullPath($testDirectory)
    if (-not $resolvedTestDirectory.StartsWith(
        $temporaryRoot,
        [System.StringComparison]::OrdinalIgnoreCase
    )) {
        throw "Refusing to remove a test directory outside the system temp path: $resolvedTestDirectory"
    }
    if (Test-Path -LiteralPath $resolvedTestDirectory) {
        Remove-Item -LiteralPath $resolvedTestDirectory -Recurse -Force
    }
}
