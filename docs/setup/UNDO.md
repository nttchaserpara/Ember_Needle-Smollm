# Undo one action

Updated 15 September 2026. Ask `undo it` to restore the last supported change
performed by the current Ember session. Undo is a normal Needle-selected tool,
not a slash command, response mode, or keyword shortcut. The executor owns its
state; SmolLM only presents the reported result afterwards.

## Supported actions

| Platform | Actions |
| --- | --- |
| Windows | `set_volume`, `volume_up`, `volume_down`, `mute_volume`, `set_brightness` |
| Windows and Linux/Pi | `add_task`, `complete_task`, `remove_task` |

Windows volume restoration preserves the original scalar precision and mute
state and checks the audio endpoint identity. Brightness snapshots identify
each monitor and preserve its individual level. Restoration verifies readback.
Task completion/deletion captures the old row inside the same SQLite write
transaction as the action. Task undo compares the current row and restores only
that row inside a write transaction, preserving its ID and metadata. Task
snapshots retained for undo are limited to 8 KiB; large snapshots disable undo
for that action. No additional model, dependency or OS service is introduced.

## Slot semantics

- There is one slot per `ToolRegistry` session. New state-changing execution
  replaces it. Undo does not create a redo entry or expose older entries.
- Read-only tools, conversation-memory controls, routing refusals and invalid
  arguments do not replace the slot: no state-changing action was executed.
- Unsupported, failed, partial or interrupted state-changing executions block
  undo of earlier actions. A successful action that made no change also leaves
  nothing to reverse. Snapshot failure preserves normal tool execution, while
  making undo unavailable for that action.
- Unknown/custom tools are barriers by default. File/shell operations, app
  launches, bulk task clearing and other unlisted actions cannot be undone.
- A changed endpoint, monitor set, device value or task record blocks restoration.
  A supplied `target` (volume/brightness/task) must match the stored action;
  the executor never searches backwards to find a matching older action.
- Once a restore attempt begins, its slot is consumed, even if it fails or is
  interrupted. A wrong named target is rejected before beginning the attempt.
  An unverified restore reports partial failure, never an automatic retry.
- Restarting Ember discards the slot. Saved conversation text is never replayed
  to reconstruct an undo. Clearing conversation history does not clear the
  separate session undo slot.

The undo manager is platform-independent and small. Windows-only adapters check
the platform before importing COM dependencies or running PowerShell. Task
restoration works against the same SQLite code on Linux. These boundaries do
not constitute a physical Pi hardware or memory measurement.

## Routing and presentation

The tool catalogue now has 78 entries. Existing confidence thresholds, native
negation checks, argument validation and refusal of multiple proposed actions
remain in force. Optional target validation is an executor check, not proof
that the router correctly interpreted every target phrase.

After Needle selects one confident, affirmative undo call, the router preserves
an explicit `volume`, `brightness`, or `task` argument from the request. These
values come from the executor's enum. A conflicting/invented model target or
multiple named targets is rejected. This is argument grounding after selection;
it cannot select undo when Needle declined or chose a different tool. No undo
synonym parser, exact-match command bypass, extra model, or aliases are added.

`undo it` is the verified baseline request. Some variants still fail, including
`nevermind undo it` (the model may also propose shutdown cancellation) and
`undo the volume change` (the model may fail to select the new tool). These
remain failures in the evaluation fixtures; they are not rewritten into a
successful test. Multiple proposed actions are rejected without executing them.
No typo correction, general reference resolution or multi-step execution is
added by this feature.

Reply generation/fallback follows the existing automatic pipeline. Checks also
reject replies that lose undo direction, swap task creation/completion/deletion,
change mute state or describe restored absolute levels as relative changes.
Mechanical output checks are not a complete semantic guarantee.

## Verification

Real Needle/SmolLM with temporary task/history databases, no native device writes:

```powershell
.\venv\Scripts\python.exe scripts/verify_undo.py --output logs/undo.json
```

On Pi:

```sh
./venv/bin/python scripts/verify_undo.py --output logs/pi-undo.json
```

The script seeds two tasks, undoes the last one through the application, then
checks that a second undo does not remove the earlier task. It records final
outcomes, displayed-history agreement, time, peak agent/worker RSS and surviving
child processes. Native device adapters are not exercised by this script.

Routing-only regression with execution replaced by a recorder:

```powershell
.\venv\Scripts\python.exe scripts/evaluate_routing.py --cases experiments/undo_routing_cases.json --output logs/undo-routing.json
.\venv\Scripts\python.exe scripts/evaluate_routing.py --output logs/routing.json
```

Use the Linux interpreter for the same scripts on Pi. The routing report counts
correct proposals; it does not establish successful device restoration.

## Recorded verification

On Windows, 155 regression tests passed, covering temporary task storage,
undo-slot boundaries, stale-state rejection, output checks, mock audio and real
PowerShell against fake CIM providers. Real volume/brightness snapshot reads
also succeeded without changing the device settings. Native restoration was
not tested by modifying the user's hardware. The supplied 15 September Pi run
passed both temporary-task verification turns; native Windows adapters are
still only verified with mocked restoration and actual read-only snapshots.

The real application pipeline passed both verification turns: it undid the
last task, then rejected a second undo while preserving the earlier task. It
used real Needle/SmolLM, with one reply falling back. Requests took 4.529 and
4.378 seconds; peak combined RSS was 262.25 MiB, with no workers remaining.
Report: `logs/undo-pipeline-windows.json` (local).

Existing routing remained 26/41 with unchanged pass/fail cases. The 77 original
Needle schemas and confidence threshold are unchanged. Focused undo routing
passed 7/13 with zero executed proposals on negative cases. In that final run,
`undo it` and `undo the last action` passed; bare `undo` and `revert the last
change` also failed, in addition to the variants described above. Optional
named-target extraction was not reliable. These are known routing limits,
not successful undo tests. Reports: `logs/undo-routing-after.json` and
`logs/undo-routing-cases.json` (local).

The description and target-grounding changes on 15 September are measured
separately in [PI_VALIDATION.md](PI_VALIDATION.md). Failed phrases remain
positive expectations in the fixture. The negation case additionally requires
the `no_action` route, rather than merely accepting any result with no execution.
