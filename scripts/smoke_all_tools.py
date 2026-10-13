#!/usr/bin/env python3
"""Exercise every registered EmberOS Web tool safely on headless Linux.

Stateful probes use a temporary data directory and fixture files. Host-control
operations are checked at the Web confirmation boundary, never executed.
"""

from pathlib import Path
import argparse
import os
import platform
import shutil
import subprocess
import sys
import tempfile


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--skip-model", action="store_true",
        help="Skip summary generation when a separate local-model benchmark runs next.",
    )
    args = parser.parse_args()
    if platform.system() != "Linux":
        raise SystemExit("Run this device smoke test on Linux.")

    root = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(root))

    with tempfile.TemporaryDirectory(prefix="emberos-tool-smoke-") as temporary:
        temp = Path(temporary)
        state = temp / "state"
        os.environ["EMBER_DATA_DIR"] = str(state)

        from emberos.memory import ConversationMemory
        from emberos.platform_detect import IS_LINUX_HEADLESS
        from web_ember import (
            WEB_BLOCKED_ALWAYS,
            WEB_CONFIRM_TOOLS,
            WEB_SAFE_TOOLS,
            WebSafeToolRegistry,
        )

        if not IS_LINUX_HEADLESS:
            raise SystemExit("Run on a headless Linux session; this test must not change desktop state.")

        memory = ConversationMemory(state / "conversation.sqlite3")
        memory.record(
            "EmberOS smoke history",
            "Temporary history fixture",
            {"route": "tool_call", "tool": "create_note", "arguments": {},
             "status": "success", "success": True},
        )
        registry = WebSafeToolRegistry(memory=memory)
        reports = {}

        def report(name, status, detail=""):
            if name in reports:
                raise AssertionError(f"Tool reported twice: {name}")
            reports[name] = status
            detail = str(detail).replace("\n", " ")[:150]
            print(f"{status:20} {name}" + (f" - {detail}" if detail else ""))

        def call(name, arguments=None, *, confirmed=False):
            args = arguments or {}
            result = (registry.execute_confirmed(name, args) if confirmed
                      else registry.execute_tool(name, args))
            if not result.success:
                raise AssertionError(f"{name}: {result.status}: {result.error}")
            report(name, "PASS", result.status)
            return result

        def unsupported(name, arguments=None):
            result = registry.execute_tool(name, arguments or {})
            if result.success or result.status != "unsupported":
                raise AssertionError(
                    f"{name} should report unsupported on headless Linux; "
                    f"got success={result.success}, status={result.status}, error={result.error}"
                )
            report(name, "UNSUPPORTED", result.error)

        def optional_sensor(name):
            result = registry.execute_tool(name, {})
            if result.success:
                report(name, "PASS", result.status)
            elif result.status == "unsupported":
                report(name, "UNSUPPORTED", result.error)
            else:
                raise AssertionError(f"{name}: {result.status}: {result.error}")

        def policy_blocked(name, arguments=None):
            result = registry.execute_tool(name, arguments or {})
            if result.success or result.status != "unsupported":
                raise AssertionError(f"Web policy did not block {name}: {result.to_dict()}")
            report(name, "POLICY-BLOCKED", result.error)

        fixtures = temp / "fixtures"
        fixtures.mkdir()
        text_a = fixtures / "needle-smoke.txt"
        text_a.write_text(
            "EmberOS smoke fixture. Contact pi-smoke@example.test.\n"
            "The package test checks notes, tasks, and Linux tools.\n",
            encoding="utf-8",
        )
        text_b = fixtures / "comparison.txt"
        text_b.write_text("Different EmberOS smoke text.\n", encoding="utf-8")

        # General host/system probes.
        for name in ("get_system_info", "disk_usage", "ram_status",
                     "running_processes", "system_uptime", "cpu_info"):
            args = {"filter_name": "python"} if name == "running_processes" else {}
            call(name, args)
        optional_sensor("cpu_temperature")
        optional_sensor("battery_status")

        # File and document tools, isolated to this temporary directory.
        written = fixtures / "written.txt"
        call("write_file", {"path": str(written), "content": "EmberOS fixture output.\n"})
        call("read_file", {"path": str(text_a)})
        call("list_dir", {"path": str(fixtures)})
        call("find_files", {"query": "needle-smoke", "search_root": str(fixtures)})
        call("get_file_info", {"path": str(text_a)})
        call("create_directory", {"path": str(temp / "created-directory")})
        call("copy_file", {"src": str(text_a), "dst": str(temp / "copied.txt")})
        call("grep_file", {"path": str(text_a), "pattern": "package"})
        call("grep_folder", {"folder": str(fixtures), "pattern": "EmberOS",
                              "extensions": ["txt"]})
        call("diff_files", {"path_a": str(text_a), "path_b": str(text_b)})
        call("extract_patterns", {"path": str(text_a), "pattern_types": ["email"]})
        call("batch_read_folder", {"folder": str(fixtures), "extensions": ["txt"]})
        call("folder_explain", {"folder": str(fixtures)})
        call("find_large_files", {"root": str(fixtures), "min_mb": 0, "limit": 5})
        call("find_old_files", {"root": str(fixtures), "older_than_days": 0, "limit": 5})

        duplicates = temp / "duplicates"
        duplicates.mkdir()
        (duplicates / "one.txt").write_text("same bytes", encoding="utf-8")
        (duplicates / "two.txt").write_text("same bytes", encoding="utf-8")
        call("find_duplicate_files", {"root": str(duplicates), "limit": 5})

        archive = temp / "fixture.zip"
        call("compress_to_zip", {"sources": [str(text_a)], "dst": str(archive)})
        call("list_archive_contents", {"src": str(archive)})
        call("extract_archive", {"src": str(archive), "dst": str(temp / "extracted")})
        call("write_document", {"path": str(temp / "smoke.md"),
                                "content": "# EmberOS smoke document"})
        call("create_spreadsheet", {
            "path": str(temp / "smoke.xlsx"),
            "rows": [["Tool", "Result"], ["smoke", "pass"]],
            "open_after": False,
        })
        call("analyze_files", {"file_paths": [str(text_a)],
                                "user_message": "Read this small smoke fixture."})

        # Confirmation-required file operations only touch temporary fixtures.
        organize = temp / "organize"
        organize.mkdir()
        organize_file = organize / "organize.txt"
        organize_file.write_text("safe fixture", encoding="utf-8")
        call("organize_folder", {"folder": str(organize), "preview_only": True},
             confirmed=True)
        call("organize_folder", {"folder": str(organize), "preview_only": False},
             confirmed=True)

        moved = temp / "move-me.txt"
        moved.write_text("move fixture", encoding="utf-8")
        call("move_file", {"src": str(moved), "dst": str(temp / "moved.txt")},
             confirmed=True)
        renamed = temp / "rename-me.txt"
        renamed.write_text("rename fixture", encoding="utf-8")
        call("rename_file", {"path": str(renamed), "new_name": "renamed.txt"},
             confirmed=True)
        deleted = temp / "delete-me.txt"
        deleted.write_text("delete fixture", encoding="utf-8")
        call("delete_file", {"path": str(deleted)}, confirmed=True)

        # Image processing uses only a generated 8x8 PNG fixture.
        from PIL import Image
        image_path = temp / "image.png"
        Image.new("RGB", (8, 8), (255, 40, 20)).save(image_path)
        call("get_image_info", {"src": str(image_path)})
        call("resize_image", {"src": str(image_path), "width": 4, "height": 4,
                              "dst": str(temp / "resized.png")})
        call("rotate_image", {"src": str(image_path), "degrees": 90,
                              "dst": str(temp / "rotated.png")})
        call("convert_image", {"src": str(image_path), "target_format": "jpg",
                               "dst": str(temp / "converted.jpg")})

        # Local-model tool, tested against the bundled fixture/model.
        if args.skip_model:
            report("summarize_file", "COVERED-SEPARATELY",
                   "the package smoke runs benchmark_local_llm.py after this suite")
        else:
            call("summarize_file", {"path": str(text_a)})

        # App state remains isolated; no user's notes or tasks are touched.
        call("create_note", {"title": "Smoke note", "content": "fixture note",
                             "tags": ["smoke"]})
        call("search_notes", {"query": "fixture note"})
        call("search_conversation_history", {"query": "smoke", "scope": "all"})
        task = call("add_task", {"title": "Smoke task"})
        task_id = task.data["id"]
        call("list_tasks", {})
        call("undo_last_action", {"target": "task"})
        task = call("add_task", {"title": "Smoke completed task"})
        task_id = task.data["id"]
        call("complete_task", {"task_id": task_id})
        call("clear_completed_tasks", {}, confirmed=True)
        task = call("add_task", {"title": "Smoke removed task"})
        call("remove_task", {"task_id": task.data["id"]}, confirmed=True)

        # This kills only a child process started by this test.
        child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
        try:
            call("kill_process", {"target": str(child.pid)}, confirmed=True)
        finally:
            try:
                child.wait(timeout=5)
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait(timeout=5)

        # Tools that must return a clear platform-unavailable result here.
        for name, args in (
            ("open_file", {"path": str(text_a)}),
            ("get_active_window", {}),
            ("launch_app", {"app_name": "emberos-smoke-no-such-app"}),
            ("take_screenshot", {"save_path": str(temp / "screen.png")}),
            ("volume_up", {"steps": 1}),
            ("volume_down", {"steps": 1}),
            ("mute_volume", {}),
            ("get_volume", {}),
            ("set_volume", {"level": 50}),
            ("get_brightness", {}),
            ("set_brightness", {"level": 50}),
            ("toggle_dark_mode", {}),
            ("set_dark_mode", {"enable": True}),
            ("get_open_windows", {}),
            ("minimize_all_windows", {}),
            ("focus_window", {"title_fragment": "emberos-smoke-no-such-window"}),
        ):
            unsupported(name, args)

        # Media extraction is supported only when FFmpeg is installed.
        if shutil.which("ffmpeg") is None:
            unsupported("extract_audio", {"video_src": str(temp / "missing.mp4")})
            unsupported("extract_video_clip", {"src": str(temp / "missing.mp4"),
                                                "start": "0", "duration": "1"})
        else:
            video = temp / "smoke.mp4"
            subprocess.run([
                "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
                "-f", "lavfi", "-i", "testsrc=size=64x64:rate=5:duration=1",
                "-f", "lavfi", "-i", "sine=frequency=1000:duration=1",
                "-c:v", "mpeg4", "-c:a", "aac", str(video),
            ], check=True, timeout=30, capture_output=True)
            call("extract_audio", {"video_src": str(video),
                                   "dst": str(temp / "smoke-audio.mp3")})
            call("extract_video_clip", {"src": str(video), "start": "0",
                                        "duration": "0.5", "dst": str(temp / "clip.mp4")})

        # Never execute OS-wide control actions as part of an unattended smoke.
        for name, args in (
            ("run_shell", {"cmd": "touch /tmp/emberos-smoke-must-not-run"}),
            ("get_clipboard", {}),
            ("set_clipboard", {"text": "smoke"}),
            ("search_web", {"url": "https://example.invalid"}),
            ("close_window", {"title": "emberos-smoke"}),
            ("lock_screen", {}),
            ("sleep_system", {}),
            ("shutdown_system", {"delay_seconds": 1}),
            ("restart_system", {"delay_seconds": 1}),
        ):
            policy_blocked(name, args)

        # These can mutate host state or create remote documents, so report why
        # they were not invoked rather than claiming a successful smoke test.
        report("cancel_shutdown", "SKIPPED-SAFETY", "could cancel a real pending system shutdown")
        report("create_google_doc", "SKIPPED-SETUP", "requires configured Google credentials and would create a real document")

        registered = set(registry._tools)
        missing = registered - reports.keys()
        extra = reports.keys() - registered
        if missing or extra:
            raise AssertionError(f"Tool coverage mismatch; missing={sorted(missing)}, extra={sorted(extra)}")

        counts = {}
        for status in reports.values():
            counts[status] = counts.get(status, 0) + 1
        print(f"\nCOVERAGE {len(reports)}/{len(registered)} registered tools: {counts}")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
