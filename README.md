# NEXUS — Autonomous AI Software Engineering Platform

> Give it a GitHub repo and a task. It plans, codes, tests, reviews, and opens a Pull Request — automatically.

**Live Demo:** [43.205.119.173/dashboard](http://43.205.119.173/dashboard)

--- 

## What It Does

You type: *"Add JWT authentication to this Flask API"*

Then 5 specialized AI agents take over:

| Agent | Job |
|-------|-----|
| 🧠 Architect | Reads the codebase using RAG, creates a file-by-file plan |
| 💻 Coder | Reads existing files, writes complete implementations |
| 🧪 Tester | Writes and runs unit tests, self-corrects on failures |
| 🔍 Reviewer | Checks for bugs, security issues, missing error handling |
| 🔀 Git Agent | Creates branch, commits, opens Pull Request on GitHub |

Every agent message streams to your browser in real time.

---

## Architecture

```
User submits task (React UI)
         ↓
FastAPI → PostgreSQL (task created)
         ↓
Celery worker picks up task
         ↓
┌─────────────────────────────────┐
│  1. Clone GitHub repo           │
│  2. AST-parse all files         │
│  3. Embed → ChromaDB (RAG)      │
│  4. Architect reads codebase    │
│  5. Coder writes + runs code    │
│  6. Tester writes + runs tests  │
│  7. Reviewer checks quality     │
│  8. Git Agent creates PR        │
└─────────────────────────────────┘
         ↓
Redis pub/sub → FastAPI WebSocket → Browser (real-time)
         ↓
GitHub Pull Request created ✅
```

---

## Tech Stack

| Layer | Technology |
|-------|-----------|
| **AI Agents** | AutoGen (multi-agent, GroupChat, tool-use) |
| **RAG** | ChromaDB, sentence-transformers (all-MiniLM-L6-v2), Python AST |
| **Backend** | FastAPI, async SQLAlchemy, PostgreSQL |
| **Task Queue** | Celery + Redis |
| **Real-time** | Redis pub/sub → FastAPI WebSocket |
| **Auth** | JWT (access + refresh tokens) |
| **GitHub** | GitPython, PyGithub |
| **Frontend** | React, Vite |
| **DevOps** | Docker Compose (5 services) |

---

## Key Engineering Decisions

### AST-Based Code Chunking (not naive text splitting)
Most RAG systems split code every 500 characters — cutting functions in half. NEXUS uses Python's `ast` module to extract complete functions and classes as semantic units. The embedding always represents a complete, meaningful code block.

```python
for node in tree.body:
    if isinstance(node, ast.FunctionDef):
        chunk = extract_complete_function(node, lines)
```

### Redis Pub/Sub Bridge (sync → async)
AutoGen runs synchronously inside Celery. FastAPI WebSocket is async. These are separate processes with no shared memory. Redis pub/sub bridges them:

```
Celery worker → redis.publish("task:abc:events", message)
                     ↓ instantly
FastAPI → pubsub.listen() → websocket.send_json(message) → Browser
```

Result: Agent messages appear in the browser under 100ms, zero polling.

### Per-Stage Executor Isolation
A critical AutoGen bug: reusing the same `UserProxyAgent` across multiple stages causes tool registrations to accumulate. The same function gets registered 3 times and executes 10+ times per call. Fix: create a fresh executor for each agent stage.

```python
def _make_executor(self, termination_token: str):
    return autogen.UserProxyAgent(
        name="executor",
        is_termination_msg=lambda m: termination_token in m.get("content", ""),
        ...
    )
```

---

## Project Structure

```
nexus/
├── docker-compose.yml          # 5 services: API, worker, postgres, redis, chroma
├── Dockerfile
├── requirements.txt
│
├── app/
│   ├── main.py                 # FastAPI entry point
│   ├── config.py               # Settings from .env
│   │
│   ├── auth/                   # JWT auth (access + refresh tokens)
│   │   ├── router.py
│   │   ├── models.py
│   │   ├── service.py
│   │   └── dependencies.py
│   │
│   ├── tasks/                  # Task CRUD + Celery worker
│   │   ├── router.py           # REST endpoints + WebSocket
│   │   ├── models.py           # Task, TaskLog SQLAlchemy models
│   │   └── worker.py           # Celery task: full pipeline
│   │
│   ├── agents/
│   │   └── orchestrator.py     # 5-agent pipeline coordinator
│   │
│   ├── rag/
│   │   ├── ast_parser.py       # Python AST → semantic chunks
│   │   ├── indexer.py          # Embed + store in ChromaDB
│   │   └── retriever.py        # Semantic search
│   │
│   ├── tools/
│   │   ├── code_tools.py       # File I/O, shell commands
│   │   ├── github_tools.py     # Clone, branch, commit, PR
│   │   └── search_tools.py     # Web search, PyPI lookup
│   │
│   ├── database/
│   │   └── connection.py       # Async SQLAlchemy engine
│   │
│   └── streaming/
│       └── websocket.py        # Redis pub/sub + WebSocket manager
│
├── frontend/                   # React + Vite
    └── nexusfrontend
        ## Frontend

        Frontend is maintained in a separate repository:

        👉 https://github.com/jassisamuran/nexusfrontend

        Built using React + Vite and connects to FastAPI backend.


```

---

## Running Locally

### Prerequisites
- Docker + Docker Compose
- OpenAI API key
- GitHub Personal Access Token (for PR creation)

### 1. Clone the repo
```bash
git clone https://github.com/jassisamuran/nexus
cd nexus
```

### 2. Create .env file
```env
OPENAI_API_KEY=sk-your-key-here
GITHUB_TOKEN=ghp_your-token-here
SECRET_KEY=your-32-char-secret-key
JWT_SECRET=your-jwt-secret-key
DEBUG=false
REPOS_DIR=/app/repos
```

### 3. Start all services
```bash
docker-compose up --build
```

This starts:
- **PostgreSQL** on port 5432
- **Redis** on port 6379
- **ChromaDB** on port 8001
- **FastAPI API** on port 8000
- **Celery Worker** (background agent runner)

### 4. Open the app
```
check https://github.com/jassisamuran/nexusfrontend
```

Register an account, paste a GitHub repo URL, describe the task, and watch the agents work.

---

## Running Without Docker (Development)

```bash
# Install dependencies
pip install -r requirements.txt

# Start external services
docker-compose up postgres redis chromadb -d

# Start FastAPI
python run.py

# Start Celery worker (separate terminal)
celery -A app.tasks.worker.celery_app worker --loglevel=info

# Start React frontend (separate terminal)
# Clone frontend repo
git clone https://github.com/jassisamuran/nexusfrontend
cd nexusfrontend
npm install
npm run dev
```

Frontend runs on `http://localhost:5173` with Vite proxy to FastAPI.

---

## Running Tests

```bash
pip install pytest pytest-asyncio httpx
pytest tests/ -v
```

---

## Example Task Results

| Task | Repo Type | Files Created | Tests Passing | Time |
|------|-----------|---------------|---------------|------|
| Add word count endpoint | Express.js | 6 | 4/4 | 3 min |
| Add pagination to GET /tasks | Node.js REST API | 2 modified | 6/6 | 4 min |
| Add input validation | Flask API | 3 | 8/8 | 5 min |

---

## What I Learned Building This

- **AST parsing beats text splitting** for code RAG — complete semantic units produce dramatically better search results
- **Never reuse AutoGen executors across stages** — tool registrations accumulate and cause repeated executions
- **Redis pub/sub is the right bridge** between synchronous Celery workers and async FastAPI WebSocket handlers
- **LLMs self-correct surprisingly well** — the coder agent debugged its own failing tests without human input
- **System messages are 80% of agent quality** — a focused, specific system message outperforms a complex architecture with vague prompts

---

## Author

**Jaspreet Singh** — AI/Backend Engineer

- GitHub: [github.com/jassisamuran](https://github.com/jassisamuran)
- LinkedIn: [linkedin.com/in/jaspreet-singh](https://www.linkedin.com/in/jaspreet-singh-7315ba220/)
- Email: samuran3132@gmail.com

---

## License

MIT
