import json
import os
from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from agents import build_agent

app = FastAPI(title="Deep Agents FastAPI", version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


def get_agent():
    model = os.getenv("MODEL")
    if not model:
        raise HTTPException(status_code=500, detail="MODEL environment variable is not set")
    return build_agent()


class InvokeRequest(BaseModel):
    prompt: str


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/invoke")
def invoke(request: InvokeRequest):
    agent = get_agent()
    result = agent.invoke(
        {"messages": [{"role": "user", "content": request.prompt}]}
    )
    message = result["messages"][-1].content
    return {"message": message}


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    try:
        while True:
            raw = await websocket.receive_text()
            try:
                payload = json.loads(raw)
            except json.JSONDecodeError:
                await websocket.send_json({"type": "error", "message": "Invalid JSON payload"})
                continue

            prompt = payload.get("prompt")
            if not prompt:
                await websocket.send_json({"type": "error", "message": "Missing 'prompt' field"})
                continue

            await websocket.send_json({"type": "status", "message": "started"})
            await websocket.send_json({"type": "status", "message": "running coordinator"})

            agent = get_agent()
            result = agent.invoke(
                {"messages": [{"role": "user", "content": prompt}]}
            )
            message = result["messages"][-1].content

            await websocket.send_json({"type": "result", "message": message})
            await websocket.send_json({"type": "status", "message": "finished"})
    except WebSocketDisconnect:
        return
