[CmdletBinding()]
param(
    [string]$DbRoot = 'D:\DB',
    [string]$DataRoot = 'D:\flow-data',
    [string]$BackupRoot = 'D:\flow-backups',
    [string]$OutputDirectory = (Join-Path ([IO.Path]::GetTempPath()) 'flow-storage-snapshots'),
    [string]$PreviousSnapshot
)

Set-StrictMode -Version 2.0
$ErrorActionPreference = 'Stop'

function Get-TreeStats {
    param([string]$RootPath)

    $result = [ordered]@{ Path = $RootPath; Exists = $false; Files = 0L; Directories = 0L; Bytes = 0L; LatestMtimeUtc = $null; Inaccessible = $false }
    try {
        if (-not (Test-Path -LiteralPath $RootPath -PathType Container)) { return [pscustomobject]$result }
        $rootItem = Get-Item -LiteralPath $RootPath -Force -ErrorAction Stop
        if (($rootItem.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
            $result.Inaccessible = $true
            return [pscustomobject]$result
        }
        $result.Exists = $true
        $stack = New-Object 'System.Collections.Generic.Stack[string]'
        $stack.Push($rootItem.FullName)
        $latest = $null
        while ($stack.Count -gt 0) {
            $current = $stack.Pop()
            try { $children = Get-ChildItem -LiteralPath $current -Force -ErrorAction Stop }
            catch { $result.Inaccessible = $true; continue }
            foreach ($item in $children) {
                try {
                    if (($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) { continue }
                    if ($item.PSIsContainer) {
                        $result.Directories++
                        $stack.Push($item.FullName)
                    } else {
                        $result.Files++
                        $result.Bytes += [long]$item.Length
                        $modified = $item.LastWriteTimeUtc
                        if ($null -eq $latest -or $modified -gt $latest) { $latest = $modified }
                    }
                } catch { $result.Inaccessible = $true }
            }
        }
        if ($null -ne $latest) { $result.LatestMtimeUtc = $latest.ToString('o') }
    } catch { $result.Inaccessible = $true }
    return [pscustomobject]$result
}

function Get-RelativeGroups {
    param([string]$RootPath, [object]$RootStats)
    $groups = New-Object System.Collections.Generic.List[object]
    $groups.Add([pscustomobject]@{ Group = '.'; Path = $RootPath; Exists = $rootStats.Exists; Files = $rootStats.Files; Directories = $rootStats.Directories; Bytes = $rootStats.Bytes; LatestMtimeUtc = $rootStats.LatestMtimeUtc; Inaccessible = $rootStats.Inaccessible })
    if (-not $rootStats.Exists) { return $groups.ToArray() }
    try {
        $knownNames = @('logs','chat','uploads','cache','jobs','history','home_conversations','auto_report','auto_report/output','auto_report/runtime','splittable','splittable/knob_s0_daily','file_versions','uploads/.trash')
        $knownSet = @{}
        foreach ($n in $knownNames) { $knownSet[$n.Replace('/','\')] = $true }
        $topItems = Get-ChildItem -LiteralPath $RootPath -Force -ErrorAction Stop
        foreach ($item in $topItems) {
            if (($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) { continue }
            if (-not $item.PSIsContainer) { continue }
            $rel = [IO.Path]::GetFileName($item.FullName)
            if ($knownSet.ContainsKey($rel)) { continue }
            $stats = Get-TreeStats -RootPath $item.FullName
            $groups.Add([pscustomobject]@{ Group = $rel; Path = $rel; Exists = $stats.Exists; Files = $stats.Files; Directories = $stats.Directories; Bytes = $stats.Bytes; LatestMtimeUtc = $stats.LatestMtimeUtc; Inaccessible = $stats.Inaccessible })
        }
        foreach ($name in $knownNames) {
            $relativePath = $name.Replace('/','\')
            $knownPath = Join-Path $RootPath $relativePath
            try {
                $safeBranch = $true
                $branchPath = $RootPath
                foreach ($segment in $relativePath.Split('\')) {
                    $branchPath = Join-Path $branchPath $segment
                    $branchItem = Get-Item -LiteralPath $branchPath -Force -ErrorAction Stop
                    if (-not $branchItem.PSIsContainer -or ($branchItem.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
                        $safeBranch = $false
                        break
                    }
                }
                if (-not $safeBranch) { continue }
                $knownItem = Get-Item -LiteralPath $knownPath -Force -ErrorAction Stop
                if (-not $knownItem.PSIsContainer -or ($knownItem.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) { continue }
                $knownStats = Get-TreeStats -RootPath $knownPath
                $groups.Add([pscustomobject]@{ Group = $name; Path = $relativePath; Exists = $knownStats.Exists; Files = $knownStats.Files; Directories = $knownStats.Directories; Bytes = $knownStats.Bytes; LatestMtimeUtc = $knownStats.LatestMtimeUtc; Inaccessible = $knownStats.Inaccessible })
            } catch [System.Management.Automation.ItemNotFoundException] {
                continue
            } catch {
                if (Test-Path -LiteralPath $knownPath) { $groups.Add([pscustomobject]@{ Group = $name; Path = $relativePath; Exists = $false; Files = 0L; Directories = 0L; Bytes = 0L; LatestMtimeUtc = $null; Inaccessible = $true }) }
            }
        }
    } catch { $groups.Add([pscustomobject]@{ Group = '(enumeration incomplete)'; Path = $RootPath; Exists = $true; Files = 0L; Directories = 0L; Bytes = 0L; LatestMtimeUtc = $null; Inaccessible = $true }) }
    return $groups.ToArray()
}

function Get-LogFileStats {
    param([string]$RootPath, [string]$RootName)
    $logPath = Join-Path $RootPath 'logs'
    $rows = New-Object System.Collections.Generic.List[object]
    try {
        $logItem = Get-Item -LiteralPath $logPath -Force -ErrorAction Stop
        if (-not $logItem.PSIsContainer -or ($logItem.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) { return @() }
        $files = Get-ChildItem -LiteralPath $logPath -File -Force -ErrorAction Stop
        foreach ($file in $files) {
            if (($file.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) { continue }
            $rows.Add([pscustomobject]@{ Root = $RootName; Basename = $file.Name; Bytes = [long]$file.Length; ModifiedUtc = $file.LastWriteTimeUtc.ToString('o') })
        }
    } catch [System.Management.Automation.ItemNotFoundException] { return @() }
    catch { return @([pscustomobject]@{ Root = $RootName; Basename = $null; Bytes = $null; ModifiedUtc = $null; Inaccessible = $true }) }
    return $rows.ToArray()
}

function Get-DriveStats {
    param([string]$DriveName)
    try {
        $drive = Get-CimInstance -ClassName Win32_LogicalDisk -Filter ("DeviceID='{0}:'" -f $DriveName) -ErrorAction Stop
        if ($null -eq $drive) { return [pscustomobject]@{ Drive = $DriveName; SizeBytes = $null; FreeBytes = $null; Available = $false } }
        return [pscustomobject]@{ Drive = $DriveName; SizeBytes = [long]$drive.Size; FreeBytes = [long]$drive.FreeSpace; Available = $true }
    } catch { return [pscustomobject]@{ Drive = $DriveName; SizeBytes = $null; FreeBytes = $null; Available = $false } }
}

New-Item -ItemType Directory -Force -Path $OutputDirectory | Out-Null
$stamp = (Get-Date).ToUniversalTime().ToString('yyyyMMddTHHmmssZ')
$rootSpecs = @(
    [pscustomobject]@{ Name = 'DB'; Path = $DbRoot },
    [pscustomobject]@{ Name = 'flow-data'; Path = $DataRoot },
    [pscustomobject]@{ Name = 'flow-backups'; Path = $BackupRoot }
)
$roots = New-Object System.Collections.Generic.List[object]
$groups = New-Object System.Collections.Generic.List[object]
$groupStatsByRoot = @{}
$logFiles = New-Object System.Collections.Generic.List[object]
foreach ($spec in $rootSpecs) {
    $stats = Get-TreeStats -RootPath $spec.Path
    $roots.Add([pscustomobject]@{ Name = $spec.Name; Path = $spec.Path; Exists = $stats.Exists; Files = $stats.Files; Directories = $stats.Directories; Bytes = $stats.Bytes; LatestMtimeUtc = $stats.LatestMtimeUtc; Inaccessible = $stats.Inaccessible })
    $rootGroups = @(Get-RelativeGroups -RootPath $spec.Path -RootStats $stats)
    $groupStatsByRoot[$spec.Name] = $rootGroups
    foreach ($g in $rootGroups) {
        $groups.Add([pscustomobject]@{ Root = $spec.Name; Group = $g.Group; RelativePath = $g.Path; Exists = $g.Exists; Files = $g.Files; Directories = $g.Directories; Bytes = $g.Bytes; LatestMtimeUtc = $g.LatestMtimeUtc; Inaccessible = $g.Inaccessible })
    }
    foreach ($log in (Get-LogFileStats -RootPath $spec.Path -RootName $spec.Name)) { $logFiles.Add($log) }
}

$comparison = $null
if ($PreviousSnapshot) {
    try {
        $previous = Get-Content -LiteralPath $PreviousSnapshot -Raw -ErrorAction Stop | ConvertFrom-Json -ErrorAction Stop
        $prevTime = [DateTime]::Parse($previous.CapturedAtUtc).ToUniversalTime()
        $days = ((Get-Date).ToUniversalTime() - $prevTime).TotalDays
        $comparisonRows = New-Object System.Collections.Generic.List[object]
        foreach ($current in $roots) {
            $prior = @($previous.Roots | Where-Object { $_.Name -eq $current.Name }) | Select-Object -First 1
            $rate = $null
            $delta = $null
            $samePath = $false
            if ($null -ne $prior -and $null -ne $prior.Path) {
                $currentPath = [IO.Path]::GetFullPath($current.Path).TrimEnd('\').ToLowerInvariant()
                $priorPath = [IO.Path]::GetFullPath([string]$prior.Path).TrimEnd('\').ToLowerInvariant()
                $samePath = ($currentPath -eq $priorPath)
            }
            $priorComplete = ($null -ne $prior -and $prior.Exists -and -not $prior.Inaccessible)
            if ($samePath -and $priorComplete -and $current.Exists -and -not $current.Inaccessible -and $days -gt 0) { $delta = [long]$current.Bytes - [long]$prior.Bytes; $rate = [math]::Round($delta / $days, 2) }
            $comparisonRows.Add([pscustomobject]@{ Root = $current.Name; PreviousBytes = $(if ($null -ne $prior) { [long]$prior.Bytes } else { $null }); CurrentBytes = [long]$current.Bytes; NetChangeBytes = $delta; NetChangeBytesPerDay = $rate })
        }
        $comparison = [pscustomobject]@{ PreviousSnapshot = $PreviousSnapshot; DaysBetween = [math]::Round($days, 4); Roots = @($comparisonRows.ToArray()) }
    } catch { Write-Warning ("Previous snapshot comparison skipped: {0}" -f $_.Exception.Message) }
}

$tempPath = [IO.Path]::GetTempPath()
$tempDrive = [IO.Path]::GetPathRoot($tempPath)
$snapshot = [pscustomobject]@{
    SchemaVersion = 1
    CapturedAtUtc = (Get-Date).ToUniversalTime().ToString('o')
    Roots = @($roots.ToArray())
    FolderGroups = @($groups.ToArray())
    LogFiles = @($logFiles.ToArray())
    Drives = @((Get-DriveStats -DriveName 'C'), (Get-DriveStats -DriveName 'D'))
    TemporaryDriveRoot = $tempDrive
    Comparison = $comparison
    Notes = @('Counts and sizes include only reachable files under the three configured roots.', 'Reparse points are skipped; inaccessible entries can make counts lower than actual.', 'No file contents are read; only log basenames are recorded, and non-log source filenames are omitted.', 'Folder group totals overlap: the root total includes child directories, so do not sum the root and its groups.')
}
$jsonPath = Join-Path $OutputDirectory ("flow-storage-{0}.json" -f $stamp)
$csvPath = Join-Path $OutputDirectory ("flow-storage-folders-{0}.csv" -f $stamp)
$snapshot | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $jsonPath -Encoding UTF8
$groups.ToArray() | Export-Csv -LiteralPath $csvPath -NoTypeInformation -Encoding UTF8

Write-Output ("Snapshot saved: {0}" -f $jsonPath)
Write-Output ("Folder groups saved: {0}" -f $csvPath)
foreach ($root in $roots) {
    $status = if ($root.Exists) { 'measured' } else { 'missing' }
    $extra = if ($root.Inaccessible) { '; some folders inaccessible or skipped' } else { '' }
    Write-Output ("{0}: {1:N0} bytes, {2:N0} files ({3}{4})" -f $root.Name, $root.Bytes, $root.Files, $status, $extra)
}
if ($null -ne $comparison) {
    foreach ($row in $comparison.Roots) {
        if ($null -ne $row.NetChangeBytesPerDay) { Write-Output ("{0}: net change {1:N0} bytes/day ({2:+#;-#;0} bytes total)" -f $row.Root, $row.NetChangeBytesPerDay, $row.NetChangeBytes) }
        else { Write-Output ("{0}: growth rate unavailable (no prior record or elapsed time)" -f $row.Root) }
    }
}
Write-Output ("Drive C: {0}; free {1}" -f $(if ($snapshot.Drives[0].Available) { '{0:N0} bytes' -f $snapshot.Drives[0].SizeBytes } else { 'unavailable' }), $(if ($snapshot.Drives[0].Available) { '{0:N0} bytes' -f $snapshot.Drives[0].FreeBytes } else { 'unavailable' }))
Write-Output ("Drive D: {0}; free {1}" -f $(if ($snapshot.Drives[1].Available) { '{0:N0} bytes' -f $snapshot.Drives[1].SizeBytes } else { 'unavailable' }), $(if ($snapshot.Drives[1].Available) { '{0:N0} bytes' -f $snapshot.Drives[1].FreeBytes } else { 'unavailable' }))
Write-Output (".NET temp drive: {0}" -f $tempDrive)
