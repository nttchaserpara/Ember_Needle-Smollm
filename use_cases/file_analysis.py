"""File attachment analysis for EmberOS-Windows."""

import csv
import difflib
import io
import logging
import re
import tarfile
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path
from emberos.outcomes import ToolOutput

logger = logging.getLogger("emberos.use_cases.file_analysis")

_TEXT_EXTENSIONS = {
    ".txt", ".md", ".py", ".js", ".ts", ".csv", ".json", ".log",
    ".html", ".xml", ".yaml", ".yml", ".toml", ".cfg", ".ini",
    ".bat", ".ps1", ".c", ".cpp", ".h", ".rs", ".go", ".java",
    ".css", ".sh", ".rb",
}

_IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".svg", ".webp", ".tiff"}


def _read_text_file(path: Path, max_chars: int = 6000) -> str:
    try:
        with path.open(encoding="utf-8", errors="replace") as source:
            text = source.read(max_chars + 1)
        if len(text) > max_chars:
            return text[:max_chars] + "\n[... truncated]"
        return text
    except Exception as e:
        return ToolOutput.failure(f"Error reading: {e}")


def _read_csv_file(path: Path) -> str:
    try:
        lines = []
        with open(path, "r", encoding="utf-8", errors="replace", newline="") as f:
            reader = csv.reader(f)
            for i, row in enumerate(reader):
                if i >= 100:
                    lines.append("[... truncated at 100 rows]")
                    break
                lines.append(" | ".join(row))
        return "\n".join(lines)
    except Exception as e:
        return ToolOutput.failure(f"Error reading CSV: {e}")


def _read_docx(path: Path) -> str:
    try:
        with zipfile.ZipFile(str(path), "r") as z:
            if "word/document.xml" not in z.namelist():
                return ToolOutput.failure("Could not find document.xml in .docx")
            xml_data = z.read("word/document.xml")
        root = ET.fromstring(xml_data)
        ns = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
        paragraphs = []
        for para in root.iter("{http://schemas.openxmlformats.org/wordprocessingml/2006/main}p"):
            texts = []
            for t in para.iter("{http://schemas.openxmlformats.org/wordprocessingml/2006/main}t"):
                if t.text:
                    texts.append(t.text)
            if texts:
                paragraphs.append("".join(texts))
        text = "\n".join(paragraphs)
        if len(text) > 6000:
            return text[:6000] + "\n[... truncated]"
        return text if text else "Empty document"
    except Exception as e:
        return ToolOutput.failure(f"Error reading .docx: {e}")


def _read_xlsx(path: Path) -> str:
    try:
        with zipfile.ZipFile(str(path), "r") as z:
            # Read shared strings
            shared = []
            if "xl/sharedStrings.xml" in z.namelist():
                ss_xml = z.read("xl/sharedStrings.xml")
                ss_root = ET.fromstring(ss_xml)
                ns = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
                for si in ss_root.iter(f"{{{ns}}}si"):
                    parts = []
                    for t in si.iter(f"{{{ns}}}t"):
                        if t.text:
                            parts.append(t.text)
                    shared.append("".join(parts))

            # Read first sheet
            sheet_xml = z.read("xl/worksheets/sheet1.xml")
            sheet_root = ET.fromstring(sheet_xml)
            ns = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
            rows = []
            for row_el in sheet_root.iter(f"{{{ns}}}row"):
                cells = []
                for c in row_el.iter(f"{{{ns}}}c"):
                    val_el = c.find(f"{{{ns}}}v")
                    val = val_el.text if val_el is not None else ""
                    cell_type = c.get("t", "")
                    if cell_type == "s" and val:
                        idx = int(val)
                        val = shared[idx] if idx < len(shared) else val
                    elif cell_type == "inlineStr":
                        val = "".join(t.text or "" for t in c.iter(f"{{{ns}}}t"))
                    cells.append(val or "")
                rows.append(" | ".join(cells))
                if len(rows) >= 100:
                    rows.append("[... truncated at 100 rows]")
                    break
            return "\n".join(rows) if rows else "Empty spreadsheet"
    except Exception as e:
        return ToolOutput.failure(f"Error reading .xlsx: {e}")


def _read_pdf(path: Path) -> str:
    """Extract text from PDF using pdfplumber (pure Python, self-contained)."""
    try:
        import pdfplumber
        with pdfplumber.open(path) as pdf:
            text = ""
            for page in pdf.pages:
                extracted = page.extract_text()
                if extracted:
                    text += extracted + "\n"
            text = text.strip()
            text = re.sub(r'\(cid:\d+\)\s*', '', text)
            if len(text) > 15000:
                return text[:15000] + "\n[... truncated]"
            return text if text else "PDF is empty or has no extractable text"
    except ImportError:
        return ToolOutput.failure("PDF extraction requires pdfplumber (should be installed)")
    except Exception as e:
        return ToolOutput.failure(f"Error reading PDF: {e}")


def _read_pptx(path: Path, max_chars: int = 6000) -> str:
    """Extract text from .pptx using pure zipfile + XML (no external deps)."""
    try:
        import re
        with zipfile.ZipFile(str(path), "r") as z:
            slide_files = sorted(
                [n for n in z.namelist() if n.startswith("ppt/slides/slide") and n.endswith(".xml")]
            )
            parts = []
            for slide_name in slide_files:
                xml_data = z.read(slide_name)
                root = ET.fromstring(xml_data)
                texts = []
                for t in root.iter("{http://schemas.openxmlformats.org/drawingml/2006/main}t"):
                    if t.text and t.text.strip():
                        texts.append(t.text.strip())
                if texts:
                    parts.append(" ".join(texts))
            text = "\n".join(parts).strip()
            if not text:
                return "Presentation has no extractable text"
            return text[:max_chars] + "\n[... truncated]" if len(text) > max_chars else text
    except Exception as e:
        return ToolOutput.failure(f"Error reading .pptx: {e}")


def _read_pptx_full(path: Path, max_chars: int = 25_000) -> str:
    return _read_pptx(path, max_chars)


def _read_archive(path: Path) -> str:
    try:
        ext = path.suffix.lower()
        if ext == ".zip":
            with zipfile.ZipFile(str(path), "r") as z:
                names = z.namelist()[:100]
                return f"Archive contents ({len(z.namelist())} files):\n" + "\n".join(names)
        elif ext in (".tar", ".gz", ".bz2", ".tgz"):
            mode = "r:gz" if ext in (".gz", ".tgz") else "r:bz2" if ext == ".bz2" else "r"
            with tarfile.open(str(path), mode) as t:
                names = t.getnames()[:100]
                return f"Archive contents ({len(t.getnames())} files):\n" + "\n".join(names)
    except Exception as e:
        return ToolOutput.failure(f"Error reading archive: {e}")
    return f"[Archive: {path.name}]"


def read_attached_file(path: str) -> str:
    """Read content from an attached file based on its type."""
    p = Path(path)
    if not p.exists():
        return ToolOutput.failure(f"File not found: {path}")

    ext = p.suffix.lower()
    size = p.stat().st_size

    if ext == ".csv":
        return _read_csv_file(p)
    elif ext in _TEXT_EXTENSIONS:
        return _read_text_file(p)
    elif ext == ".pdf":
        return _read_pdf(p)
    elif ext == ".docx":
        return _read_docx(p)
    elif ext == ".xlsx":
        return _read_xlsx(p)
    elif ext == ".pptx":
        return _read_pptx(p)
    elif ext in _IMAGE_EXTENSIONS:
        return ToolOutput.failure(f"[Image file: {p.name}, {size} bytes — vision analysis not available in this version]")
    elif ext in (".zip", ".tar", ".gz", ".bz2", ".tgz", ".7z"):
        if ext == ".7z":
            return ToolOutput.failure(f"[7z archive: {p.name} — extraction requires 7-Zip]")
        return _read_archive(p)
    else:
        return ToolOutput.failure(f"[Binary file: {p.name} — cannot analyze content]")


def _read_full(path: Path, max_chars: int = 25_000) -> str:
    """Read as much of a file as possible for summarization purposes."""
    ext = path.suffix.lower()
    if ext == ".csv":
        return _read_csv_file(path)
    if ext in _TEXT_EXTENSIONS:
        try:
            with path.open(encoding="utf-8", errors="replace") as source:
                return source.read(max_chars)
        except Exception as e:
            return ToolOutput.failure(f"Error reading: {e}")
    if ext == ".pdf":
        return _read_pdf_full(path, max_chars)
    if ext == ".docx":
        return _read_docx_full(path, max_chars)
    if ext == ".xlsx":
        return _read_xlsx(path)
    if ext == ".pptx":
        return _read_pptx_full(path, max_chars)
    return ""


def _read_pdf_full(path: Path, max_chars: int = 25_000) -> str:
    """Extract text from PDF with a larger character budget using pdfplumber."""
    try:
        import pdfplumber
        with pdfplumber.open(path) as pdf:
            page_count = len(pdf.pages)
            text = ""
            for page in pdf.pages:
                extracted = page.extract_text()
                if extracted:
                    text += extracted + "\n"
                page.close()
                if len(text) >= max_chars:
                    break
            text = text.strip()
            # Clean unresolved PDF glyph markers (e.g. (cid:127))
            text = re.sub(r'\(cid:\d+\)\s*', '', text)
            if not text and page_count > 0:
                plural = "s" if page_count != 1 else ""
                return f"[Image PDF: {page_count} page{plural} — scanned document, no extractable text]"
            return text[:max_chars] if len(text) > max_chars else text
    except ImportError:
        return ToolOutput.failure("PDF extraction requires pdfplumber")
    except Exception as e:
        return ToolOutput.failure(f"Error reading PDF: {e}")


def _read_docx_full(path: Path, max_chars: int = 25_000) -> str:
    """Extract text from .docx with a larger character budget."""
    try:
        with zipfile.ZipFile(str(path), "r") as z:
            if "word/document.xml" not in z.namelist():
                return ToolOutput.failure("Could not find document.xml in .docx")
            xml_data = z.read("word/document.xml")
        root = ET.fromstring(xml_data)
        paragraphs = []
        for para in root.iter("{http://schemas.openxmlformats.org/wordprocessingml/2006/main}p"):
            texts = []
            for t in para.iter("{http://schemas.openxmlformats.org/wordprocessingml/2006/main}t"):
                if t.text:
                    texts.append(t.text)
            if texts:
                paragraphs.append("".join(texts))
        text = "\n".join(paragraphs)
        return text[:max_chars] if len(text) > max_chars else text or "Empty document"
    except Exception as e:
        return ToolOutput.failure(f"Error reading .docx: {e}")


def _sample_for_llm(content: str, budget: int = 3_400) -> tuple[str, bool]:
    """Return (snippet, was_sampled).

    If content fits in budget, returns it whole.
    Otherwise samples start (60%) + middle (25%) + end (15%) so the LLM
    sees representative sections across the entire document.
    """
    if len(content) <= budget:
        return content, False

    start_n  = int(budget * 0.60)
    mid_n    = int(budget * 0.25)
    end_n    = budget - start_n - mid_n

    start  = content[:start_n]
    mid_off = max(start_n, len(content) // 2 - mid_n // 2)
    middle = content[mid_off: mid_off + mid_n]
    end    = content[-end_n:] if end_n > 0 else ""

    sampled = (
        start
        + f"\n\n[— middle section —]\n\n"
        + middle
        + (f"\n\n[— near end —]\n\n" + end if end else "")
    )
    return sampled, True


_CHUNK_SIZE    = 10_000   # chars per chunk (~2 500 tokens)
_CHUNK_OVERLAP = 400      # overlap to avoid cutting mid-sentence
_CHUNK_SUMMARY_TOKENS  = 120
_SYNTHESIS_TOKENS      = 160
_SYNTHESIS_INPUT_CAP   = 6_000   # max chars of section summaries fed to synthesis


def _chunked_summarize(content: str, filename: str, word_count: int,
                       llm_client) -> str:
    """Multi-pass summarization for long documents.

    Pass 1: summarize each ~10 000-char chunk in 3-5 sentences.
    Pass 2: synthesize all section summaries into a coherent overview.
    Failure propagates to summarize_file, which selects a source-only fallback.
    """
    # Build non-overlapping chunks with a small overlap for continuity
    step = _CHUNK_SIZE - _CHUNK_OVERLAP
    chunks = [
        content[i: i + _CHUNK_SIZE]
        for i in range(0, len(content), step)
        if content[i: i + _CHUNK_SIZE].strip()
    ]
    if not chunks:
        return _extractive_summary(content) if content.strip() else "Document appears to be empty."

    total = len(chunks)

    # --- Pass 1: per-chunk section summaries ---
    section_summaries: list[str] = []
    for idx, chunk in enumerate(chunks):
        raw = llm_client.chat(
            [
                {"role": "system",
                 "content": "You are a document summarizer. Reply with a concise summary of the passage only — no preamble."},
                {"role": "user",
                 "content": (
                     f"Document: {filename}  (section {idx + 1} of {total})\n\n"
                     f"{chunk}\n\n"
                     "Summarize this section in 2-3 clear sentences."
                 )},
            ],
            max_tokens=_CHUNK_SUMMARY_TOKENS,
        )
        if not raw or not raw.strip():
            raise ValueError("Empty section summary")
        section_summaries.append(raw.strip())

    if not section_summaries:
        return _extractive_summary(content)

    # Single-chunk doc — the one summary is the final answer
    if total == 1:
        return section_summaries[0]

    # --- Pass 2: synthesise section summaries into a full overview ---
    numbered = "\n\n".join(
        f"[Section {i + 1}] {s}" for i, s in enumerate(section_summaries)
    )
    if len(numbered) > _SYNTHESIS_INPUT_CAP:
        raise ValueError("Section summaries exceed the legacy synthesis budget")

    synthesis = llm_client.chat(
        [
            {"role": "system",
             "content": "You are a document summarizer. Write a concise, factual summary."},
            {"role": "user",
             "content": (
                 f"The document '{filename}' (~{word_count:,} words) was split into "
                 f"{total} sections. Section summaries:\n\n{numbered}\n\n"
                 "Write a concise overview of the full document in 2-3 sentences."
             )},
        ],
        max_tokens=_SYNTHESIS_TOKENS,
    )
    if not synthesis or not synthesis.strip():
        raise ValueError("Empty synthesized summary")
    return synthesis.strip()


_INLINE_SUMMARY_RX = re.compile(
    r'^(?:Executive\s+)?(?:Summary|Abstract|Overview|Introduction)\s*[—\-:\.]\s*',
    re.IGNORECASE,
)
_SUMMARY_HEADING_RX = re.compile(
    r'^(?:Executive\s+)?(?:Summary|Abstract|Overview|Introduction)\s*$',
    re.IGNORECASE,
)
_HEADING_LINE_RX = re.compile(
    r'^(?:'
    r'[A-Z][A-Z \d&:,\-]{4,}$'
    r'|(?:Phase|Section|Chapter|Part|Step|Item)\b'
    r'|\d+[\.\)]\s+\S'
    r'|#{1,4}\s+\S'
    r'|[A-Z][\w\s]+[:\-]\s*$'
    r')'
)


def _extractive_summary(content: str, max_sentences: int = 8) -> str:
    """Return a multi-sentence paragraph covering the full document.

    Strategy:
    1. Pull 2-3 sentences from the executive summary / abstract (inline marker).
    2. Walk the remaining sections; contribute one key sentence per section.
    3. Fallback: first substantive sentences, skipping short title fragments.

    Result is up to max_sentences sentences joined into a single paragraph.
    """
    # Pre-clean unresolved PDF glyph markers that may survive through _read_full
    lines = [re.sub(r'\(cid:\d+\)\s*', '', line).strip()
             for line in content.splitlines()]

    collected: list[str] = []
    summary_end_idx = -1

    # --- Pass 1: inline summary / abstract marker ---
    for i, line in enumerate(lines):
        if not line:
            continue
        m = _INLINE_SUMMARY_RX.match(line)
        if not m:
            continue
        parts = [line[m.end():].strip()]
        summary_end_idx = i
        for j, cont in enumerate(lines[i + 1:], i + 1):
            if not cont:
                summary_end_idx = j
                break
            if _HEADING_LINE_RX.match(cont) and len(cont) < 120:
                summary_end_idx = j
                break
            parts.append(cont)
            summary_end_idx = j
        body = " ".join(p for p in parts if p)
        if len(body.split()) >= 6:
            collected.extend(_split_sentences(body)[:3])
        break

    # --- Pass 2: standalone "Summary" / "Abstract" heading block ---
    if not collected:
        in_block, block = False, []
        for i, line in enumerate(lines):
            if not line:
                continue
            if _SUMMARY_HEADING_RX.match(line):
                in_block, block, summary_end_idx = True, [], i
                continue
            if in_block:
                if _HEADING_LINE_RX.match(line) and len(line) < 120:
                    break
                block.append(line)
                summary_end_idx = i
        if block:
            collected.extend(_split_sentences(" ".join(block))[:3])

    # --- Pass 3: one key sentence per section heading (after the intro) ---
    past_summary = summary_end_idx < 0
    current_body: list[str] = []

    for i, line in enumerate(lines):
        if not past_summary:
            if i > summary_end_idx:
                past_summary = True
            else:
                continue
        if not line:
            continue
        is_heading = bool(_HEADING_LINE_RX.match(line)) and len(line) < 120
        if is_heading:
            if current_body and len(collected) < max_sentences:
                sents = _split_sentences(" ".join(current_body))
                for s in sents:
                    if len(s.split()) >= 6:
                        collected.append(s)
                        break
            current_body = []
        else:
            current_body.append(line)

    # Last section's body
    if current_body and len(collected) < max_sentences:
        sents = _split_sentences(" ".join(current_body))
        for s in sents:
            if len(s.split()) >= 6:
                collected.append(s)
                break

    if collected:
        return " ".join(collected[:max_sentences])

    # --- Fallback: skip short title/author fragments ---
    substantive = [l for l in lines if l and len(l.split()) >= 8]
    text = " ".join(substantive) if substantive else " ".join(l for l in lines if l)
    sents = _split_sentences(text)
    return " ".join(sents[:4]) if sents else content[:500]


def _split_sentences(text: str) -> list[str]:
    """Split text into sentences, keeping only those with 4+ words."""
    parts = re.split(r'(?<=[.!?])\s+', text)
    return [s.strip() for s in parts if len(s.split()) >= 4]


def summarize_file(path: str, llm_client=None) -> str:
    """Read a file at the given path and return a summary.

    Managed clients split text using the model's tokenizer and synthesize
    section summaries as needed. Legacy clients use character-based chunks.
    Falls back to selected source sentences when generation is unavailable
    or incomplete.
    """
    p = Path(path)
    if not p.exists():
        return None  # caller handles "not found"

    # Read more content upfront so chunked summarization has enough material
    if not p.is_file():
        return ToolOutput.failure(f"Not a document file: {p.name}")
    if p.suffix.lower() not in _TEXT_EXTENSIONS | {".pdf", ".docx", ".xlsx", ".pptx"}:
        return ToolOutput.failure(f"Cannot extract text from {p.name}: unsupported document format.", status="unsupported")
    content = _read_full(p, max_chars=80_001)

    if not content or not content.strip():
        return ToolOutput.failure(f"The file {p.name} appears to be empty.")
    if content.startswith("[Binary"):
        return ToolOutput.failure(f"Cannot extract text from {p.name} — binary file.", status="unsupported")
    if content.startswith("[Image PDF:"):
        return ToolOutput.failure(
            f"{p.name}: {content}. "
            "No summary was created. OCR software is required to read scanned documents.",
            status="unsupported",
        )
    if isinstance(content, ToolOutput) and not content.success:
        return content
    if content.startswith("Error reading PDF"):
        return ToolOutput.failure(f"Could not extract text from {p.name}: {content}")
    if content.startswith("Error reading"):
        return ToolOutput.failure(content)
    if content.startswith(("PDF extraction requires", "Could not find document.xml")):
        return ToolOutput.failure(f"Could not extract text from {p.name}: {content}")
    if content in ("Empty document", "Empty spreadsheet", "Presentation has no extractable text"):
        return ToolOutput.failure(f"The file {p.name} has no extractable text.")
    if not any(character.isalnum() for character in content):
        return ToolOutput.failure(f"The file {p.name} has no readable text to summarize.")
    source_limited = len(content) > 80_000
    content = content[:80_000]

    size = p.stat().st_size
    size_str = f"{size // 1024} KB" if size >= 1024 else f"{size} B"
    word_count = len(content.split())
    header = f"Here is a summary of {p.name} ({size_str}, ~{word_count:,} words):"
    if source_limited:
        header += "\nSource limited to the first 80,000 extracted characters."

    if llm_client:
        try:
            if callable(getattr(llm_client, "summarize_document", None)):
                summary = llm_client.summarize_document(content, p.name)
            elif len(content) <= 8_000:
                # Single pass: the whole text fits comfortably
                summary = llm_client.chat(
                    [
                        {"role": "system",
                         "content": "You are a document summarizer. Reply with one clear summary paragraph only — no preamble, no bullet points."},
                        {"role": "user",
                         "content": (
                             f"Write a concise summary of this document. "
                             f"Describe what it covers, its main points, and any notable details.\n\n"
                             f"File: {p.name}\n\n{content}"
                         )},
                    ],
                    max_tokens=600,
                )
            else:
                # Multi-pass chunked summarization for large documents
                summary = _chunked_summarize(content, p.name, word_count, llm_client)

            if summary and summary.strip():
                return ToolOutput(f"{header}\n\n{summary.strip()}",
                                  data={"method": "model", "path": str(p), "source_limited": source_limited})
        except Exception as exc:
            logger.warning("Using extractive summary for %s: %s", p.name, exc)

    # Fallback: structure-aware extractive summary (no LLM or LLM failed)
    extract = _extractive_summary(content)
    return ToolOutput(f"{header}\n\nExtractive summary (selected source sentences):\n{extract}",
                      status="fallback", message="Model summarization was unavailable or incomplete. Here are selected sentences from the source.",
                      data={"method": "extractive", "path": str(p), "source_limited": source_limited})


def find_similar_files(filename: str, search_roots: list = None) -> list:
    """Return up to 3 files whose names closely match `filename`."""
    import difflib

    stem = Path(filename).stem.lower()
    ext  = Path(filename).suffix.lower()

    if not search_roots:
        home = Path.home()
        search_roots = [
            home / "Desktop", home / "Downloads", home / "Documents",
            home / "Pictures", home / "Videos", home,
        ]
    else:
        search_roots = [Path(r) for r in search_roots]

    candidates = []
    pattern = f"*{ext}" if ext else "*"
    for root in search_roots:
        if not root.exists():
            continue
        try:
            for p in root.rglob(pattern):
                candidates.append(p)
                if len(candidates) >= 300:
                    break
        except (PermissionError, OSError):
            pass
        if len(candidates) >= 300:
            break

    scored = [
        (difflib.SequenceMatcher(None, stem, c.stem.lower()).ratio(), c)
        for c in candidates
    ]
    scored.sort(key=lambda x: -x[0])
    return [str(c) for score, c in scored[:3] if score >= 0.4]


def analyze_attached_files(file_paths: list[str], user_message: str,
                           llm_client=None) -> str:
    """Analyze multiple attached files and build a combined prompt."""
    sections = []
    for fp in file_paths:
        content = read_attached_file(fp)
        name = Path(fp).name
        sections.append(f"=== FILE: {name} ===\n{content}")

    combined = "\n\n".join(sections)
    # Truncate total to 6000 chars
    if len(combined) > 6000:
        combined = combined[:6000] + "\n[... truncated]"

    prompt = (
        f"The user attached {len(file_paths)} file(s) and asks: '{user_message}'\n\n"
        + combined
    )

    if llm_client:
        try:
            response = llm_client.chat([
                {"role": "system", "content": "Analyze the attached files and answer the user's question concisely."},
                {"role": "user", "content": prompt},
            ])
            return response
        except Exception as e:
            return ToolOutput.failure(f"Analysis error: {e}\n\nRaw file contents:\n{combined}")

    return combined


# ---------------------------------------------------------------------------
# Content search / diff / pattern extraction
# ---------------------------------------------------------------------------

def grep_file(path: str, pattern: str, context_lines: int = 2,
              case_sensitive: bool = False) -> str:
    """Search for a pattern inside a text file and return matching lines with context."""
    p = Path(path)
    if not p.exists():
        return ToolOutput.failure(f"File not found: {path}")
    try:
        text = p.read_text(encoding="utf-8", errors="replace")
    except Exception as e:
        return ToolOutput.failure(f"Could not read file: {e}")

    lines = text.splitlines()
    flags = 0 if case_sensitive else re.IGNORECASE
    try:
        rx = re.compile(pattern, flags)
    except re.error as e:
        return ToolOutput.failure(f"Invalid pattern: {e}")

    hits = []
    for i, line in enumerate(lines):
        if rx.search(line):
            start = max(0, i - context_lines)
            end   = min(len(lines), i + context_lines + 1)
            block = []
            for j in range(start, end):
                prefix = ">> " if j == i else "   "
                block.append(f"{j+1:4d}{prefix}{lines[j]}")
            hits.append("\n".join(block))
        if len(hits) >= 50:
            hits.append("[... more matches truncated]")
            break

    if not hits:
        return f"No matches for '{pattern}' in {p.name}."
    header = f"{len(hits)} match(es) for '{pattern}' in {p.name}:\n"
    return header + "\n---\n".join(hits)


def diff_files(path_a: str, path_b: str) -> str:
    """Show a unified diff between two text files."""
    pa, pb = Path(path_a), Path(path_b)
    for p in (pa, pb):
        if not p.exists():
            return ToolOutput.failure(f"File not found: {p}")

    try:
        lines_a = pa.read_text(encoding="utf-8", errors="replace").splitlines(keepends=True)
        lines_b = pb.read_text(encoding="utf-8", errors="replace").splitlines(keepends=True)
    except Exception as e:
        return ToolOutput.failure(f"Could not read files: {e}")

    diff = list(difflib.unified_diff(
        lines_a, lines_b,
        fromfile=pa.name, tofile=pb.name,
        n=3,
    ))
    if not diff:
        return f"Files are identical: {pa.name} and {pb.name}"
    result = "".join(diff)
    if len(result) > 8000:
        result = result[:8000] + "\n[... diff truncated]"
    return result


_PATTERN_REGEXES = {
    "email":   r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}",
    "url":     r"https?://[^\s\"'<>]+",
    "phone":   r"(?:\+?\d[\d\s\-\(\)]{7,}\d)",
    "date":    r"\b(?:\d{4}[-/]\d{2}[-/]\d{2}|\d{2}[-/]\d{2}[-/]\d{4})\b",
    "ipv4":    r"\b(?:\d{1,3}\.){3}\d{1,3}\b",
}


def extract_patterns(path: str, pattern_types: list[str] = None) -> dict:
    """Extract emails, URLs, phone numbers, dates, IPs from a text file."""
    p = Path(path)
    if not p.exists():
        return {"error": f"File not found: {path}"}
    try:
        text = p.read_text(encoding="utf-8", errors="replace")
    except Exception as e:
        return {"error": f"Could not read file: {e}"}

    types_to_check = pattern_types or list(_PATTERN_REGEXES.keys())
    results = {}
    for pt in types_to_check:
        rx_str = _PATTERN_REGEXES.get(pt)
        if not rx_str:
            continue
        matches = list(dict.fromkeys(re.findall(rx_str, text, re.IGNORECASE)))
        results[pt] = matches[:100]

    total = sum(len(v) for v in results.values())
    results["_summary"] = f"Found {total} pattern(s) across {len(results)-1} type(s) in {p.name}."
    return results


# ---------------------------------------------------------------------------
# Batch folder reading and folder description
# ---------------------------------------------------------------------------

_READABLE_EXTENSIONS = _TEXT_EXTENSIONS | {
    ".pdf", ".docx", ".xlsx", ".pptx", ".csv",
}


def batch_read_folder(folder: str, extensions: list = None,
                      max_chars_per_file: int = 4000,
                      max_total_chars: int = 40000) -> dict:
    """Read all readable documents in a folder.

    Returns:
        {"files": [{"name": str, "text": str}], "total_files": int,
         "skipped": [str], "total_chars": int}
    Only processes files at the top level of the folder (no recursion),
    to keep results focused.
    """
    root = Path(folder)
    if not root.exists():
        return {"error": f"Not found: {folder}"}
    if not root.is_dir():
        return {"error": f"Not a directory: {folder}"}

    filter_exts = _READABLE_EXTENSIONS
    if extensions:
        filter_exts = {f".{e.lstrip('.')}" for e in extensions}

    files_out = []
    skipped = []
    total_chars = 0

    for p in sorted(root.iterdir()):
        if not p.is_file():
            continue
        if p.suffix.lower() not in filter_exts:
            skipped.append(p.name)
            continue
        if total_chars >= max_total_chars:
            skipped.append(f"{p.name} (budget exhausted)")
            continue
        try:
            text = _read_full(p, max_chars=max_chars_per_file)
        except Exception as e:
            skipped.append(f"{p.name} (read error: {e})")
            continue
        if not text or text.startswith("[Binary") or text.startswith("[Image"):
            skipped.append(f"{p.name} (not readable)")
            continue
        remaining = max_total_chars - total_chars
        if len(text) > remaining:
            text = text[:remaining] + "\n[... truncated]"
        files_out.append({"name": p.name, "text": text})
        total_chars += len(text)

    return {
        "files": files_out,
        "total_files": len(files_out),
        "skipped": skipped,
        "total_chars": total_chars,
    }


def folder_explain(folder: str) -> dict:
    """Describe what a folder contains: file counts by type, total size, largest files."""
    root = Path(folder)
    if not root.exists():
        return {"error": f"Not found: {folder}"}
    if not root.is_dir():
        return {"error": f"Not a directory: {folder}"}

    _type_map = {
        "PDF": {".pdf"},
        "Documents": {".docx", ".doc", ".odt", ".rtf", ".txt", ".md"},
        "Spreadsheets": {".xlsx", ".xls", ".csv", ".ods"},
        "Presentations": {".pptx", ".ppt"},
        "Images": {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".svg", ".webp", ".tiff"},
        "Code": {".py", ".js", ".ts", ".java", ".c", ".cpp", ".h", ".rs",
                 ".go", ".html", ".css", ".sh", ".rb", ".ps1", ".bat"},
        "Archives": {".zip", ".tar", ".gz", ".bz2", ".7z", ".rar"},
        "Videos": {".mp4", ".mkv", ".avi", ".mov", ".wmv"},
        "Audio": {".mp3", ".wav", ".flac", ".aac", ".ogg"},
    }

    counts: dict[str, int] = {}
    total_size = 0
    file_list = []

    try:
        for p in root.iterdir():
            if not p.is_file():
                continue
            try:
                size = p.stat().st_size
            except OSError:
                size = 0
            total_size += size
            ext = p.suffix.lower()
            category = "Other"
            for cat, exts in _type_map.items():
                if ext in exts:
                    category = cat
                    break
            counts[category] = counts.get(category, 0) + 1
            file_list.append({"name": p.name, "size": size, "category": category})
    except PermissionError as e:
        return {"error": f"Permission denied: {e}"}

    file_list.sort(key=lambda x: -x["size"])

    def _hsize(b: int) -> str:
        if b < 1024:
            return f"{b} B"
        if b < 1024 * 1024:
            return f"{b / 1024:.1f} KB"
        return f"{b / (1024 * 1024):.1f} MB"

    return {
        "folder": folder,
        "total_files": len(file_list),
        "total_size": _hsize(total_size),
        "by_type": counts,
        "largest_files": [
            {"name": f["name"], "size": _hsize(f["size"])}
            for f in file_list[:5]
        ],
    }


def grep_folder(folder: str, pattern: str, extensions: list = None,
                case_sensitive: bool = False, max_files: int = 50,
                max_snippets_per_file: int = 3) -> dict:
    """Search for text or a regex pattern across all readable files in a folder.

    Only searches top-level files (no recursion), consistent with batch_read_folder.
    Extracts text from PDFs, DOCX, TXT, CSV, XLSX, PPTX etc. before searching.

    Returns:
        {
            "matches": [{"path": str, "name": str, "snippets": [str, ...]}],
            "searched": int,   # files examined (text extracted + searched)
            "matched": int,    # files that contained at least one match
        }
    On folder error: {"error": str, "matches": [], "searched": 0, "matched": 0}
    """
    import re

    root = Path(folder)
    if not root.exists():
        return {
            "error": f"Folder not found: {folder}",
            "matches": [], "searched": 0, "matched": 0,
        }
    if not root.is_dir():
        return {
            "error": f"Not a directory: {folder}",
            "matches": [], "searched": 0, "matched": 0,
        }

    # Resolve extension filter
    if extensions:
        ext_set = {("." + e.lstrip(".")).lower() for e in extensions}
    else:
        ext_set = _READABLE_EXTENSIONS

    # Compile search regex; treat the pattern as a literal if it is not valid regex
    flags = 0 if case_sensitive else re.IGNORECASE
    try:
        compiled = re.compile(pattern, flags)
    except re.error:
        compiled = re.compile(re.escape(pattern), flags)

    # Collect candidate files sorted alphabetically for deterministic results
    candidates = sorted(
        (p for p in root.iterdir() if p.is_file() and p.suffix.lower() in ext_set),
        key=lambda p: p.name.lower(),
    )

    searched = 0
    matches = []

    for p in candidates[:max_files]:
        # Extract readable text from the file
        try:
            text = _read_full(p, max_chars=20000)
        except Exception:
            continue

        # Skip files whose text extraction failed or returned a non-text sentinel
        if not text:
            continue
        text_lower = text[:60].lower()
        if (text_lower.startswith("[binary")
                or text_lower.startswith("[image")
                or text_lower.startswith("error reading")):
            continue

        searched += 1

        # Search line-by-line to collect context snippets
        lines = text.splitlines()
        snippets: list[str] = []

        for i, line in enumerate(lines):
            if not compiled.search(line):
                continue
            # Build a short snippet: one line of context before and after the match
            start = max(0, i - 1)
            end = min(len(lines), i + 2)
            snippet_parts = [ln.strip() for ln in lines[start:end] if ln.strip()]
            snippet = " … ".join(snippet_parts)
            if len(snippet) > 220:
                snippet = snippet[:220] + "…"
            if snippet:
                snippets.append(snippet)
            if len(snippets) >= max_snippets_per_file:
                break

        if snippets:
            matches.append({
                "path": str(p),
                "name": p.name,
                "snippets": snippets,
            })

    return {
        "matches": matches,
        "searched": searched,
        "matched": len(matches),
    }
