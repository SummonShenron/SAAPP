## Requirements & Automation

The application uses a setup script designed for **Windows (PowerShell)**. The script leverages Windows Package Manager (`winget`) to check for and interactively install system-level dependencies if they are missing upon user consent.

To ensure the automated setup completes successfully:
1. Run your terminal as an **Administrator** or in Visual Studio Code (required for `winget` installations).
2. Ensure you have an active internet connection to download packages and the **Llama 3** model.
3. if in VSC, create a virtual environment for the install(s)

---

## Getting Started & Execution

PowerShell scripts are provided in the root directory to automate cross-platform execution and environment builds.

> *Note: If you encounter a script execution policy restriction in your terminal, run `Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass` before executing.*

### 1. First-Time Environment Setup
If you are running the project for the first time, simply execute the bootstrapper script:
./start.ps1

What this script automates for you:

Looks for Python 3.11, Node.js, and Ollama and prompts for consent to install via winget if missing.

Provisions an isolated Python virtual environment (.venv) and upgrades pip.

Installs all backend dependencies from requirements.txt.

Compiles and installs frontend UI dependencies (npm install).

Wakes up the Ollama background engine and pulls the Llama 3 model file.

Purges stale indexes and triggers the initialization chunking pipeline (ingest.py).

Launches the FastAPI server and Vite frontend dev server in separate background instances.

2. Subsequent Faster Bootups
Once the initial configuration is complete, you can bypass dependency and model installation checks to instantly launch the web app by running:
./local_start.ps1

## Automated Data Ingestion Pipelines

The project implements a two-tiered data pipeline architecture to separate initial system bootstrapping from runtime data adjustments:

### 1. Database Bootstrapper (`ingest.py`)
When the application is launched for the first time via `start.ps1`, the system automatically invokes the bootstrapper script.
- **Purpose:** Purges stale database remnants to prevent vector index collisions, scans the root drop-zones for default data, splits content into clean `600-token` overlapping semantic structures, and locks down the initial vector blocks inside `chroma_db/`.

if you would like to ingest new material, you must place a new .pdf inside either the index-db\Affiliate_A or Affilaite_B (or create a new one) folders, then navigate to the function app directory (cd local-function-app) and run start.ps1 to start it. it will pick up and ingest the new files and exit upon completion. Additionally, you can also use the self-service page in the app itself to ingest and remove material.