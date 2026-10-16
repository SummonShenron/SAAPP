<#
.SYNOPSIS
    Starts SAAPP (Sonic Assistant) for local development: the FastAPI backend on 8000 and the Vite frontend on 8080, each in its own window.

.EXAMPLE
    .\start_saapp.ps1                 # both
    .\start_saapp.ps1 -BackendOnly    # just the API
    .\start_saapp.ps1 -FrontendOnly   # just the UI
#>
param(
    [switch]$BackendOnly,
    [switch]$FrontendOnly
)

$ProjectDir  = $PSScriptRoot
$FrontendDir = Join-Path $ProjectDir "local"
$Python      = Join-Path $ProjectDir ".venv\Scripts\python.exe"
$Erragent    = Join-Path $ProjectDir ".venv\Scripts\erragent.exe"
$BackendPort  = 8000
$FrontendPort = 8080

$startBackend  = -not $FrontendOnly
$startFrontend = -not $BackendOnly

function Write-Step($text) { Write-Host "[*] $text" -ForegroundColor Yellow }
function Write-Ok($text)   { Write-Host "[+] $text" -ForegroundColor Green }
function Stop-Here($text)  { Write-Host "[X] $text" -ForegroundColor Red; Write-Host ""; exit 1 }

Clear-Host
Write-Host "=====================================================================" -ForegroundColor Cyan
Write-Host "                 SAAPP (SONIC ASSISTANT) LOCAL STARTUP               " -ForegroundColor Cyan
Write-Host "=====================================================================" -ForegroundColor Cyan
Write-Host ""

# --- 1. Ports: a server left over from an earlier run keeps serving its old code (new routes 404), so refuse to start next to one ---
$busy = @()
$wanted = @()
if ($startBackend)  { $wanted += $BackendPort }
if ($startFrontend) { $wanted += $FrontendPort }
foreach ($port in $wanted) {
    $listener = Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($listener) {
        $proc = Get-CimInstance Win32_Process -Filter "ProcessId=$($listener.OwningProcess)" -ErrorAction SilentlyContinue
        $busy += "port $port is already in use by PID $($listener.OwningProcess) (started $($proc.CreationDate))"
    }
}
if ($busy.Count -gt 0) {
    $busy | ForEach-Object { Write-Host "[!] $_" -ForegroundColor Yellow }
    Stop-Here "Close the window running it (Ctrl+C) and run this again, so the new code is what gets served."
}

# --- 2. Python environment ---
if ($startBackend) {
    if (-not (Test-Path $Python)) {
        Write-Step "No .venv found; creating it and installing requirements.txt..."
        Set-Location $ProjectDir
        python -m venv .venv
        if ($LASTEXITCODE -ne 0) { Stop-Here "Could not create the virtual environment. Is Python on your PATH?" }
        & $Python -m pip install --upgrade pip --quiet
        & $Python -m pip install -r requirements.txt
        if ($LASTEXITCODE -ne 0) { Stop-Here "Installing requirements failed." }
    }
    Write-Ok ".venv ready."
}

# --- 3. Frontend dependencies ---
if ($startFrontend) {
    if (-not (Get-Command npm -ErrorAction SilentlyContinue)) { Stop-Here "npm was not found. Install Node.js and try again." }
    if (-not (Test-Path (Join-Path $FrontendDir "node_modules"))) {
        Write-Step "First run: installing frontend packages (npm install)..."
        Push-Location $FrontendDir
        npm install
        $code = $LASTEXITCODE
        Pop-Location
        if ($code -ne 0) { Stop-Here "npm install failed." }
    }
    Write-Ok "Frontend packages ready."
}

# --- 4. Launch ---
if ($startBackend) {
    Write-Step "Starting the backend on port $BackendPort..."
    # Reload watches the whole project by default, which includes the virtual environments and node_modules; leaving them (and the data files the app
    # rewrites as it runs) out keeps the watcher quick and stops data writes from restarting the server.
    $excludes = @(
        "chat_history.json", "directory.json", "saved_conversations.json", "chroma_db", "index-db", "saapp_data", "temp", "eval_results",
        ".venv", "local", "local_agent", "local_function_app", ".git", "__pycache__"
    ) | ForEach-Object { "--reload-exclude '$_'" }
    $uvicorn = "`"$Python`" -m uvicorn app:app --host 127.0.0.1 --port $BackendPort --reload " + ($excludes -join " ")
    if (Test-Path $Erragent) {
        # erragent forwards crashes (see backend/utils for the log-redaction rules); the server runs the same without it.
        $uvicorn = "`"$Erragent`" dev --root . -- " + $uvicorn
    }
    $backendCommand = "`$env:PYTHONUNBUFFERED=1; `$Host.UI.RawUI.WindowTitle = 'SAAPP Backend'; cd '$ProjectDir'; $uvicorn"
    Start-Process powershell -ArgumentList "-NoExit", "-Command", $backendCommand
}

if ($startBackend -and $startFrontend) { Start-Sleep -Seconds 2 }   # let the backend bind its port first

if ($startFrontend) {
    Write-Step "Starting the frontend on port $FrontendPort..."
    $frontendCommand = "`$Host.UI.RawUI.WindowTitle = 'SAAPP Frontend (Vite)'; cd '$FrontendDir'; npm run dev -- --host 127.0.0.1 --port $FrontendPort"
    Start-Process powershell -ArgumentList "-NoExit", "-Command", $frontendCommand
}

Write-Host ""
Write-Host "=====================================================================" -ForegroundColor Green
Write-Host "  SAAPP is starting in separate windows." -ForegroundColor Green
if ($startBackend)  { Write-Host "  - Backend:  http://127.0.0.1:$BackendPort   (API docs: /docs)" -ForegroundColor Green }
if ($startFrontend) { Write-Host "  - Frontend: http://127.0.0.1:$FrontendPort" -ForegroundColor Green }
Write-Host "=====================================================================" -ForegroundColor Green
Write-Host ""
