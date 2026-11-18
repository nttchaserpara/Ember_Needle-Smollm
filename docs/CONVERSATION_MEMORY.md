# Conversation memory

[Back to the project guide](../README.md) · [Usage examples](USAGE.md#5-memory-percakapan)

Run shell commands below from the repository root with the project's Python
environment. Enter `/memory` commands at Ember's `You>` prompt.

The interactive CLI saves requests, displayed answers, tool arguments, reported
outcomes, and UTC timestamps in `data/conversation.sqlite3`. History survives
restarts and stays local; `data/` is excluded from Git. Existing logs are not
imported. Saved user text and tool output are historical records, not verified
facts or instructions to execute again.

At the `You>` prompt:

```text
/memory
/memory search scholarship
/memory clear
```

These explicit CLI commands show recent history, search by topic words, and
clear conversation history. They do not need model inference. Clearing history
does not clear separate tool logs, notes, tasks, or backups. Set `EMBER_MEMORY=0`
before starting Ember to disable history reads and writes without deleting it:

```bash
EMBER_MEMORY=0 ./venv/bin/python run_ember.py
```

In PowerShell, set `$env:EMBER_MEMORY = '0'` before running Ember; remove the
variable with `Remove-Item Env:EMBER_MEMORY` to enable memory on the next launch.

Each input is a fresh request. The experimental numbered memory menus and
pending-answer state have been removed. Device-information tools do not request
a conversation-versus-RAM choice. An unresolved request does not capture the
next message as a search topic.

The router recognizes a limited set of explicit English recall requests.
For example, `Which budgets did we discuss last time?` searches for
`budgets` in the previous saved session. `Show my recent conversations`
lists recent excerpts across sessions. These forms use the existing history
tool through the active registry. A recognized history request with an unclear
scope or additional action asks for clarification.

For other inputs, a small Needle selection view checks for conversation
recall. It can prevent a conflicting live action but cannot execute tools or
turn the full question into a search query. The main Needle router must select
the history tool and generate valid arguments at the existing confidence
threshold. A disagreement or uncertain result returns an unresolved status
with `/memory` command guidance, without opening a menu. Both views use the
existing engine and weights. The selection descriptions distinguish remembered
conversation contents from physical RAM capacity. In the Windows regression
check, "what's in memory now?" now declines instead of reading hardware;
it still does not successfully retrieve history through natural language.

Natural-language history and time interpretation remain unreliable. Removing
the menus restores independent input handling; it does not solve semantic
retrieval. Use `/memory` and `/memory search <topic>` for direct access to the
stored history while model-based conversation handling is evaluated separately.

Lookup is literal: every topic word must match a whole word in a saved request,
answer, or arguments. Matching results precede failed attempts, with duplicate
excerpts suppressed. Failed response text is excluded from matching; failures
remain searchable by their request/arguments and retain their original status.
A no-match result never falls back to unrelated recent history. For a Python
traceback frame pasted into a request, lookup skips the parent directories and
keeps the filename searchable. The saved excerpt still shows the original path.

The backend supports all, current, and previous saved sessions. The previous
session is the latest earlier session containing retained non-recall turns,
not necessarily yesterday or the latest discussion of a particular topic.
Session filtering happens before ranking; the old UI no longer selects it.

Brief outcome replies can now be rephrased by the existing local model after
execution. The displayed answer is stored in history; debug comparisons are
not stored as extra conversations. This presentation layer does not resolve
history intent, reference resolution or multi-step clarification. Evaluate
faithfulness, latency and total RAM on the physical Pi before relying on it.

Evaluate storage and actual fresh-request routing separately:

```bash
python scripts/evaluate_memory.py --output logs/memory-content.json
python scripts/evaluate_memory.py --with-routing --output logs/memory-routing.json
```

These checks use disposable history and stub all non-history tools. They never
open the user's database. A nonzero exit code reports unmet expectations;
known history-routing failures are not counted as successes. The routing-only
suite is `scripts/evaluate_routing.py`; `--without-memory` disables storage
while preserving production intent guards. `--catalogue-only` bypasses the
history guard for an isolated catalogue diagnostic.

History is not automatically appended to OS-action prompts. Bounded file-reference
resolution and opt-in multi-step execution exist, but arbitrary action chains
and general conversational reference resolution are not reliable.

Memory uses Python's standard SQLite library, with no embedding model or
SmolLM worker. It retains up to 1,000 turns, caps each request/answer/arguments
field at 2/4/2 KiB (previews are marked), and returns at most five excerpts within
6,000 bytes. SQLite's page cache target is 256 KiB and database page allocation
is capped at 16 MiB; these are not limits on total process RAM. SQLite journals
may temporarily take additional disk space. Topic search uses bounded text
matching, not semantic embeddings or model training. A storage failure reports
that the turn was not saved and does not rerun the tool.

### Pi Zero 2 W recall checkpoint

The device run of `scripts/evaluate_memory.py` passed 7/9 checks at
`f2ed092` and 9/9 at `46998d2`. The two recovered cases cover traceback
exclusion and first-result document ranking within the current session.
Both runs used the evaluator's temporary SQLite database with routing disabled.

Those functional reports did not measure peak memory. To collect a comparable
resource report on the Pi, run the following from a clone containing both commits:

```sh
./venv/bin/python scripts/benchmark_history_recall.py --output ~/emberos-baseline/recall-resources.json
```

The benchmark reads both snapshots from Git. It runs each revision three times
in a fresh process and records sampled peak RSS (parent and children), minimum
system available RAM, system swap change, observed children remaining, and
elapsed time including interpreter startup, fixture setup and report writing.
The external monitor uses psutil and waits 10 ms between samples. Brief spikes
can fall between samples. Model context and inference threads are inapplicable
for this workload.

The following measurements come from the six terminal results reported from
the Pi Zero 2 W. The device saved the JSON report to
`~/emberos-baseline/recall-resources.json`.

| Revision | Run | Checks passed | Sampled peak process tree RSS (MiB) | Minimum system RAM available (MiB) | Elapsed (s) |
| --- | ---: | ---: | ---: | ---: | ---: |
| Before (`f2ed092`) | 1 | 7/9 | 14.21 | 271.61 | 0.697 |
| After (`46998d2`) | 1 | 9/9 | 14.17 | 271.72 | 0.692 |
| After | 2 | 9/9 | 14.16 | 271.71 | 0.691 |
| Before | 2 | 7/9 | 14.21 | 271.62 | 0.719 |
| Before | 3 | 7/9 | 14.21 | 264.51 | 0.693 |
| After | 3 | 9/9 | 14.16 | 264.56 | 0.693 |

Each run reported a system swap delta of 0 MiB and zero observed child processes
remaining at completion. Median elapsed time was 0.697 s before and 0.692 s
after. The largest sampled process tree RSS across the three runs was 14.21 MiB
before and 14.17 MiB after.

### Pi Zero 2 W before and after explicit history routing

At commit `40b1e57`, one device run with `--with-routing` passed 11/16
checks. Direct retrieval passed 9/9. Of the seven natural-language checks,
both live disk requests passed and all five history requests failed. A history
check requires both the expected tool call and the expected content. Non-history
tool calls use a stub in this evaluator.

The Python wrapper recorded 122.23 seconds for the full run and 56.21 MiB
maximum RSS using Linux `getrusage(RUSAGE_CHILDREN).ru_maxrss`. This run
exited with code 1 for failed checks. The Pi saved per-case diagnostics in
`~/emberos-baseline/routing-before.json` and resource measurements in
`~/emberos-baseline/routing-before-resources.json`.

The Pi diagnostics showed that two requests stopped at the history selector.
For recent conversations the main router proposed history with an empty scope
and low confidence. For pending tasks it proposed a live task list and
abstained. For the RAM discussion it called the evaluator's CPU-info stub at
confidence 0.7339.

On Linux x86-64 the synthetic evaluator passed 11/16 before explicit recall
routing and 16/16 afterward. The Pi also passed all 16 checks at `538e630`.

| Pi revision | Direct recall | History requests | Live disk requests | Full run (s) | Maximum RSS (MiB) | Exit code |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Before (`40b1e57`) | 9/9 | 0/5 | 2/2 | 122.23 | 56.21 | 1 |
| After (`538e630`) | 9/9 | 5/5 | 2/2 | 16.703 | 53.05 | 0 |

Each row represents one full evaluator run on the Pi. Both used the same nine
recall and seven routing cases and the same Linux peak RSS method. The device
saved the after-run reports as `routing-after.json` and
`routing-after-resources.json` in `~/emberos-baseline/`.

To repeat the baseline method on the Pi and keep the original reports:

```sh
./venv/bin/python scripts/benchmark_memory_routing.py --output ~/emberos-baseline/routing-after.json
```

### Compare a saved disk report with a current reading

In the CLI or web chat, ask `How much free disk space do I have?` to save a
disk report. Start a new EmberOS session using the same history database, then
ask `Compare my current disk space with last time.` The comparison uses the
previous saved session as defined above. In web mode, starting a new backend
session requires restarting the server.

The coordinator runs the following roles in order:

- The retrieval specialist requests the newest complete, successful
  `disk_usage` turn in the previous session through the active registry. It
  passes on the history ID, mount, and rounded free-space value. Ordinary chat
  messages cannot supply a measurement.
- The disk observation specialist receives that mount, requests one current
  `disk_usage` reading through the same registry, and selects the matching
  drive. Missing historical evidence stops the request before this step.
- The verifier compares the two reported values and labels both sources. If
  the current reading is unavailable or the mounts disagree, it abstains.

This first workflow recognizes a limited English comparison request and
supports the root mount on Linux and C: on Windows. It accepts the existing
disk table format, uses at most two tool calls, reads at most 4,096 characters
per table, and launches no generation worker. A five-second deadline is
checked between synchronous steps. A slow OS call can run past the deadline
before control returns. Repeated comparison answers are excluded from recall.
The result carries a short role/status trace with the historical turn ID.

To evaluate the same prompt on the Pi at baseline commit `538e630` and the
current checkout, run:

```sh
./venv/bin/python scripts/evaluate_disk_comparison.py --output ~/emberos-baseline/disk-comparison.json
```

The evaluator reads the older sources from Git, uses disposable databases and
stubs device actions. It prints separate request and full-process durations,
sampled peak RSS, minimum available system RAM, swap change, and remaining
observed workers. Acceptance requires the correct call order, source ID, old
and new values, and direction of change. Context size and generation threads
are inapplicable to this deterministic workflow. Additional CLI and web
registry checks run with `./venv/bin/python scripts/test_disk_comparison.py`.

On Linux x86-64 the baseline did not produce a comparison. The new workflow
reported 40 GiB from synthetic history turn 1 and 33 GiB from the simulated
current reading, correctly identifying a decrease. The existing 16 recall and
routing checks also passed.

On the Raspberry Pi Zero 2 W, both runs of that same synthetic request failed
at the baseline revision and passed at the revision with disk comparison.

| Run | Revision | Comparison | Request (s) | Full evaluator process (s) | Sampled peak process tree RSS (MiB) | Minimum system RAM available (MiB) |
| ---: | --- | --- | ---: | ---: | ---: | ---: |
| 1 | Before (`538e630`) | FAIL | 23.0043 | 124.435 | 52.75 | 243.00 |
| 1 | After (`4a98854`) | PASS | 0.0257 | 102.756 | 47.82 | 246.48 |
| 2 | Before (`538e630`) | FAIL | 22.8975 | 124.990 | 52.74 | 243.21 |
| 2 | After (`4a98854`) | PASS | 0.0244 | 102.968 | 47.82 | 246.32 |

All four measurements reported a system swap delta of 0 MiB and no observed
children remaining. Request time covers the call to `handle_request` with the
registry and synthetic history already initialized. The full process includes
setup and teardown. Both terminal summaries are recorded here. The two runs
used the same output path, so the JSON file on the Pi contains the second run.

### Relevance baseline for notes and conversation history

From the repository root, run:

```sh
python3 scripts/evaluate_retrieval.py --output ~/emberos-baseline/retrieval-ranking-before.json
```

The evaluator sends 13 English queries to the existing search methods and
compares their first five results against hand-labeled records. It creates
nine notes with text exports and eight conversation turns across three sessions
in a temporary directory. Notes and history are searched independently.
The report lists the intended information need, scope, relevant records and
actual ranking for every query. Short queries such as `backup` can have
several interpretations; the fixture labels the data-restoration intent.

For the 11 queries with relevant records, Precision@5 is the number of
relevant hits divided by five, including unfilled positions. MRR@5 averages
the reciprocal rank of the first relevant hit, using zero for a miss.
Recall@5 measures the fraction of all labeled relevant records retrieved.
The two queries with no relevant records are scored separately for an empty
response. A case passes when the first hit is relevant or an expected empty
response is empty, and all returned records respect the requested scope.

| Local baseline | Queries with relevant records | Precision@5 | MRR@5 | Recall@5 |
| --- | ---: | ---: | ---: | ---: |
| Notes | 6 | 0.167 | 0.417 | 0.500 |
| History | 5 | 0.280 | 0.700 | 0.800 |
| All | 11 | 0.218 | 0.545 | 0.636 |

Two fresh local runs of the implementation at `dc68b77` produced identical
rankings. Seven of 13 first-hit or empty-result checks passed, including both
empty results. No out-of-scope history was returned. For `disk space`, both
searches ranked a poster discussion ahead of storage information. The notes
query `deadline scholarship` missed the note titled “Scholarship deadline.”
The evaluator writes its JSON report before exiting with code 1 when any
check fails.
