# Correspa AI

Autonomous customer-support email assistant for Gmail.

Correspa watches your inbox, classifies each message, drafts a reply with RAG when needed, proofreads it, and saves the result as a **Gmail draft** — you stay in control of what gets sent.

## Features

- Gmail inbox monitoring for unanswered threads
- Email categories: product enquiry, complaint, feedback, unrelated
- RAG answers grounded in your own docs under `data/`
- Writer + proofreader loop before drafting
- Local runner and FastAPI / LangServe API

## Stack

| Layer | Tech |
| --- | --- |
| Orchestration | LangChain, LangGraph |
| LLMs | Groq, Google Gemini |
| Retrieval | Chroma + Gemini embeddings |
| Email | Gmail API |
| API | FastAPI, LangServe, Uvicorn |

## Project layout

```
correspa-ai/
  main.py
  deploy_api.py
  create_index.py
  authorize_gmail.py
  dashboard_api.py
  data/
    agency.txt
  db/
  src/
    agents.py
    graph.py
    nodes.py
    prompts.py
    state.py
    structure_outputs.py
    tools/
      GmailTools.py
```

- `main.py` — local workflow loop
- `deploy_api.py` — FastAPI + LangServe
- `create_index.py` — build Chroma index from `data/`
- `authorize_gmail.py` — one-time Gmail OAuth
- `dashboard_api.py` — dashboard/status routes
- `data/agency.txt` — knowledge base source
- `db/` — Chroma store (local, gitignored)

## Setup

### Requirements

- Python 3.10+
- Groq API key
- Google Gemini API key
- Gmail OAuth desktop credentials (`credentials.json`)

### Install

```
cd correspa-ai
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
```

### Environment

Create a `.env` file in this folder:

```
MY_EMAIL=you@gmail.com
GROQ_API_KEY=your_groq_key
GOOGLE_API_KEY=your_gemini_key
```

### Gmail OAuth

1. Enable Gmail API in Google Cloud
2. Create an OAuth **Desktop** client
3. Download JSON as `credentials.json` here
4. Add your Gmail as a test user on the OAuth consent screen
5. Run:

```
python authorize_gmail.py
```

This creates `token.json` (do not commit it).

### Knowledge index

```
python create_index.py
```

Replace `data/agency.txt` with your own docs first if you want custom answers.

## Run

### Local workflow

```
python main.py
```

### API server (port 8000)

```
python deploy_api.py
```

- Docs: http://localhost:8000/docs

## Notes

- Only recent unanswered threads are processed (last ~8 hours)
- Threads that already have a draft are skipped
- Replies are saved as drafts — nothing is auto-sent
- Keep `.env`, `credentials.json`, `token.json`, `venv/`, and `db/` out of git

## Author

**Tushar Jawale**

## License

MIT
