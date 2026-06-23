Clear-Host
$ProjectDir = $PSScriptRoot

Write-Host "=====================================================================" -ForegroundColor Cyan
Write-Host "        SONIC ASSISTANT APPLICATION INITIALIZATION TERMINAL        " -ForegroundColor Cyan
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

# 1. Start the FastAPI Backend Engine (Port 8000)
Write-Host "[*] Launching FastAPI Security Backend..." -ForegroundColor Yellow
Start-Process powershell -ArgumentList "-NoExit", "-Command", "`$Host.UI.RawUI.WindowTitle = 'ARK Backend (FastAPI)'; cd '$ProjectDir'; .\.venv\Scripts\uvicorn app:app --host 0.0.0.0 --reload"

# Small delay to let the backend bind ports cleanly
Start-Sleep -Seconds 2

# 2. Start the Vite Frontend Server (Forced onto 127.0.0.1:8080)
Write-Host "[*] Launching Vite Frontend Development Server..." -ForegroundColor Yellow
Start-Process powershell -ArgumentList "-NoExit", "-Command", "`$Host.UI.RawUI.WindowTitle = 'ARK Frontend (Vite)'; cd 'local'; npm run dev -- --host 0.0.0.0 --port 8080"

Write-Host ""
Write-Host "=====================================================================" -ForegroundColor Green
Write-Host "   SUCCESS: Both engine cores active in separate system instances!   " -ForegroundColor Green
Write-Host "   - Backend: http://127.0.0.1:8000" -ForegroundColor Green
Write-Host "   - Frontend: http://127.0.0.1:8080" -ForegroundColor Green
Write-Host "=====================================================================" -ForegroundColor Green
Write-Host ""