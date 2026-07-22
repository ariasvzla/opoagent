import os
from deepagents import create_deep_agent
from langchain_deepseek import ChatDeepSeek
from prompts import AGENT_SYSTEM_PROMPT, ANALIZADOR_TEMARIO_PROMPT, CALIBRADOR_PROMPT, COHERENCIA_BLOQUE_PROMPT, COORDINADOR_GENERAL_PROMPT, FUENTES_NORMATIVAS_PROMPT, GENERADOR_TESTS_PROMPT, MAQUETADOR_PROMPT, PNL_PEDAGOGICO_PROMPT, REDACTOR_ESPECIALISTA_PROMPT, REVISION_CALIDAD_PROMPT, REVISION_NUMERICA_PROMPT
from .tools import save_section, read_section, assemble_document, upload_document_to_s3, read_uploaded_file

model_llm = ChatDeepSeek(
    model="deepseek-v4-flash",
    temperature=0.2
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
            "name": "revision_numerica",
            "description": "Revisa informes normativos, detecta problemas y propone correcciones o mejoras para los bloques.",
            "system_prompt": REVISION_NUMERICA_PROMPT,
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

    return create_deep_agent(
        model=model_llm,
        tools=[save_section, read_section, assemble_document, upload_document_to_s3, read_uploaded_file],
        subagents=subagents,
        system_prompt=AGENT_SYSTEM_PROMPT,
        name="document_coordinator",
    )
