"""Safe OS-agent fallback and lazy, native SmolLM2 text client."""

OS_AGENT_RESPONSE = (
    "I'm currently running as a local OS agent. I can help with applications, "
    "files, windows, tasks, and system status, but I don't have a verified "
    "source for general or current facts, so I won't guess an answer."
)


def get_local_text_client():
    from use_cases.local_llm import LocalTextClient
    return LocalTextClient()


def run_fallback(query: str):
    return OS_AGENT_RESPONSE, False
