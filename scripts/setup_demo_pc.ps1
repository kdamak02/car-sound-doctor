# One-time setup of the FULL chain (Telegram + n8n + Agent 1 + Agent 2 + RAG + Agent 3) on a Windows PC.
# Run from the repository root, in PowerShell:   .\scripts\setup_demo_pc.ps1
# Safe to run again: every step is skipped when it is already done.
# What it cannot do for you (it tells you when): install Docker Desktop / Ollama, create the Telegram bot,
# get a Groq key, and get the 6 owner's manuals (copyrighted PDFs, not in Git: ask group B).
#   .\scripts\setup_demo_pc.ps1 -TelegramToken "<token>" -GroqKey "<key>"   (no questions asked: you can leave it running)
param([string]$TelegramToken = "", [string]$GroqKey = "", [switch]$NoPrompt)
$ErrorActionPreference = "Continue"
$repo = Split-Path -Parent $PSScriptRoot
Set-Location $repo
$todo = @()
function Step($t) { Write-Host "`n=== $t" -ForegroundColor Cyan }
function Ok($t) { Write-Host "  OK   $t" -ForegroundColor Green }
function Todo($t) { Write-Host "  TODO $t" -ForegroundColor Yellow; $script:todo += $t }
# set KEY=value in a .env file, UTF-8 WITHOUT BOM (a BOM would break the first key for Python and Docker)
function SetEnv($file, $key, $value) {
    $path = Join-Path $repo $file
    $lines = @(if (Test-Path $path) { [IO.File]::ReadAllLines($path) })
    $found = $false
    $lines = $lines | ForEach-Object { if ($_ -match "^$key=") { $found = $true; "$key=$value" } else { $_ } }
    if (-not $found) { $lines = @($lines) + "$key=$value" }
    [IO.File]::WriteAllLines($path, [string[]]$lines)
}

Step "1/9 Tools on this PC"
if (Get-Command python -ErrorAction SilentlyContinue) { Ok (python --version) } else { Write-Host "Python is missing: install Python 3.11 from python.org (tick 'Add to PATH') and run again."; exit 1 }
if (Get-Command docker -ErrorAction SilentlyContinue) { Ok "Docker installed" } else { Todo "Install Docker Desktop: https://www.docker.com/products/docker-desktop/ (then restart this script)" }
$ollama = Get-Command ollama -ErrorAction SilentlyContinue
if (-not $ollama -and (Test-Path "$env:LOCALAPPDATA\Programs\Ollama\ollama.exe")) { $ollama = "$env:LOCALAPPDATA\Programs\Ollama\ollama.exe" }
if ($ollama) { Ok "Ollama installed" } else { Todo "Optional: install Ollama (https://ollama.com/download) for the bot's P1/P2/P7 prompts; without it the bot uses its code fallbacks" }

Step "2/9 Agent 1 environment (.venv-agent1: torch CPU, transformers, onnxruntime)"
if (-not (Test-Path ".venv-agent1")) { python -m venv .venv-agent1 }
.\.venv-agent1\Scripts\python.exe -m pip install -q --upgrade pip
.\.venv-agent1\Scripts\python.exe -m pip install -q -r agent1_audio\requirements-real.txt --extra-index-url https://download.pytorch.org/whl/cpu
if ($LASTEXITCODE -eq 0) { Ok "Agent 1 packages" } else { Todo "Agent 1 packages failed: run the pip line of this script by hand and read the error" }

Step "3/9 Chain environment (.venv-chain: RAG + Agent 3 + test page)"
if (-not (Test-Path ".venv-chain")) { python -m venv .venv-chain }
$py = "$repo\.venv-chain\Scripts\python.exe"
& $py -m pip install -q --upgrade pip
& $py -m pip install -q torch --index-url https://download.pytorch.org/whl/cpu
& $py -m pip install -q sentence-transformers fastapi "uvicorn[standard]" rank-bm25 pymupdf pyyaml requests streamlit numpy pytest jsonschema -r agent3_repair\requirements.txt
if ($LASTEXITCODE -eq 0) { Ok "Chain packages ($py)" } else { Todo "Chain packages failed: run the two pip lines of step 3 by hand" }

Step "4/9 Configuration files (.env, agent3_repair\.env)"
if (-not (Test-Path ".env")) {
    Copy-Item .env.example .env
    $k = -join ((48..57) + (97..122) | Get-Random -Count 40 | ForEach-Object { [char]$_ })
    SetEnv ".env" "N8N_ENCRYPTION_KEY" $k
    Ok ".env created (random n8n key)"
}
$envText = Get-Content .env -Raw
if ($envText -notmatch '(?m)^TELEGRAM_BOT_TOKEN=\S+') {
    $t = $TelegramToken; if (-not $t -and -not $NoPrompt) { $t = Read-Host "Telegram bot token from @BotFather (Enter to skip)" }
    if ($t) { SetEnv ".env" "TELEGRAM_BOT_TOKEN" $t; Ok "Telegram token saved in .env (never commit .env)" }
    else { Todo "Create the bot: Telegram -> @BotFather -> /newbot, then put TELEGRAM_BOT_TOKEN=... in .env" }
} else { Ok "TELEGRAM_BOT_TOKEN set" }
if (-not $ollama -and $envText -notmatch '(?m)^TELEGRAM_LLM=') { SetEnv ".env" "TELEGRAM_LLM" "off"; Ok "TELEGRAM_LLM=off (no Ollama: the bot uses its code fallbacks)" }
if (-not (Test-Path "agent3_repair\.env")) { Copy-Item agent3_repair\.env.example agent3_repair\.env }
if ((Get-Content agent3_repair\.env -Raw) -notmatch '(?m)^GROQ_API_KEY=\S{20,}') {
    $g = $GroqKey; if (-not $g -and -not $NoPrompt) { $g = Read-Host "Groq API key (free at https://console.groq.com/keys, or the team key; Enter to skip)" }
    if ($g) { SetEnv "agent3_repair\.env" "GROQ_API_KEY" $g; Ok "Groq key saved in agent3_repair\.env" }
    else { Todo "Put GROQ_API_KEY=... in agent3_repair\.env (Agent 3 and the Groq workflow need it)" }
} else { Ok "GROQ_API_KEY set" }

Step "5/9 Demo clips (public samples, local tests only) + a speech clip for scenario s03"
$clips = "data\audio\demo_clips"; New-Item -ItemType Directory -Force $clips | Out-Null
$base = "https://raw.githubusercontent.com/jlacsam/cardiag/main/samples"
foreach ($n in "sample_failed_start_0000","sample_failed_start_0001","sample_failed_start_0002","sample_failed_start_0003","sample_failed_start_0004","sample_normal_engine_startup_0000") {
    if (-not (Test-Path "$clips\$n.wav")) { Invoke-WebRequest "$base/$n.wav" -OutFile "$clips\$n.wav" -UseBasicParsing }
}
if (-not (Test-Path "$clips\speech_1001_DFA_NEU_XX.wav")) {
    Invoke-WebRequest "https://media.githubusercontent.com/media/CheyneyComputerScience/CREMA-D/master/AudioWAV/1001_DFA_NEU_XX.wav" -OutFile "$clips\speech_1001_DFA_NEU_XX.wav" -UseBasicParsing
}
$ff = & .\.venv-agent1\Scripts\python.exe -c "import imageio_ffmpeg; print(imageio_ffmpeg.get_ffmpeg_exe())"
function ToOgg($src, $dst) { if (-not (Test-Path $dst)) { & $ff -y -loglevel error -i $src -ar 48000 -ac 1 -c:a libopus -b:a 48k $dst } }
ToOgg "$clips\sample_failed_start_0002.wav" "$clips\demo_failed_start.ogg"
ToOgg "$clips\sample_normal_engine_startup_0000.wav" "$clips\sample_normal_engine_startup_0000.ogg"
if (Test-Path "$clips\demo_failed_start.ogg") { Ok "demo_failed_start.ogg ready (failed start, voice-note format)" } else { Todo "Demo clip conversion failed (ffmpeg)" }

Step "6/9 Owner's manuals and the search index (RAG of Agent 2)"
$index = "agent2_diagnosis\dev_rag\index\structure\embeddings.npy"
$man = "data\rag\raw\manuals"
$files = (Get-Content agent2_diagnosis\dev_rag\manuals.json -Raw | ConvertFrom-Json).file
if (Test-Path $index) { Ok "Search index already built" }
else {
    $missing = $files | Where-Object { -not (Test-Path "$man\$_") }
    if ($missing) {
        Todo ("Get from group B (B2): EITHER the folder agent2_diagnosis\dev_rag\index\ (fastest, copy it as is) OR these PDFs into $man : " + ($missing -join ", ") + ". Then run this script again.")
    } else {
        Write-Host "  Building the index on CPU (BGE-M3; 20-60 min the first time, the model is downloaded once)..."
        & $py agent2_diagnosis\dev_rag\ingest.py structure
        & $py agent2_diagnosis\dev_rag\ingest.py obdex
        if (Test-Path $index) { Ok "Index built" } else { Todo "Index build failed: read the messages above" }
    }
}
if (-not (Test-Path "agent2_diagnosis\dev_rag\index\obdex") -and (Test-Path $index)) { & $py agent2_diagnosis\dev_rag\ingest.py obdex }

Step "7/9 Download the models once (so the demo does not wait): AST, BGE-M3, bge-reranker"
$env:HF_HUB_DISABLE_SYMLINKS_WARNING = "1"
& .\.venv-agent1\Scripts\python.exe -c "from transformers import ASTModel, ASTFeatureExtractor as F; m='MIT/ast-finetuned-audioset-10-10-0.4593'; F.from_pretrained(m); ASTModel.from_pretrained(m); print('  AST ok')"
& $py -c "from sentence_transformers import SentenceTransformer, CrossEncoder; SentenceTransformer('BAAI/bge-m3'); CrossEncoder('BAAI/bge-reranker-v2-m3'); print('  BGE-M3 + reranker ok')"

Step "8/9 Local LLM (optional)"
if ($ollama) { & $ollama pull qwen2.5:7b; Ok "qwen2.5:7b pulled" } else { Write-Host "  skipped (no Ollama)" }

Step "9/9 Offline tests (no key, no network)"
& .\.venv-agent1\Scripts\python.exe -m pip install -q pytest httpx jsonschema
Push-Location agent1_audio; & ..\.venv-agent1\Scripts\python.exe -m pytest -q tests; Pop-Location
& $py -m pytest -q agent2_diagnosis\tests
if (Get-Command node -ErrorAction SilentlyContinue) { node n8n\code_nodes\test_agent2_nodes.js; node n8n\telegram_front\test_telegram_front.js }

Write-Host "`n=================== SUMMARY" -ForegroundColor Cyan
if ($todo.Count -eq 0) { Write-Host "Everything is ready." -ForegroundColor Green } else { $todo | ForEach-Object { Write-Host " - $_" -ForegroundColor Yellow } }
Write-Host "`nThen, every time (Docker Desktop running):"
Write-Host ("  .\agent2_diagnosis\start_full_chain.ps1 -Python `"$py`" -RagDevice cpu" + $(if ($ollama) { "" } else { " -NoOllama" }))
Write-Host "  n8n http://localhost:5678 : import n8n\workflows\full_chain_groq.json + telegram_front.json, set the Groq credential, activate both"
Write-Host "  $py agent2_diagnosis\tests\preflight.py   then   $py agent2_diagnosis\tests\live_check.py"
