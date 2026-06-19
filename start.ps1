Clear-Host
$ErrorActionPreference = "Stop"
$ProjectDir = $PSScriptRoot

Write-Host "=====================================================================" -ForegroundColor Cyan
Write-Host "            SONIC ASSISTANT APPLICATION INITIALIZATION TERMINAL            " -ForegroundColor Cyan
Write-Host "=====================================================================" -ForegroundColor Cyan
Write-Host ""

# --- 1. PRE-REQUISITE AUTOMATION (WINGET) ---

# Check Python
if (-not (Get-Command "python" -ErrorAction SilentlyContinue)) {
    Write-Warning "[!] Python was not detected on this system."
    $Choice = Read-Host "Would you like to automatically install Python 3.11 via Winget? (Y/N)"
    if ($Choice.ToUpper() -eq "Y") {
        Write-Host "[*] Installing Python... Please accept any Windows UAC prompts." -ForegroundColor Yellow
        winget install Python.Python.3.11 --silent --accept-package-agreements --accept-source-agreements
        Write-Host "[+] Python installed. Please restart this terminal if the script fails next." -ForegroundColor Green
    } else {
        Write-Host "[X] Exiting: Python is required to run the backend." -ForegroundColor Red; Pause; Exit
    }
}

# Check Node.js
if (-not (Get-Command "node" -ErrorAction SilentlyContinue)) {
    Write-Warning "[!] Node.js (npm) was not detected on this system."
    $Choice = Read-Host "Would you like to automatically install Node.js via Winget? (Y/N)"
    if ($Choice.ToUpper() -eq "Y") {
        Write-Host "[*] Installing Node.js... Please accept any Windows UAC prompts." -ForegroundColor Yellow
        winget install OpenJS.NodeJS --silent --accept-package-agreements --accept-source-agreements
        Write-Host "[+] Node.js installed." -ForegroundColor Green
    } else {
        Write-Host "[X] Exiting: Node.js is required to run the frontend UI." -ForegroundColor Red; Pause; Exit
    }
}

# Check Ollama
if (-not (Get-Command "ollama" -ErrorAction SilentlyContinue)) {
    Write-Warning "[!] Ollama AI Engine was not detected on this system."
    $Choice = Read-Host "Would you like to automatically install Ollama via Winget? (Y/N)"
    if ($Choice.ToUpper() -eq "Y") {
        Write-Host "[*] Installing Ollama... Please accept any Windows UAC prompts." -ForegroundColor Yellow
        winget install Ollama.Ollama --silent --accept-package-agreements --accept-source-agreements
        Write-Host "[+] Ollama installed successfully! Starting background service..." -ForegroundColor Green
        Start-Process "ollama" -ArgumentList "serve" -WindowStyle Hidden
        Start-Sleep -Seconds 3
    } else {
        Write-Host "[X] Exiting: Ollama is required for local AI execution." -ForegroundColor Red; Pause; Exit
    }
}


# --- 2. PYTHON VIRTUAL ENVIRONMENT ---
$VenvPath = Join-Path $ProjectDir ".venv"
if (-not (Test-Path $VenvPath)) {
    Write-Host "[*] Provisioning isolated Python runtime environment..." -ForegroundColor Yellow
    python -m venv .venv
    & ".\.venv\Scripts\python.exe" -m pip install --upgrade pip --quiet
    Write-Host "[*] Syncing backend dependencies from requirements.txt..." -ForegroundColor Yellow
    & ".\.venv\Scripts\pip.exe" install -r requirements.txt
}

# --- 3. FRONTEND DEPENDENCY INSTALLATION (THE MISSING LINK) ---
$FrontendDir = Join-Path $ProjectDir "local"  # Points to your 'local' Vite folder
if (-not (Test-Path (Join-Path $FrontendDir "node_modules"))) {
    Write-Host "[*] First-time setup: Installing frontend UI dependencies (npm install)..." -ForegroundColor Yellow
    Set-Location $FrontendDir
    npm install
    Set-Location $ProjectDir
}

# --- 4. MODEL SYNC & VECTOR INGESTION ---
Write-Host "[*] Ensuring Ollama background service is awake..." -ForegroundColor Yellow
Start-Process "ollama" -ArgumentList "serve" -WindowStyle Hidden -ErrorAction SilentlyContinue
Start-Sleep -Seconds 3 # Give it a moment to bind its local port
Write-Host "[*] Verifying local LLM allocation (Llama3)..." -ForegroundColor Yellow
ollama pull llama3

Write-Host "[*] Triggering local repository vector ingestion engine..." -ForegroundColor Yellow
& ".\.venv\Scripts\python.exe" ingest.py


# --- 5. ORCHESTRATION LAYER (LAUNCH) ---
Write-Host "[*] Launching FastAPI Security Backend Server..." -ForegroundColor Yellow
Start-Process powershell -ArgumentList "-NoExit", "-Command", "`$Host.UI.RawUI.WindowTitle = 'ARK Backend (FastAPI)'; cd '$ProjectDir'; .\.venv\Scripts\uvicorn app:app --reload"

Start-Sleep -Seconds 2

Write-Host "[*] Launching Vite Frontend Development Server..." -ForegroundColor Yellow
Start-Process powershell -ArgumentList "-NoExit", "-Command", "`$Host.UI.RawUI.WindowTitle = 'ARK Frontend (Vite)'; cd '$FrontendDir'; npm run dev -- --host 127.0.0.1 --port 8080"

Write-Host ""
Write-Host "=====================================================================" -ForegroundColor Green
Write-Host "   SUCCESS: Both engine cores active in separate system instances!   " -ForegroundColor Green
Write-Host "   - Backend: http://127.0.0.1:8000" -ForegroundColor Green
Write-Host "   - Frontend: http://127.0.0.1:8080" -ForegroundColor Green
Write-Host "=====================================================================" -ForegroundColor Green
Write-Host ""