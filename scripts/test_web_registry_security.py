"""Test WebSafeToolRegistry directly -- bypasses Needle routing entirely,
so results don't depend on model confidence/phrasing luck."""

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from web_ember import WebSafeToolRegistry


def test_delete_file_rejected_before_execution(tmp_path):
    registry = WebSafeToolRegistry(memory=None)
    target = tmp_path / "victim.txt"
    target.write_text("do not delete me")

    result = registry.execute_tool("delete_file", {"path": str(target)})

    assert result.success is False, "delete_file must be refused"
    assert result.status == "unsupported"
    assert target.exists(), "the real proof: file must survive"


def test_run_shell_rejected_before_execution(tmp_path):
    registry = WebSafeToolRegistry(memory=None)
    marker = tmp_path / "should_not_exist.txt"

    result = registry.execute_tool(
        "run_shell", {"cmd": f'echo pwned > "{marker}"'}
    )

    assert result.success is False
    assert result.status == "unsupported"
    assert not marker.exists(), "shell command must never have run"


def test_chain_with_one_unsafe_step_blocks_whole_chain(tmp_path):
    registry = WebSafeToolRegistry(memory=None)
    target = tmp_path / "victim2.txt"
    target.write_text("still here?")

    calls = [
        {"name": "ram_status", "arguments": {}},                       # safe
        {"name": "delete_file", "arguments": {"path": str(target)}},   # unsafe
    ]
    results, composite = registry.execute_tool_chain(calls)

    assert target.exists(), "chain must not have run at all"
    assert composite is None, "no undo entry -- nothing executed"
    assert all(r.get("success") is not True for r in results)


if __name__ == "__main__":
    import tempfile
    for fn in (test_delete_file_rejected_before_execution,
               test_run_shell_rejected_before_execution,
               test_chain_with_one_unsafe_step_blocks_whole_chain):
        with tempfile.TemporaryDirectory() as d:
            fn(Path(d))
        print(f"PASS: {fn.__name__}")