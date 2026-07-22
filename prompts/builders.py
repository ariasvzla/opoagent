"""Reusable prompt builders used across the agent system.

Keeping all prompt templates in one place makes them easier to tune
without touching application logic.
"""

from __future__ import annotations


def build_document_prompt(prompt: str, files: list[dict]) -> str:
    """Build a prompt that supports document-only uploads and asks for a
    summary plus next steps."""
    normalized_prompt = (prompt or "").strip()
    if normalized_prompt:
        return normalized_prompt

    file_names = ", ".join(item.get("name", "archivo") for item in files or [])
    return (
        "Analiza el contenido del documento adjunto y proporciona un resumen claro y útil. "
        f"Documentos recibidos: {file_names or 'sin nombre'}. "
        "Además, pregunta al usuario qué desea hacer con este documento y ofrece opciones prácticas para el siguiente paso."
    )


def build_full_tema_prompt(
    base_prompt: str,
    tema: str,
    tema_index: int,
    total_temas: int,
    calibration_context: str = "",
) -> str:
    """Build a prompt that instructs the coordinator to run the quality
    chain for a single topic.

    The calibrator runs once globally (not per-tema) — its output is
    injected via *calibration_context*.
    """

    cal_block = ""
    if calibration_context.strip():
        cal_block = (
            "\n\n## Contexto de calibración global\n"
            f"{calibration_context.strip()}\n"
        )

    return (
        f"{base_prompt}{cal_block}\n\n"
        f"---\n"
        f"TEMA OBJETIVO [{tema_index}/{total_temas}]: {tema}\n"
        f"---\n\n"
        "Tu tarea para este único tema es ejecutar el flujo de calidad "
        "usando los subagentes disponibles. El calibrador ya ha ejecutado "
        "a nivel global (ver contexto arriba) — NO lo invoques de nuevo.\n\n"
        "1. **Redactor especialista**: redacta el contenido completo del tema "
        "con rigor técnico, organizado por epígrafes. Los epígrafes pueden "
        "estar numerados (1.1, 1.2) o separados por punto y seguido dentro "
        "del enunciado del tema. Cada epígrafe debe tener al menos 2-3 "
        "párrafos sustantivos. Incluye introducción y conclusión. "
        "Guarda el resultado con save_section.\n"
        "2. **PNL pedagógico**: mejora el texto con enfoque didáctico, "
        "conexiones y claridad pedagógica.\n"
        "3. **Revisión de calidad**: evalúa el resultado. Si RECHAZADO, "
        "corrige y vuelve a evaluar hasta APROBADO.\n"
        "4. **Generador de tests**: crea exactamente 20 preguntas tipo test "
        "con 4 opciones y explicaciones.\n\n"
        "Al terminar, presenta un resumen con:\n"
        "- Tema procesado y resultado (APROBADO/RECHAZADO)\n"
        "- Archivos generados (contenido y tests)\n"
        "- Observaciones si las hay\n\n"
        "No mezcles contenido de otros temas. Céntrate exclusivamente en este."
    )
