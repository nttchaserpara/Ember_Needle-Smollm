"""File management operations for EmberOS-Windows."""

import hashlib
import logging
import os
import shutil
import tarfile
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from emberos.outcomes import ToolOutput

logger = logging.getLogger("emberos.use_cases.file_ops")

_TEXT_EXTENSIONS = {
    ".txt", ".py", ".js", ".ts", ".json", ".csv", ".log", ".md", ".html",
    ".xml", ".yaml", ".yml", ".toml", ".cfg", ".ini", ".bat", ".ps1",
    ".c", ".cpp", ".h", ".rs", ".go", ".java", ".css", ".sh", ".rb",
}

_FILE_CATEGORIES = {
    "PDFs": {".pdf"},
    "Images": {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".svg", ".webp", ".tiff"},
    "Documents": {".doc", ".docx", ".txt", ".md", ".odt", ".rtf"},
    "Spreadsheets": {".xls", ".xlsx", ".csv", ".ods"},
    "Videos": {".mp4", ".mkv", ".avi", ".mov", ".wmv"},
    "Audio": {".mp3", ".wav", ".flac", ".aac", ".ogg"},
    "Archives": {".zip", ".tar", ".gz", ".bz2", ".7z", ".rar"},
    "Software": {".exe", ".msi", ".msix", ".appx", ".appxbundle"},
    "Code": {".py", ".js", ".ts", ".java", ".c", ".cpp", ".h", ".rs", ".go", ".html", ".css"},
}

_TYPE_EXTENSIONS = {
    "pdf": {".pdf"},
    "image": {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".svg", ".webp", ".tiff"},
    "document": {".doc", ".docx", ".txt", ".md", ".odt", ".rtf", ".pdf"},
    "spreadsheet": {".xls", ".xlsx", ".csv", ".ods"},
    "video": {".mp4", ".mkv", ".avi", ".mov", ".wmv"},
    "audio": {".mp3", ".wav", ".flac", ".aac", ".ogg"},
    "archive": {".zip", ".tar", ".gz", ".bz2", ".7z", ".rar"},
    "code": {".py", ".js", ".ts", ".java", ".c", ".cpp", ".h", ".rs", ".go", ".html", ".css"},
}


def _human_size(size_bytes: int) -> str:
    if size_bytes < 1024:
        return f"{size_bytes} B"
    elif size_bytes < 1024 * 1024:
        return f"{size_bytes / 1024:.1f} KB"
    elif size_bytes < 1024 * 1024 * 1024:
        return f"{size_bytes / (1024 * 1024):.1f} MB"
    else:
        return f"{size_bytes / (1024 * 1024 * 1024):.1f} GB"


def find_files(query: str, search_root: str = None, file_type: str = None,
               modified_within_days: int = None) -> list[str]:
    root = Path(search_root) if search_root else Path.home()
    if not root.exists():
        return []

    cutoff = None
    if modified_within_days:
        cutoff = datetime.now().timestamp() - (modified_within_days * 86400)

    ext_filter = None
    if file_type:
        ft = file_type.lower()
        if ft in _TYPE_EXTENSIONS:
            ext_filter = _TYPE_EXTENSIONS[ft]
        elif not ft.startswith("."):
            ext_filter = {f".{ft}"}
        else:
            ext_filter = {ft}

    results = []
    query_lower = query.lower()
    try:
        for p in root.rglob("*"):
            if len(results) >= 50:
                break
            if not p.is_file():
                continue
            if ext_filter and p.suffix.lower() not in ext_filter:
                continue
            if cutoff:
                try:
                    if os.path.getmtime(str(p)) < cutoff:
                        continue
                except OSError:
                    continue
            if query_lower and query_lower not in p.name.lower():
                continue
            results.append(str(p))
    except PermissionError:
        pass
    return results


def read_file_contents(path: str) -> str:
    p = Path(path)
    if not p.exists():
        return ToolOutput.failure(f"File not found: {path}")
    if not p.is_file():
        return ToolOutput.failure(f"Not a file: {path}")
    if p.suffix.lower() in _TEXT_EXTENSIONS:
        try:
            text = p.read_text(encoding="utf-8", errors="replace")
            if len(text) > 8000:
                return text[:8000] + "\n[... truncated]"
            return text
        except Exception as e:
            return ToolOutput.failure(f"Error reading file: {e}")
    return ToolOutput.failure(f"Binary or unreadable file: {p.name}")


def get_file_info(path: str) -> dict:
    p = Path(path)
    if not p.exists():
        return {"error": f"Not found: {path}"}
    stat = p.stat()
    return {
        "name": p.name,
        "size_bytes": stat.st_size,
        "size_human": _human_size(stat.st_size),
        "modified": datetime.fromtimestamp(stat.st_mtime).isoformat(),
        "created": datetime.fromtimestamp(stat.st_ctime).isoformat(),
        "extension": p.suffix,
        "is_dir": p.is_dir(),
        "permissions": oct(stat.st_mode),
    }


def create_directory(path: str) -> str:
    p = Path(path)
    p.mkdir(parents=True, exist_ok=True)
    return f"Directory created: {p.resolve()}"


def move_file(src: str, dst: str, snapshot_mgr=None) -> str:
    s = Path(src)
    if not s.exists():
        return ToolOutput.failure(f"Source not found: {src}")
    if snapshot_mgr:
        snapshot_mgr.snapshot_file(src, "move")
    shutil.move(str(s), dst)
    return f"Moved: {src} → {dst}"


def copy_file(src: str, dst: str) -> str:
    s = Path(src)
    if not s.exists():
        return ToolOutput.failure(f"Source not found: {src}")
    if s.is_dir():
        shutil.copytree(str(s), dst)
    else:
        Path(dst).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(str(s), dst)
    return f"Copied: {src} → {dst}"


def rename_file(path: str, new_name: str, snapshot_mgr=None) -> str:
    p = Path(path)
    if not p.exists():
        return ToolOutput.failure(f"Not found: {path}")
    if snapshot_mgr:
        snapshot_mgr.snapshot_file(path, "rename")
    new_path = p.parent / new_name
    p.rename(new_path)
    return f"Renamed: {p.name} → {new_name}"


def delete_file(path: str, snapshot_mgr=None) -> str:
    p = Path(path)
    if not p.exists():
        return ToolOutput.failure(f"Not found: {path}")
    if snapshot_mgr:
        snapshot_mgr.snapshot_file(path, "delete")
    if p.is_dir():
        shutil.rmtree(str(p))
        return f"Deleted directory: {path}"
    else:
        os.remove(str(p))
        return f"Deleted: {path}"


def organize_folder_by_type(folder: str, preview_only: bool = True,
                            snapshot_mgr=None) -> dict:
    root = Path(folder)
    if not root.exists() or not root.is_dir():
        return {"error": f"Not a directory: {folder}"}

    groups = {}
    for f in root.iterdir():
        if not f.is_file():
            continue
        ext = f.suffix.lower()
        category = "Other"
        for cat, exts in _FILE_CATEGORIES.items():
            if ext in exts:
                category = cat
                break
        groups.setdefault(category, []).append(f)

    summary = {cat: len(files) for cat, files in groups.items()}

    if preview_only:
        return {"preview": summary, "total_files": sum(summary.values())}

    moved = {}
    for category, files in groups.items():
        cat_dir = root / category
        cat_dir.mkdir(exist_ok=True)
        count = 0
        for f in files:
            dst = cat_dir / f.name
            if snapshot_mgr:
                snapshot_mgr.snapshot_file(str(f), "organize")
            shutil.move(str(f), str(dst))
            count += 1
        moved[category] = count
    return {"moved": moved, "total_moved": sum(moved.values())}


# ---------------------------------------------------------------------------
# Smart content-aware organization
# ---------------------------------------------------------------------------

_IMAGE_EXTENSIONS = {
    ".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp", ".tiff", ".svg",
}

_DOC_EXTENSIONS = {
    ".pdf", ".docx", ".doc", ".txt", ".md", ".rtf", ".odt",
    ".csv", ".xls", ".xlsx", ".pptx", ".ppt",
}


def _group_images_by_filename(image_files: list) -> dict:
    """Group image Path objects by common meaningful words in their filenames."""
    import re

    def _tokens(stem: str) -> list:
        # Strip date patterns and pure numbers, then split on separators
        s = re.sub(r'\b\d{4}[\-_]?\d{2}[\-_]?\d{2}\b', '', stem)
        s = re.sub(r'\b\d+\b', '', s)
        return [t.lower() for t in re.split(r'[\s\-_]+', s) if len(t) >= 3]

    file_tokens = {f: _tokens(f.stem) for f in image_files}

    # Count how many files each token appears in
    token_count: dict = {}
    for tokens in file_tokens.values():
        for t in set(tokens):
            token_count[t] = token_count.get(t, 0) + 1

    # Best tokens first (most files matched, then alphabetical for stability)
    ranked = sorted(
        [(t, c) for t, c in token_count.items() if c >= 2],
        key=lambda x: (-x[1], x[0]),
    )

    groups: dict = {}
    assigned: set = set()

    for token, _ in ranked:
        eligible = [f for f in image_files if f not in assigned and token in file_tokens[f]]
        if eligible:
            groups[token.title()] = eligible
            assigned.update(eligible)

    remaining = [f for f in image_files if f not in assigned]
    if remaining:
        groups.setdefault("Images", []).extend(remaining)

    return groups


def _group_docs_by_llm(doc_files: list, llm_client) -> dict | None:
    """Ask the LLM to cluster documents by content. Returns {folder: [Path]} or None."""
    from use_cases.file_analysis import _read_full

    lines = []
    for f in doc_files[:25]:   # cap to keep prompt small
        try:
            raw = _read_full(f, max_chars=500)
            excerpt = " ".join((raw or "").split())[:250]
        except Exception:
            excerpt = ""
        lines.append(f"- {f.name}: {excerpt}")

    prompt = (
        "Group the files below into meaningful folders based on their content.\n"
        "Rules:\n"
        "- Research papers, academic or scientific documents → use 'Research' or a topic name.\n"
        "- Invoices, proposals, contracts, briefs → use the company or client name as the folder "
        "(e.g. 'Fieldmark', 'Acme Corp'). If there are multiple companies, create a folder per company.\n"
        "- Demo, marketing, or promotional content → 'Marketing' or 'Demos'.\n"
        "- Avoid generic names like 'Documents', 'Misc', or 'Files' unless nothing else fits.\n"
        "Use 2-6 folders total. Reply with ONLY lines in this exact format (no JSON, no explanation):\n"
        "FolderName: file1.pdf, file2.txt\n\n"
        + "\n".join(lines)
    )

    try:
        response = llm_client.chat(
            [
                {"role": "system",
                 "content": (
                     "You organize files into folders. "
                     "Detect research papers and group them under Research or a topic name. "
                     "Detect business documents and group them by company name. "
                     "Reply only with folder lines."
                 )},
                {"role": "user", "content": prompt},
            ],
            max_tokens=300,
        )
    except Exception:
        return None

    # Parse "FolderName: file1.pdf, file2.txt" lines
    groups_raw: dict = {}
    for line in response.strip().splitlines():
        if ":" not in line:
            continue
        folder_part, files_part = line.split(":", 1)
        folder_name = folder_part.strip().strip(" -\"'").title()
        if not folder_name:
            continue
        names = [n.strip().strip("\"'") for n in files_part.split(",") if n.strip()]
        if names:
            groups_raw.setdefault(folder_name, []).extend(names)

    if not groups_raw:
        return None

    # Resolve LLM-returned names back to actual Path objects (case-insensitive)
    lookup = {f.name.lower(): f for f in doc_files}
    groups: dict = {}
    assigned: set = set()

    for folder_name, names in groups_raw.items():
        resolved = []
        for name in names:
            match = lookup.get(name.lower())
            if match and match not in assigned:
                resolved.append(match)
                assigned.add(match)
        if resolved:
            groups[folder_name] = resolved

    # Files the LLM missed → generic fallback folder
    missed = [f for f in doc_files if f not in assigned]
    if missed:
        groups.setdefault("Documents", []).extend(missed)

    return groups or None


def smart_organize_folder(folder: str, llm_client=None,
                           preview_only: bool = True,
                           snapshot_mgr=None) -> dict:
    """Organize a folder by grouping similar content together.

    Documents (PDF, DOCX, TXT, etc.) are clustered by LLM content analysis.
    Images are grouped by common words in their filenames.
    Other file types fall back to extension-based categories.

    Returns (preview_only=True):
        {"groups": {folder_name: [file_names]}, "total_files": int}
    Returns (preview_only=False):
        {"moved": {folder_name: count}, "total_moved": int}
    On error:
        {"error": str}
    """
    root = Path(folder)
    if not root.exists() or not root.is_dir():
        return {"error": f"Not a directory: {folder}"}

    doc_files, image_files, other_files = [], [], []

    for f in sorted(root.iterdir(), key=lambda p: p.name.lower()):
        if not f.is_file():
            continue
        ext = f.suffix.lower()
        if ext in _IMAGE_EXTENSIONS:
            image_files.append(f)
        elif ext in _DOC_EXTENSIONS | _TEXT_EXTENSIONS:
            doc_files.append(f)
        else:
            other_files.append(f)

    if not doc_files and not image_files and not other_files:
        return {"error": "No files found in the folder."}

    groups: dict = {}   # folder_name -> [Path]

    # 1. Documents — LLM content clustering with type-based fallback
    if doc_files:
        doc_groups = _group_docs_by_llm(doc_files, llm_client) if llm_client else None
        if doc_groups:
            for name, files in doc_groups.items():
                groups.setdefault(name, []).extend(files)
        else:
            for f in doc_files:
                ext = f.suffix.lower()
                cat = ("PDFs" if ext == ".pdf"
                       else "Spreadsheets" if ext in {".xls", ".xlsx", ".csv"}
                       else "Documents")
                groups.setdefault(cat, []).append(f)

    # 2. Images — filename token grouping
    if image_files:
        for name, files in _group_images_by_filename(image_files).items():
            groups.setdefault(name, []).extend(files)

    # 3. Other — extension categories
    for f in other_files:
        ext = f.suffix.lower()
        cat = "Other"
        for c, exts in _FILE_CATEGORIES.items():
            if ext in exts:
                cat = c
                break
        groups.setdefault(cat, []).append(f)

    if preview_only:
        return {
            "groups": {name: [f.name for f in files] for name, files in groups.items()},
            "total_files": sum(len(v) for v in groups.values()),
        }

    # Execute moves
    moved: dict = {}
    for folder_name, files in groups.items():
        target = root / folder_name
        target.mkdir(exist_ok=True)
        count = 0
        for f in files:
            dst = target / f.name
            if dst.exists():
                dst = target / f"{f.stem}_1{f.suffix}"
            if snapshot_mgr:
                snapshot_mgr.snapshot_file(str(f), "smart_organize")
            shutil.move(str(f), str(dst))
            count += 1
        moved[folder_name] = count

    return {"moved": moved, "total_moved": sum(moved.values())}


def list_directory(path: str) -> list[dict]:
    root = Path(path)
    if not root.exists():
        return [{"error": f"Not found: {path}"}]
    items = []
    for entry in sorted(root.iterdir(), key=lambda e: (not e.is_dir(), e.name.lower())):
        stat = entry.stat()
        items.append({
            "name": entry.name,
            "type": "directory" if entry.is_dir() else "file",
            "size_human": _human_size(stat.st_size) if entry.is_file() else "-",
            "modified": datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M"),
        })
    return items


# ---------------------------------------------------------------------------
# Archive operations
# ---------------------------------------------------------------------------

def compress_to_zip(sources: list[str], dst: str = None) -> str:
    """Compress one or more files/folders into a zip archive."""
    paths = [Path(s) for s in sources]
    missing = [str(p) for p in paths if not p.exists()]
    if missing:
        return ToolOutput.failure(f"Not found: {', '.join(missing)}")

    if dst:
        out = Path(dst)
    else:
        first = paths[0]
        out = first.parent / f"{first.stem}_archive.zip"

    try:
        with zipfile.ZipFile(str(out), "w", zipfile.ZIP_DEFLATED) as zf:
            for p in paths:
                if p.is_dir():
                    for f in p.rglob("*"):
                        if f.is_file():
                            zf.write(str(f), f.relative_to(p.parent))
                else:
                    zf.write(str(p), p.name)
        size = _human_size(out.stat().st_size)
        return f"Archive created: {out} ({size})"
    except Exception as e:
        return ToolOutput.failure(f"Compression failed: {e}")


def extract_archive(src: str, dst: str = None) -> str:
    """Extract a .zip or .tar/.tar.gz/.tar.bz2 archive."""
    p = Path(src)
    if not p.exists():
        return ToolOutput.failure(f"File not found: {src}")

    out_dir = Path(dst) if dst else p.parent / p.stem
    out_dir.mkdir(parents=True, exist_ok=True)

    try:
        ext = p.suffix.lower()
        name_lower = p.name.lower()
        if ext == ".zip":
            with zipfile.ZipFile(str(p), "r") as zf:
                zf.extractall(str(out_dir))
        elif ext in (".tar",) or name_lower.endswith((".tar.gz", ".tgz", ".tar.bz2")):
            mode = "r:*"
            with tarfile.open(str(p), mode) as tf:
                tf.extractall(str(out_dir))
        elif ext in (".gz", ".bz2"):
            mode = f"r:{ext.lstrip('.')}"
            with tarfile.open(str(p), mode) as tf:
                tf.extractall(str(out_dir))
        else:
            return ToolOutput.failure(f"Unsupported archive format: {ext}")
        return f"Extracted to: {out_dir}"
    except Exception as e:
        return ToolOutput.failure(f"Extraction failed: {e}")


def list_archive_contents(src: str) -> str:
    """List files inside a .zip or .tar archive without extracting."""
    p = Path(src)
    if not p.exists():
        return ToolOutput.failure(f"File not found: {src}")
    try:
        ext = p.suffix.lower()
        if ext == ".zip":
            with zipfile.ZipFile(str(p), "r") as zf:
                names = zf.namelist()
        elif ext in (".tar", ".gz", ".bz2", ".tgz"):
            mode = "r:*"
            with tarfile.open(str(p), mode) as tf:
                names = tf.getnames()
        else:
            return ToolOutput.failure(f"Unsupported archive format: {ext}")
        total = len(names)
        shown = names[:100]
        lines = [f"Archive: {p.name} ({total} entries)"] + shown
        if total > 100:
            lines.append(f"[... and {total - 100} more]")
        return "\n".join(lines)
    except Exception as e:
        return ToolOutput.failure(f"Could not read archive: {e}")


# ---------------------------------------------------------------------------
# File analysis helpers
# ---------------------------------------------------------------------------

def find_large_files(root: str = None, min_mb: float = 100,
                     limit: int = 20) -> list[dict]:
    """Find files larger than min_mb megabytes."""
    search_root = Path(root) if root else Path.home()
    min_bytes = int(min_mb * 1024 * 1024)
    results = []
    try:
        for p in search_root.rglob("*"):
            if not p.is_file():
                continue
            try:
                size = p.stat().st_size
            except OSError:
                continue
            if size >= min_bytes:
                results.append({"path": str(p), "size_bytes": size,
                                 "size_human": _human_size(size)})
            if len(results) >= limit * 3:
                break
    except PermissionError:
        pass
    results.sort(key=lambda x: -x["size_bytes"])
    return results[:limit]


def find_old_files(root: str = None, older_than_days: int = 365,
                   limit: int = 20) -> list[dict]:
    """Find files not modified in the last older_than_days days."""
    search_root = Path(root) if root else Path.home()
    cutoff = datetime.now().timestamp() - (older_than_days * 86400)
    results = []
    try:
        for p in search_root.rglob("*"):
            if not p.is_file():
                continue
            try:
                mtime = p.stat().st_mtime
            except OSError:
                continue
            if mtime < cutoff:
                results.append({
                    "path": str(p),
                    "last_modified": datetime.fromtimestamp(mtime).strftime("%Y-%m-%d"),
                })
            if len(results) >= limit * 3:
                break
    except PermissionError:
        pass
    results.sort(key=lambda x: x["last_modified"])
    return results[:limit]


def find_duplicate_files(root: str = None, limit: int = 50) -> dict:
    """Find duplicate files (same content) using MD5 hashing."""
    search_root = Path(root) if root else Path.home()
    hashes: dict[str, list[str]] = {}
    count = 0
    try:
        for p in search_root.rglob("*"):
            if not p.is_file():
                continue
            try:
                h = hashlib.md5(p.read_bytes()).hexdigest()
                hashes.setdefault(h, []).append(str(p))
                count += 1
                if count >= 2000:
                    break
            except (OSError, PermissionError):
                continue
    except PermissionError:
        pass
    duplicates = {h: paths for h, paths in hashes.items() if len(paths) > 1}
    # Return up to `limit` groups
    subset = dict(list(duplicates.items())[:limit])
    total_wasted = sum(
        Path(paths[0]).stat().st_size * (len(paths) - 1)
        for paths in subset.values()
        if Path(paths[0]).exists()
    )
    return {
        "duplicate_groups": len(duplicates),
        "shown": len(subset),
        "wasted_space": _human_size(total_wasted),
        "groups": subset,
    }
