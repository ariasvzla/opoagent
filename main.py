import asyncio
import json
from pathlib import Path
from typing import Any, Awaitable, Callable, Optional
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
    parallelism: int = 10
    batch_size: int = 10


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


def _build_test_prompt(tema: str, tema_content: str) -> str:
    return (
        "Actua como el subagente generador_tests para oposiciones. "
        "A partir del contenido del tema, genera un test practico de calidad.\n\n"
        f"Tema: {tema}\n\n"
        "Requisitos:\n"
        "- Genera 20 preguntas tipo test (A, B, C, D).\n"
        "- Marca la respuesta correcta en cada pregunta.\n"
        "- Incluye explicacion breve por respuesta correcta.\n"
        "- Mantente fiel al contenido dado, no inventes normativa externa.\n\n"
        "Contenido base del tema:\n"
        f"{tema_content}"
    )


def _chunked(items: list[str], size: int) -> list[list[str]]:
    chunk_size = max(1, size)
    return [items[i:i + chunk_size] for i in range(0, len(items), chunk_size)]


def _preview_text(value: str, limit: int = 160) -> str:
    clean = " ".join((value or "").strip().split())
    if len(clean) <= limit:
        return clean
    return clean[: limit - 3] + "..."


async def _run_temas_parallel(
    base_prompt: str,
    temas: list[str],
    parallelism: int,
    batch_size: int,
    progress_hook: Optional[Callable[[str], Awaitable[None]]] = None,
) -> dict[str, Any]:
    run_id = f"run-{uuid4().hex[:10]}"
    run_workspace = create_run_workspace(run_id)
    semaphore = asyncio.Semaphore(max(1, min(parallelism, 10)))
    tema_results: list[dict[str, Any]] = []
    test_results: list[dict[str, Any]] = []

    async def process_tema(index: int, tema: str) -> dict[str, Any]:
        tema_label = f"tema-{index + 1:02d}"
        tema_workspace = create_tema_workspace(run_id, tema_label)

        if progress_hook is not None:
            await progress_hook(
                f"🤖 [redactor_especialista] Inicia {tema_label} sobre contenido: {_preview_text(tema)}"
            )

        async with semaphore:
            def invoke_one() -> str:
                set_output_workspace(tema_workspace)
                try:
                    agent = get_agent()
                    prompt = _build_tema_prompt(base_prompt, tema)
                    result = agent.invoke({"messages": [{"role": "user", "content": prompt}]})
                    message = _extract_final_message(result)
                    markdown_path = Path(tema_workspace) / "tema-content.md"
                    markdown_path.write_text(message or "", encoding="utf-8")
                    return message
                finally:
                    set_output_workspace(None)

            message = await asyncio.to_thread(invoke_one)
            if progress_hook is not None:
                await progress_hook(
                    f"✅ [redactor_especialista] Completa {tema_label}. Salida: {_preview_text(message)}"
                )
            return {
                "tema": tema,
                "tema_label": tema_label,
                "workspace": tema_workspace,
                "message": message,
                "content_filename": f"{tema_label}/tema-content.md",
            }

    async def process_tests(item: dict[str, Any]) -> dict[str, Any]:
        tema = item["tema"]
        tema_label = item["tema_label"]
        tema_workspace = item["workspace"]
        tema_content = item["message"] or ""

        if progress_hook is not None:
            await progress_hook(
                f"🧪 [generador_tests] Inicia {tema_label} sobre contenido: {_preview_text(tema_content)}"
            )

        async with semaphore:
            def invoke_tests() -> str:
                set_output_workspace(tema_workspace)
                try:
                    agent = get_agent()
                    prompt = _build_test_prompt(tema, tema_content)
                    result = agent.invoke({"messages": [{"role": "user", "content": prompt}]})
                    message = _extract_final_message(result)
                    markdown_path = Path(tema_workspace) / "tema-tests.md"
                    markdown_path.write_text(message or "", encoding="utf-8")
                    return message
                finally:
                    set_output_workspace(None)

            tests_message = await asyncio.to_thread(invoke_tests)
            if progress_hook is not None:
                await progress_hook(
                    f"✅ [generador_tests] Completa {tema_label}. Salida: {_preview_text(tests_message)}"
                )
            return {
                "tema": tema,
                "tema_label": tema_label,
                "workspace": tema_workspace,
                "message": tests_message,
                "tests_filename": f"{tema_label}/tema-tests.md",
            }

    batches = _chunked(temas, max(1, batch_size))
    global_index = 0
    for batch_idx, batch in enumerate(batches, start=1):
        if progress_hook is not None:
            await progress_hook(f"🚀 Lote {batch_idx}/{len(batches)}: generando {len(batch)} tema(s).")

        processed = await asyncio.gather(
            *(process_tema(global_index + i, tema) for i, tema in enumerate(batch))
        )
        tema_results.extend(processed)
        global_index += len(batch)

    test_batches = _chunked([item["tema_label"] for item in tema_results], max(1, batch_size))
    label_to_item = {item["tema_label"]: item for item in tema_results}
    for batch_idx, test_batch in enumerate(test_batches, start=1):
        if progress_hook is not None:
            await progress_hook(f"🧪 Lote test {batch_idx}/{len(test_batches)}: generando tests para {len(test_batch)} tema(s).")

        processed_tests = await asyncio.gather(
            *(process_tests(label_to_item[label]) for label in test_batch)
        )
        test_results.extend(processed_tests)

    def assemble_all() -> str:
        set_output_workspace(run_workspace)
        try:
            filenames = [item["content_filename"] for item in tema_results]
            title = "Temario consolidado"
            return assemble_document(title=title, filenames=filenames, output_filename="temario-final.docx")
        finally:
            set_output_workspace(None)

    def assemble_tests() -> str:
        set_output_workspace(run_workspace)
        try:
            filenames = [item["tests_filename"] for item in test_results]
            title = "Tests de practica del temario"
            return assemble_document(title=title, filenames=filenames, output_filename="temario-tests.docx")
        finally:
            set_output_workspace(None)

    final_doc = await asyncio.to_thread(assemble_all)
    tests_doc = await asyncio.to_thread(assemble_tests)
    return {
        "run_id": run_id,
        "run_workspace": run_workspace,
        "final_doc": final_doc,
        "tests_doc": tests_doc,
        "items": tema_results,
        "test_items": test_results,
    }


@app.post("/invoke")
def invoke(request: InvokeRequest):
    full_prompt = build_prompt_with_files(request.prompt, request.files)
    temas = [tema.strip() for tema in (request.temas or []) if tema and tema.strip()]

    if len(temas) > 1:
        batch_result = asyncio.run(
            _run_temas_parallel(full_prompt, temas, request.parallelism, request.batch_size)
        )
        return {
            "message": "Temario y tests procesados en paralelo por lotes.",
            "run_id": batch_result["run_id"],
            "run_workspace": batch_result["run_workspace"],
            "final_doc": batch_result["final_doc"],
            "tests_doc": batch_result["tests_doc"],
            "items": [{"tema": item["tema"], "workspace": item["workspace"]} for item in batch_result["items"]],
        }

    run_id = f"run-{uuid4().hex[:10]}"
    run_workspace = create_run_workspace(run_id)
    set_output_workspace(run_workspace)
    try:
        agent = get_agent()
        result = agent.invoke(
            {"messages": [{"role": "user", "content": full_prompt}]}
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
            batch_size = int(payload.get("batch_size") or 10)
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
            is_parallel_request = len(temas) > 1
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
                    await websocket.send_json({
                        "type": "stage",
                        "message": (
                            f"⚙️ Detectados {len(temas)} temas. "
                            f"Procesando en lotes de {max(1, batch_size)} con max {max(1, min(parallelism, 10))} simultaneos."
                        ),
                    })

                    async def notify_progress(progress_message: str) -> None:
                        await websocket.send_json({"type": "stage", "message": progress_message})

                    batch_result = await _run_temas_parallel(
                        full_prompt,
                        temas,
                        parallelism,
                        batch_size,
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
                    await websocket.send_json({
                        "type": "stage",
                        "message": f"🧪 Documento de tests: {batch_result['tests_doc']}",
                    })

                    summary_lines = [
                        "Temario y tests procesados en paralelo:",
                        *[f"- {item['tema']} ({item['workspace']})" for item in batch_result["items"]],
                    ]
                    await websocket.send_json({"type": "result", "message": "\n".join(summary_lines)})
                    await websocket.send_json({"type": "stage", "message": "✅ Parallel tema + tests workflow completed."})
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
                                await websocket.send_json({
                                    "type": "subagent_started",
                                    "subagent": subagent_name,
                                    "message": (
                                        f"Subagente iniciado: {subagent_name}. "
                                        f"Objetivo: {_preview_text(target_content)}"
                                    ),
                                })
                            else:
                                input_preview = _preview_text(_stringify_content(tool_input))
                                await websocket.send_json({
                                    "type": "tool_started",
                                    "tool": tool_name,
                                    "message": f"Herramienta iniciada: {tool_name}. Entrada: {input_preview}",
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
                                    "message": (
                                        f"Subagente completado: {subagent_name}. "
                                        f"Salida: {_preview_text(preview)}"
                                    ),
                                })
                            else:
                                await websocket.send_json({
                                    "type": "tool_completed",
                                    "tool": tool_name,
                                    "message": f"Herramienta completada: {tool_name}.",
                                })
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
                if single_run_workspace:
                    (Path(single_run_workspace) / "final-response.md").write_text(message or "", encoding="utf-8")

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
                if single_run_workspace:
                    set_output_workspace(None)

    except WebSocketDisconnect:
        return

