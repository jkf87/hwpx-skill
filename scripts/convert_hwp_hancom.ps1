[CmdletBinding()]
param(
    [Parameter(Mandatory, Position = 0, ValueFromPipeline, ValueFromPipelineByPropertyName)]
    [Alias('FullName')]
    [string[]] $InputPath,

    [string] $OutputDirectory,

    [switch] $Recurse,

    [switch] $Overwrite,

    [switch] $Visible
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

function Get-HwpInputFiles {
    param(
        [Parameter(Mandatory)]
        [string[]] $Paths,

        [Parameter(Mandatory)]
        [bool] $IncludeSubdirectories
    )

    $files = [System.Collections.Generic.List[System.IO.FileInfo]]::new()
    $seen = [System.Collections.Generic.HashSet[string]]::new(
        [System.StringComparer]::OrdinalIgnoreCase
    )

    foreach ($path in $Paths) {
        $item = Get-Item -LiteralPath $path
        if ($item -is [System.IO.DirectoryInfo]) {
            $children = Get-ChildItem -LiteralPath $item.FullName -File -Recurse:$IncludeSubdirectories |
                Where-Object { $_.Extension -ieq '.hwp' }
            foreach ($child in $children) {
                if ($seen.Add($child.FullName)) {
                    $files.Add($child)
                }
            }
            continue
        }

        if ($item.Extension -ine '.hwp') {
            throw "Only .hwp files are accepted: $($item.FullName)"
        }

        if ($seen.Add($item.FullName)) {
            $files.Add($item)
        }
    }

    return $files.ToArray()
}

function Register-HwpFilePathModule {
    param(
        [Parameter(Mandatory)]
        [__ComObject] $Hwp
    )

    $registryPath = 'Registry::HKEY_CURRENT_USER\Software\HNC\HwpAutomation\Modules'
    if (-not (Test-Path -LiteralPath $registryPath)) {
        throw 'No Hancom file-path security module is registered for HwpAutomation.'
    }

    $registryItem = Get-ItemProperty -LiteralPath $registryPath
    $moduleNames = $registryItem.PSObject.Properties |
        Where-Object { $_.Name -notlike 'PS*' -and $_.Value -is [string] } |
        Select-Object -ExpandProperty Name

    foreach ($moduleName in $moduleNames) {
        if ($Hwp.RegisterModule('FilePathCheckDLL', $moduleName)) {
            return $moduleName
        }
    }

    throw 'Could not register a Hancom file-path security module.'
}

$inputFiles = @(Get-HwpInputFiles -Paths $InputPath -IncludeSubdirectories $Recurse.IsPresent)
if ($inputFiles.Count -eq 0) {
    throw 'No HWP files were found.'
}

$resolvedOutputDirectory = $null
if ($OutputDirectory) {
    $createdDirectory = New-Item -ItemType Directory -Path $OutputDirectory -Force
    $resolvedOutputDirectory = $createdDirectory.FullName
}

$targets = [System.Collections.Generic.HashSet[string]]::new(
    [System.StringComparer]::OrdinalIgnoreCase
)
foreach ($source in $inputFiles) {
    $targetDirectory = if ($resolvedOutputDirectory) {
        $resolvedOutputDirectory
    }
    else {
        $source.DirectoryName
    }
    $target = Join-Path $targetDirectory ($source.BaseName + '.hwpx')
    if (-not $targets.Add([System.IO.Path]::GetFullPath($target))) {
        throw "Multiple input files resolve to the same output path: $target"
    }
}

$hwp = $null
$failures = [System.Collections.Generic.List[string]]::new()
$totalTimer = [System.Diagnostics.Stopwatch]::StartNew()

try {
    $hwp = New-Object -ComObject 'HWPFrame.HwpObject'
    $hwp.XHwpWindows.Item(0).Visible = $Visible.IsPresent
    $moduleName = Register-HwpFilePathModule -Hwp $hwp
    Write-Host "Hancom engine $($hwp.Version), security module $moduleName"

    foreach ($source in $inputFiles) {
        $targetDirectory = if ($resolvedOutputDirectory) {
            $resolvedOutputDirectory
        }
        else {
            $source.DirectoryName
        }
        $target = Join-Path $targetDirectory ($source.BaseName + '.hwpx')

        if ((Test-Path -LiteralPath $target) -and -not $Overwrite) {
            Write-Warning "Skipped because output exists: $target"
            continue
        }

        $temporary = Join-Path $targetDirectory (
            ".{0}.{1}.hwpx" -f $source.BaseName, [Guid]::NewGuid().ToString('N')
        )
        $backup = $temporary + '.bak'
        $fileTimer = [System.Diagnostics.Stopwatch]::StartNew()
        $documentOpen = $false
        try {
            $opened = $hwp.Open(
                $source.FullName,
                'HWP',
                'forceopen:true;suspendpassword:true;lock:false;'
            )
            if (-not $opened) {
                throw 'The Hancom engine could not open the document.'
            }
            $documentOpen = $true

            $saved = $hwp.SaveAs(
                $temporary,
                'HWPX',
                'lock:false;backup:false;fullsave:true;'
            )
            if (-not $saved -or -not (Test-Path -LiteralPath $temporary)) {
                throw 'The Hancom engine could not save the HWPX document.'
            }

            [void] $hwp.Clear(1)
            $documentOpen = $false

            if (Test-Path -LiteralPath $target) {
                [System.IO.File]::Replace($temporary, $target, $backup, $true)
                Remove-Item -LiteralPath $backup -Force
            }
            else {
                [System.IO.File]::Move($temporary, $target)
            }

            $fileTimer.Stop()
            $outputFile = Get-Item -LiteralPath $target
            [PSCustomObject]@{
                Source = $source.FullName
                Output = $outputFile.FullName
                Seconds = [Math]::Round($fileTimer.Elapsed.TotalSeconds, 3)
                Bytes = $outputFile.Length
            }
        }
        catch {
            $failures.Add("$($source.FullName): $($_.Exception.Message)")
            Write-Error -ErrorAction Continue "Conversion failed: $($source.FullName) - $($_.Exception.Message)"
        }
        finally {
            if ($documentOpen) {
                [void] $hwp.Clear(1)
            }
            if (Test-Path -LiteralPath $temporary) {
                Remove-Item -LiteralPath $temporary -Force
            }
            if (Test-Path -LiteralPath $backup) {
                Remove-Item -LiteralPath $backup -Force
            }
        }
    }
}
finally {
    $totalTimer.Stop()
    if ($null -ne $hwp) {
        try {
            $hwp.Quit()
        }
        finally {
            [void] [System.Runtime.InteropServices.Marshal]::FinalReleaseComObject($hwp)
        }
    }
}

Write-Host "Total seconds: $([Math]::Round($totalTimer.Elapsed.TotalSeconds, 3))"
if ($failures.Count -gt 0) {
    $failureText = $failures -join [Environment]::NewLine
    throw "$($failures.Count) conversion(s) failed.$([Environment]::NewLine)$failureText"
}
