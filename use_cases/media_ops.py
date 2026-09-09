"""Media operations for EmberOS-Windows: screenshots and image processing."""

import logging
import subprocess
from datetime import datetime
from pathlib import Path
from emberos.outcomes import ToolOutput

logger = logging.getLogger("emberos.use_cases.media_ops")

_SCREENSHOTS_DIR = Path.home() / "Pictures" / "EmberOS Screenshots"


def _pil_available() -> bool:
    try:
        import PIL  # noqa: F401
        return True
    except ImportError:
        return False


def take_screenshot(save_path: str = None) -> str:
    """Capture a full-screen screenshot. Returns the saved file path."""
    if not _pil_available():
        return ToolOutput.failure("Screenshot requires Pillow (PIL) — not available in this environment.")
    try:
        from PIL import ImageGrab
        _SCREENSHOTS_DIR.mkdir(parents=True, exist_ok=True)
        if save_path:
            out = Path(save_path)
        else:
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            out = _SCREENSHOTS_DIR / f"screenshot_{ts}.png"
        img = ImageGrab.grab()
        img.save(str(out))
        return f"Screenshot saved: {out}"
    except Exception as e:
        return ToolOutput.failure(f"Screenshot failed: {e}")


def resize_image(src: str, width: int, height: int, dst: str = None) -> str:
    """Resize an image to the given dimensions."""
    if not _pil_available():
        return ToolOutput.failure("Image editing requires Pillow — not available.")
    try:
        from PIL import Image
        p = Path(src)
        if not p.exists():
            return ToolOutput.failure(f"File not found: {src}")
        img = Image.open(str(p))
        resized = img.resize((width, height), Image.LANCZOS)
        out = Path(dst) if dst else p.parent / f"{p.stem}_resized{p.suffix}"
        resized.save(str(out))
        return f"Resized to {width}x{height}: {out}"
    except Exception as e:
        return ToolOutput.failure(f"Resize failed: {e}")


def convert_image(src: str, target_format: str, dst: str = None) -> str:
    """Convert an image to a different format (e.g. PNG → JPEG)."""
    if not _pil_available():
        return ToolOutput.failure("Image conversion requires Pillow — not available.")
    try:
        from PIL import Image
        p = Path(src)
        if not p.exists():
            return ToolOutput.failure(f"File not found: {src}")
        fmt = target_format.lstrip(".").upper()
        ext = f".{fmt.lower()}"
        if fmt == "JPG":
            fmt = "JPEG"
            ext = ".jpg"
        out = Path(dst) if dst else p.parent / f"{p.stem}{ext}"
        img = Image.open(str(p))
        if fmt == "JPEG" and img.mode in ("RGBA", "P"):
            img = img.convert("RGB")
        img.save(str(out), format=fmt)
        return f"Converted to {fmt}: {out}"
    except Exception as e:
        return ToolOutput.failure(f"Conversion failed: {e}")


def rotate_image(src: str, degrees: float, dst: str = None) -> str:
    """Rotate an image by the given degrees (counter-clockwise)."""
    if not _pil_available():
        return ToolOutput.failure("Image editing requires Pillow — not available.")
    try:
        from PIL import Image
        p = Path(src)
        if not p.exists():
            return ToolOutput.failure(f"File not found: {src}")
        img = Image.open(str(p))
        rotated = img.rotate(degrees, expand=True)
        out = Path(dst) if dst else p.parent / f"{p.stem}_rotated{p.suffix}"
        rotated.save(str(out))
        return f"Rotated {degrees}°: {out}"
    except Exception as e:
        return ToolOutput.failure(f"Rotation failed: {e}")


def get_image_info(src: str) -> str:
    """Return dimensions, mode, and format of an image."""
    if not _pil_available():
        return ToolOutput.failure("Image info requires Pillow — not available.")
    try:
        from PIL import Image
        p = Path(src)
        if not p.exists():
            return ToolOutput.failure(f"File not found: {src}")
        img = Image.open(str(p))
        size = p.stat().st_size
        size_str = f"{size // 1024} KB" if size >= 1024 else f"{size} B"
        return (f"{p.name}: {img.width}x{img.height} px, mode={img.mode}, "
                f"format={img.format}, size={size_str}")
    except Exception as e:
        return ToolOutput.failure(f"Could not read image info: {e}")


def _ffmpeg_available() -> bool:
    try:
        result = subprocess.run(
            ["ffmpeg", "-version"], capture_output=True, timeout=5
        )
        return result.returncode == 0
    except Exception:
        return False


def extract_audio(video_src: str, dst: str = None) -> str:
    """Extract audio track from a video file to MP3."""
    if not _ffmpeg_available():
        return (ToolOutput.failure("Audio extraction requires FFmpeg. "
                "Install it and ensure it is on the system PATH."))
    try:
        p = Path(video_src)
        if not p.exists():
            return ToolOutput.failure(f"File not found: {video_src}")
        out = Path(dst) if dst else p.parent / f"{p.stem}_audio.mp3"
        result = subprocess.run(
            ["ffmpeg", "-i", str(p), "-q:a", "2", "-map", "a", str(out), "-y"],
            capture_output=True, text=True, timeout=120,
        )
        if result.returncode != 0:
            return ToolOutput.failure(f"FFmpeg error: {result.stderr[-300:]}")
        return f"Audio extracted: {out}"
    except subprocess.TimeoutExpired:
        return ToolOutput.failure("Audio extraction timed out.")
    except Exception as e:
        return ToolOutput.failure(f"Audio extraction failed: {e}")


def extract_video_clip(src: str, start: str, duration: str, dst: str = None) -> str:
    """Extract a clip from a video. start/duration in HH:MM:SS or seconds format."""
    if not _ffmpeg_available():
        return (ToolOutput.failure("Video clipping requires FFmpeg. "
                "Install it and ensure it is on the system PATH."))
    try:
        p = Path(src)
        if not p.exists():
            return ToolOutput.failure(f"File not found: {src}")
        out = Path(dst) if dst else p.parent / f"{p.stem}_clip{p.suffix}"
        result = subprocess.run(
            ["ffmpeg", "-i", str(p), "-ss", str(start), "-t", str(duration),
             "-c", "copy", str(out), "-y"],
            capture_output=True, text=True, timeout=300,
        )
        if result.returncode != 0:
            return ToolOutput.failure(f"FFmpeg error: {result.stderr[-300:]}")
        return f"Clip saved: {out}"
    except subprocess.TimeoutExpired:
        return ToolOutput.failure("Video clip extraction timed out.")
    except Exception as e:
        return ToolOutput.failure(f"Clip extraction failed: {e}")
