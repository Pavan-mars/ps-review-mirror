# PS3 cleanup dry-run (Windows). Read-only checks before endpoint retirement.

$ErrorActionPreference = "Continue"
$Region = if ($env:AWS_REGION) { $env:AWS_REGION } else { "us-east-1" }
$Ts = (Get-Date).ToUniversalTime().ToString("yyyyMMddTHHmmssZ")
$LogDir = Join-Path $PSScriptRoot "out"
New-Item -ItemType Directory -Force -Path $LogDir | Out-Null
$Log = Join-Path $LogDir "ps3_cleanup_${Ts}.log"

function Log($msg) { $msg | Tee-Object -FilePath $Log -Append }

Log "DRY RUN (PowerShell) - PS3 cleanup checks only"
Log "region=$Region ts=$Ts"
Log ""

Log "STEP 1 - ECR cubic-pdm/mars-ps3 (LIVE - do NOT delete repo)"
$tags = aws ecr list-images --repository-name "cubic-pdm/mars-ps3" --region $Region --query "imageIds[*].imageTag" --output text 2>&1
if ($LASTEXITCODE -ne 0) {
    Log "  ECR list failed: $tags"
} else {
    Log "  tags: $tags"
    if ($tags -match "latest") {
        Log "  [WARN] :latest tag present - pin model package to digest before retrain"
    }
}

Log ""
Log "STEP 2 - SageMaker endpoint (capture-gated for deletion)"
$CapDir = Join-Path $PSScriptRoot "out\endpoint_capture"
foreach ($ep in @("chicago-ps3-rootcause-v1")) {
    $cap = Join-Path $CapDir "$ep.endpointconfig.json"
    if (Test-Path $cap) {
        Log "  $ep - capture present (deletion permitted on --apply)"
    } else {
        Log "  $ep - BLOCKED (no capture at $cap)"
    }
    $desc = aws sagemaker describe-endpoint --endpoint-name $ep --region $Region --query "EndpointStatus" --output text 2>&1
    Log "  status: $desc"
}

Log ""
Log "STEP 3 - PS3 Lambda loaders (verify only)"
foreach ($fn in @(
    "cubic-mars-ps3-v25-loader",
    "cubic-mars-ps3-rc-loader",
    "cubic-mars-ps3-v2-loader"
)) {
    $st = aws lambda get-function-configuration --function-name $fn --region $Region --query "State" --output text 2>&1
    Log "  $fn state=$st"
}

Log ""
Log "STEP 4 - EventBridge PS3 rules (if any)"
$rules = aws events list-rules --region $Region --output json 2>&1
if ($LASTEXITCODE -ne 0) {
    Log "  events list failed: $rules"
} else {
    ($rules | ConvertFrom-Json).Rules | Where-Object { $_.Name -like "*ps3*" } | ForEach-Object {
        Log ("  {0}  state={1}" -f $_.Name, $_.State)
    }
    if (-not (($rules | ConvertFrom-Json).Rules | Where-Object { $_.Name -like "*ps3*" })) {
        Log "  (no PS3 EventBridge rules yet - expected pre go-live)"
    }
}

Log ""
Log "NOTE: cubic-pdm/mars-ps3 ECR repo is LIVE (model package references it)."
Log "      Only retire the ENDPOINT after batch parity - not the ECR repo."
Log ""
Log "SUMMARY: DRY RUN complete. Transcript: $Log"
Write-Host "Wrote $Log"
