import asyncio
import json
from pathlib import Path
from typing import Optional
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from agents import build_agent
from agents.tools import build_document_prompt, save_uploaded_file, set_progress_callback


app = FastAPI(title="Deep Agents FastAPI", version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


def get_agent():
    return build_agent()


class FileInput(BaseModel):
    name: str
    content: str
    mime_type: Optional[str] = None


class InvokeRequest(BaseModel):
    prompt: str
    files: list[FileInput] = []


@app.get("/health")
def health():
    return {"status": "ok"}


def build_prompt_with_files(prompt: str, files: list[FileInput]) -> str:
    prompt_text = build_document_prompt(prompt, [{"name": file.name, "content": file.content} for file in files])
    if not files:
        return prompt_text

    sections = [prompt_text, "\n\nArchivos adjuntos:"]
    for file_item in files:
        safe_name = Path(file_item.name).name
        saved_path = save_uploaded_file(safe_name, file_item.content)
        sections.append(f"- {file_item.name} -> {saved_path}")
        sections.append(file_item.content[:6000])
    return "\n\n".join(sections)


@app.post("/invoke")
def invoke(request: InvokeRequest):
    agent = get_agent()
    full_prompt = build_prompt_with_files(request.prompt, request.files)
    result = agent.invoke(
        {"messages": [{"role": "user", "content": full_prompt}]}
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

            prompt = payload.get("prompt") or ""
            files = []
            for raw_file in payload.get("files", []) or []:
                try:
                    files.append(FileInput(**raw_file))
                except Exception:
                    await websocket.send_json({"type": "error", "message": "Invalid file payload"})
                    files = []
                    break

            full_prompt = build_prompt_with_files(prompt, files)

            await websocket.send_json({"type": "status", "message": "started"})
            await websocket.send_json({"type": "stage", "message": "🧠 Coordinator started planning the documentation workflow."})
            if files:
                await websocket.send_json({"type": "stage", "message": f"📎 Se analizarán {len(files)} archivo(s) adjunto(s)."})
            await websocket.send_json({"type": "stage", "message": "🤖 The agent is now thinking through the document structure."})

            async def send_progress(event_type: str, message: str) -> None:
                try:
                    await websocket.send_json({"type": event_type, "message": message})
                except Exception:
                    pass

            set_progress_callback(send_progress)
            try:
                def run_agent() -> dict:
                    agent = get_agent()
                    return agent.invoke({"messages": [{"role": "user", "content": full_prompt}]})

                result = await asyncio.to_thread(run_agent)
                message = result["messages"][-1].content

                await websocket.send_json({"type": "result", "message": message})
                await websocket.send_json({"type": "stage", "message": "✅ Documentation workflow completed."})
            except Exception as agent_error:
                import traceback
                error_trace = traceback.format_exc()
                print(f"Error en el agente:\n{error_trace}")
                await websocket.send_json({
                    "type": "error",
                    "message": f"Agent execution failed: {str(agent_error)}"
                })
            finally:
                set_progress_callback(None)

    except WebSocketDisconnect:
        return

