"""Local AI utilities built on Ollama.

The project uses a free local model for optional narrative summaries and analyst
assistants without sending sensitive risk data to a remote API.
"""

from quantrisk.ai.ollama_client import DEFAULT_MODEL, generate_summary, is_ollama_available

__all__ = ["DEFAULT_MODEL", "generate_summary", "is_ollama_available"]
