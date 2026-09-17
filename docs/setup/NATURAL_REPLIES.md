# Natural replies

Updated 15 September 2026. Short outcomes automatically use the existing
SmolLM2-135M-Instruct Q4_K_M through llama.cpp. Needle selects the tool, the
executor returns its outcome, then SmolLM writes the response. If generation
fails or output checks reject it, the original response is used automatically.
The application displays one answer; there are no reply mode controls.

## Setup and normal use

Use the same runtime and model as document summaries; no new dependency or
model is needed. Restart the application after updating its code:

```powershell
.\venv\Scripts\python.exe run_ember.py
```

On Raspberry Pi OS Lite:

```sh
./venv/bin/python run_ember.py
```

Follow [LOCAL_LLM.md](LOCAL_LLM.md) if the runtime/model is not installed.
Enter ordinary requests, such as `what is my current volume` on Windows.
Volume and brightness actions currently report unsupported on Linux.

## Context and accuracy

The model receives the current request and reported outcome. For eligible
tool outcomes it also receives up to two complete saved turns for **the same
tool**, oldest first. A parameterized SQLite lookup selects recent records
across saved sessions after execution. It neither classifies intent nor fills
tool arguments. No embedding model or extra model worker is added.

History supplies conversational context, not current device state. Only the
current tool result supplies current facts. Each saved context turn is limited
to 200 request characters and 300 response characters; longer or previously
truncated turns are skipped intact. History lookup failure allows generation
from the current result alone. Unresolved requests do not trigger this lookup.
General semantic recall and conversational clarification remain separate work.

This is context use during inference, **not training or weight updates**.
The model has no hardcoded response table or training examples in the runtime
reply layer. It may still use identical wording when the source already reads
naturally. Different wording is not required for acceptance. Temperature stays
at zero to avoid adding randomness purely for variety.

- Complete, short, plain outcomes are eligible for generation. Raw file and
  clipboard contents, notes/history search results, structured output, tables,
  long messages and successful document summaries are displayed intact.
- The current result is bounded to 1,000 characters; long results are never
  cut into a shorter prompt. A request over 500 characters is omitted whole
  from reply context. This does not change what the router receives.
- Generation requests at most 80 tokens, with a 450-character/65-word output
  cap. Token/context overflow, empty output, timeout, missing runtime/model,
  cancellation or rejected text uses the original response. No retry is made.
- Output checks cover number occurrences, units, references, quoted facts,
  selected structured facts, failure/refusal markers and some action claims.
  Generated status labels are rejected; code supplies the actual outcome label.
  These checks do **not** prove semantic equivalence.
- Error, partial, and refusal outcomes must retain the complete original
  wording (case, spacing and punctuation may differ). A negative word alone
  does not preserve a diagnosis. Added explanations, advice, or file locations
  therefore trigger fallback, even if the candidate also quotes the error.
  This deliberately limits paraphrasing of diagnostic data; successful short
  outcomes still support natural wording with the existing fact checks.
- The displayed answer is saved once. The tool status, arguments and result
  remain intact. Presentation and storage failures cannot re-execute an action.

## Windows and Pi deployment

The same reply pipeline runs on Windows and Linux. `LocalTextClient` chooses
`llama-server.exe` on Windows and `llama-server` on Linux, and only supplies
Windows process flags on Windows. Linux runtime builds are platform-specific;
the GGUF is shared. See the setup guide for ARM build instructions.

Reply generation uses the existing CPU configuration: no GPU layers, one
worker slot, four threads and a 2,048-token context by default. Bounded history
fits inside that existing context; token accounting rejects overflow. The
normal worker is stopped and reaped after each job. The previously available
`EMBER_LLM_PERSISTENT=1` deployment setting changes residency and is outside
the baseline evaluation below.

`EMBER_REPLY_TIMEOUT` sets the whole generation budget, including startup;
it defaults to 45 seconds. Existing model, runtime, thread and context settings
are shared with summaries. Summary jobs retain their own timeouts and prompts.
The reply budget can be configured for a device without changing application
behavior. A timeout selects the fallback.

There is no need for a second reply implementation merely because the CPU is
ARM. Native executable selection differs; the outcome and memory logic is
portable. Windows tests and mocked Linux startup do not establish total RAM
usage or latency on a physical 512 MB Pi Zero 2 W.

## Automated evaluation

The maintained evaluator runs the application reply pipeline against fixed
outcomes and explicit saved histories, without executing OS actions or writing
the user's conversation database:

```powershell
.\venv\Scripts\python.exe scripts/evaluate_replies.py --output logs/replies.json
```

```sh
./venv/bin/python scripts/evaluate_replies.py --output logs/pi-replies.json
```

Use `--case volume_changed_with_history` to select one case. Each case has an
isolated temporary database. The report contains the original result, candidate,
selected answer, rejection reason, used history IDs, generation diagnostics,
time, simultaneous agent/worker peak RSS and remaining processes. Needle is
imported for RAM sampling; its routing correctness is not evaluated here.

A nonzero exit can mean an expected model reply fell back. Generated-answer
coverage and execution/storage correctness are reported separately; a working
fallback is not counted as successful model generation. Inspect candidate
meaning as well as the automatic checks. Test reports are local under `logs/`.

The Windows evaluation on 14 September used generated text for 9 of 14 eligible
outcomes and fell back on 5; 3 payload cases bypassed generation. All execution,
history-selection and storage checks passed. A past success could still make
the model claim success for a current failure; output checks rejected that
case. Context support therefore does not establish general understanding.
Generation took 1.28-2.27 seconds with peak agent/worker RSS of 250.46 MiB and
no remaining workers (`logs/replies-context-2026-09-14.json`, local report).
Physical Pi validation was still pending at that point.

The supplied Pi run on 15 September completed the application reply pipeline,
with 10 generated replies and 7 fallbacks among 17 eligible cases. However,
accepted deletion/brightness errors added an unsupported location or replaced
the cause. Those were false acceptances, not successful factual paraphrases.
The diagnostic check above closes that observed gap. Captured bad candidates
are permanent fixtures, replayed regardless of future model output:

```sh
./venv/bin/python scripts/evaluate_replies.py --validation-only --output logs/pi-reply-guards.json
```

The full evaluator also runs these checks and reports them under
`validation_regressions`. It still reports generation coverage separately;
falling back accurately must not be presented as improved generation quality.
Current results and remaining limitations: [Pi validation](PI_VALIDATION.md).
