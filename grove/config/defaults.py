"""Default configuration values for Grove.

Used by ``grove init`` to write the initial ``.grove/config.yaml``.
The structure here must match the nested provider/routing schema
defined in ARCH.md exactly.
"""

from __future__ import annotations

# Directory structure created by ``grove init``
GROVE_DIRS: list[str] = [
    ".grove",
    ".grove/prompts",
    ".grove/logs",
    "raw",
    "wiki",
    "queries",
    "outputs",
]

# Default config.yaml content — mirrors ARCH.md schema verbatim.
# ${ANTHROPIC_API_KEY} is written literally so ConfigLoader can
# interpolate it from the environment at load time.
DEFAULT_CONFIG: dict[str, object] = {
    "llm": {
        "providers": {
            "anthropic": {
                "api_key": "${ANTHROPIC_API_KEY}",
            },
            "ollama": {
                "base_url": "http://localhost:11434",
            },
            "azure_foundry": {
                "endpoint": "${AZURE_AI_ENDPOINT}",
                "api_key": "${AZURE_AI_KEY}",
                "deployment": "",
            },
            "openai": {
                "api_key": "${OPENAI_API_KEY}",
                "base_url": "https://api.openai.com/v1",
            },
            "mistral": {
                "api_key": "${MISTRAL_API_KEY}",
                "base_url": "https://api.mistral.ai/v1",
            },
        },
        "routing": {
            "fast": {
                "provider": "ollama",
                "model": "llama3.2",
                "fallback": {
                    "provider": "anthropic",
                    "model": "claude-haiku-4-5-20251001",
                },
            },
            "standard": {
                "provider": "anthropic",
                "model": "claude-sonnet-4-6",
            },
            "powerful": {
                "provider": "anthropic",
                "model": "claude-opus-4-6",
            },
        },
    },
    "budget": {
        "daily_limit_usd": 5.00,
        "warn_at_usd": 3.00,
    },
    "compile": {
        "quality_threshold": "partial",
        "phase": 0,
        "max_output_tokens": 65536,
    },
    "search": {
        "embedding_model": "nomic-embed-text",
        "hybrid_alpha": 0.5,
    },
    "git": {
        "auto_commit": True,
        "commit_message_prefix": "grove:",
    },
}

# .gitignore lines written by ``grove init``
GITIGNORE_LINES: list[str] = [
    ".grove/search.db",
    ".grove/logs/",
]

# Empty state written by ``grove init``
EMPTY_STATE: dict[str, object] = {}
