# Starts everything the full chain needs on one PC (Windows PowerShell, from the repo root):
#   .\agent2_diagnosis\start_full_chain.ps1 -Python "C:\path\to\python.exe" [-RagDevice cuda|cpu] [-NoOllama]
#
#   n8n :5678 (Docker) | Ollama :11434 | RAG :8002 | Agent 1 :8001 | Agent 3 :8003 | test page :8501
# -Python    a Python that has the packages of agent2_diagnosis/dev_rag (sentence-transformers, fastapi, uvicorn, rank-bm25,
#            streamlit, requests) AND agent3_repair/requirements.txt. Agent 1 uses its own .venv-agent1 (created by its script).
# -RagDevice cpu (default, slow: 40-150 s per run) or cuda (6-50 s). With cuda the GPU is shared with Ollama: use Groq/Gemini then.
# Needs: Docker Desktop running; agent3_repair\.env with GROQ_API_KEY (Agent 3); .env at the root (copy .env.example).
param(
    [string]$Python = "python",
    [ValidateSet("cpu", "cuda")][string]$RagDevice = "cpu",
    [switch]$NoOllama
)
$repo = Split-Path -Parent $PSScriptRoot
Set-Location $repo

docker info *> $null
if ($LASTEXITCODE -ne 0) { Write-Host "Docker is not running: start Docker Desktop, wait for it, then run this again."; exit 1 }

function Start-Hidden($file, $argList, $dir) {
    Start-Process -FilePath $file -ArgumentList $argList -WorkingDirectory $dir -WindowStyle Hidden
}
function Listening($port) { [bool](Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue) }

# n8n only (the compose mock services must not take ports 8001/8002)
docker compose up -d --no-deps n8n 2>&1 | Out-Null

if (-not $NoOllama) {
    $env:OLLAMA_HOST = "0.0.0.0:11434"   # 0.0.0.0 so that n8n inside Docker can reach it
    if (-not (Get-Process -Name "ollama" -ErrorAction SilentlyContinue)) {
        Start-Hidden "$env:LOCALAPPDATA\Programs\Ollama\ollama.exe" "serve" $repo
    }
}

if (-not (Listening 8002)) {   # dev RAG (about 1 minute to load the embedding and reranking models)
    $env:DEV_RAG_DEVICE = $RagDevice; $env:DEV_RAG_RERANK_DEVICE = $RagDevice
    Start-Hidden $Python "-m uvicorn agent2_diagnosis.dev_rag.app:app --host 0.0.0.0 --port 8002" $repo
}
if (-not (Listening 8001)) {   # Agent 1: first run creates .venv-agent1 and installs torch (about 10 minutes)
    Start-Hidden "powershell.exe" "-NoProfile -ExecutionPolicy Bypass -File `"$repo\agent1_audio\start_agent1.ps1`"" $repo
}
if (-not (Listening 8003)) {   # Agent 3 (needs agent3_repair\.env)
    Start-Hidden $Python "-m uvicorn api:app --app-dir agent3_repair --host 127.0.0.1 --port 8003" $repo
}
if (-not (Listening 8501)) {   # test page (opens in full-chain mode: audio upload, Agent 1 + 2 + 3)
    $env:AGENT3_URL = "http://127.0.0.1:8003/generate"
    Start-Hidden $Python "-m streamlit run agent2_diagnosis\streamlit_app.py --server.headless true --server.address localhost --server.port 8501 --browser.gatherUsageStats false" $repo
}

Write-Host "Started. Models take 1-2 minutes to load (Agent 1: 10+ minutes the very first time). Checking every 10 s for up to 3 minutes..."
$checks = [ordered]@{ "n8n" = "http://localhost:5678/healthz"; "RAG" = "http://localhost:8002/health"; "Agent 1" = "http://localhost:8001/health";
                      "Agent 3" = "http://127.0.0.1:8003/health"; "Test page" = "http://localhost:8501" }
for ($i = 0; $i -lt 18; $i++) {
    $up = @{}
    foreach ($k in $checks.Keys) { try { Invoke-WebRequest $checks[$k] -TimeoutSec 3 -UseBasicParsing | Out-Null; $up[$k] = $true } catch { $up[$k] = $false } }
    if (($up.Values | Where-Object { -not $_ }).Count -eq 0) { break }
    Start-Sleep 10
}
foreach ($k in $checks.Keys) { Write-Host ("{0,-10} {1}" -f $k, $(if ($up[$k]) { "up" } else { "NOT UP yet" })) }
Write-Host "`nNext: import n8n/workflows/full_chain_<ollama|groq|gemini>.json in n8n (http://localhost:5678), pick the model credential, activate it. See agent2_diagnosis/HANDOVER.md."
