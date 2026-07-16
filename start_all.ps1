Clear-Host
$ProjectDir = $PSScriptRoot

Write-Host "=====================================================================" -ForegroundColor Cyan
Write-Host "    UNIFIED RAG COCKPIT INITIALIZATION (SAAPP & PAAPP)               " -ForegroundColor Cyan
Write-Host "=====================================================================" -ForegroundColor Cyan
Write-Host ""

# --- VIRTUAL ENVIRONMENT AUTOMATION LAYER ---
$VenvPath = Join-Path $ProjectDir ".venv"

if (-not (Test-Path $VenvPath)) {
    Write-Host "[!] Target environment container (.venv) not found." -ForegroundColor Yellow
    Write-Host "[*] Provisioning isolated runtime engine..." -ForegroundColor Yellow
    
    Set-Location $ProjectDir
    python -m venv .venv
    
    if ($LASTEXITCODE -ne 0) {
        Write-Host "[X] ERROR: Python execution failed. Verify Python is added to your System PATH variables." -ForegroundColor Red
        Pause
        Exit
    }
    
    Write-Host "[*] Syncing enterprise dependencies from requirements.txt..." -ForegroundColor Yellow
    & ".\.venv\Scripts\python.exe" -m pip install --upgrade pip --quiet
    & ".\.venv\Scripts\pip.exe" install -r requirements.txt
    
    Write-Host "[+] Secure boundary packages initialized successfully!`n" -ForegroundColor Green
} else {
    Write-Host "[+] Verified localized runtime container consistency (.venv active)." -ForegroundColor Green
}

# --- APPLICATION ORCHESTRATION LAYER ---

# 1. Start PAAPP Headless Tool Hub (Port 8003)
# Note: We cd into 'local-agent' so relative paths (like directory.json) resolve correctly
Write-Host "[*] Launching PAAPP Headless Tool Hub..." -ForegroundColor Yellow
Start-Process powershell -ArgumentList "-NoExit", "-Command", "`$Host.UI.RawUI.WindowTitle = 'PAAPP Headless (FastAPI)'; cd '$ProjectDir\local_agent'; .\.venv\Scripts\uvicorn headless_app:app --reload --port 8003"

# 2. Start SAAPP FastAPI Backend Engine (Port 8000)
Write-Host "[*] Launching SAAPP Security Backend..." -ForegroundColor Yellow
Start-Process powershell -ArgumentList "-NoExit", "-Command", "`$env:PYTHONUNBUFFERED=1; `$Host.UI.RawUI.WindowTitle = 'SAAPP Backend'; cd '$ProjectDir'; .\.venv\Scripts\uvicorn app:app --reload --reload-exclude 'chat_history.json' --reload-exclude 'directory.json' --reload-exclude 'chroma_db' --reload-exclude 'index-db'"

# Small delay to let the backend bind ports cleanly before hitting the frontend
Start-Sleep -Seconds 2

# 3. Start SAAPP Vite Frontend Server (Port 8080)
Write-Host "[*] Launching Vite Frontend Development Server..." -ForegroundColor Yellow
Start-Process powershell -ArgumentList "-NoExit", "-Command", "`$Host.UI.RawUI.WindowTitle = 'SAAPP Frontend (Vite)'; cd '$ProjectDir\local'; npm run dev -- --host 127.0.0.1 --port 8080"

Write-Host ""
Write-Host "=====================================================================" -ForegroundColor Green
Write-Host "  SUCCESS: All systems active in separate system instances!          " -ForegroundColor Green
Write-Host "  - SAAPP Backend: http://127.0.0.1:8000                             " -ForegroundColor Green
Write-Host "  - SAAPP Frontend: http://127.0.0.1:8080                            " -ForegroundColor Green
Write-Host "  - PAAPP Headless: http://127.0.0.1:8003/docs                       " -ForegroundColor Green
Write-Host "=====================================================================" -ForegroundColor Green
Write-Host ""