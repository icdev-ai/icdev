# [TEMPLATE: CUI // SP-CTI]
"""Agent execution data models — dataclasses for request/response/retry."""

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional, List, Dict, Any


class RetryCode(Enum):
    """Retry decision codes for agent execution."""
    SUCCESS = "success"
    RETRYABLE_ERROR = "retryable_error"
    FATAL_ERROR = "fatal_error"
    TIMEOUT = "timeout"
    RATE_LIMITED = "rate_limited"


@dataclass
class AgentPromptRequest:
    """Request to execute an agent prompt via Claude Code CLI."""
    prompt: str
    model: str = "sonnet"  # sonnet, opus, haiku
    project_dir: Optional[str] = None
    output_format: str = "json"  # json, text
    max_turns: int = 10
    allowed_tools: Optional[List[str]] = None
    system_prompt: Optional[str] = None
    env_vars: Optional[Dict[str, str]] = None
    timeout_seconds: int = 300
    classification: str = "CUI"


@dataclass
class AgentPromptResponse:
    """Response from an agent prompt execution."""
    execution_id: str = ""
    status: str = "pending"  # started, completed, failed, retried, timeout
    retry_code: RetryCode = RetryCode.SUCCESS
    retry_count: int = 0
    output_text: str = ""
    output_path: Optional[str] = None
    input_tokens: int = 0
    output_tokens: int = 0
    duration_ms: int = 0
    error_message: Optional[str] = None
    model: str = ""
    prompt_hash: str = ""
    task_id: Optional[str] = None
    session_id: Optional[str] = None
    classification: str = "CUI"
    raw_jsonl: Optional[List[Dict[str, Any]]] = field(default_factory=list)
