import asyncio
import json
from pathlib import Path
from typing import Any, Optional
from uuid import uuid4
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from agents import build_agent
from agents.tools import (
    assemble_document,
    build_document_prompt,
    create_run_workspace,
    create_tema_workspace,
    save_uploaded_file,
    set_output_workspace,
    set_progress_callback,
)


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
    temas: list[str] = []
    parallelism: int = 3


@app.get("/health")
def health():
    return {"status": "ok"}


def build_prompt_with_files(prompt: str, files: list[FileInput]) -> str:
    prompt_text = build_document_prompt(prompt, [{"name": file.name, "content": file.content} for file in files])

    if not (prompt or "").strip() and files:
        # Keep user input minimal by asking only for the two critical unknowns.
        prompt_text = (
            f"{prompt_text}\n\n"
            "Si el usuario quiere construir un temario, antes de producir el documento final "
            "haz solo estas 2 preguntas de aclaracion si faltan datos criticos:\n"
            "1) Cual es el tipo de proceso selectivo exacto (cuerpo/escala/especialidad).\n"
            "2) Cual es la salida esperada primero (estructura de bloques o desarrollo completo).\n"
            "Si puedes inferirlas con alta confianza desde los archivos, indicalo y procede sin mas preguntas."
        )

    if not files:
        return prompt_text

    sections = [prompt_text, "\n\nArchivos adjuntos:"]
    for file_item in files:
        safe_name = Path(file_item.name).name
        saved_path = save_uploaded_file(safe_name, file_item.content)
        sections.append(f"- {file_item.name} -> {saved_path}")
        sections.append(file_item.content[:6000])
    return "\n\n".join(sections)


def _stringify_content(content: Any) -> str:
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        chunks: list[str] = []
        for item in content:
            if isinstance(item, str):
                chunks.append(item)
                continue
            if isinstance(item, dict):
                text_value = item.get("text")
                if isinstance(text_value, str):
                    chunks.append(text_value)
        return "\n".join(part for part in chunks if part)
    if isinstance(content, dict):
        text_value = content.get("text")
        if isinstance(text_value, str):
            return text_value
    return str(content)


def _extract_final_message(payload: Any) -> str:
    if payload is None:
        return ""
    if isinstance(payload, dict):
        messages = payload.get("messages")
        if isinstance(messages, list) and messages:
            last_message = messages[-1]
            if isinstance(last_message, dict):
                return _stringify_content(last_message.get("content"))
            message_content = getattr(last_message, "content", None)
            return _stringify_content(message_content)
        output = payload.get("output")
        if output is not None:
            nested = _extract_final_message(output)
            if nested:
                return nested

    message_content = getattr(payload, "content", None)
    if message_content is not None:
        return _stringify_content(message_content)
    return _stringify_content(payload)


def _build_tema_prompt(base_prompt: str, tema: str) -> str:
    return (
        f"{base_prompt}\n\n"
        f"Tema objetivo: {tema}\n"
        "Desarrolla exclusivamente este tema. No mezcles contenido de otros temas. "
        "Si necesitas referencias cruzadas, limitalas a una seccion breve de conexiones."
    )


async def _run_temas_parallel(
    base_prompt: str,
    temas: list[str],
    parallelism: int,
) -> dict[str, Any]:
    run_id = f"run-{uuid4().hex[:10]}"
    run_workspace = create_run_workspace(run_id)
    semaphore = asyncio.Semaphore(max(1, min(parallelism, 10)))
    results: list[dict[str, Any]] = []

    async def process_tema(index: int, tema: str) -> dict[str, Any]:
        tema_label = f"tema-{index + 1:02d}"
        tema_workspace = create_tema_workspace(run_id, tema_label)

        async with semaphore:
            def invoke_one() -> str:
                set_output_workspace(tema_workspace)
                try:
                    agent = get_agent()
                    prompt = _build_tema_prompt(base_prompt, tema)
                    result = agent.invoke({"messages": [{"role": "user", "content": prompt}]})
                    message = _extract_final_message(result)
                    markdown_path = Path(tema_workspace) / "tema-final.md"
                    markdown_path.write_text(message or "", encoding="utf-8")
                    return message
                finally:
                    set_output_workspace(None)

            message = await asyncio.to_thread(invoke_one)
            return {
                "tema": tema,
                "tema_label": tema_label,
                "workspace": tema_workspace,
                "message": message,
                "filename": f"{tema_label}/tema-final.md",
            }

    processed = await asyncio.gather(*(process_tema(i, tema) for i, tema in enumerate(temas)))
    results.extend(processed)

    def assemble_all() -> str:
        set_output_workspace(run_workspace)
        try:
            filenames = [item["filename"] for item in results]
            title = "Temario consolidado"
            return assemble_document(title=title, filenames=filenames, output_filename="temario-final.docx")
        finally:
            set_output_workspace(None)

    final_doc = await asyncio.to_thread(assemble_all)
    return {
        "run_id": run_id,
        "run_workspace": run_workspace,
        "final_doc": final_doc,
        "items": results,
    }


@app.post("/invoke")
def invoke(request: InvokeRequest):
    full_prompt = build_prompt_with_files(request.prompt, request.files)
    temas = [tema.strip() for tema in (request.temas or []) if tema and tema.strip()]
    if len(temas) > 1:
        batch_result = asyncio.run(_run_temas_parallel(full_prompt, temas, request.parallelism))
        return {
            "message": "Temas procesados en paralelo.",
            "run_id": batch_result["run_id"],
            "run_workspace": batch_result["run_workspace"],
            "final_doc": batch_result["final_doc"],
            "items": [{"tema": item["tema"], "workspace": item["workspace"]} for item in batch_result["items"]],
        }

    agent = get_agent()
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
            temas = [tema.strip() for tema in (payload.get("temas") or []) if isinstance(tema, str) and tema.strip()]
            parallelism = int(payload.get("parallelism") or 3)
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
                if len(temas) > 1:
                    await websocket.send_json({
                        "type": "stage",
                        "message": f"⚙️ Procesando {len(temas)} temas en paralelo (max {max(1, min(parallelism, 10))} simultaneos).",
                    })

                    batch_result = await _run_temas_parallel(full_prompt, temas, parallelism)
                    await websocket.send_json({
                        "type": "stage",
                        "message": f"📂 Workspace temporal: {batch_result['run_workspace']}",
                    })
                    await websocket.send_json({
                        "type": "stage",
                        "message": f"📄 Documento final: {batch_result['final_doc']}",
                    })

                    summary_lines = [
                        "Temas procesados en paralelo:",
                        *[f"- {item['tema']} ({item['workspace']})" for item in batch_result["items"]],
                    ]
                    await websocket.send_json({"type": "result", "message": "\n".join(summary_lines)})
                    await websocket.send_json({"type": "stage", "message": "✅ Parallel tema workflow completed."})
                    continue

                agent = get_agent()
                payload_input = {"messages": [{"role": "user", "content": full_prompt}]}

                subagent_run_names: dict[str, str] = {}
                final_message = ""

                try:
                    async for event in agent.astream_events(payload_input, version="v2"):
                        event_name = event.get("event") or ""
                        event_data = event.get("data") or {}
                        run_id = str(event.get("run_id") or "")
                        tool_name = event.get("name") or ""

                        if event_name == "on_tool_start":
                            tool_input = event_data.get("input") or {}
                            if tool_name == "task":
                                subagent_name = "subagente"
                                if isinstance(tool_input, dict):
                                    subagent_name = str(
                                        tool_input.get("subagent_type")
                                        or tool_input.get("name")
                                        or subagent_name
                                    )
                                subagent_run_names[run_id] = subagent_name
                                await websocket.send_json({
                                    "type": "subagent_started",
                                    "subagent": subagent_name,
                                    "message": f"Subagente iniciado: {subagent_name}",
                                })
                            continue

                        if event_name == "on_tool_end":
                            tool_output = _stringify_content(event_data.get("output"))
                            preview = tool_output[:280] if tool_output else ""

                            if tool_name == "task":
                                subagent_name = subagent_run_names.pop(run_id, "subagente")
                                await websocket.send_json({
                                    "type": "subagent_completed",
                                    "subagent": subagent_name,
                                    "message": f"Subagente completado: {subagent_name}",
                                })
                            else:
                                if preview:
                                    await websocket.send_json({
                                        "type": "tool_output_delta",
                                        "tool": tool_name,
                                        "message": preview,
                                    })
                            continue

                        if event_name == "on_tool_error":
                            if tool_name == "task":
                                subagent_name = subagent_run_names.pop(run_id, "subagente")
                                await websocket.send_json({
                                    "type": "subagent_failed",
                                    "subagent": subagent_name,
                                    "message": f"Subagente con error: {subagent_name}",
                                })
                            else:
                                await websocket.send_json({
                                    "type": "tool_failed",
                                    "tool": tool_name,
                                    "message": f"Herramienta con error: {tool_name}",
                                })
                            continue

                        if event_name in {"on_chain_end", "on_graph_end"}:
                            maybe_output = event_data.get("output")
                            candidate = _extract_final_message(maybe_output)
                            if candidate:
                                final_message = candidate

                except Exception:
                    # Fallback to non-streaming execution if event streaming is unavailable.
                    result = await asyncio.to_thread(agent.invoke, payload_input)
                    final_message = _extract_final_message(result)

                message = final_message or "Proceso completado sin mensaje final estructurado."

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

