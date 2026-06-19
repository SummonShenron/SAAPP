#!/bin/bash

# ARK Knowledge Gateway macOS Bootstrap & Run Script
# Exit immediately if any command fails
set -e

# Clear the terminal window
clear

echo "====================================================================="
echo "    SONIC ASSISTANT APPLICATION INITIALIZATION TERMINAL (macOS)     "
echo "====================================================================="
echo ""

# Get the directory of the current script
PROJECT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
FRONTEND_DIR="$PROJECT_DIR/local"

# --- 1. PRE-REQUISITE AUTOMATION (HOMEBREW) ---

# Check for Homebrew (macOS Package Manager)
if ! command -v brew &> /dev/null; then
    echo "[!] Homebrew (brew) was not detected on this system."
    read -p "Would you like to automatically install Homebrew? (Y/N): " choice
    if [[ "$choice" =~ ^[Yy]$ ]]; then
        echo "[*] Installing Homebrew... You may be prompted for your Mac password."
        /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
        # Add brew to path for the current session
        eval "$(/opt/homebrew/bin/brew shellenv)" || eval "$(/usr/local/bin/brew shellenv)"
    else
        echo "[X] Exiting: Homebrew is highly recommended to auto-install dependencies."
        exit 1
    fi
fi

# Check Python 3
if ! command -v python3 &> /dev/null; then
    echo "[!] Python 3 was not detected on this system."
    read -p "Would you like to install Python 3 via Homebrew? (Y/N): " choice
    if [[ "$choice" =~ ^[Yy]$ ]]; then
        echo "[*] Installing Python 3..."
        brew install python
    else
        echo "[X] Exiting: Python is required to run the backend."
        exit 1
    fi
fi

# Check Node.js
if ! command -v node &> /dev/null; then
    echo "[!] Node.js (npm) was not detected on this system."
    read -p "Would you like to install Node.js via Homebrew? (Y/N): " choice
    if [[ "$choice" =~ ^[Yy]$ ]]; then
        echo "[*] Installing Node.js..."
        brew install node
    else
        echo "[X] Exiting: Node.js is required to run the frontend UI."
        exit 1
    fi
fi

# Check Ollama
if ! command -v ollama &> /dev/null; then
    echo "[!] Ollama AI Engine was not detected on this system."
    read -p "Would you like to install Ollama via Homebrew? (Y/N): " choice
    if [[ "$choice" =~ ^[Yy]$ ]]; then
        echo "[*] Installing Ollama..."
        brew install ollama
        echo "[+] Starting Ollama background service..."
        brew services start ollama
        sleep 3
    else
        echo "[X] Exiting: Ollama is required for local AI execution."
        exit 1
    fi
fi

# --- 2. PYTHON VIRTUAL ENVIRONMENT (macOS Path Adjustments) ---
VENV_PATH="$PROJECT_DIR/.venv"
if [ ! -d "$VENV_PATH" ]; then
    echo "[*] Provisioning isolated Python runtime environment..."
    python3 -m venv .venv
    ./.venv/bin/python -m pip install --upgrade pip --quiet
    echo "[*] Syncing backend dependencies from requirements.txt..."
    ./.venv/bin/pip install -r requirements.txt
fi

# --- 3. FRONTEND DEPENDENCY INSTALLATION ---
if [ ! -d "$FRONTEND_DIR/node_modules" ]; then
    echo "[*] First-time setup: Installing frontend UI dependencies (npm install)..."
    cd "$FRONTEND_DIR"
    npm install
    cd "$PROJECT_DIR"
fi

# --- 4. MODEL SYNC & VECTOR INGESTION ---
echo "[*] Verifying local LLM allocation (Llama3)..."
ollama pull llama3

# Ingest data using the local virtual environment's Python binary
if [ -f "$PROJECT_DIR/ingest.py" ]; then
    echo "[*] Triggering local repository vector ingestion engine..."
    ./.venv/bin/python ingest.py
fi

# --- 5. ORCHESTRATION LAYER (LAUNCH MULTI-TERMINAL ON macOS) ---
echo "[*] Initializing engine launch sequence..."

# We use macOS AppleScript to open two separate, clean Terminal windows for our servers
osascript <<EOF
tell application "Terminal"
    # Window 1: FastAPI Backend
    do script "cd '$PROJECT_DIR' && source .venv/bin/activate && uvicorn app:app --reload"
    
    # Window 2: Vite Frontend
    do script "cd '$FRONTEND_DIR' && npm run dev -- --host 127.0.0.1 --port 8080"
end tell
EOF

echo ""
echo "====================================================================="
echo "   SUCCESS: Both engine cores initialized in separate windows!       "
echo "   - Backend active on: http://127.0.0.1:8000"
echo "   - Frontend active on: http://127.0.0.1:8080"
echo "====================================================================="
echo ""