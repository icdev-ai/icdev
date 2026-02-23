# [TEMPLATE: CUI // SP-CTI]

# Agent Skill Design Prompt

## Role

You are designing skills for an A2A-compatible agent in the ICDEV multi-agent system. Each skill is a discrete unit of work that an agent can perform. Skills are the atomic building blocks of agent capabilities — they are registered in agent cards, invoked via JSON-RPC 2.0, and tracked in the audit trail.

## Skill Structure

Every skill must satisfy these requirements:

1. **Unique ID** — kebab-case identifier (e.g., `code-generation`, `sast-scan`, `ssp-generation`)
2. **Input/output schemas** — JSON Schema defining expected parameters and return values
3. **Validation logic** — Check all required parameters before execution; fail fast with clear errors
4. **JSON-RPC 2.0 compliance** — Return results in standard `{ "jsonrpc": "2.0", "result": {...}, "id": "..." }` format
5. **Audit trail logging** — Log execution start, completion, and failure to the append-only audit trail
6. **Confidence tracking** — Report execution confidence (0.0-1.0) for self-healing integration
7. **Idempotency** — Running the same skill with the same inputs should produce the same result

## Implementation Template

```python
"""
CUI // SP-CTI
Skill: {{skill-id}}
Agent: {{agent-name}}
"""

import json
import logging
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)


class {{SkillName}}Skill:
    """{{One-sentence description of what this skill does.}}"""

    SKILL_ID = "{{skill-id}}"
    VERSION = "1.0.0"

    # Input schema (JSON Schema format)
    INPUT_SCHEMA = {
        "type": "object",
        "properties": {
            "project_id": {"type": "string", "description": "Project identifier"},
            # Add skill-specific parameters here
        },
        "required": ["project_id"],
    }

    # Output schema
    OUTPUT_SCHEMA = {
        "type": "object",
        "properties": {
            "status": {"type": "string", "enum": ["success", "failure", "partial"]},
            "confidence": {"type": "number", "minimum": 0.0, "maximum": 1.0},
            "result": {"type": "object"},
            "errors": {"type": "array", "items": {"type": "string"}},
        },
        "required": ["status", "confidence"],
    }

    def __init__(self, db_path: str = "data/icdev.db"):
        self.db_path = db_path

    def validate(self, params: dict) -> tuple[bool, list[str]]:
        """Validate input parameters against INPUT_SCHEMA.

        Returns:
            (is_valid, list_of_error_messages)
        """
        errors = []
        for field in self.INPUT_SCHEMA.get("required", []):
            if field not in params:
                errors.append(f"Missing required parameter: {field}")
        return (len(errors) == 0, errors)

    def execute(self, params: dict) -> dict:
        """Execute the skill with validated parameters.

        Returns:
            JSON-RPC 2.0 compatible result dict with status, confidence, and result.
        """
        is_valid, errors = self.validate(params)
        if not is_valid:
            return {
                "status": "failure",
                "confidence": 1.0,
                "result": {},
                "errors": errors,
            }

        try:
            # Skill-specific logic here
            result = self._do_work(params)
            return {
                "status": "success",
                "confidence": result.get("confidence", 0.9),
                "result": result,
                "errors": [],
            }
        except Exception as e:
            logger.error(f"Skill {self.SKILL_ID} failed: {e}")
            return {
                "status": "failure",
                "confidence": 0.0,
                "result": {},
                "errors": [str(e)],
            }

    def _do_work(self, params: dict) -> dict:
        """Override this method with skill-specific implementation."""
        raise NotImplementedError

    def get_skill_card(self) -> dict:
        """Return the skill card for agent card registration."""
        return {
            "id": self.SKILL_ID,
            "version": self.VERSION,
            "description": self.__doc__ or "",
            "input_schema": self.INPUT_SCHEMA,
            "output_schema": self.OUTPUT_SCHEMA,
            "tags": [],
        }
```

## Registration

Skills are registered in agent cards via `register_skill()`:

```python
agent_card = {
    "agent_id": "builder-agent",
    "skills": [
        CodeGenerationSkill().get_skill_card(),
        TestWritingSkill().get_skill_card(),
        LintSkill().get_skill_card(),
    ]
}
```

The agent card is published at `/.well-known/agent.json` and stored in `tools/agent/cards/<agent-name>.json`.

## Confidence Tracking

Every skill execution reports a confidence score:

| Confidence | Meaning | Self-Healing Action |
|------------|---------|---------------------|
| >= 0.7 | High confidence | Auto-remediate if failure recurs |
| 0.3 - 0.7 | Medium confidence | Suggest fix, require human approval |
| < 0.3 | Low confidence | Escalate with full context |

The knowledge agent tracks confidence trends. Declining confidence triggers pattern analysis.

## Error Handling

- **Validation errors:** Return immediately with `status: failure`, `confidence: 1.0` (we are confident the input is wrong)
- **Execution errors:** Catch exceptions, log to audit trail, return `status: failure` with error details
- **Partial results:** Return `status: partial` with what succeeded and what failed
- **Timeout:** Skills must respect the agent's configured timeout (default: 300s). Long-running skills should report progress.

## Testing Requirements

Every skill must have:
- Unit tests for `validate()` with valid and invalid inputs
- Unit tests for `execute()` with mocked dependencies
- Integration tests that verify audit trail entries are created
- Edge case tests for empty inputs, missing optional fields, and boundary values
