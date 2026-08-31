"""Application configuration management.

This module handles environment-specific configuration loading, parsing, and management
for the application. It includes environment detection, .env file loading, and
configuration value parsing.
"""

import os
from enum import Enum
from pathlib import Path

from dotenv import load_dotenv


# Define environment types
class Environment(str, Enum):
    """Application environment types.

    Defines the possible environments the application can run in:
    development, staging, production, and test.
    """

    DEVELOPMENT = "development"
    STAGING = "staging"
    PRODUCTION = "production"
    TEST = "test"


# Determine environment
def get_environment() -> Environment:
    """Get the current environment.

    Returns:
        Environment: The current environment (development, staging, production, or test)
    """
    match os.getenv("APP_ENV", "development").lower():
        case "production" | "prod":
            return Environment.PRODUCTION
        case "staging" | "stage":
            return Environment.STAGING
        case "test":
            return Environment.TEST
        case _:
            return Environment.DEVELOPMENT


# Load appropriate .env file based on environment
def load_env_file():
    """Load environment-specific .env file."""
    env = get_environment()
    print(f"Loading environment: {env}")
    base_dir = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))

    # Define env files in priority order
    env_files = [
        os.path.join(base_dir, f".env.{env.value}.local"),
        os.path.join(base_dir, f".env.{env.value}"),
        os.path.join(base_dir, ".env.local"),
        os.path.join(base_dir, ".env"),
    ]

    # Load the first env file that exists
    for env_file in env_files:
        if os.path.isfile(env_file):
            load_dotenv(dotenv_path=env_file)
            print(f"Loaded environment from {env_file}")
            return env_file

    # Fall back to default if no env file found
    return None


ENV_FILE = load_env_file()


# Parse list values from environment variables
def parse_list_from_env(env_key, default=None):
    """Parse a comma-separated list from an environment variable."""
    value = os.getenv(env_key)
    if not value:
        return default or []

    # Remove quotes if they exist
    value = value.strip("\"'")
    # Handle single value case
    if "," not in value:
        return [value]
    # Split comma-separated values
    return [item.strip() for item in value.split(",") if item.strip()]


# Parse dict of lists from environment variables with prefix
def parse_dict_of_lists_from_env(prefix, default_dict=None):
    """Parse dictionary of lists from environment variables with a common prefix."""
    result = default_dict or {}

    # Look for all env vars with the given prefix
    for key, value in os.environ.items():
        if key.startswith(prefix):
            endpoint = key[len(prefix) :].lower()  # Extract endpoint name
            # Parse the values for this endpoint
            if value:
                value = value.strip("\"'")
                if "," in value:
                    result[endpoint] = [item.strip() for item in value.split(",") if item.strip()]
                else:
                    result[endpoint] = [value]

    return result


class Settings:
    """Application settings without using pydantic."""

    def __init__(self):
        """Initialize application settings from environment variables.

        Loads and sets all configuration values from environment variables,
        with appropriate defaults for each setting. Also applies
        environment-specific overrides based on the current environment.
        """
        # Set the environment
        self.ENVIRONMENT = get_environment()

        # Application Settings
        self.PROJECT_NAME = os.getenv("PROJECT_NAME", "QA Agent")
        self.VERSION = os.getenv("VERSION", "1.0.0")
        self.DESCRIPTION = os.getenv("DESCRIPTION", "A production-oriented RAG agent with LangGraph and local tracing")
        self.API_V1_STR = os.getenv("API_V1_STR", "/api/v1")
        self.DEBUG = os.getenv("DEBUG", "false").lower() in ("true", "1", "t", "yes")

        # CORS Settings
        self.ALLOWED_ORIGINS = parse_list_from_env("ALLOWED_ORIGINS", ["*"])

        # Lightweight application tracing
        self.TRACING_ENABLED = os.getenv("TRACING_ENABLED", "true").lower() in (
            "true",
            "1",
            "t",
            "yes",
        )
        self.TRACE_DIR = Path(os.getenv("TRACE_DIR", "logs/traces"))
        self.TRACE_FILE_RETENTION_DAYS = int(os.getenv("TRACE_FILE_RETENTION_DAYS", "7"))
        self.TRACE_SLOW_SPAN_THRESHOLD_MS = float(os.getenv("TRACE_SLOW_SPAN_THRESHOLD_MS", "2000"))

        # LangGraph Configuration
        self.DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY", "")
        self.DEEPSEEK_BASE_URL = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com")
        self.DEEPSEEK_THINKING_ENABLED = os.getenv("DEEPSEEK_THINKING_ENABLED", "false").lower() in (
            "true",
            "1",
            "t",
            "yes",
        )
        self.EXPOSE_REASONING_CONTENT = os.getenv("EXPOSE_REASONING_CONTENT", "false").lower() in (
            "true",
            "1",
            "t",
            "yes",
        )
        self.DEFAULT_LLM_MODEL = os.getenv("DEFAULT_LLM_MODEL", "deepseek-v4-flash")
        self.SESSION_NAMING_ENABLED = os.getenv("SESSION_NAMING_ENABLED", "true").lower() == "true"
        self.DEFAULT_LLM_TEMPERATURE = float(os.getenv("DEFAULT_LLM_TEMPERATURE", "0.2"))
        self.MAX_TOKENS = int(os.getenv("MAX_TOKENS", "2000"))
        self.MAX_LLM_CALL_RETRIES = int(os.getenv("MAX_LLM_CALL_RETRIES", "3"))
        self.LLM_TOTAL_TIMEOUT = int(os.getenv("LLM_TOTAL_TIMEOUT", "60"))
        self.USER_SETTINGS_ENCRYPTION_KEY = os.getenv("USER_SETTINGS_ENCRYPTION_KEY", "")
        self.ALLOWED_LLM_BASE_URLS = parse_list_from_env(
            "ALLOWED_LLM_BASE_URLS",
            ["https://api.deepseek.com"],
        )

        # Long term memory Configuration
        self.LONG_TERM_MEMORY_MODEL = os.getenv("LONG_TERM_MEMORY_MODEL", "deepseek-v4-flash")
        self.LONG_TERM_MEMORY_EMBEDDER_MODEL = os.getenv("LONG_TERM_MEMORY_EMBEDDER_MODEL", "BAAI/bge-m3")
        self.LONG_TERM_MEMORY_COLLECTION_NAME = os.getenv("LONG_TERM_MEMORY_COLLECTION_NAME", "longterm_memory")
        self.MEMORY_JOB_POLL_SECONDS = float(os.getenv("MEMORY_JOB_POLL_SECONDS", "1"))
        self.MEMORY_JOB_MAX_ATTEMPTS = int(os.getenv("MEMORY_JOB_MAX_ATTEMPTS", "5"))
        self.MEMORY_JOB_STALE_AFTER_SECONDS = int(os.getenv("MEMORY_JOB_STALE_AFTER_SECONDS", "300"))
        self.MEMORY_JOB_SHUTDOWN_TIMEOUT = float(os.getenv("MEMORY_JOB_SHUTDOWN_TIMEOUT", "10"))

        # Knowledge Base / RAG Configuration (SiliconFlow free embeddings by default)
        self.SILICONFLOW_API_KEY = os.getenv("SILICONFLOW_API_KEY", "")
        self.SILICONFLOW_BASE_URL = os.getenv("SILICONFLOW_BASE_URL", "https://api.siliconflow.cn/v1")
        self.EMBEDDING_MODEL = os.getenv("EMBEDDING_MODEL", "BAAI/bge-m3")
        self.EMBEDDING_DIM = int(os.getenv("EMBEDDING_DIM", "1024"))
        self.EMBEDDING_TIMEOUT = int(os.getenv("EMBEDDING_TIMEOUT", "30"))
        self.EMBEDDING_BATCH_SIZE = int(os.getenv("EMBEDDING_BATCH_SIZE", "64"))
        self.KNOWLEDGE_TABLE = os.getenv("KNOWLEDGE_TABLE", "knowledge_chunks")
        self.KNOWLEDGE_DEFAULT_SPACE = os.getenv("KNOWLEDGE_DEFAULT_SPACE", "default-public")
        self.KNOWLEDGE_TOP_K = int(os.getenv("KNOWLEDGE_TOP_K", "5"))
        self.KNOWLEDGE_DENSE_CANDIDATES = int(os.getenv("KNOWLEDGE_DENSE_CANDIDATES", "50"))
        self.KNOWLEDGE_LEXICAL_CANDIDATES = int(os.getenv("KNOWLEDGE_LEXICAL_CANDIDATES", "50"))
        self.KNOWLEDGE_RRF_K = int(os.getenv("KNOWLEDGE_RRF_K", "60"))
        self.KNOWLEDGE_MIN_SIMILARITY = float(os.getenv("KNOWLEDGE_MIN_SIMILARITY", "0.3"))
        self.KNOWLEDGE_CHUNK_SIZE = int(os.getenv("KNOWLEDGE_CHUNK_SIZE", "800"))
        self.KNOWLEDGE_CHUNK_OVERLAP = int(os.getenv("KNOWLEDGE_CHUNK_OVERLAP", "100"))
        self.OPENSEARCH_URL = os.getenv("OPENSEARCH_URL", "")
        self.OPENSEARCH_INDEX = os.getenv("OPENSEARCH_INDEX", "qaagent-knowledge-v1")
        self.OPENSEARCH_USERNAME = os.getenv("OPENSEARCH_USERNAME", "")
        self.OPENSEARCH_PASSWORD = os.getenv("OPENSEARCH_PASSWORD", "")
        self.OPENSEARCH_VERIFY_SSL = os.getenv("OPENSEARCH_VERIFY_SSL", "true").lower() == "true"
        self.OPENSEARCH_TIMEOUT = float(os.getenv("OPENSEARCH_TIMEOUT", "5"))
        self.RERANK_ENABLED = os.getenv("RERANK_ENABLED", "false").lower() == "true"
        self.RERANK_API_KEY = os.getenv("RERANK_API_KEY") or self.SILICONFLOW_API_KEY
        self.RERANK_BASE_URL = os.getenv("RERANK_BASE_URL", self.SILICONFLOW_BASE_URL)
        self.RERANK_MODEL = os.getenv("RERANK_MODEL", "BAAI/bge-reranker-v2-m3")
        self.RERANK_TIMEOUT = float(os.getenv("RERANK_TIMEOUT", "8"))
        self.RERANK_CANDIDATES = int(os.getenv("RERANK_CANDIDATES", "20"))
        self.KNOWLEDGE_GRAPH_MAX_CHUNKS = int(os.getenv("KNOWLEDGE_GRAPH_MAX_CHUNKS", "20"))

        # Guard against invalid chunking configuration (would break the text splitter).
        if self.KNOWLEDGE_CHUNK_OVERLAP >= self.KNOWLEDGE_CHUNK_SIZE:
            clamped = max(0, self.KNOWLEDGE_CHUNK_SIZE - 1)
            print(
                f"WARNING: KNOWLEDGE_CHUNK_OVERLAP ({self.KNOWLEDGE_CHUNK_OVERLAP}) >= "
                f"KNOWLEDGE_CHUNK_SIZE ({self.KNOWLEDGE_CHUNK_SIZE}); clamping overlap to {clamped}"
            )
            self.KNOWLEDGE_CHUNK_OVERLAP = clamped
        # JWT Configuration
        self.JWT_SECRET_KEY = os.getenv("JWT_SECRET_KEY", "")
        self.JWT_ALGORITHM = os.getenv("JWT_ALGORITHM", "HS256")
        self.JWT_ACCESS_TOKEN_EXPIRE_DAYS = int(os.getenv("JWT_ACCESS_TOKEN_EXPIRE_DAYS", "30"))

        # Logging Configuration
        self.LOG_DIR = Path(os.getenv("LOG_DIR", "logs"))
        self.LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")
        self.LOG_FORMAT = os.getenv("LOG_FORMAT", "json")  # "json" or "console"

        # Profiling Configuration (DEBUG only)
        self.PROFILING_DIR = Path(os.getenv("PROFILING_DIR", "/tmp/fastapi_profiles"))
        self.PROFILING_THRESHOLD_SECONDS = float(os.getenv("PROFILING_THRESHOLD_SECONDS", "2.0"))

        # Postgres Configuration
        self.POSTGRES_HOST = os.getenv("POSTGRES_HOST", "localhost")
        self.POSTGRES_PORT = int(os.getenv("POSTGRES_PORT", "5432"))
        self.POSTGRES_DB = os.getenv("POSTGRES_DB", "qa_agent")
        self.POSTGRES_USER = os.getenv("POSTGRES_USER", "postgres")
        self.POSTGRES_PASSWORD = os.getenv("POSTGRES_PASSWORD", "postgres")
        self.POSTGRES_POOL_SIZE = int(os.getenv("POSTGRES_POOL_SIZE", "20"))
        self.POSTGRES_MAX_OVERFLOW = int(os.getenv("POSTGRES_MAX_OVERFLOW", "10"))
        self.CHECKPOINT_TABLES = ["checkpoint_blobs", "checkpoint_writes", "checkpoints"]

        # Valkey/Redis Cache Configuration (optional — if host is set, caching is enabled)
        self.VALKEY_HOST = os.getenv("VALKEY_HOST", "")
        self.VALKEY_PORT = int(os.getenv("VALKEY_PORT", "6379"))
        self.VALKEY_DB = int(os.getenv("VALKEY_DB", "0"))
        self.VALKEY_PASSWORD = os.getenv("VALKEY_PASSWORD", "")
        self.VALKEY_MAX_CONNECTIONS = int(os.getenv("VALKEY_MAX_CONNECTIONS", "20"))
        self.CACHE_TTL_SECONDS = int(os.getenv("CACHE_TTL_SECONDS", "60"))

        # Rate Limiting Configuration
        self.RATE_LIMIT_DEFAULT = parse_list_from_env("RATE_LIMIT_DEFAULT", ["200 per day", "50 per hour"])

        # Rate limit endpoints defaults
        default_endpoints = {
            "chat": ["30 per minute"],
            "chat_stream": ["20 per minute"],
            "messages": ["50 per minute"],
            "register": ["10 per hour"],
            "login": ["20 per minute"],
            "root": ["10 per minute"],
            "health": ["20 per minute"],
            "session": ["30 per minute"],
            "sessions": ["60 per minute"],
            "user_settings": ["30 per minute"],
            "user_settings_test": ["5 per minute"],
            "research": ["5 per minute"],
            "wiki": ["5 per minute"],
        }

        # Update rate limit endpoints from environment variables
        self.RATE_LIMIT_ENDPOINTS = default_endpoints.copy()
        for endpoint in default_endpoints:
            env_key = f"RATE_LIMIT_{endpoint.upper()}"
            value = parse_list_from_env(env_key)
            if value:
                self.RATE_LIMIT_ENDPOINTS[endpoint] = value

        # Evaluation Configuration
        self.EVALUATION_LLM = os.getenv("EVALUATION_LLM", "deepseek-v4-flash")
        self.EVALUATION_BASE_URL = os.getenv("EVALUATION_BASE_URL", self.DEEPSEEK_BASE_URL)
        self.EVALUATION_API_KEY = os.getenv("EVALUATION_API_KEY", self.DEEPSEEK_API_KEY)
        self.EVALUATION_SLEEP_TIME = int(os.getenv("EVALUATION_SLEEP_TIME", "10"))
        self.EVALUATION_DATA_FILE = Path(os.getenv("EVALUATION_DATA_FILE", "evals/data/input.jsonl"))

        # Apply environment-specific settings
        self.apply_environment_settings()
        self.validate_security_settings()

    def validate_security_settings(self) -> None:
        """Reject placeholder or weak secrets in production."""
        if self.ENVIRONMENT != Environment.PRODUCTION:
            return
        invalid_markers = ("your-", "replace-", "change-me", "mypassword", "supersecret")
        secrets = {
            "JWT_SECRET_KEY": self.JWT_SECRET_KEY,
            "POSTGRES_PASSWORD": self.POSTGRES_PASSWORD,
            "DEEPSEEK_API_KEY": self.DEEPSEEK_API_KEY,
            "SILICONFLOW_API_KEY": self.SILICONFLOW_API_KEY,
            "USER_SETTINGS_ENCRYPTION_KEY": self.USER_SETTINGS_ENCRYPTION_KEY,
        }
        minimum_lengths = {"USER_SETTINGS_ENCRYPTION_KEY": 32}
        invalid = [
            name
            for name, value in secrets.items()
            if len(value) < minimum_lengths.get(name, 24) or any(marker in value.lower() for marker in invalid_markers)
        ]
        if invalid:
            raise RuntimeError(f"production secrets are missing or weak: {', '.join(invalid)}")

    def apply_environment_settings(self):
        """Apply environment-specific settings based on the current environment."""
        env_settings = {
            Environment.DEVELOPMENT: {
                "DEBUG": True,
                "LOG_LEVEL": "DEBUG",
                "LOG_FORMAT": "console",
                "RATE_LIMIT_DEFAULT": ["1000 per day", "200 per hour"],
            },
            Environment.STAGING: {
                "DEBUG": False,
                "LOG_LEVEL": "INFO",
                "RATE_LIMIT_DEFAULT": ["500 per day", "100 per hour"],
            },
            Environment.PRODUCTION: {
                "DEBUG": False,
                "LOG_LEVEL": "WARNING",
                "RATE_LIMIT_DEFAULT": ["200 per day", "50 per hour"],
            },
            Environment.TEST: {
                "DEBUG": True,
                "LOG_LEVEL": "DEBUG",
                "LOG_FORMAT": "console",
                "RATE_LIMIT_DEFAULT": ["1000 per day", "1000 per hour"],  # Relaxed for testing
            },
        }

        # Get settings for current environment
        current_env_settings = env_settings.get(self.ENVIRONMENT, {})

        # Apply settings if not explicitly set in environment variables
        for key, value in current_env_settings.items():
            env_var_name = key.upper()
            # Only override if environment variable wasn't explicitly set
            if env_var_name not in os.environ:
                setattr(self, key, value)


# Create settings instance
settings = Settings()
