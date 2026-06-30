from pathlib import Path

OUTPUT_DIR = Path("output")
OUTPUT_DIR.mkdir(exist_ok=True)


def save_section(filename: str, content: str) -> str:
    """Save a generated markdown section to disk."""
    path = OUTPUT_DIR / filename
    path.write_text(content, encoding="utf-8")
    return f"saved:{path}"


def read_section(filename: str) -> str:
    """Read a generated markdown section from disk."""
    path = OUTPUT_DIR / filename
    if not path.exists():
        return f"missing:{path}"
    return path.read_text(encoding="utf-8")


def assemble_document(title: str, filenames: list[str], output_filename: str = "05-final-document.md") -> str:
    """Assemble markdown files into a final document."""
    parts = [f"# {title}"]
    for name in filenames:
        path = OUTPUT_DIR / name
        if path.exists():
            parts.append(path.read_text(encoding="utf-8"))
            parts.append("")
    final_path = OUTPUT_DIR / output_filename
    final_path.write_text("".join(parts), encoding="utf-8")
    return f"assembled:{final_path}"
