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
## Requirements

Local development uses a PowerShell script (**Windows**) to start the app. You need these installed first:
1. **Python 3.11 or newer**, on your `PATH`.
2. **Node.js** (includes `npm`).
3. An internet connection: the app calls the Google Gemini API and a hosted MongoDB, so it needs your API keys and connection settings to be configured.

---

## Getting Started & Execution

> *Note: If you encounter a script execution policy restriction in your terminal, run `Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass` before executing.*

### Starting the App
One script starts everything for local development, first run or not:
```powershell
./start_saapp.ps1
```

What it does:

- Refuses to start if port 8000 or 8080 is already in use, and says which process holds it, so a leftover server can't keep serving old code.
- Creates the Python virtual environment (`.venv`) and installs `requirements.txt` if the environment is missing.
- Runs `npm install` in `local/` if `local/node_modules` is missing.
- Starts the FastAPI backend (http://127.0.0.1:8000, with auto-reload) and the Vite frontend (http://127.0.0.1:8080), each in its own window.

Use `-BackendOnly` or `-FrontendOnly` to start just one of them.

---

## Automated Data Ingestion Pipelines

The project separates initial bootstrapping from runtime data changes:

### 1. Database bootstrapper (`ingest.py`)
Run it by hand to seed the initial documents (it is not part of the start script):
```powershell
.\.venv\Scripts\python.exe ingest.py
```
It purges stale database remnants to prevent vector index collisions, scans the root drop-zones for default data, splits content into 600-token overlapping chunks, and indexes them into MongoDB.

### 2. Adding new material
Use the built-in Self-Service page in the web application to upload, ingest, and manage material on the fly.