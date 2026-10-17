# Start Agent 1 (audio service) in real mode on port 8001, without Docker.
# Run from the repository root in PowerShell:   .\agent1_audio\start_agent1.ps1
# First time only: it creates .venv-agent1 and installs the packages (about 10 minutes).
# Needs models\agent1_ast.joblib and models\agent1_ae.pt (not in Git, ask group A).
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

foreach ($m in "models\agent1_ast.joblib", "models\agent1_ae.pt") {
    if (-not (Test-Path $m)) { Write-Error "Missing $m : ask group A for the model files." }
}
if (-not (Test-Path ".venv-agent1")) {
    python -m venv .venv-agent1
    .\.venv-agent1\Scripts\python.exe -m pip install -r agent1_audio\requirements-real.txt --extra-index-url https://download.pytorch.org/whl/cpu
}
$env:MOCK_MODE = "false"
$env:HF_HUB_DISABLE_SYMLINKS_WARNING = "1"
Set-Location agent1_audio
# 0.0.0.0 so that n8n inside Docker can call it at http://host.docker.internal:8001
..\.venv-agent1\Scripts\python.exe -m uvicorn service.app:app --host 0.0.0.0 --port 8001
