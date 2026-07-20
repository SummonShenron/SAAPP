# Sonic Assistant (SAAPP)

## Why?
I created the Sonic Assistant (SAAPP) to prove I can independently architect, build, and deploy a modern, cloud-native AI system leveraging both RAG and Agentic workflows. It includes and demonstrates:
* Full-stack cloud engineering (React, Vite, FastAPI)
* Cloud-ready architecture (Deployed on Vercel & Render using MongoDB and Google Gemini API)
* Agentic workflows with LangGraph
* Retrieval grading, query rewriting, and multi-step reasoning
* Multi-tenant secure data isolation
* Asynchronous token streaming
* Hybrid retrieval and vector indexing powered by MongoDB

---

## Architecture & Tech Stack
* **Frontend:** React, Vite, Tailwind/Custom CSS, deployed globally on **Vercel** (`sonicassistant.com`).
* **Backend:** FastAPI service deployed on **Render** (`https://saapp.onrender.com`).
* **AI Engine:** Google **Gemini API** for high-performance generation and reasoning.
* **Database & Vector Store:** **MongoDB** for secure multi-tenant data storage and retrieval.
* **Authentication:** **Clerk** integrated with role-based access control (RBAC).

---

## Summary and Explanation of Workflow

### Security & Identity Controls
The application implements a multi-tenant security architecture designed to isolate data between different "Affiliates / Knowledge Bases" (e.g., Sonic Lore vs. Dragon Ball Data).
* **Identity Context:** Authenticated via Clerk, mapping user identities and session states to authorized organization scopes.
* **Role-Based Access Control (RBAC):** The backend maps users to specific authorized "affiliates" via `/api/affiliates` and checks permissions via `/api/user/groups`. 
* **Data Isolation:** When performing a search or ingestion, the system enforces a strict scope query against MongoDB. Only documents tagged with an affiliate allowed to the current user are retrieved, preventing cross-tenant data leakage at the query level.
* **Multi-KB Access:** Users with access to multiple knowledge bases can query them simultaneously or scope their responses down to a specific target KB.

### LangGraph & Agent Workflows
The model coordinates its reasoning through a compiled LangGraph workflow that manages conversation flow by routing queries and responses through specialized nodes and edges:
* **GraphState:** Tracks conversation history, authenticated user identity, permitted affiliate scope, and retrieved documents across every step.
* **Workflow Execution:**
  * **Routing:** `route_user_query` determines whether to trigger the `retrieve_node` agent (for document-backed questions) or the `conversational_node` agent (for general chat).
  * **Retrieval & Grading:** `retrieve_node` fetches documents based on user scope from MongoDB. The `grading_node` evaluates relevance, returning a binary yes/no response.
  * **Rewrite & Generate:** If retrieval quality is low, `rewrite_query_node` rephrases the question before trying again. Finally, `generate_node` synthesizes the final response using Gemini.

### Search Strategies
The system uses a unified search service designed to adapt its strategy based on query intent:
* **Intelligent Routing:** Analyzes query text to pick the optimal retrieval strategy:
  * **Lexical:** Triggered by temporal or keyword markers (e.g., "latest", "date", "timeline").
  * **Hybrid:** Triggered by comparative keywords (e.g., "compare", "analyze", "connection").
  * **Vector:** Default fallback for semantic queries.
* **Graph-Enhanced Context:** Integrates with a NetworkX knowledge graph to surface multi-hop entity relationships and enrich retrieved context.

---
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

### First-Time Environment Setup
If you are running the project for the first time, execute the bootstrapper script:
```powershell
./start.ps1
What this script automates for you:

Checks for Python 3.11 and Node.js, prompting for consent to install via winget if missing.

Provisions an isolated Python virtual environment (.venv) and upgrades pip.

Installs all backend dependencies from requirements.txt.

Compiles and installs frontend UI dependencies (npm install).

Configures MongoDB connection hooks and cleans stale collections.

Triggers the initialization chunking pipeline (ingest.py) to seed initial documents.

Launches the FastAPI server and Vite frontend dev server in separate background instances.

2. Subsequent Faster Bootups
Once the initial configuration is complete, you can bypass dependency checks to instantly launch the local web app by running:

PowerShell
./local_start.ps1
Optional: Enable Admin Features with PAAPP
SAAPP can run entirely on its own for chat and RAG workloads. However, to unlock the full admin feature set (including calendar tools, sticky notes, time tracking, and multi-agent workflows), you can run the PAAPP headless agent locally:

Bash
git clone [https://github.com/SummonShenron/PAAPP](https://github.com/SummonShenron/PAAPP)
cd PAAPP
pip install -r requirements.txt
uvicorn local_agent.headless_app:app --reload --port 8000
SAAPP automatically detects PAAPP via http://127.0.0.1:8000/api/headless-chat.

Automated Data Ingestion Pipelines
The project implements a two-tiered data pipeline architecture to separate initial system bootstrapping from runtime data adjustments:

1. Database Bootstrapper (ingest.py)
When the application is launched for the first time via start.ps1, the system automatically invokes the bootstrapper script.

Purpose: Purges stale database remnants to prevent vector index collisions, scans the root drop-zones for default data, splits content into clean 600-token overlapping semantic structures, and indexes them securely into MongoDB.

2. Adding New Material
If you would like to ingest new material locally:

Place a new .pdf inside either index-db\Affiliate_A or index-db\Affiliate_B (or create a custom affiliate folder).

Navigate to the function app directory (cd local-function-app) and run its start.ps1 script to process and push the new files into MongoDB.

Alternatively, you can use the built-in Self-Service page directly inside the web application UI to upload, ingest, and manage material on the fly.