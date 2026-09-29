<#
Move existing Flow data to the new Windows server storage (D:\flow-data, D:\DB).

Copies with robocopy so it can be re-run safely (only new/changed files are
copied again). Stop Flow on the source before the final copy so SQLite files
(product wiki, conversations) are consistent.

Examples
  # 1) preview what would be copied
  powershell -ExecutionPolicy Bypass -File .\scripts\windows\migrate_flow_data.ps1 -SourceData "\\old-server\share\flow-data" -DryRun
  # 2) copy flow-data (and DB) to D:
  powershell -ExecutionPolicy Bypass -File .\scripts\windows\migrate_flow_data.ps1 -SourceData "E:\backup\flow-data" -SourceDb "E:\backup\DB"
  # 3) skip rebuildable caches (faster, caches are rebuilt automatically)
  powershell -ExecutionPolicy Bypass -File .\scripts\windows\migrate_flow_data.ps1 -SourceData "E:\backup\flow-data" -SkipCache

Options
  -SourceData   existing flow-data folder (required)
  -SourceDb     existing DB folder (optional; skip if DB is delivered to D:\DB separately)
  -TargetRoot   storage drive/folder (default D:\) -> <TargetRoot>\flow-data, <TargetRoot>\DB
  -SkipCache    do not copy cache folders (they are regenerated)
  -DryRun       list only, copy nothing
#>
param(
    [Parameter(Mandatory = $true)][string]$SourceData,
    [string]$SourceDb = "",
    [string]$TargetRoot = "D:\",
    [switch]$SkipCache,
    [switch]$DryRun
)

$ErrorActionPreference = "Stop"

function Copy-Tree([string]$src, [string]$dst, [string[]]$excludeDirs) {
    if (-not (Test-Path $src)) { throw "Source not found: $src" }
    # A dry run must not create the target: an empty D:\flow-data would already
    # be picked up by Flow as its storage.
    if (-not $DryRun) { New-Item -ItemType Directory -Force -Path $dst | Out-Null }
    $rcArgs = @($src, $dst, "/E", "/COPY:DAT", "/DCOPY:T", "/R:1", "/W:1", "/MT:16", "/NP", "/XF", "*.tmp", "*.lock")
    if ($excludeDirs.Count -gt 0) { $rcArgs += "/XD"; $rcArgs += $excludeDirs }
    if ($DryRun) { $rcArgs += "/L" }
    Write-Host "robocopy $($rcArgs -join ' ')"
    & robocopy @rcArgs
    # robocopy exit codes 0-7 are success (8+ = failure)
    if ($LASTEXITCODE -ge 8) { throw "robocopy failed ($LASTEXITCODE): $src -> $dst" }
}

$dataTarget = Join-Path $TargetRoot "flow-data"
$dbTarget = Join-Path $TargetRoot "DB"

# Runtime-only folders that must not travel: worker queue/claims and temp files.
$dataExcludes = @("worker", "tmp", "__pycache__")
if ($SkipCache) { $dataExcludes += @("cache") }

Write-Host "== flow-data: $SourceData -> $dataTarget"
Copy-Tree $SourceData $dataTarget $dataExcludes

if ($SourceDb) {
    $dbExcludes = @()
    if ($SkipCache) { $dbExcludes += @("cache") }
    Write-Host "== DB: $SourceDb -> $dbTarget"
    Copy-Tree $SourceDb $dbTarget $dbExcludes
}

if (-not $DryRun) {
    # Old absolute root overrides would point back to the old server; drop them
    # so D:\DB / D:\flow-data (flow_env.bat) are used.
    $admin = Join-Path $dataTarget "admin_settings.json"
    if (Test-Path $admin) {
        $json = Get-Content $admin -Raw -Encoding UTF8 | ConvertFrom-Json
        if ($json.PSObject.Properties.Name -contains "data_roots") {
            Copy-Item $admin "$admin.pre_migration.bak" -Force
            $json.PSObject.Properties.Remove("data_roots")
            # BOM-less UTF-8 (Windows PowerShell's -Encoding UTF8 adds a BOM).
            [IO.File]::WriteAllText($admin, ($json | ConvertTo-Json -Depth 50), (New-Object Text.UTF8Encoding($false)))
            Write-Host "admin_settings.json: removed old data_roots override (backup: admin_settings.json.pre_migration.bak)"
        }
    }
    $files = (Get-ChildItem $dataTarget -Recurse -File -ErrorAction SilentlyContinue | Measure-Object).Count
    Write-Host "Done. flow-data files at target: $files"
    Write-Host "Next: run scripts\windows\flow_run.bat (or install_autostart.ps1 -StartNow) and open http://localhost:8080"
}
