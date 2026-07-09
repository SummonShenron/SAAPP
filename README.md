## Why? ##

I created the Sonic Assistant (SAAPP) to prove I can independently architect, build, and deploy a modern AI system leveraging both RAG and Agentic workflows. It includes and demonstrates:

full-stack engineering
cloud-ready architecture
agentic workflows
retrieval grading
query rewriting
multi-step reasoning
secure data isolation
async streaming
vector + lexical + hybrid retrieval

## Summary and Explanation of Workflow ##

1. Security & Identity Controls
The application implements a multi-tenant security architecture designed to isolate data between different "Affiliates/Knowledge Bases" (e.g., Sonic Lore vs. Dragon Ball Data).

Simulated Identity Context: the landing page provides users to select a "Persona" (e.g., jack_admin or sonic_user), which is stored in localStorage as x-user-id -- in an azure environment, this would get passed to the backend and a GraphAPI lookup would ensue to obtain the users entra id group memberships via a directory.read.all configured client permission in the app registration

Role-Based Access Control (RBAC): The backend simulates an Entra ID environment. It maps users to specific authorized "affiliates" via /api/affiliates and checks for "Ingester" permissions via /api/user/groups. This ensures data isolation on the user access level

Data Isolation: When performing a search or ingestion, the system enforces a strict scope. Only documents tagged with an affiliate allowed to the current user are retrieved, ensuring cross-tenant data leakage is prevented at the vector database query level (Chroma DB filters). This means the model cannot hallucinate data from other knowledge bases because it can only respond with chunks tagged with the scoped affiliate(s)

Multi-KB Access: While data is isolated between KB's, users with access to multiple KB's can still query them all at once or filter the response to a specific KB.

2. LangGraph & Agent Workflows
This is how the model "thinks" via a compiled LangGraph workflow that manages the conversation flow by routing queries and responses through several nodes and edges such as a retrieval node, a grading node, and a generation node.

GraphState: Every step of the process shares a GraphState object, which tracks conversation history, the authenticated user identity, the permitted affiliate scope, and retrieved documents.

Workflow Execution:

Routing: The route_user_query function decides whether to trigger the retrieve_node agent (for informational/document-based questions) or the conversational_node agent (for greetings or general chat).

Retrieval & Grading: The retrieve_node agent fetches documents based on the user's scope. These are then passed to the grading_node agent which evaluates relevance and returns a yes/no response.

Rewrite & Generate: If the retrieval quality is poor and the grading_node agent responds with "no", the rewrite_query_node agent is triggered to rephrase the question before attempting retrieval again. Finally, the generate_node synthesizes the response. If the models first response returns a non-contextual answer like "I can't find the answer", it will rewrite the query one more time to try to find an actual answer.

3. Search Strategies
The system uses a unified search service designed to adapt its strategy based on the type of query.

Intelligent Routing: The _detect_routing_strategy function analyzes the query text for specific markers:

Lexical: Triggered by temporal or keyword markers (e.g., "latest", "date", "timeline").

Hybrid: Triggered by complex or analytical keywords (e.g., "compare", "analyze", "connection") to handle multi-clause or comparative questions.

Vector: The default fallback for general semantic queries.

Graph-Enhanced Context: In addition to standard vector search, the system integrates with a knowledge_graph (using NetworkX) to provide multi-hop content relationships, enriching the retrieved context with explicit entity connections

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

Optional: Enable Admin Features with PAAPP
SAAPP can run entirely on its own.

However, to unlock the full admin feature set, you must also run the PAAPP
headless agent locally.

Admin features include:

Calendar tools

Sticky notes

Time tracking

Multi‑agent workflows

Clone PAAPP (optional but recommended)

git clone https://github.com/SummonShenron/PAAPP
cd PAAPP
pip install -r requirements.txt
uvicorn local_agent.headless_app:app --reload --port 8000

SAAPP will automatically detect PAAPP via:
http://127.0.0.1:8000/api/headless-chat


## Automated Data Ingestion Pipelines

The project implements a two-tiered data pipeline architecture to separate initial system bootstrapping from runtime data adjustments:

### 1. Database Bootstrapper (`ingest.py`)
When the application is launched for the first time via `start.ps1`, the system automatically invokes the bootstrapper script.
- **Purpose:** Purges stale database remnants to prevent vector index collisions, scans the root drop-zones for default data, splits content into clean `600-token` overlapping semantic structures, and locks down the initial vector blocks inside `chroma_db/`.

if you would like to ingest new material, you must place a new .pdf inside either the index-db\Affiliate_A or Affilaite_B (or create a new one) folders, then navigate to the function app directory (cd local-function-app) and run start.ps1 to start it. it will pick up and ingest the new files and exit upon completion. Additionally, you can also use the self-service page in the app itself to ingest and remove material.