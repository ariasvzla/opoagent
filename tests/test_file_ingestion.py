from pathlib import Path

from agents.tools import build_document_prompt, save_uploaded_file, extract_text_from_file


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
