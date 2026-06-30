# Deep Agents FastAPI WebSocket Scaffold

This project exposes a LangChain Deep Agents coordinator through FastAPI HTTP and WebSocket endpoints.

## Endpoints

- `GET /health`
- `POST /invoke`
- `WebSocket /ws`

## Run locally

```bash
cp .env.example .env
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn main:app --reload
```

## Run the React UI separately

```bash
cd frontend
npm install
npm run dev
```

The Vite app runs on http://localhost:5173 and expects the FastAPI backend on http://localhost:8000.

## Build Docker image

```bash
docker build -t deepagents-fastapi .
```

## Run container

```bash
docker run --rm -p 8000:8000 --env-file .env deepagents-fastapi
```

## Test WebSocket

Send JSON like:

```json
{"prompt": "Create a technical document describing the multi-agent architecture and deployment plan."}
```
