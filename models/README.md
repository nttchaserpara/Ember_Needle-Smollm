# Local model files

The setup script downloads **SmolLM2-135M-Instruct-Q4_K_M.gguf** here.
See the [setup guide](../docs/setup/LOCAL_LLM.md). Run commands from the project root.
`manifest.json` records its source revision, size, and SHA-256 checksum.

```sh
python scripts/setup_local_llm.py --model-only
```

The model is 105,454,432 bytes (about 100.6 MiB). Model binaries are excluded
from Git. After cloning, run setup again or copy the verified GGUF from the PC
into this directory. There is no automatic download during a user request.

Source: [bartowski/SmolLM2-135M-Instruct-GGUF](https://huggingface.co/bartowski/SmolLM2-135M-Instruct-GGUF).
Base model: [HuggingFaceTB/SmolLM2-135M-Instruct](https://huggingface.co/HuggingFaceTB/SmolLM2-135M-Instruct).
The model repositories declare the Apache-2.0 license; retain applicable
license/attribution information when redistributing model artifacts.
