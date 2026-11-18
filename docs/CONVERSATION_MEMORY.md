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

Topic lookup matches case-folded whole words across the request, answer and
arguments. One- and two-word queries require all terms. Longer queries require
at least two thirds of the distinct terms. Coverage takes priority within each
success group, followed by field weights and the concentration of query terms in
the request. Matching results precede failed attempts, with duplicate
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

### English history-intent and slot fixture

The small labeled corpus `scripts/fixtures/history_language_en.json` has 21
queries: eight history requests, eight other requests, and five requests that
should abstain and ask for clarification. It covers extracted topic and
session scope, including direct live-data requests and negated or compound
history requests. `scripts/evaluate_history_language.py` scores the bounded
history parser without invoking a model or tools. On Linux x86-64 it passed
21/21 cases: route accuracy 1.000, history-slot exact match 1.000, safe
non-history rate 1.000, and zero false history routes. These are parser-fixture
scores; they do not measure the Needle model's general language understanding.

On the Raspberry Pi Zero 2 W at `5e195f4`, the same corpus also passed 21/21
with route accuracy 1.000, slot exact match 1.000, safe non-history rate 1.000,
and zero false history routes. `scripts/test_explicit_history.py` passed on the
Pi, including both registry implementations and previous/current-session
isolation. The reported run did not capture RSS, available system RAM, swap, or
elapsed time, so it is a functional result rather than a resource benchmark.

`scripts/test_explicit_history.py` additionally sends the history and
abstention examples through both tool registries, checks live-query boundaries,
and verifies previous/current-session isolation.

Run the corpus and integration checks on the Pi with:

```sh
./venv/bin/python scripts/evaluate_history_language.py --output ~/emberos-baseline/history-language.json
./venv/bin/python scripts/test_explicit_history.py
```

### Compare a saved disk report with a current reading

In the CLI or web chat, ask `How much free disk space do I have?` to save a
disk report. Start a new EmberOS session using the same history database, then
ask `Compare my current disk space with last time.` The comparison uses the
previous saved session as defined above. In web mode, starting a new backend
session requires restarting the server.

The coordinator runs the following roles in order:

- The planner accepts only the bounded comparison request and emits the fixed
  `retrieval → os_action → disk_observer → verifier` plan. Other requests stay
  on the existing router path; negated and compound requests do not run tools.
- The retrieval specialist requests the newest complete, successful
  `disk_usage` turn in the previous session through the active registry. It
  passes on the history ID, mount, and rounded free-space value. Ordinary chat
  messages cannot supply a measurement.
- The OS action specialist proposes `disk_usage` with empty arguments. The
  coordinator accepts only that exact read-only proposal, validates its
  arguments with the active registry, and executes it through the registry.
- The disk observation specialist receives the bounded tool result, mount, and
  history ID, then selects the matching drive. It never receives conversation
  text. Missing historical evidence stops the request before the live check.
- The verifier compares the two reported values and labels both sources. If
  the current reading is unavailable or the mounts disagree, it abstains.

This first workflow recognizes a limited English comparison request and
supports the root mount on Linux and C: on Windows. It accepts the existing
disk table format, uses at most two tool calls, reads at most 4,096 characters
per table, and launches no generation worker. A five-second deadline is
checked between synchronous steps. A slow OS call can run past the deadline
before control returns. Repeated comparison answers are excluded from recall.
Each role receives a strict `AgentTask` envelope with a unique task ID, parent
task ID, and only its allowed inputs. It returns an `AgentResult` tied to that
task, with status and source ID where applicable. The observer gets only the
drive mount, history ID, and a bounded live tool result, not saved conversation
text. The coordinator rejects tool proposals outside its allowlist before
execution. The verifier gets typed readings and checks that both refer to the
same drive. The short audit trace records role, task/parent IDs, status,
reason, source ID, and the validated tool proposal; it does not store model
reasoning.

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
These checks also inject a `run_shell` proposal and verify that only the
history lookup runs; the proposed shell command is rejected before execution.

On Linux x86-64 the baseline did not produce a comparison. The new workflow
reported 40 GiB from synthetic history turn 1 and 33 GiB from the simulated
current reading, correctly identifying a decrease. The Linux x86-64
development worktree with strict task/result envelopes and a structured
read-only proposal passed with a 0.0093-second request, 50.74 MiB sampled
process-tree RSS, 7,840.73 MiB minimum available RAM, zero swap change, an
18.414-second full evaluator, and no remaining child processes. This is not a
Raspberry Pi result. The existing 16 recall and routing checks and
web/confirmation regressions also passed.

On the Raspberry Pi Zero 2 W, all three runs of the same synthetic request
failed at the baseline revision and passed with the disk comparison workflow.
The third run validates `a2b75e4` before the structured proposal step added
afterward; that latest step still needs its Pi run.

| Run | Revision | Comparison | Request (s) | Full evaluator process (s) | Sampled peak process tree RSS (MiB) | Minimum system RAM available (MiB) | Swap delta (MiB) | Children remaining |
| ---: | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |
| 1 | Before (`538e630`) | FAIL | 23.0043 | 124.435 | 52.75 | 243.00 | 0 | 0 |
| 1 | After (`4a98854`) | PASS | 0.0257 | 102.756 | 47.82 | 246.48 | 0 | 0 |
| 2 | Before (`538e630`) | FAIL | 22.8975 | 124.990 | 52.74 | 243.21 | 0 | 0 |
| 2 | After (`4a98854`) | PASS | 0.0244 | 102.968 | 47.82 | 246.32 | 0 | 0 |
| 3 | Before (`538e630`) | FAIL | 22.7119 | 124.396 | 52.77 | 241.58 | 0 | 0 |
| 3 | After (`a2b75e4`) | PASS | 0.0223 | 102.785 | 47.54 | 243.56 | 0 | 0 |

Request time covers the call to `handle_request` with the registry and
synthetic history already initialized. The full process includes setup and
teardown. The `538e630` revision predates this workflow, so these runs measure
the end-to-end feature against its absent baseline; they are not a direct
regression comparison between `4a98854` and `a2b75e4`. Each pair reported zero
swap change and no observed child processes. Runs 1 and 2 used the same output
path; the latest Pi report is saved as
`~/emberos-baseline/disk-comparison-agents.json`.

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

The Pi reproduced the baseline with 7/13 checks, P@5 of 0.218, MRR@5 of
0.545, Recall@5 of 0.636 and zero out-of-scope hits.

### Field-weighted lexical ranking

Notes and conversation history share a whole-word scoring function. Matches
in a note title or conversation request receive weight 4, tags or tool
arguments receive weight 2, and note contents or conversation answers receive
weight 1. When scores tie, the higher record ID comes first. The existing
session, error-output, and traceback filters still apply to conversation
history.

Notes use an on-disk SQLite FTS5 index to find candidates for ASCII queries
with at most 128 expanded terms. Existing notes are indexed when first opened
with this version. SQLite triggers maintain the derived index transactionally
on inserts, updates, deletes and undo. On SQLite builds without FTS5, notes
are scored directly from the source table; reopening with FTS5 rebuilds the
index after such writes. Note search retains the older substring lookup when
there are no ranked matches. History continues to scan its bounded store.

| Same 13-query fixture | First-hit or empty checks | P@5 | MRR@5 | Recall@5 | Scope leaks |
| --- | ---: | ---: | ---: | ---: | ---: |
| Baseline on Pi | 7/13 | 0.218 | 0.545 | 0.636 | 0 |
| Updated ranking on Linux x86-64 | 11/13 | 0.273 | 0.864 | 0.909 | 0 |
| Updated ranking on Pi Zero 2 W | 11/13 | 0.273 | 0.864 | 0.909 | 0 |

For both sources, storage records now precede the poster for `disk space`.
The one-word notes query `backup` is ambiguous between storage capacity and
restoration. The history query `grant due date` remains a missing funding
synonym at this checkpoint. Both expected-empty queries remain empty. These results cover a small English
fixture. To measure the updated version on the Pi, compare both revisions in
fresh processes with the same evaluator:

```sh
./venv/bin/python scripts/benchmark_retrieval.py --output ~/emberos-baseline/retrieval-ranking-comparison.json
```

The resource figures cover the full evaluator, including database creation,
exports, index setup and report writing. The separate regression script
`scripts/test_retrieval_ranking.py` exercises a battery-voltage topic,
session isolation, legacy note migration and index updates.

The Raspberry Pi Zero 2 W produced the following measurements in one run per
revision. Its report is saved at `~/emberos-baseline/retrieval-ranking-comparison.json`.

| Revision | Peak sampled process tree RSS (MiB) | Minimum system RAM available (MiB) | Swap delta (MiB) | Full evaluator (s) | Observed children remaining |
| --- | ---: | ---: | ---: | ---: | ---: |
| Before (`1fd1ddc`) | 15.65 | 269.12 | 0 | 0.926 | 0 |
| After (`62e15ad`) | 15.87 | 269.38 | 0 | 0.977 | 0 |

The Pi reran both suites after excluding mural styling mentions:

| Suite | Revision | Checks | P@5 | MRR@5 | Recall@5 | Peak RSS (MiB) | Minimum available (MiB) | Swap (MiB) | Duration (s) |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| Original, 13 queries | Before (`62e15ad`) | 11/13 | 0.273 | 0.864 | 0.909 | 15.89 | 259.22 | 0 | 0.999 |
| Original, 13 queries | After (`14346cc`) | 11/13 | 0.273 | 0.864 | 0.909 | 15.86 | 260.79 | 0 | 0.972 |
| Transfer, 12 queries | Before (`62e15ad`) | 9/12 | 0.178 | 0.778 | 0.778 | 15.90 | 261.35 | 0 | 0.871 |
| Transfer, 12 queries | After (`14346cc`) | 10/12 | 0.178 | 0.778 | 0.778 | 15.87 | 262.99 | 0 | 0.860 |

All four runs observed no child process remaining. The original suite retained
its quality scores. The updated transfer suite returned the expected empty
result for all three negative cases.

On the Pi, the mural filter left the original suite at 11/13. On the transfer
suite, it raised the first relevant result from 9/12 to 10/12 and changed the
empty-result check from 2/3 to 3/3. Scores were P@5 0.178, MRR@5 0.778 and
Recall@5 0.778 in both transfer runs. Peak RSS was 15.90 MiB or less and the
swap delta was 0 MiB.

### Small synonym groups for topic retrieval

A few bounded English term groups cover ceramics, water management and
recurring schedules. Retrieval treats “pottery oven” as evidence to look for
clay and kiln terms. It treats “irrigation routine” as a watering schedule.
For a saved answer such as “every Tuesday”, the schedule concept is added only
when the text explicitly says “every” followed by a weekday. Notes search
expands those terms before its FTS5 candidate lookup. Conversation search
applies the same normalized terms after filtering by session.

The regression test checks both paraphrases, verifies that a one-time mention
of Tuesday does not count as a routine, and retains the scoped history and
index migration checks.

| Linux x86-64 comparison to `14346cc` | Original, 13 queries | Transfer, 12 queries |
| --- | ---: | ---: |
| Before | 11/13, P@5 0.273, MRR@5 0.864, Recall@5 0.909 | 10/12, P@5 0.178, MRR@5 0.778, Recall@5 0.778 |
| With synonym groups | 11/13, P@5 0.273, MRR@5 0.864, Recall@5 0.909 | 12/12, P@5 0.222, MRR@5 1.000, Recall@5 1.000 |

All six expected-empty checks across both suites remained empty. Scope leaks
remained at zero. In the synthetic evaluator, sampled peak RSS changed from
18.11 to 18.16 MiB for the original suite and from 18.15 to 18.12 MiB for the
transfer suite. The updated transfer run took 0.302 seconds. Device results
for the synonym change are recorded below.

Run both comparable Pi suites after pulling the change:

```sh
for suite in original transfer; do
  ./venv/bin/python scripts/benchmark_retrieval.py --baseline 14346cc --suite "$suite" \
    --output "$HOME/emberos-baseline/retrieval-synonyms-$suite.json" || break
done
```

The Pi measured the first synonym expansion at commit `9d92867`:

| Suite | Revision | Checks | P@5 | MRR@5 | Recall@5 | Peak RSS (MiB) | Minimum available (MiB) | Swap (MiB) | Duration (s) |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| Original, 13 queries | Before (`14346cc`) | 11/13 | 0.273 | 0.864 | 0.909 | 15.85 | 265.53 | 0 | 1.000 |
| Original, 13 queries | After (`9d92867`) | 11/13 | 0.273 | 0.864 | 0.909 | 15.87 | 269.43 | 0 | 0.998 |
| Transfer, 12 queries | Before (`14346cc`) | 10/12 | 0.178 | 0.778 | 0.778 | 15.85 | 270.31 | 0 | 0.893 |
| Transfer, 12 queries | After (`9d92867`) | 12/12 | 0.222 | 1.000 | 1.000 | 15.87 | 270.18 | 0 | 0.942 |

None of the four runs left child processes. The transfer suite now retrieves
both paraphrases while preserving all three expected-empty results.

The first 13-query suite used the ambiguous one-word query `backup` with only
the restore note labeled relevant. A later review changed that query to the
clearer `restore backup`. I also added small funding and deadline term groups,
so `grant due date` can match a saved scholarship deadline when the request
and result support it.

| Linux x86-64, corrected label | Before (`9d92867`) | After (`a02ec95`) |
| --- | ---: | ---: |
| Original, 13 queries | 12/13, P@5 0.273, MRR@5 0.909, Recall@5 0.909 | 13/13, P@5 0.291, MRR@5 1.000, Recall@5 1.000 |
| Transfer, 12 queries | 12/12, P@5 0.222, MRR@5 1.000, Recall@5 1.000 | 12/12, P@5 0.222, MRR@5 1.000, Recall@5 1.000 |

All five expected-empty queries stayed empty and the requested session scopes
had no leaks. Sampled peak RSS was 18.16 MiB before and 18.17 MiB after for
the transfer set. The Pi results below confirm the same relevance scores on
the target device.

The Pi completed both sets with the corrected query and funding terms:

| Suite | Revision | Checks | P@5 | MRR@5 | Recall@5 | Peak RSS (MiB) | Minimum available (MiB) | Swap (MiB) | Duration (s) |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| Original, 13 queries | Before (`9d92867`) | 12/13 | 0.273 | 0.909 | 0.909 | 15.86 | 272.91 | 0 | 1.058 |
| Original, 13 queries | After (`a02ec95`) | 13/13 | 0.291 | 1.000 | 1.000 | 15.90 | 272.57 | 0 | 0.972 |
| Transfer, 12 queries | Before (`9d92867`) | 12/12 | 0.222 | 1.000 | 1.000 | 15.86 | 272.02 | 0 | 0.902 |
| Transfer, 12 queries | After (`a02ec95`) | 12/12 | 0.222 | 1.000 | 1.000 | 15.96 | 271.85 | 0 | 0.860 |

All four runs reported zero swap change and no remaining child processes.
The corrected original suite now passes every case on the Pi. Peak RSS rose
0.04 MiB on that suite and 0.10 MiB on the transfer suite.

Measure the funding and deadline term groups on the Pi with:

```sh
for suite in original transfer; do
  ./venv/bin/python scripts/benchmark_retrieval.py --baseline 9d92867 --suite "$suite" \
    --output "$HOME/emberos-baseline/retrieval-funding-$suite.json" || break
done
```

### Ranking on additional topics

A second synthetic English fixture was labeled after the ranker at `62e15ad`
was frozen. It contains 12 queries about transport, maintenance, cinema and
other topics across notes and three conversation sessions. Nine queries have
relevant records and three expect an empty response. Both implementations
received the same fixture and evaluator.

| Linux x86-64 | First-hit or empty checks | P@5 | MRR@5 | Recall@5 | Expected empty returned empty | Scope leaks |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Original ranking | 5/12 | 0.133 | 0.444 | 0.556 | 2/3 | 0 |
| Field-weighted ranking | 9/12 | 0.178 | 0.778 | 0.778 | 2/3 | 0 |

At `62e15ad` the ranker missed two paraphrases. It also returned a mural discussion
for a current-session train timetable query even though the session contained
no departure times. That hit came from the correct session. To reproduce:

```sh
./venv/bin/python scripts/benchmark_retrieval.py --suite transfer --output ~/emberos-baseline/retrieval-new-topics.json
```

History search now handles one English pattern for incidental mentions. In
“review the train timetable mural”, the topic modifies a visual artifact.
When the whole reply is a short styling instruction such as “Use green paint”,
and neither the reply nor the tool arguments mention the topic, the turn is
omitted for that topic. Searches for the artwork or styling can still retrieve
it. This rule does not verify arbitrary claims or interpret paraphrases.

With this change the additional fixture passed 10/12 locally. All three
expected-empty queries returned no results. The positive-query MRR@5 stayed
at 0.778, and the original fixture stayed at 11/13. To isolate this change on
the Pi, pass `--baseline 62e15ad` when benchmarking each suite.
