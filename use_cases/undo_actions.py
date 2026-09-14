"""Verified undo adapters. Native device access stays behind Windows guards."""

from contextlib import contextmanager
import json
import math
import subprocess

from emberos.outcomes import ToolOutput
from emberos.undo import UndoConflict, UndoEntry, UndoUnavailable


@contextmanager
def _audio_endpoint():
    from use_cases import system_queries
    if not system_queries._IS_WINDOWS:
        raise UndoUnavailable("Audio control is not supported on this platform.")
    import comtypes
    from pycaw.pycaw import AudioUtilities
    comtypes.CoInitialize()
    device = endpoint = None
    try:
        device = AudioUtilities.GetSpeakers()
        if device is None:
            raise UndoUnavailable("No default audio output device is available.")
        endpoint = device.EndpointVolume
        yield device.id, endpoint
    finally:
        endpoint = device = None
        comtypes.CoUninitialize()


def _audio_state(device_id, endpoint):
    level = float(endpoint.GetMasterVolumeLevelScalar())
    if not isinstance(device_id, str) or not device_id or not math.isfinite(level) or not 0 <= level <= 1:
        raise UndoUnavailable("Audio endpoint state could not be verified.")
    return {"device": device_id, "level": level, "muted": bool(endpoint.GetMute())}


def _same_audio(left, right):
    return (left["device"] == right["device"] and left["muted"] == right["muted"]
            and abs(left["level"] - right["level"]) < 0.00001)


def _read_audio():
    with _audio_endpoint() as resource:
        try:
            return _audio_state(*resource)
        finally:
            # Release the yielded interface reference before COM is balanced.
            resource = None


def _restore_audio(before, after, name):
    with _audio_endpoint() as resource:
        try:
            if not _same_audio(_audio_state(*resource), after):
                raise UndoConflict("The audio device or its settings changed after the last action.")
            # Reuse this exact endpoint, including its original scalar precision.
            resource[1].SetMasterVolumeLevelScalar(before["level"], None)
            if before["muted"] != after["muted"]:
                resource[1].SetMute(before["muted"], None)
            actual = _audio_state(*resource)
            if not _same_audio(actual, before):
                raise RuntimeError("The audio device did not report the restored settings.")
        finally:
            resource = None
    level = round(before["level"] * 100)
    mute = "muted" if before["muted"] else "unmuted"
    return ToolOutput(f"Undid the last audio change. Volume is back to {level}% ({mute}).",
                      data={"undone_tool": name, "feature": "volume", "level": level, "muted": before["muted"]})


_BRIGHTNESS_READ = (
    "$ErrorActionPreference = 'Stop'; "
    "ConvertTo-Json -Compress -InputObject @(Get-CimInstance -Namespace root/WMI "
    "-ClassName WmiMonitorBrightness | Select-Object InstanceName,CurrentBrightness)"
)


def _powershell(script, payload=None):
    from use_cases import system_queries
    if not system_queries._IS_WINDOWS:
        raise UndoUnavailable("Brightness control is not supported on this platform.")
    result = subprocess.run(
        ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
        input=json.dumps(payload) if payload is not None else None,
        capture_output=True, text=True, timeout=15,
    )
    if result.returncode:
        raise RuntimeError(result.stderr.strip()[:200] or "Display access failed.")
    return json.loads(result.stdout)


def _read_brightness():
    rows = _powershell(_BRIGHTNESS_READ)
    if not isinstance(rows, list) or not rows or len(rows) > 16:
        raise UndoUnavailable("No bounded set of display levels was reported.")
    state = {}
    for row in rows:
        identity, level = row.get("InstanceName"), row.get("CurrentBrightness")
        if (not isinstance(identity, str) or not identity or identity in state
                or type(level) is not int or not 0 <= level <= 100):
            raise UndoUnavailable("Display identity or brightness could not be verified.")
        state[identity] = level
    return state


def _restore_brightness(before, after):
    # IDs/data travel over stdin, never through interpolated PowerShell code.
    script = r"""
$ErrorActionPreference = 'Stop'
try {
    $data = [Console]::In.ReadToEnd() | ConvertFrom-Json
    function Read-Levels {
        $levels = @{}
        foreach ($monitor in @(Get-CimInstance -Namespace root/WMI -ClassName WmiMonitorBrightness)) {
            $levels[$monitor.InstanceName] = [int]$monitor.CurrentBrightness
        }
        return $levels
    }
    function Matches($levels, $wanted) {
        $properties = @($wanted.PSObject.Properties)
        if ($levels.Count -ne $properties.Count) { return $false }
        foreach ($property in $properties) {
            if (-not $levels.ContainsKey($property.Name) -or $levels[$property.Name] -ne $property.Value) { return $false }
        }
        return $true
    }
    if (-not (Matches (Read-Levels) $data.after)) {
        @{conflict='The displays or brightness changed after the last action.'} | ConvertTo-Json -Compress
        exit 0
    }
    $methods = @(Get-CimInstance -Namespace root/WMI -ClassName WmiMonitorBrightnessMethods)
    foreach ($property in $data.before.PSObject.Properties) {
        if (@($methods | Where-Object { $_.InstanceName -eq $property.Name }).Count -ne 1) {
            @{conflict='A display cannot be identified for restoration.'} | ConvertTo-Json -Compress
            exit 0
        }
    }
    foreach ($property in $data.before.PSObject.Properties) {
        $monitor = $methods | Where-Object { $_.InstanceName -eq $property.Name }
        $change = Invoke-CimMethod -InputObject $monitor -MethodName WmiSetBrightness -Arguments @{Timeout=[uint32]0; Brightness=[byte]$property.Value}
        if ($null -ne $change.ReturnValue -and $change.ReturnValue -ne 0) { throw 'Display rejected brightness restoration.' }
    }
    for ($attempt = 0; $attempt -lt 6; $attempt++) {
        $actual = Read-Levels
        if (Matches $actual $data.before) { break }
        if ($attempt -lt 5) { Start-Sleep -Milliseconds 100 }
    }
    @{levels=$actual} | ConvertTo-Json -Compress
} catch { [Console]::Error.WriteLine($_.Exception.Message); exit 1 }
"""
    result = _powershell(script, {"before": before, "after": after})
    if result.get("conflict"):
        raise UndoConflict(result["conflict"])
    if result.get("levels") != before:
        raise RuntimeError("The displays did not report the restored brightness levels.")
    levels = list(before.values())
    message = (f"Undid the last brightness change. Brightness is back to {levels[0]}%."
               if len(set(levels)) == 1 else "Undid the last brightness change and restored each display's previous level.")
    return ToolOutput(message, data={"undone_tool": "set_brightness", "feature": "brightness", "levels": levels})


def _prepare_task(name):
    from emberos.tools import _get_task_manager
    manager = _get_task_manager()

    def capture(result):
        data = result.data or {}
        if name == "add_task":
            before, after = None, {**data, "completed_at": data.get("completed_at")}
            task_id = data["id"]
        elif name == "complete_task":
            before = dict(data["previous_task"])
            after = {key: value for key, value in data.items() if key != "previous_task"}
            task_id = before["id"]
        else:
            before, after = dict(data["previous_task"]), data["task"]
            task_id = before["id"]
        if len(json.dumps([before, after], ensure_ascii=False).encode("utf-8")) > 8192:
            raise UndoUnavailable("The task snapshot exceeds the undo memory limit.")
        if before == after:
            raise UndoUnavailable("The action made no change.")
        if manager.get(task_id) != after:
            raise UndoUnavailable("The task changed before undo could be recorded.")

        def restore():
            manager.restore_snapshot(task_id, before, after)
            verb = {"add_task": "creation", "complete_task": "completion", "remove_task": "deletion"}[name]
            return ToolOutput(f"Undid the {verb} of task #{task_id}.", data={"undone_tool": name, "task_id": task_id})

        return UndoEntry(name, restore, "task")
    return capture


def prepare_undo(name, params):
    if name in {"set_volume", "volume_up", "volume_down", "mute_volume"}:
        before = _read_audio()

        def capture(result):
            after = _read_audio()
            if before["device"] != after["device"]:
                raise UndoUnavailable("The default audio device changed during the action.")
            if name == "set_volume" and (round(after["level"] * 100) != params["level"] or after["muted"] != before["muted"]):
                raise UndoUnavailable("Audio settings changed before undo could be recorded.")
            if _same_audio(before, after):
                raise UndoUnavailable("The action made no change.")
            return UndoEntry(name, lambda: _restore_audio(before, after, name), "volume")
        return capture
    if name == "set_brightness":
        before = _read_brightness()

        def capture(result):
            after = _read_brightness()
            if before.keys() != after.keys():
                raise UndoUnavailable("The displays changed during the action.")
            if any(level != params["level"] for level in after.values()):
                raise UndoUnavailable("Brightness changed before undo could be recorded.")
            if before == after:
                raise UndoUnavailable("The action made no change.")
            return UndoEntry(name, lambda: _restore_brightness(before, after), "brightness")
        return capture
    if name in {"add_task", "complete_task", "remove_task"}:
        return _prepare_task(name)
    return None
