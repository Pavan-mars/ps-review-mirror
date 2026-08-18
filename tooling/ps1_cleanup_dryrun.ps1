# PS1 cleanup dry-run (Windows). Mirrors tooling/ps1_cleanup.sh read-only steps.

$ErrorActionPreference = "Continue"
$Region = if ($env:AWS_REGION) { $env:AWS_REGION } else { "us-east-1" }
$Ts = (Get-Date).ToUniversalTime().ToString("yyyyMMddTHHmmssZ")
$LogDir = Join-Path $PSScriptRoot "out"
New-Item -ItemType Directory -Force -Path $LogDir | Out-Null
$Log = Join-Path $LogDir "ps1_cleanup_${Ts}.log"

function Log($msg) { $msg | Tee-Object -FilePath $Log -Append }

Log "DRY RUN (PowerShell) - nothing will be changed"
Log "region=$Region ts=$Ts"
Log ""

Log "STEP 1 - ECR repositories (list only)"
$repoOut = aws ecr describe-repositories --region $Region --query "repositories[].repositoryName" --output text 2>&1
if ($LASTEXITCODE -ne 0) {
    Log "  ECR list failed: $repoOut"
} else {
    foreach ($r in ($repoOut -split "\s+")) {
        if (-not $r) { continue }
        $n = aws ecr list-images --repository-name $r --region $Region --query "length(imageIds)" --output text 2>&1
        Log ("  {0,-40} images={1}" -f $r, $n)
    }
}

Log ""
Log "STEP 2 - training-pipeline EventBridge rules"
$rules = aws events list-rules --region $Region --output json 2>&1
if ($LASTEXITCODE -ne 0) {
    Log "  events list failed: $rules"
} else {
    ($rules | ConvertFrom-Json).Rules | Where-Object { $_.Name -like "*training-pipeline*" } | ForEach-Object {
        Log ("  {0}  state={1}" -f $_.Name, $_.State)
    }
}

Log ""
Log "STEP 3 - cubic-pdm/mars-ps1 lifecycle policy"
$lc = aws ecr get-lifecycle-policy --repository-name "cubic-pdm/mars-ps1" --region $Region 2>&1
$lc | ForEach-Object { Log "  $_" }

Log ""
Log "STEP 4 - SageMaker endpoints (capture-gated)"
$CapDir = Join-Path $PSScriptRoot "out\endpoint_capture"
foreach ($ep in @(
    "chicago-ps1-3d-gate-failure-v1",
    "chicago-ps1-3d-tvm-failure-v1",
    "chicago-ps1-3d-validator-failure-v1"
)) {
    $cap = Join-Path $CapDir "$ep.endpointconfig.json"
    if (Test-Path $cap) {
        Log "  $ep - capture present (deletion permitted on --apply)"
    } else {
        Log "  $ep - BLOCKED (no capture)"
    }
}

Log ""
Log "STEP 5 - Path B ps1-rds-push (verify only)"
$conc = aws lambda get-function-concurrency --function-name cubic-mars-ps1-rds-push --region $Region 2>&1
$conc | ForEach-Object { Log "  $_" }

Log ""
Log "SUMMARY: DRY RUN complete. Transcript: $Log"
Write-Host "Wrote $Log"
