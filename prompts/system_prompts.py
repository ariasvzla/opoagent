from pathlib import Path


PROMPTS_DIR = Path(__file__).resolve().parent / "agents"


def _read_prompt(filename: str) -> str:
    path = PROMPTS_DIR / filename
    return path.read_text(encoding="utf-8").strip()


AGENT_SYSTEM_PROMPT = _read_prompt("coordinator_system.md")
ANALIZADOR_TEMARIO_PROMPT = _read_prompt("analizador_tematario.md")
COORDINADOR_GENERAL_PROMPT = _read_prompt("coordinador_general.md")
CALIBRADOR_PROMPT = _read_prompt("calibrador.md")
FUENTES_NORMATIVAS_PROMPT = _read_prompt("fuentes_normativas.md")
REVISION_NORMATIVA_PROMPT = _read_prompt("revision_normativa.md")
REDACTOR_ESPECIALISTA_PROMPT = _read_prompt("redactor_especialista.md")
PNL_PEDAGOGICO_PROMPT = _read_prompt("pnl_pedagogico.md")
REVISION_CALIDAD_PROMPT = _read_prompt("revision_calidad.md")
COHERENCIA_BLOQUE_PROMPT = _read_prompt("coherencia_bloque.md")
GENERADOR_TESTS_PROMPT = _read_prompt("generador_tests.md")
MAQUETADOR_PROMPT = _read_prompt("maquetador.md")
