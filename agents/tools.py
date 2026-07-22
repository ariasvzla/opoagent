import asyncio
import os
from pathlib import Path
from typing import Awaitable, Callable, Optional

import boto3
from docx import Document
from pypdf import PdfReader

OUTPUT_DIR = Path("output")
OUTPUT_DIR.mkdir(exist_ok=True)
UPLOADS_DIR = OUTPUT_DIR / "uploads"
UPLOADS_DIR.mkdir(exist_ok=True)

_progress_callback: Optional[Callable[[str, str], Awaitable[None]]] = None


def set_progress_callback(callback: Optional[Callable[[str, str], Awaitable[None]]]) -> None:
    global _progress_callback
    _progress_callback = callback


def emit_progress(message: str, event_type: str = "stage") -> None:
    callback = _progress_callback
    if callback is None:
        return
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    loop.create_task(callback(event_type, message))


def extract_text_from_file(file_path: str) -> str:
    """Extract readable text from a local uploaded file."""
    path = Path(file_path)
    if not path.exists():
        return f"missing:{path}"

    suffix = path.suffix.lower()
    if suffix in {".txt", ".md", ".json", ".csv"}:
        return path.read_text(encoding="utf-8", errors="replace")

    if suffix == ".docx":
        try:
            doc = Document(path)
            return "\n".join(paragraph.text for paragraph in doc.paragraphs if paragraph.text.strip())
        except Exception as exc:
            return f"unable to read docx: {exc}"

    if suffix == ".pdf":
        try:
            reader = PdfReader(str(path))
            pages = [page.extract_text() or "" for page in reader.pages]
            return "\n".join(page for page in pages if page.strip())
        except Exception as exc:
            return f"unable to read pdf: {exc}"

    return f"unsupported file type: {suffix or 'unknown'}"


def build_document_prompt(prompt: str, files: list[dict]) -> str:
    """Build a prompt that supports document-only uploads and asks for a summary plus next steps."""
    normalized_prompt = (prompt or "").strip()
    if normalized_prompt:
        return normalized_prompt

    file_names = ", ".join(item.get("name", "archivo") for item in files or [])
    return (
        "Analiza el contenido del documento adjunto y proporciona un resumen claro y útil. "
        f"Documentos recibidos: {file_names or 'sin nombre'}. "
        "Además, pregunta al usuario qué desea hacer con este documento y ofrece opciones prácticas para el siguiente paso."
    )


def save_uploaded_file(filename: str, content: str) -> str:
    """Persist uploaded text content to disk so the agent can inspect it."""
    safe_name = Path(filename).name
    path = UPLOADS_DIR / safe_name
    path.write_text(content, encoding="utf-8")
    return str(path)


def read_uploaded_file(path: str) -> str:
    """Read an uploaded file from disk and return its text content."""
    return extract_text_from_file(path)


def save_section(filename: str, content: str) -> str:
    """Save a generated markdown section to disk."""
    emit_progress(f"📝 Writing section {filename}...")
    path = OUTPUT_DIR / filename
    path.write_text(content, encoding="utf-8")
    emit_progress(f"✅ Saved section {filename}.")
    return f"saved:{path}"


def read_section(filename: str) -> str:
    """Read a generated markdown section from disk."""
    path = OUTPUT_DIR / filename
    if not path.exists():
        return f"missing:{path}"
    return path.read_text(encoding="utf-8")


def assemble_document(title: str, filenames: list[str], output_filename: str = "05-final-document.docx") -> str:
    """Assemble markdown files into a .docx document."""
    emit_progress("🧩 Assembling the final document from the generated sections...")

    document = Document()
    document.add_heading(title, level=1)

    for name in filenames:
        path = OUTPUT_DIR / name
        if not path.exists():
            continue

        content = path.read_text(encoding="utf-8")
        paragraph = document.add_paragraph()
        paragraph.add_run(content)

    final_path = OUTPUT_DIR / output_filename
    document.save(final_path)
    emit_progress(f"✅ Final document assembled at {final_path}.")
    return f"assembled:{final_path}"


def upload_document_to_s3(filename: str = "05-final-document.docx", bucket: Optional[str] = None, key: Optional[str] = None) -> str:
    """Upload the assembled markdown document to S3 when the bucket is configured."""
    emit_progress(f"☁️ Preparing to upload {filename} to S3...")

    resolved_bucket = bucket or os.getenv("S3_BUCKET")
    if not resolved_bucket:
        emit_progress("⚠️ S3 upload skipped because S3_BUCKET is not configured.")
        return f"skipped:no-bucket:{filename}"

    path = OUTPUT_DIR / filename
    if not path.exists():
        emit_progress(f"⚠️ S3 upload skipped because {path} does not exist.")
        return f"skipped:missing-file:{filename}"

    resolved_key = key or filename
    region = os.getenv("AWS_REGION") or os.getenv("AWS_DEFAULT_REGION") or "us-east-1"

    try:
        client = boto3.client("s3", region_name=region)
        client.upload_file(str(path), resolved_bucket, resolved_key)
    except Exception as exc:
        emit_progress(f"⚠️ S3 upload failed: {exc}")
        return f"failed:{exc}"

    publish_url = f"https://{resolved_bucket}.s3.{region}.amazonaws.com/{resolved_key}"
    emit_progress(f"✅ Uploaded {filename} to s3://{resolved_bucket}/{resolved_key}.")
    return f"uploaded:{publish_url}"
