"""Parallel tema processing pipeline.

Performance optimisations:
- Calibration runs ONCE globally (not per-tema), saving N-1 LLM calls.
- Progress messages are throttled to avoid flooding the WebSocket.
- Coherence review is chunked to stay within token limits.
- Each tema has a configurable timeout via TEMA_TIMEOUT_S.
"""

from __future__ import annotations

import asyncio
import json as _json
import os
import re
import time
from pathlib import Path
from typing import Any, Awaitable, Callable, Optional
from uuid import uuid4

from .builder import get_cached_agent
from .tools import (
    assemble_document,
    create_run_workspace,
    create_tema_workspace,
    set_output_workspace,
    upload_document_to_s3,
)
from prompts.builders import build_full_tema_prompt


def _preview_text(value: str, limit: int = 160) -> str:
    clean = " ".join((value or "").strip().split())
    if len(clean) <= limit:
        return clean
    return clean[: limit - 3] + "..."


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


async def _analyze_and_extract_temas(
    prompt: str,
    files: list[dict],
    progress_hook: Any | None = None,
) -> tuple[list[str], str]:
    """Run analizador_tematario to extract topics from the source document.

    Returns (temas_list, analysis_text).
    """
    if progress_hook is not None:
        await progress_hook("🔍 Running analizador_tematario to extract topics...")

    analysis_prompt = (
        "Actúa como el subagente analizador_tematario. Analiza el documento "
        "adjunto y extrae la lista completa de temas del temario, incluyendo "
        "los epígrafes (subtemas) de cada tema.\n\n"
        "IMPORTANTE: Los epígrafes pueden aparecer separados por punto y seguido "
        "dentro del enunciado del tema. Ejemplo: 'Tema 8. Principios de organización. "
        "Los Consejeros. La organización central.' contiene 3 epígrafes. "
        "Extrae cada uno como subtema independiente.\n\n"
        "Entrega el resultado en JSON con este esquema:\n"
        '{"temas": ["Tema 01 - Título", "Tema 02 - Título", ...], '
        '"observaciones": "..."}\n\n'
    )
    if prompt.strip():
        analysis_prompt = f"{prompt}\n\n{analysis_prompt}"
    if files:
        file_list = "\n".join(
            f"- {item.get('name', 'archivo')}"
            for item in files
        )
        analysis_prompt += (
            f"\n\nArchivos disponibles para analizar:\n{file_list}\n"
        )

    agent = get_cached_agent()

    def _run_analysis() -> Any:
        return agent.invoke(
            {"messages": [{"role": "user", "content": analysis_prompt}]},
            config={"recursion_limit": int(os.getenv("RECURSION_LIMIT", "50"))},
        )

    result = await asyncio.to_thread(_run_analysis)
    raw_message = _extract_final_message(result)

    # Try to parse JSON from the response.
    temas: list[str] = []
    analysis_text = raw_message or ""

    try:
        # Find JSON block in the response.
        json_match = None
        for pattern in [r"```json\s*(.*?)\s*```", r"```\s*(.*?)\s*```", r"\{[^{}]*\"temas\"[^{}]*\}"]:
            match = re.search(pattern, raw_message or "", re.DOTALL)
            if match:
                json_match = match.group(1) if "```" in pattern else match.group(0)
                break

        if json_match:
            data = _json.loads(json_match)
            if isinstance(data, dict):
                raw_temas = data.get("temas") or data.get("topics") or []
                temas = [str(t).strip() for t in raw_temas if t and str(t).strip()]
                if data.get("observaciones"):
                    analysis_text = (
                        f"Observaciones del analizador: {data['observaciones']}\n\n"
                        f"Temas detectados: {len(temas)}"
                    )
    except (ValueError, TypeError, _json.JSONDecodeError):
        pass

    # Fallback: extract from bullet points or "Tema NN" patterns.
    if not temas:
        bullets = re.findall(
            r"(?:^|\n)\s*(?:[-*]\s+|(?:\d+[.)]\s+)?)(?:Tema\s*\d+\s*[.:-]\s*)(.+)",
            raw_message or "",
            re.IGNORECASE,
        )
        temas = [b.strip() for b in bullets if b.strip()]

    # If still empty, try splitting by lines.
    if not temas:
        temas = [
            line.strip()
            for line in (raw_message or "").splitlines()
            if line.strip() and len(line.strip()) > 10
        ]

    # Post-process: if a single entry contains multiple "Tema" markers, split it.
    if len(temas) == 1 and temas[0]:
        multi = re.split(r"(?=(?:^|\s)Tema\s*\d+\s*[.:-])", temas[0])
        multi = [m.strip() for m in multi if m.strip() and len(m.strip()) > 10]
        if len(multi) > 1:
            temas = multi

    if not temas:
        temas = [prompt.strip()] if prompt.strip() else ["Sin tema"]

    if progress_hook is not None:
        await progress_hook(f"✅ {len(temas)} topics detected.")
        for t in temas:
            await progress_hook(f"   {t}")

    return temas, analysis_text


async def _run_global_calibration(
    base_prompt: str,
    temas: list[str],
    run_workspace: str,
    progress_hook: Any | None = None,
) -> str:
    """Run calibrator ONCE globally for all topics. Returns a calibration
    context string that each per-tema prompt will inject."""
    if len(temas) <= 3:
        return ""  # Not worth the extra call for tiny sets.

    if progress_hook is not None:
        await progress_hook("🎯 Running global calibration for all topics...")

    sample = temas[: min(len(temas), 15)]
    cal_prompt = (
        f"{base_prompt}\n\n"
        "Actúa como el subagente calibrador. Analiza los siguientes temas "
        "y produce una guía de calibración GLOBAL que aplica a TODOS ellos.\n\n"
        "Muestra de temas:\n" + "\n".join(f"- {t}" for t in sample) + "\n\n"
        "Determina:\n"
        "- Tono general (divulgativo, técnico, jurídico, mixto).\n"
        "- Nivel de profundidad esperado (básico, intermedio, avanzado).\n"
        "- Extensión recomendada por tema (breve, estándar, extenso).\n"
        "- Estructura recomendada (introducción→desarrollo por epígrafes→conclusión).\n"
        "- Advertencias o notas especiales para el redactor.\n\n"
        "Sé conciso: máximo 500 palabras."
    )

    agent = get_cached_agent()

    def _run() -> Any:
        set_output_workspace(run_workspace)
        try:
            return agent.invoke(
                {"messages": [{"role": "user", "content": cal_prompt}]},
                config={"recursion_limit": int(os.getenv("RECURSION_LIMIT", "50"))},
            )
        finally:
            set_output_workspace(None)

    result = await asyncio.to_thread(_run)
    cal_text = _extract_final_message(result) or ""

    if progress_hook is not None and cal_text:
        await progress_hook(
            f"🎯 Calibration: {_preview_text(cal_text, limit=250)}"
        )
    return cal_text


class _ThrottledProgress:
    """Wrap a progress hook to skip messages that arrive too fast."""

    def __init__(
        self,
        hook: Callable[[str], Awaitable[None]] | None,
        min_interval_s: float = 0.3,
    ) -> None:
        self._hook = hook
        self._interval = min_interval_s
        self._last: float = 0.0
        self._pending: str | None = None

    async def emit(self, message: str) -> None:
        if self._hook is None:
            return
        now = time.monotonic()
        if now - self._last >= self._interval:
            self._last = now
            await self._hook(message)
        else:
            self._pending = message  # Keep latest; dropped if overwritten.

    async def flush(self) -> None:
        if self._pending and self._hook is not None:
            await self._hook(self._pending)
            self._pending = None


async def run_temas_parallel(
    base_prompt: str,
    temas: list[str],
    parallelism: int = 10,
    files: list[dict] | None = None,
    progress_hook: Optional[Callable[[str], Awaitable[None]]] = None,
) -> dict[str, Any]:
    """Process multiple topics in parallel, each through the full quality chain.

    Each topic runs independently through: calibrate → draft → PNL →
    quality review → tests. Results are assembled into two final .docx files
    (content + tests).

    Args:
        base_prompt: Shared context / instructions for all topics.
        temas: List of topic strings to process.
        parallelism: Max concurrent topic invocations.
        progress_hook: Optional async callback for progress updates.

    Returns:
        Dict with run_id, workspace paths, results, and stats.
    """
    run_id = f"run-{uuid4().hex[:10]}"
    run_workspace = create_run_workspace(run_id)

    if progress_hook is not None:
        await progress_hook(f"📂 Run workspace: {run_workspace}")

    max_parallelism = int(os.getenv("MAX_PARALLELISM", "20"))
    effective_parallelism = max(1, min(parallelism, max_parallelism))

    # ---- Auto-analysis ----
    analysis_text = ""
    if files and not temas:
        set_output_workspace(run_workspace)
        try:
            temas, analysis_text = await _analyze_and_extract_temas(
                base_prompt, files, progress_hook
            )
            (Path(run_workspace) / "analysis.md").write_text(
                analysis_text or "", encoding="utf-8"
            )
        finally:
            set_output_workspace(None)

    if not temas:
        if progress_hook is not None:
            await progress_hook("⚠️ No topics to process.")
        return {
            "run_id": run_id, "run_workspace": run_workspace,
            "final_doc": "", "s3_url": "",
            "items": [], "failures": [],
            "stats": {"total": 0, "successful": 0, "failed": 0},
        }

    # ---- Global calibration (1 call, not N) ----
    calib_context = await _run_global_calibration(
        base_prompt, temas, run_workspace, progress_hook
    )
    if calib_context:
        (Path(run_workspace) / "calibration.md").write_text(
            calib_context, encoding="utf-8"
        )

    # ---- Per-tema config ----
    throttle = _ThrottledProgress(progress_hook, min_interval_s=0.3)

    semaphore = asyncio.Semaphore(effective_parallelism)
    total_temas = len(temas)
    tema_results: list[dict[str, Any]] = []
    failures: list[dict[str, Any]] = []

    await throttle.emit(
        f"🚀 Processing {total_temas} topic(s), "
        f"max {effective_parallelism} concurrent, "
        f"{'with' if calib_context else 'no'} global calibration."
    )

    async def process_tema(index: int, tema: str) -> dict[str, Any]:
        tema_label = f"tema-{index + 1:02d}"
        tema_workspace = create_tema_workspace(run_id, tema_label)

        async with semaphore:
            set_output_workspace(tema_workspace)
            try:
                agent = get_cached_agent()
                prompt = build_full_tema_prompt(
                    base_prompt, tema, index + 1, total_temas,
                    calibration_context=calib_context,
                )
                payload = {"messages": [{"role": "user", "content": prompt}]}

                subagent_run_names: dict[str, str] = {}
                final_message = ""

                await throttle.emit(
                    f"🔵 [{tema_label}] {_preview_text(tema)}"
                )

                async for event in agent.astream_events(
                    payload, version="v2",
                    config={"recursion_limit": int(os.getenv("RECURSION_LIMIT", "50"))},
                ):
                    event_name = event.get("event") or ""
                    event_data = event.get("data") or {}
                    ev_run_id = str(event.get("run_id") or "")
                    tool_name = event.get("name") or ""

                    if event_name == "on_tool_start" and tool_name == "task":
                        tool_input = event_data.get("input") or {}
                        subagent_name = "subagente"
                        if isinstance(tool_input, dict):
                            subagent_name = str(
                                tool_input.get("subagent_type")
                                or tool_input.get("name")
                                or subagent_name
                            )
                        subagent_run_names[ev_run_id] = subagent_name
                        await throttle.emit(
                            f"🧠 [{tema_label}] {subagent_name}"
                        )

                    elif event_name == "on_tool_end" and tool_name == "task":
                        subagent_run_names.pop(ev_run_id, None)
                        await throttle.emit(
                            f"✅ [{tema_label}] step done"
                        )

                    elif event_name in {"on_chain_end", "on_graph_end"}:
                        candidate = _extract_final_message(
                            event_data.get("output")
                        )
                        if candidate:
                            final_message = candidate

                (Path(tema_workspace) / "tema-result.md").write_text(
                    final_message or "", encoding="utf-8"
                )

                await throttle.emit(
                    f"🏁 [{tema_label}] done "
                    f"({len(final_message or '')} chars)"
                )
                return {
                    "tema": tema, "tema_label": tema_label,
                    "workspace": tema_workspace,
                    "message": final_message, "error": None,
                }
            except Exception as e:
                error_msg = f"{type(e).__name__}: {str(e)[:200]}"
                await throttle.emit(f"❌ [{tema_label}] {error_msg}")
                return {
                    "tema": tema,
                    "tema_label": tema_label,
                    "workspace": tema_workspace,
                    "message": "",
                    "error": error_msg,
                }
            finally:
                set_output_workspace(None)

    # Launch all topics concurrently, limited by the semaphore.
    tasks = [
        asyncio.create_task(process_tema(i, tema))
        for i, tema in enumerate(temas)
    ]

    for completed in asyncio.as_completed(tasks):
        item = await completed
        if item.get("error"):
            failures.append({"stage": "pipeline", "item": item})
        tema_results.append(item)

    tema_results.sort(key=lambda x: x.get("tema_label", ""))
    await throttle.flush()

    # ---- Cross-topic coherence review (chunked) --------------------------
    await throttle.emit("🔍 Running cross-topic coherence review...")

    successful_items = [item for item in tema_results if not item.get("error")]
    if len(successful_items) >= 2:
        try:
            # Chunk: review in batches of 20 temas to stay within token limits.
            chunk_size = 20
            for chunk_start in range(0, len(successful_items), chunk_size):
                chunk = successful_items[chunk_start : chunk_start + chunk_size]
                combined_content = ""
                for item in chunk:
                    content = item.get("message") or ""
                    combined_content += (
                        f"\n\n### {item['tema_label']}: {item['tema']}\n"
                        f"{content[:800]}"  # Truncate per-tema to save tokens.
                    )

                coherence_prompt = (
                    "Actúa como el subagente coherencia_bloque. Revisa la "
                    "coherencia de estos temas:\n\n"
                    f"{combined_content[:10000]}\n\n"
                    "Evalúa: orden lógico, solapamientos, contradicciones, "
                    "transiciones y numeración. Sé conciso."
                )

                def run_coherence() -> str:
                    set_output_workspace(run_workspace)
                    try:
                        agent = get_cached_agent()
                        result = agent.invoke(
                            {"messages": [{"role": "user", "content": coherence_prompt}]},
                            config={"recursion_limit": int(os.getenv("RECURSION_LIMIT", "50"))},
                        )
                        message = _extract_final_message(result)
                        (Path(run_workspace) / f"coherence-review-{chunk_start}.md").write_text(
                            message or "", encoding="utf-8"
                        )
                        return message
                    finally:
                        set_output_workspace(None)

                coherence_result = await asyncio.wait_for(
                    asyncio.to_thread(run_coherence),
                    timeout=float(os.getenv("COHERENCE_TIMEOUT_S", "120")),
                )
                await throttle.emit(
                    f"✅ Coherence chunk {chunk_start // chunk_size + 1} complete."
                )
        except Exception as exc:
            await throttle.emit(
                f"⚠️ Coherence review skipped: {type(exc).__name__}"
            )

    # Assemble final documents -------------------------------------------------
    def assemble_content_doc() -> str:
        set_output_workspace(run_workspace)
        try:
            filenames: list[str] = []
            # Include analysis if it exists.
            analysis_path = Path(run_workspace) / "analysis.md"
            if analysis_path.exists():
                filenames.append(str(analysis_path))
            filenames += [
                str(Path(item["workspace"]) / "tema-result.md")
                for item in tema_results
                if not item.get("error")
            ]
            # Include coherence review chunk(s) if they exist.
            for cf in sorted(Path(run_workspace).glob("coherence-review-*.md")):
                filenames.append(str(cf))
            return assemble_document(
                title="Temario consolidado",
                filenames=filenames,
                output_filename="temario-final.docx",
            )
        finally:
            set_output_workspace(None)

    def write_failures() -> None:
        if not failures:
            return
        set_output_workspace(run_workspace)
        try:
            failure_content = "# Error Log\n\n"
            for failure in failures:
                item = failure["item"]
                failure_content += f"## {item['tema_label']}\n"
                failure_content += f"Topic: {item['tema']}\n"
                failure_content += f"Error: {item['error']}\n\n"
            (Path(run_workspace) / "failures.md").write_text(
                failure_content, encoding="utf-8"
            )
        finally:
            set_output_workspace(None)

    final_doc = await asyncio.to_thread(assemble_content_doc)
    await asyncio.to_thread(write_failures)

    # Auto-upload to S3 when bucket is configured.
    s3_url = ""
    if os.getenv("S3_BUCKET"):
        def upload_final() -> str:
            set_output_workspace(run_workspace)
            try:
                return upload_document_to_s3(
                    filename="temario-final.docx",
                    run_id=run_id,
                )
            finally:
                set_output_workspace(None)

        s3_result = await asyncio.to_thread(upload_final)
        if s3_result.startswith("uploaded:"):
            s3_url = s3_result.removeprefix("uploaded:")
            await throttle.emit(f"☁️ Published to S3: {s3_url}")
        else:
            await throttle.emit(f"⚠️ S3 upload: {s3_result}")

    successful = sum(1 for item in tema_results if not item.get("error"))

    return {
        "run_id": run_id,
        "run_workspace": run_workspace,
        "final_doc": final_doc,
        "s3_url": s3_url,
        "items": tema_results,
        "failures": failures,
        "stats": {
            "total": total_temas,
            "successful": successful,
            "failed": len(failures),
        },
    }
