#!/bin/bash

# ARK Knowledge Gateway Local Runtime Launcher (macOS)
# Exit immediately if any command fails
set -e

# Clear the terminal window
clear

echo "====================================================================="
echo "    SONIC ASSISTANT APPLICATION INITIALIZATION TERMINAL (macOS) "
echo "====================================================================="
echo ""

# Get the directory of the current script
PROJECT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
FRONTEND_DIR="$PROJECT_DIR/local"

# --- VIRTUAL ENVIRONMENT AUTOMATION LAYER ---
VENV_PATH="$PROJECT_DIR/.venv"

if [ ! -d "$VENV_PATH" ]; then
    echo "[!] Target environment container (.venv) not found."
    echo "[*] Provisioning isolated runtime engine..."
    
    python3 -m venv .venv
    ./.venv/bin/python -m pip install --upgrade pip --quiet
    
    echo "[*] Syncing backend dependencies from requirements.txt..."
    ./.venv/bin/pip install -r requirements.txt
    
    echo "[+] Secure boundary packages initialized successfully!"
    echo ""
else
    echo "[+] Verified localized runtime container consistency (.venv active)."
fi

# --- FRONTEND DEPENDENCY CHECK ---
if [ ! -d "$FRONTEND_DIR/node_modules" ]; then
    echo "[!] Frontend packages (node_modules) not found."
    echo "[*] Pulling UI dependencies (npm install)..."
    cd "$FRONTEND_DIR"
    npm install
    cd "$PROJECT_DIR"
fi

# --- APPLICATION ORCHESTRATION LAYER ---
echo "[*] Initializing engine launch sequence..."

# We use macOS AppleScript to open two separate, clean Terminal windows for our servers
osascript <<EOF
tell application "Terminal"
    # Window 1: FastAPI Backend (Port 8000)
    do script "cd '$PROJECT_DIR' && source .venv/bin/activate && uvicorn app:app --reload"
    
    # Window 2: Vite Frontend (Port 8080)
    do script "cd '$FRONTEND_DIR' && npm run dev -- --host 127.0.0.1 --port 8080"
end tell
EOF

echo ""
echo "====================================================================="
echo "   SUCCESS: Both engine cores active in separate system instances!   "
echo "   - Backend: http://127.0.0.1:8000"
echo "   - Frontend: http://127.0.0.1:8080"
echo "====================================================================="
echo ""