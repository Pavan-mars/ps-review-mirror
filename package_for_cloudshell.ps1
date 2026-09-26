# =====================================================================
# package_for_cloudshell.ps1   -- run in Windows PowerShell from the repo root
#
# Builds a small zip with ONLY what deploy_e2e.sh needs, for CloudShell upload.
#
# 26-Jul-2026: rewritten to stop using Compress-Archive. On Windows,
# Compress-Archive writes zip entries with BACKSLASH separators
# ("api\lambda\cubic-mars-dashboard-api\handler.py"). Linux unzip does not treat
# a backslash as a path separator, so it creates ONE flat file whose name
# contains backslashes instead of a directory tree -- which is why CloudShell
# reported:
#     warning:  cubic_deploy.zip appears to use backslashes as path separators
#     -bash: cd: cubic_deploy/api/lambda/cubic-mars-dashboard-api: ... no such tree
#
# This version writes each entry explicitly with forward slashes via
# System.IO.Compression.ZipArchive, which unzip and AWS both read correctly.
# =====================================================================
$ErrorActionPreference = "Stop"
Set-Location "D:\work data\NAM_development\AWS\development_project\sathish_repo"

$stage = "$env:TEMP\cubic_deploy"
if (Test-Path $stage) { Remove-Item $stage -Recurse -Force }
New-Item -ItemType Directory -Path $stage | Out-Null

# 26-Jul-2026 FIX. $env:TEMP can resolve to the 8.3 SHORT form
# (C:\Users\PAVANK~1\AppData\Local\Temp) while Get-ChildItem returns the LONG
# form (C:\Users\Pavan Kumar\AppData\Local\Temp). The two differ in length, so
# the Substring below cut the wrong number of characters and every zip entry
# came out as "loy/api/..." -- the tail of "cubic_dep|loy". Re-resolving through
# Get-Item forces the long form, so both strings share the same prefix.
$stage = (Get-Item $stage).FullName

New-Item -ItemType Directory -Path "$stage\api\lambda" -Force | Out-Null
Copy-Item "api\lambda\cubic-mars-dashboard-api" "$stage\api\lambda\" -Recurse
Copy-Item "api\lambda\cubic-mars-ps1-rds-push"  "$stage\api\lambda\" -Recurse
# 26-Jul-2026: the PS4 loader (SageMaker -> S3 -> Lambda -> Aurora)
Copy-Item "api\lambda\cubic-mars-ps4-rds-loader" "$stage\api\lambda\" -Recurse
# 27-Jul-2026: the daily device<->serial dimension loader
Copy-Item "api\lambda\cubic-mars-dim-loader"      "$stage\api\lambda\" -Recurse
# 27-Jul-2026: the PS2 loader. 27 tables already sitting in
# s3://<artifacts>/ps2_outputs/ from the 26-Jul run -- no notebook re-run needed.
Copy-Item "api\lambda\cubic-mars-ps2-rds-loader"  "$stage\api\lambda\" -Recurse
# 27-Jul-2026: the PS5 loader (gold/chicago/ps5/notebook_outputs -> Aurora)
Copy-Item "api\lambda\cubic-mars-ps5-rds-loader"  "$stage\api\lambda\" -Recurse
# 28-Jul-2026: the PS1 cross-wired loader. 786,428 rows carrying the SHAP triples
# and the PS2 chain columns -- the feature-importance and causation evidence that
# has never had a table to land in.
Copy-Item "api\lambda\cubic-mars-ps1-xw-loader"   "$stage\api\lambda\" -Recurse
# 29-Jul-2026: the PS4 v3 weekly loader. A NEW function -- the
# cubic-mars-ps4-rds-loader copied above is untouched and stays as Plan B.
# Without this line the v3 loader never reaches CloudShell and its deploy.sh
# fails with "No such file or directory" AFTER the upload looked clean.
Copy-Item "api\lambda\cubic-mars-ps4-v3-loader"   "$stage\api\lambda\" -Recurse
# 29-Jul-2026: the PS3 hardened-remediation loader. NEW function; every
# existing PS3 table and loader is untouched and stays as Plan B.
Copy-Item "api\lambda\cubic-mars-ps3-v2-loader"   "$stage\api\lambda\" -Recurse
Copy-Item "tooling" "$stage\tooling" -Recurse
Copy-Item "deploy_e2e.sh" "$stage\"

# drop build junk that would bloat the upload
Get-ChildItem $stage -Recurse -Include "build","fn.zip","__pycache__" -Force |
  Remove-Item -Recurse -Force -ErrorAction SilentlyContinue

$zip = "$env:USERPROFILE\Desktop\cubic_deploy.zip"
if (Test-Path $zip) { Remove-Item $zip -Force }

Add-Type -AssemblyName System.IO.Compression
Add-Type -AssemblyName System.IO.Compression.FileSystem

$fs      = [System.IO.File]::Open($zip, [System.IO.FileMode]::CreateNew)
$archive = New-Object System.IO.Compression.ZipArchive($fs, [System.IO.Compression.ZipArchiveMode]::Create)
try {
  $files = Get-ChildItem -Path $stage -Recurse -File
  foreach ($f in $files) {
    # Relative path. Substring is correct here ONLY because $stage was re-resolved
    # to its long form above; the short-vs-long mismatch was the "loy/" bug.
    # [System.IO.Path]::GetRelativePath is .NET Core only -- Windows PowerShell 5.1
    # runs on .NET Framework and does not have it, so do not reach for it here.
    if (-not $f.FullName.StartsWith($stage, [StringComparison]::OrdinalIgnoreCase)) {
      throw "stage prefix mismatch: '$($f.FullName)' does not start with '$stage'"
    }
    $rel = $f.FullName.Substring($stage.Length).TrimStart('\','/') -replace '\\','/'
    $entry  = $archive.CreateEntry($rel, [System.IO.Compression.CompressionLevel]::Optimal)
    $inStr  = [System.IO.File]::OpenRead($f.FullName)
    $outStr = $entry.Open()
    try { $inStr.CopyTo($outStr) } finally { $outStr.Dispose(); $inStr.Dispose() }
  }
  Write-Host ("{0} file(s) added" -f $files.Count)
} finally {
  $archive.Dispose()
  $fs.Dispose()
}

# ---- verify the entries really use forward slashes before you upload --------
$check = [System.IO.Compression.ZipFile]::OpenRead($zip)
try {
  $bad = @($check.Entries | Where-Object { $_.FullName -match '\\' })
  $sample = ($check.Entries | Select-Object -First 3 | ForEach-Object { $_.FullName }) -join "`n  "
  Write-Host "`nfirst entries:`n  $sample"
  if ($bad.Count -gt 0) {
    Write-Host "`nFAILED: $($bad.Count) entr(ies) still contain a backslash." -ForegroundColor Red
    exit 1
  }
  Write-Host "`nOK: all $($check.Entries.Count) entries use forward slashes." -ForegroundColor Green

  # Every entry must start at a known top-level name. This is the check that
  # would have caught the "loy/" prefix bug before the upload rather than after.
  $roots = @("api/", "tooling/", "deploy_e2e.sh")
  $stray = @($check.Entries | Where-Object {
      $n = $_.FullName; -not ($roots | Where-Object { $n.StartsWith($_) }) })
  if ($stray.Count -gt 0) {
    Write-Host "`nFAILED: $($stray.Count) entr(ies) have an unexpected path prefix:" -ForegroundColor Red
    $stray | Select-Object -First 5 | ForEach-Object { Write-Host "   $($_.FullName)" }
    exit 1
  }
  Write-Host "OK: every entry rooted at api/ , tooling/ or deploy_e2e.sh" -ForegroundColor Green

  # The three run-data files are the entire point of this build. If sql/load did
  # not make it in, load_run would report "file not present" and load nothing.
  $load = @($check.Entries | Where-Object { $_.FullName -like "*cubic-mars-dashboard-api/sql/load/*.sql" })
  Write-Host ("sql/load files in zip : {0}" -f $load.Count)
  $load | ForEach-Object { Write-Host ("   {0}  ({1:N0} bytes uncompressed)" -f $_.Name, $_.Length) }
  if ($load.Count -lt 7) {
    Write-Host "FAILED: expected 7 files under sql/load/." -ForegroundColor Red
    exit 1
  }

  # 27-Jul-2026. Copy-Item above already sweeps in EVERY file under the two
  # lambda directories, so all of sql/01..24 and all of sql/load/ travel by
  # default. This list is not that sweep -- it names the files that are NEW in
  # this build and would therefore be the ones missing if the repo copy is stale.
  #
  # It exists because a missing DDL file fails SILENTLY. migrate() skips a file
  # it cannot find and still reports success, so the table is simply never
  # created, the deploy looks clean, and the first symptom is an empty panel in
  # the dashboard hours later. Asserting by name turns that into a failed build.
  $must = @(
    "api/lambda/cubic-mars-dashboard-api/sql/22_dim_device_serial.sql",
    "api/lambda/cubic-mars-dashboard-api/sql/23_ps3_coverage.sql",
    "api/lambda/cubic-mars-dashboard-api/sql/24_ps3_all_devices.sql",
    "api/lambda/cubic-mars-dashboard-api/sql/load/ps3_coverage_20260726.sql",
    "api/lambda/cubic-mars-dim-loader/handler.py",
    "api/lambda/cubic-mars-dim-loader/deploy.sh",
    "api/lambda/cubic-mars-dim-loader/requirements.txt",
    "api/lambda/cubic-mars-ps2-rds-loader/handler.py",
    "api/lambda/cubic-mars-ps2-rds-loader/deploy.sh",
    # 27-Jul-2026. scope on ps2_network_centrality + rebuilt PK. If this file is
    # absent, migrate() skips it silently, the old 3-column key survives, and the
    # PS2 load dies again on (CHI, PRINTER, ...) -- taking 21 good tables with it.
    "api/lambda/cubic-mars-dashboard-api/sql/26_ps2_scope.sql",
    "api/lambda/cubic-mars-dashboard-api/sql/27_ps2_device_grain.sql",
    "api/lambda/cubic-mars-dashboard-api/sql/28_dim_serial_backfill.sql",
    "api/lambda/cubic-mars-dashboard-api/sql/29_ps5_outputs.sql",
    "api/lambda/cubic-mars-dashboard-api/sql/30_ps5_serial_rul.sql",
    "api/lambda/cubic-mars-dashboard-api/sql/31_ps5_serial_rul_grain.sql",
    "api/lambda/cubic-mars-dashboard-api/sql/32_ps5_serial_dedup.sql",
    "api/lambda/cubic-mars-dashboard-api/sql/67_ps5_act_now_probability.sql",
    "api/lambda/cubic-mars-dashboard-api/sql/68_ps5_shadow_drop.sql",
    "api/lambda/cubic-mars-dashboard-api/sql/69_ps4_device360_v3.sql",
    "api/lambda/cubic-mars-dashboard-api/sql/70_ps4_legacy_drop.sql",
    "api/lambda/cubic-mars-dashboard-api/sql/34_ps1_cross_wired.sql",
    "api/lambda/cubic-mars-dashboard-api/sql/35_ps1_label_onset.sql",
    "api/lambda/cubic-mars-dashboard-api/sql/36_ps1_state_framing.sql",
    "api/lambda/cubic-mars-dashboard-api/sql/37_ps1_predictions_xw.sql",
    # 29-Jul-2026. PS4 v3 weekly: six new tables, ten new views. Named here
    # because a missing DDL file fails SILENTLY -- migrate() skips what it
    # cannot find and still reports success.
    "api/lambda/cubic-mars-dashboard-api/sql/38_ps4_weekly_v3.sql",
    "api/lambda/cubic-mars-ps4-v3-loader/handler.py",
    "api/lambda/cubic-mars-ps4-v3-loader/deploy.sh",
    "api/lambda/cubic-mars-ps4-v3-loader/requirements.txt",
    "api/lambda/cubic-mars-dashboard-api/sql/39_ps3_v2.sql",
    "api/lambda/cubic-mars-dashboard-api/sql/40_ps3_v2_rootcause.sql",
    "api/lambda/cubic-mars-dashboard-api/sql/41_dim_device_bus.sql",
    "api/lambda/cubic-mars-dashboard-api/sql/load/bus_map_20260729.sql",
    "api/lambda/cubic-mars-ps3-v2-loader/handler.py",
    "api/lambda/cubic-mars-ps3-v2-loader/deploy.sh",
    "api/lambda/cubic-mars-ps3-v2-loader/spec.json",
    "api/lambda/cubic-mars-ps1-xw-loader/handler.py",
    "api/lambda/cubic-mars-ps1-xw-loader/deploy.sh",
    "api/lambda/cubic-mars-ps5-rds-loader/handler.py",
    "api/lambda/cubic-mars-ps5-rds-loader/deploy.sh",
    "api/lambda/cubic-mars-ps4-rds-loader/_merge_notif.py"
  )
  $names = @($check.Entries | ForEach-Object { $_.FullName })
  $miss  = @($must | Where-Object { $names -notcontains $_ })
  if ($miss.Count -gt 0) {
    Write-Host "`nFAILED: dimension pipeline files missing from the zip:" -ForegroundColor Red
    $miss | ForEach-Object { Write-Host "   $_" }
    exit 1
  }
  Write-Host ("OK: all {0} required new files present" -f $must.Count) -ForegroundColor Green
  $must | ForEach-Object { Write-Host "   $_" -ForegroundColor DarkGray }
} finally { $check.Dispose() }

# ---- confirm today's changes actually made it in ---------------------------
$h = Get-Content "api\lambda\cubic-mars-dashboard-api\handler.py" -Raw
$d = Get-Content "api\lambda\cubic-mars-dashboard-api\deploy.sh"  -Raw
Write-Host ("handler.py  def purge   : {0}" -f $(if ($h -match 'def purge')      {"present"} else {"MISSING"}))
Write-Host ("handler.py  _migrate_run: {0}" -f $(if ($h -match '_migrate_run')   {"present"} else {"MISSING"}))
Write-Host ("handler.py  ps4 v3 route: {0}" -f $(if ($h -match '/ps4/v3-status') {"present"} else {"MISSING"}))
Write-Host ("deploy.sh   timeout 300 : {0}" -f $(if ($d -match '--timeout 300')  {"present"} else {"MISSING"}))

"{0:N2} MB  ->  {1}" -f ((Get-Item $zip).Length/1MB), $zip
"Now, IN THIS ORDER:"
"  1. CloudShell:  rm -f ~/cubic_deploy.zip"
"     (CloudShell REFUSES to overwrite an existing file and only reports it as a"
"      transient toast -- every upload after the first silently no-ops otherwise)"
"  2. Actions -> Upload file -> pick that zip"
"  3. cd ~ && rm -rf cubic_deploy && unzip -q cubic_deploy.zip -d cubic_deploy"
