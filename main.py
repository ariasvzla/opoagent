import asyncio
import json
import os
from pathlib import Path
from typing import Any, Optional
from uuid import uuid4
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from agents import build_agent, get_cached_agent
from agents.pipeline import run_temas_parallel
from agents.tools import (
    create_run_workspace,
    save_uploaded_file,
    set_output_workspace,
    set_progress_callback,
)
from prompts.builders import build_document_prompt


app = FastAPI(title="Deep Agents FastAPI", version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


def get_agent():
    """Return the cached agent instance (built once per process)."""
    return get_cached_agent()


class FileInput(BaseModel):
    name: str
    content: str
    mime_type: Optional[str] = None


class InvokeRequest(BaseModel):
    prompt: str
    files: list[FileInput] = []
    temas: list[str] = []
    parallelism: int = 10


@app.get("/health")
def health():
    return {"status": "ok"}


def _files_for_pipeline(files: list[FileInput]) -> list[dict]:
    """Convert FileInput list to plain dicts for the pipeline analyser."""
    return [
        {"name": f.name, "content": f.content, "mime_type": f.mime_type or ""}
        for f in files
    ]


def build_prompt_with_files(prompt: str, files: list[FileInput]) -> str:
    prompt_text = build_document_prompt(prompt, [{"name": file.name, "content": file.content} for file in files])

    # Only inject clarifying questions for single-run chat mode (no files).
    # When files are present, the pipeline analyser handles discovery.
    if not (prompt or "").strip() and not files:
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


def _preview_text(value: str, limit: int = 160) -> str:
    clean = " ".join((value or "").strip().split())
    if len(clean) <= limit:
        return clean
    return clean[: limit - 3] + "..."


@app.post("/invoke")
def invoke(request: InvokeRequest):
    full_prompt = build_prompt_with_files(request.prompt, request.files)
    temas = [tema.strip() for tema in (request.temas or []) if tema and tema.strip()]

    if len(temas) > 1 or request.files:
        batch_result = asyncio.run(
            run_temas_parallel(
                full_prompt, temas, request.parallelism,
                files=_files_for_pipeline(request.files),
            )
        )
        stats = batch_result.get("stats", {})
        return {
            "message": f"Temario procesado: {stats.get('successful', 0)}/{stats.get('total', 0)} exitosos.",
            "run_id": batch_result["run_id"],
            "run_workspace": batch_result["run_workspace"],
            "final_doc": batch_result["final_doc"],
            "s3_url": batch_result.get("s3_url", ""),
            "items": [{"tema": item["tema"], "workspace": item["workspace"], "status": "failed" if item.get("error") else "ok"} for item in batch_result["items"]],
            "stats": stats,
        }

    run_id = f"run-{uuid4().hex[:10]}"
    run_workspace = create_run_workspace(run_id)
    set_output_workspace(run_workspace)
    try:
        agent = get_agent()
        result = agent.invoke(
            {"messages": [{"role": "user", "content": full_prompt}]},
            config={"recursion_limit": int(os.getenv("RECURSION_LIMIT", "50"))},
        )
        message = _extract_final_message(result)
        (Path(run_workspace) / "final-response.md").write_text(message or "", encoding="utf-8")
        return {"message": message, "run_id": run_id, "run_workspace": run_workspace}
    finally:
        set_output_workspace(None)


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
            parallelism = int(payload.get("parallelism") or 10)
            files = []
            for raw_file in payload.get("files", []) or []:
                try:
                    files.append(FileInput(**raw_file))
                except Exception:
                    await websocket.send_json({"type": "error", "message": "Invalid file payload"})
                    files = []
                    break

            full_prompt = build_prompt_with_files(prompt, files)
            single_run_workspace = ""
            is_parallel_request = len(temas) > 1 or bool(files)
            if not is_parallel_request:
                single_run_id = f"run-{uuid4().hex[:10]}"
                single_run_workspace = create_run_workspace(single_run_id)

            await websocket.send_json({"type": "status", "message": "started"})
            await websocket.send_json({"type": "stage", "message": "🧠 Coordinator started planning the documentation workflow."})
            if files:
                await websocket.send_json({"type": "stage", "message": f"📎 Se analizarán {len(files)} archivo(s) adjunto(s)."})
                await websocket.send_json({"type": "stage", "message": "🤖 The agent is now thinking through the document structure."})
            if single_run_workspace:
                await websocket.send_json({"type": "stage", "message": f"📂 Workspace temporal: {single_run_workspace}"})

            async def send_progress(event_type: str, message: str) -> None:
                try:
                    await websocket.send_json({"type": event_type, "message": message})
                except Exception:
                    pass

            set_progress_callback(send_progress)
            if single_run_workspace:
                set_output_workspace(single_run_workspace)
            try:
                if is_parallel_request:
                    max_conc = max(1, min(parallelism, int(os.getenv("MAX_PARALLELISM", "20"))))
                    if temas:
                        await websocket.send_json({
                            "type": "stage",
                            "message": (
                                f"⚙️ {len(temas)} tema(s) recibido(s). "
                                f"Procesando con max {max_conc} simultáneos."
                            ),
                        })
                    else:
                        await websocket.send_json({
                            "type": "stage",
                            "message": (
                                "🔍 Analizando el documento para detectar "
                                "y extraer los temas automáticamente..."
                            ),
                        })

                    async def notify_progress(progress_message: str) -> None:
                        await websocket.send_json({"type": "stage", "message": progress_message})

                    batch_result = await run_temas_parallel(
                        full_prompt,
                        temas,
                        parallelism,
                        files=_files_for_pipeline(files),
                        progress_hook=notify_progress,
                    )
                    await websocket.send_json({
                        "type": "stage",
                        "message": f"📂 Workspace temporal: {batch_result['run_workspace']}",
                    })
                    await websocket.send_json({
                        "type": "stage",
                        "message": f"📄 Documento final: {batch_result['final_doc']}",
                    })
                    s3_url = batch_result.get("s3_url", "")
                    if s3_url:
                        await websocket.send_json({
                            "type": "stage",
                            "message": f"☁️ Publicado en S3: {s3_url}",
                        })

                    stats = batch_result.get("stats", {})
                    if stats.get("failed", 0) > 0:
                        await websocket.send_json({
                            "type": "stage",
                            "message": f"⚠️ {stats['failed']} tema(s) fallido(s); ver failures.md en el workspace.",
                        })
                    
                    summary_lines = [
                        f"✅ Procesamiento completado: {stats.get('successful', 0)}/{stats.get('total', 0)} temas exitosos",
                        *[f"- {item['tema']} ({item['workspace']}) — {'❌ Error' if item.get('error') else '✅ OK'}" for item in batch_result["items"]],
                    ]
                    await websocket.send_json({"type": "result", "message": "\n".join(summary_lines)})
                    await websocket.send_json({"type": "stage", "message": "✅ Parallel tema + tests workflow completed."})
                    continue

                agent = get_agent()
                payload_input = {"messages": [{"role": "user", "content": full_prompt}]}

                subagent_run_names: dict[str, str] = {}
                final_message = ""

                agent_labels = {
                    "analizador_tematario": "Analizador de temario",
                    "coordinador_general": "Coordinador general",
                    "calibrador": "Calibrador",
                    "fuentes_normativas": "Fuentes normativas",
                    "revision_normativa": "Revisión normativa",
                    "redactor_especialista": "Redactor especialista",
                    "pnl_pedagogico": "PNL pedagógico",
                    "revision_calidad": "Revisión de calidad",
                    "coherencia_bloque": "Coherencia de bloque",
                    "generador_tests": "Generador de tests",
                    "maquetador": "Maquetador",
                }

                agent_start_states = {
                    "analizador_tematario": "está analizando el temario y estructurando bloques.",
                    "coordinador_general": "está organizando el flujo de trabajo.",
                    "calibrador": "está calibrando el enfoque del contenido.",
                    "fuentes_normativas": "está revisando normativa aplicable.",
                    "revision_normativa": "está validando consistencia normativa.",
                    "redactor_especialista": "está redactando el contenido del tema.",
                    "pnl_pedagogico": "está enriqueciendo el enfoque didáctico.",
                    "revision_calidad": "está evaluando la calidad del contenido.",
                    "coherencia_bloque": "está verificando coherencia entre temas.",
                    "generador_tests": "está generando preguntas tipo test.",
                    "maquetador": "está maquetando y ensamblando el documento final.",
                }

                def _find_parent_subagent(event_payload: dict[str, Any]) -> str:
                    parent_ids = event_payload.get("parent_ids") or []
                    if not isinstance(parent_ids, list):
                        return ""
                    for parent_id in reversed(parent_ids):
                        subagent = subagent_run_names.get(str(parent_id))
                        if subagent:
                            return subagent
                    return ""

                async def _emit_agent_state(subagent_name: str, status: str, detail: str = "") -> None:
                    normalized = (subagent_name or "").strip()
                    label = agent_labels.get(normalized, normalized or "Subagente")

                    if status == "started":
                        state = agent_start_states.get(normalized, "está procesando la solicitud.")
                        message = f"🤖 {label}: {state}"
                        if detail:
                            message += f" Objetivo: {_preview_text(detail)}"
                    elif status == "completed":
                        message = f"✅ {label}: completado."
                    elif status == "failed":
                        message = f"❌ {label}: error durante la ejecución."
                        if detail:
                            message += f" Detalle: {_preview_text(detail)}"
                    elif status == "assembling":
                        message = f"🧩 {label}: está ensamblando el documento .docx."
                    elif status == "assembled":
                        message = f"📄 {label}: ensamblado del .docx completado."
                    else:
                        message = f"ℹ️ {label}: {status}"

                    await websocket.send_json({
                        "type": "agent_state",
                        "subagent": normalized or "subagente",
                        "status": status,
                        "message": message,
                    })

                try:
                    async for event in agent.astream_events(
                        payload_input, version="v2",
                        config={"recursion_limit": int(os.getenv("RECURSION_LIMIT", "50"))},
                    ):
                        event_name = event.get("event") or ""
                        event_data = event.get("data") or {}
                        run_id = str(event.get("run_id") or "")
                        tool_name = event.get("name") or ""

                        if event_name == "on_tool_start":
                            tool_input = event_data.get("input") or {}
                            if tool_name == "task":
                                subagent_name = "subagente"
                                target_content = ""
                                if isinstance(tool_input, dict):
                                    subagent_name = str(
                                        tool_input.get("subagent_type")
                                        or tool_input.get("name")
                                        or subagent_name
                                    )
                                    target_content = str(
                                        tool_input.get("task")
                                        or tool_input.get("description")
                                        or tool_input.get("input")
                                        or ""
                                    )
                                subagent_run_names[run_id] = subagent_name
                                await _emit_agent_state(subagent_name, "started", target_content)
                            else:
                                owner_subagent = _find_parent_subagent(event)
                                if tool_name == "assemble_document":
                                    await _emit_agent_state(owner_subagent or "maquetador", "assembling")
                            continue

                        if event_name == "on_tool_end":
                            tool_output = _stringify_content(event_data.get("output"))
                            preview = tool_output[:280] if tool_output else ""

                            if tool_name == "task":
                                subagent_name = subagent_run_names.pop(run_id, "subagente")
                                await _emit_agent_state(subagent_name, "completed", preview)
                            else:
                                owner_subagent = _find_parent_subagent(event)
                                if tool_name == "assemble_document":
                                    await _emit_agent_state(owner_subagent or "maquetador", "assembled", preview)
                            continue

                        if event_name == "on_tool_error":
                            if tool_name == "task":
                                subagent_name = subagent_run_names.pop(run_id, "subagente")
                                await _emit_agent_state(subagent_name, "failed")
                            else:
                                owner_subagent = _find_parent_subagent(event)
                                await _emit_agent_state(owner_subagent or "subagente", "failed", tool_name)
                            continue

                        if event_name in {"on_chain_end", "on_graph_end"}:
                            maybe_output = event_data.get("output")
                            candidate = _extract_final_message(maybe_output)
                            if candidate:
                                final_message = candidate

                except Exception:
                    # Fallback to non-streaming execution if event streaming is unavailable.
                    result = await asyncio.to_thread(
                        lambda: agent.invoke(
                            payload_input,
                            config={"recursion_limit": int(os.getenv("RECURSION_LIMIT", "50"))},
                        )
                    )
                    final_message = _extract_final_message(result)

                message = final_message or "Proceso completado sin mensaje final estructurado."
                if single_run_workspace:
                    (Path(single_run_workspace) / "final-response.md").write_text(message or "", encoding="utf-8")

                await websocket.send_json({"type": "result", "message": message})
                await websocket.send_json({"type": "stage", "message": "✅ Flujo de documentación completado."})
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
                if single_run_workspace:
                    set_output_workspace(None)

    except WebSocketDisconnect:
        return

