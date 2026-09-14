# Upload pinned PS1 feature contracts to the artifacts bucket (TRAP 3 prerequisite).
# Usage: powershell -File tooling/upload_ps1_feature_contracts.ps1

$ErrorActionPreference = "Stop"
$Region = if ($env:AWS_REGION) { $env:AWS_REGION } else { "us-east-1" }
$Bucket = "cubic-mars-pm-s3-datalake-dev-artifacts-170202974600"
$Root = Split-Path $PSScriptRoot -Parent
$Out = Join-Path $Root "tooling\out"

foreach ($fleet in @("gate", "tvm", "validator")) {
    $src = Join-Path $Out "ps1_${fleet}_feature_contract.json"
    if (-not (Test-Path $src)) {
        Write-Error "Missing $src - run feature export from production notebook CELL 22 first."
    }
    $key = "chicago/ps1/contracts/ps1_${fleet}_feature_contract.json"
    Write-Host "Uploading $src -> s3://$Bucket/$key"
    aws s3 cp $src "s3://$Bucket/$key" --region $Region
}

Write-Host "Done. Contracts at s3://$Bucket/chicago/ps1/contracts/"
