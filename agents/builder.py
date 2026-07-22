import os
from deepagents import create_deep_agent
from deepagents.backends import CompositeBackend, StateBackend, StoreBackend
from deepagents.backends.utils import create_file_data
from langchain_deepseek import ChatDeepSeek
from langgraph.store.memory import InMemoryStore
from prompts import AGENT_SYSTEM_PROMPT, ANALIZADOR_TEMARIO_PROMPT, CALIBRADOR_PROMPT, COHERENCIA_BLOQUE_PROMPT, COORDINADOR_GENERAL_PROMPT, FUENTES_NORMATIVAS_PROMPT, GENERADOR_TESTS_PROMPT, MAQUETADOR_PROMPT, PNL_PEDAGOGICO_PROMPT, REDACTOR_ESPECIALISTA_PROMPT, REVISION_CALIDAD_PROMPT, REVISION_NORMATIVA_PROMPT
from .tools import save_section, read_section, assemble_document, upload_document_to_s3, read_uploaded_file

model_llm = ChatDeepSeek(
    model="deepseek-v4-flash",
    temperature=0.2
)

_CACHED_AGENT = None
_STORE: InMemoryStore | None = None


def _get_store() -> InMemoryStore:
    """Return the shared in-memory store; seed on first access."""
    global _STORE
    if _STORE is None:
        _STORE = InMemoryStore()
        _seed_memory(_STORE)
    return _STORE


def _seed_memory(store: InMemoryStore) -> None:
    """Seed the agent memory with initial knowledge about oposiciones workflow."""
    agent_ns = ("document_coordinator",)
    store.put(
        agent_ns,
        "/memories/agent_knowledge.md",
        create_file_data("""# Agent Knowledge Base

## Project purpose
This agent generates technical documentation and study materials (temarios) for
Spanish public examination processes (oposiciones). It can analyze official calls
(convocatorias), structure syllabi into relational blocks, draft topic content,
generate practice tests, and produce final .docx deliverables.

## Workflow conventions
- Always analyze the input document before starting production.
- When multiple topics are detected, process them in parallel batches.
- Each topic goes through: calibrate → draft → PNL → quality review → tests.
- Cross-topic coherence review runs before final assembly.
- Final output is a .docx file with all topics and coherence review appended.

## Quality standards
- Content must be technically rigorous and aligned with the official syllabus.
- Never invent normative references not present in the source.
- Tests must have exactly 20 questions with 4 options each (A, B, C, D).
- Quality review must output APROBADO or RECHAZADO explicitly.

## Tool usage
- save_section / read_section: persist and retrieve generated content.
- assemble_document: create the final .docx from markdown files.
- upload_document_to_s3: publish to S3 when bucket is configured.
- read_uploaded_file: inspect user-uploaded documents.
"""),
    )

def build_agent():
    subagents = [
        {
            "name": "analizador_tematario",
            "description": "Analiza la convocatoria, identifica el tipo de proceso selectivo y estructura el temario en bloques relacionales y mandatos de proyecto.",
            "system_prompt": ANALIZADOR_TEMARIO_PROMPT,
            "tools": [save_section, read_section, read_uploaded_file],
        },
        {
            "name": "coordinador_general",
            "description": "Coordina el flujo global del proyecto, gestiona estados, prerequisitos y comunicación con el usuario.",
            "system_prompt": COORDINADOR_GENERAL_PROMPT,
            "tools": [save_section, read_section, assemble_document],
        },
        {
            "name": "calibrador",
            "description": "Analiza el temario completo y el tipo de proceso para preparar la calibración general del contenido y la orientación de la producción.",
            "system_prompt": CALIBRADOR_PROMPT,
            "tools": [save_section, read_section, read_uploaded_file],
        },
        {
            "name": "fuentes_normativas",
            "description": "Revisa y estructura la base normativa por bloque, detectando normativa relevante, derogada o conflictiva.",
            "system_prompt": FUENTES_NORMATIVAS_PROMPT,
            "tools": [save_section, read_section, read_uploaded_file],
        },
        {
            "name": "revision_normativa",
            "description": "Revisa informes normativos, detecta problemas y propone correcciones o mejoras para los bloques.",
            "system_prompt": REVISION_NORMATIVA_PROMPT,
            "tools": [save_section, read_section, read_uploaded_file],
        },
        {
            "name": "redactor_especialista",
            "description": "Redacta el contenido temático de cada tema dentro de un bloque, guiado por informes normativos y de calibración.",
            "system_prompt": REDACTOR_ESPECIALISTA_PROMPT,
            "tools": [save_section, read_section, read_uploaded_file],
        },
        {
            "name": "pnl_pedagogico",
            "description": "Enriquece el texto del tema con enfoque pedagógico, didáctico y de conexión entre epígrafes.",
            "system_prompt": PNL_PEDAGOGICO_PROMPT,
            "tools": [save_section, read_section, read_uploaded_file],
        },
        {
            "name": "revision_calidad",
            "description": "Evalúa la calidad del contenido ya revisado normativamente y decide si el tema está listo o necesita corrección.",
            "system_prompt": REVISION_CALIDAD_PROMPT,
            "tools": [save_section, read_section, read_uploaded_file],
        },
        {
            "name": "coherencia_bloque",
            "description": "Verifica la consistencia interna entre los temas aprobados de un bloque y detecta problemas de solapamiento o incoherencia.",
            "system_prompt": COHERENCIA_BLOQUE_PROMPT,
            "tools": [save_section, read_section, read_uploaded_file],
        },
        {
            "name": "generador_tests",
            "description": "Genera preguntas tipo test para los temas ya aprobados, adaptadas al tipo de prueba y al contexto del proceso selectivo.",
            "system_prompt": GENERADOR_TESTS_PROMPT,
            "tools": [save_section, read_section, read_uploaded_file],
        },
        {
            "name": "maquetador",
            "description": "Compone el tema final con texto y preguntas en un formato listo para entrega y validación humana.",
            "system_prompt": MAQUETADOR_PROMPT,
            "tools": [save_section, read_section, assemble_document],
        },
    ]

    store = _get_store()

    return create_deep_agent(
        model=model_llm,
        tools=[save_section, read_section, assemble_document, upload_document_to_s3, read_uploaded_file],
        subagents=subagents,
        system_prompt=AGENT_SYSTEM_PROMPT,
        memory=["/memories/agent_knowledge.md"],
        backend=CompositeBackend(
            default=StateBackend(),
            routes={
                "/memories/": StoreBackend(
                    namespace=lambda rt: ("document_coordinator",),
                ),
            },
        ),
        store=store,
        name="document_coordinator",
    )


def get_cached_agent():
    """Return a cached instance of the deep agent; build once per process."""
    global _CACHED_AGENT
    if _CACHED_AGENT is None:
        _CACHED_AGENT = build_agent()
    return _CACHED_AGENT
