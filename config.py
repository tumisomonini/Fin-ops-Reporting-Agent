"""
Configuration for the Finance/Ops Report Summarizer agent.

All values are read from environment variables (or a .env file, if present)
so credentials never need to be hardcoded.
"""
import os
from dotenv import load_dotenv

load_dotenv()

# --- Neo4j ---
NEO4J_URI = os.environ.get("NEO4J_URI", "bolt://localhost:7687")
NEO4J_USER = os.environ.get("NEO4J_USER") or os.environ.get("NEO4J_USERNAME", "neo4j")
NEO4J_PASSWORD = os.environ.get("NEO4J_PASSWORD", "")
NEO4J_DATABASE = os.environ.get("NEO4J_DATABASE", "")

# --- Anthropic ---
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
CLAUDE_MODEL = os.environ.get("CLAUDE_MODEL", "claude-sonnet-4-6")

# --- Thresholds used by the anomaly-detection queries ---
BUDGET_OVERRUN_PCT = float(os.environ.get("BUDGET_OVERRUN_PCT", "15"))   # % over budget to flag
LARGE_TRANSACTION_ZAR = float(os.environ.get("LARGE_TRANSACTION_ZAR", "50000"))  # flag as "large" for review
OVERDUE_GRACE_DAYS = int(os.environ.get("OVERDUE_GRACE_DAYS", "0"))      # days past due before "overdue"

# --- Memory Settings ---
MEMORY_CONVERSATION_MAX_HISTORY = int(os.environ.get("MEMORY_CONVERSATION_MAX_HISTORY", "100"))  # Max conversation messages to keep
MEMORY_REASONING_PERSIST_FILE = os.environ.get("MEMORY_REASONING_PERSIST_FILE", "reasoning_traces.json")  # File for reasoning traces
MEMORY_CONVERSATION_PERSIST_FILE = os.environ.get("MEMORY_CONVERSATION_PERSIST_FILE", "conversation_history.json")  # File for conversation history
MEMORY_ENABLE_NEO4J_STORAGE = os.environ.get("MEMORY_ENABLE_NEO4J_STORAGE", "true").lower() == "true"  # Store reasoning in Neo4j
MEMORY_ENABLE_REASONING = os.environ.get("MEMORY_ENABLE_REASONING", "false").lower() == "true"  # Enable reasoning traces by default


def require_config():
    """Raise a clear error early if required credentials are missing."""
    missing = []
    if not NEO4J_PASSWORD:
        missing.append("NEO4J_PASSWORD")
    if not ANTHROPIC_API_KEY:
        missing.append("ANTHROPIC_API_KEY")
    if missing:
        raise EnvironmentError(
            f"Missing required environment variables: {', '.join(missing)}. "
            "Set them in your environment or in a .env file (see .env.example)."
        )
