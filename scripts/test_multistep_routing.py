"""
scripts/test_multistep_routing.py

Script pengujian mandiri (standalone) untuk memverifikasi:
  1. Perbaikan routing volume (set_volume vs volume_down) pada compound queries.
  2. Perilaku mode multistep (multi_step_mode=False vs multi_step_mode=True).
  3. Non-regression: perintah perubahan volume relatif tetap tidak terganggu.
  4. Kompatibilitas multi-platform (Windows & Raspberry Pi / Linux).

Jalankan di shell (PowerShell di Windows, atau bash di Pi):
    python scripts/test_multistep_routing.py
"""

import inspect
import json
import os
from pathlib import Path
import platform
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import needle_router
from needle_router import route_and_execute, needle_agent, CONFIDENCE_THRESHOLD
from emberos.tools import ToolRegistry

_IS_WINDOWS = platform.system() == "Windows"
_ARCH = platform.machine()


def run_checks():
    print("=" * 70)
    print(" EmberOS - Standalone Multi-step & Volume Routing Test")
    print(f" Platform: {platform.system()} ({_ARCH}) | Python: {sys.version.split()[0]}")
    print(f" Needle Confidence Threshold: {CONFIDENCE_THRESHOLD}")
    print("=" * 70)

    registry = ToolRegistry()
    total_tests = 0
    passed_tests = 0

    # -----------------------------------------------------------------------
    # TEST 1: Compound routing checks (Needle router + Guard)
    # -----------------------------------------------------------------------
    print("\n[TEST 1] Verifikasi Routing Compound Query (set_volume vs volume_down)")
    print("-" * 70)

    compound_cases = [
        (
            "set volume to 50 and set brightness to 50",
            ["set_volume", "set_brightness"],
            {"set_volume": {"level": 50}, "set_brightness": {"level": 50}},
        ),
        (
            "set volume to 30 and set brightness to 70",
            ["set_volume", "set_brightness"],
            {"set_volume": {"level": 30}, "set_brightness": {"level": 70}},
        ),
        (
            "set brightness to 50 and set volume to 50",
            ["set_brightness", "set_volume"],
            {"set_volume": {"level": 50}, "set_brightness": {"level": 50}},
        ),
    ]

    for query, expected_tools, expected_args in compound_cases:
        total_tests += 1
        # Panggil route_and_execute dengan multi_step_mode=True
        result = route_and_execute(query, registry, multi_step_mode=True)
        route = result.get("route")
        calls = result.get("calls") or []
        actual_tools = [c.get("name") for c in calls]
        confidence = result.get("confidence")
        conf_str = f"{confidence:.4f}" if confidence is not None else "N/A"

        # Cek kesesuaian tool
        tools_ok = actual_tools == expected_tools
        # Cek arguments
        args_ok = True
        for c in calls:
            name = c.get("name")
            if name in expected_args:
                for k, v in expected_args[name].items():
                    if c.get("arguments", {}).get(k) != v:
                        args_ok = False

        if tools_ok and args_ok and route == "multi_tool_call":
            passed_tests += 1
            print(f"  [PASS] {query!r}")
            print(f"         -> Tools: {actual_tools} (confidence: {conf_str})")
        else:
            print(f"  [FAIL] {query!r}")
            print(f"         Expected tools: {expected_tools}")
            print(f"         Actual tools  : {actual_tools}")
            print(f"         Actual route  : {route}")
            if calls:
                print(f"         Calls         : {calls}")

    # -----------------------------------------------------------------------
    # TEST 2: Multi-step mode switch (OFF vs ON)
    # -----------------------------------------------------------------------
    print("\n[TEST 2] Verifikasi Flag multi_step_mode (OFF vs ON)")
    print("-" * 70)

    test_q = "set volume to 50 and set brightness to 50"

    # Saat multi_step_mode = False -> harus menolak multiple actions
    total_tests += 1
    res_off = route_and_execute(test_q, registry, multi_step_mode=False)
    if res_off.get("route") == "unresolved_tool_request" and res_off.get("reason") == "multiple_actions":
        passed_tests += 1
        print(f"  [PASS] multi_step_mode=False menolak query majemuk dengan aman.")
        print(f"         -> reason: {res_off.get('reason')!r}")
    else:
        print(f"  [FAIL] multi_step_mode=False tidak menolak sebagaimana mestinya.")
        print(f"         Result: {res_off}")

    # Saat multi_step_mode = True -> harus menerima sebagai multi_tool_call
    total_tests += 1
    res_on = route_and_execute(test_q, registry, multi_step_mode=True)
    if res_on.get("route") == "multi_tool_call":
        passed_tests += 1
        print(f"  [PASS] multi_step_mode=True menerima dan menjalankan rantai aksi.")
        print(f"         -> route: {res_on.get('route')!r}")
    else:
        print(f"  [FAIL] multi_step_mode=True gagal menghasilkan multi_tool_call.")

    # -----------------------------------------------------------------------
    # TEST 3: Non-regression check untuk perubahan volume relatif
    # -----------------------------------------------------------------------
    print("\n[TEST 3] Verifikasi Perintah Relatif Tidak Terdampak Guard")
    print("-" * 70)

    relative_cases = [
        ("decrease the volume by 3 steps", "volume_down", {"steps": 3}),
        ("turn up my volume by 5 steps", "volume_up", {"steps": 5}),
    ]

    for query, expected_tool, expected_args in relative_cases:
        total_tests += 1
        needle_agent.reset()
        raw = needle_agent.complete(query)
        calls = raw.get("function_calls") or []
        fixed = needle_router._fix_set_volume_misroute(calls, query)

        call_ok = len(fixed) == 1 and fixed[0].get("name") == expected_tool
        args_ok = call_ok and all(fixed[0].get("arguments", {}).get(k) == v for k, v in expected_args.items())

        if call_ok and args_ok:
            passed_tests += 1
            print(f"  [PASS] {query!r} -> {expected_tool}{fixed[0].get('arguments')}")
        else:
            print(f"  [FAIL] {query!r} -> Got: {fixed}")

    # -----------------------------------------------------------------------
    # TEST 4: Eksekusi riil multi-step lintas platform (Linux / Windows)
    # -----------------------------------------------------------------------
    print("\n[TEST 4] Verifikasi Eksekusi Rantai Tool Multi-Platform")
    print("-" * 70)

    # Tool yang dijamin bisa dieksekusi di Windows maupun Linux (Pi):
    # ram_status dan disk_usage keduanya adalah READ_ONLY_TOOLS yang didukung psutil.
    total_tests += 1
    multi_calls = [
        {"name": "ram_status", "arguments": {}},
        {"name": "disk_usage", "arguments": {}},
    ]
    results, composite = registry.execute_tool_chain(multi_calls)
    all_success = len(results) == 2 and all(r.get("success") for r in results)

    if all_success:
        passed_tests += 1
        print(f"  [PASS] Eksekusi execute_tool_chain (ram_status + disk_usage) berhasil.")
        for idx, r in enumerate(results):
            tool_name = r.get("tool")
            res_preview = str(r.get("result", "")).strip().splitlines()[0][:60]
            print(f"         Step {idx+1} [{tool_name}]: {res_preview}...")
    else:
        print(f"  [FAIL] Eksekusi execute_tool_chain gagal: {results}")

    # -----------------------------------------------------------------------
    # Ringkasan
    # -----------------------------------------------------------------------
    print("\n" + "=" * 70)
    print(f" HASIL AKHIR: {passed_tests}/{total_tests} PENGUJIAN LULUS")
    print("=" * 70)
    return passed_tests == total_tests


if __name__ == "__main__":
    success = run_checks()
    sys.exit(0 if success else 1)

