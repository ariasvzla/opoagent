from pathlib import Path

from agents.tools import save_uploaded_file, extract_text_from_file
from prompts.builders import build_document_prompt
from main import FileInput, build_prompt_with_files


def test_save_uploaded_file_and_extract_text(tmp_path):
    sample = tmp_path / "sample.txt"
    sample.write_text("Hola mundo desde el archivo", encoding="utf-8")

    saved_path = save_uploaded_file(sample.name, sample.read_text(encoding="utf-8"))
    extracted = extract_text_from_file(saved_path)

    assert Path(saved_path).exists()
    assert "Hola mundo" in extracted


def test_build_document_prompt_uses_summary_default_when_prompt_is_empty():
    prompt = build_document_prompt("", [{"name": "doc.pdf", "content": "contenido"}])

    assert "resumen" in prompt.lower()
    assert "qué desea hacer" in prompt.lower() or "qué quiere hacer" in prompt.lower()


def test_build_prompt_with_files_adds_low_input_temario_guidance():
    files = [FileInput(name="convocatoria.txt", content="Convocatoria de oposicion", mime_type="text/plain")]

    prompt = build_prompt_with_files("", files)

    assert "haz solo estas 2 preguntas" in prompt.lower()
    assert "tipo de proceso selectivo" in prompt.lower()
    assert "salida esperada" in prompt.lower()


def test_extract_text_from_invalid_pdf_uses_text_fallback(tmp_path):
    fake_pdf = tmp_path / "invalid.pdf"
    fake_pdf.write_text("PROG contenido de prueba", encoding="utf-8")

    extracted = extract_text_from_file(str(fake_pdf))

    assert "PROG" in extracted
