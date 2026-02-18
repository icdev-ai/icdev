# CUI // SP-CTI
# Goal: Multi-Agent Orchestration

## Purpose
Execute complex tasks using parallel multi-agent collaboration with domain authority enforcement, cross-agent memory, and structured reasoning.

## When to Use
- Tasks requiring multiple agent specializations (e.g., build + test + security + compliance)
- Tasks where domain experts should review/veto output (security review of code, compliance review of artifacts)
- Large tasks that benefit from parallel execution

## Workflow

### Step 1: Task Decomposition
- Tool: `tools/agent/team_orchestrator.py --decompose`
- Input: Task description, project_id
- Output: Workflow with DAG of subtasks
- Uses BedrockClient with structured output for intelligent decomposition
- Fallback: sequential decomposition if Bedrock unavailable

### Step 2: Authority Check
- Tool: `tools/agent/authority.py`
- Check which domain authorities need to review the workflow output
- Pre-register required veto checkpoints

### Step 3: Memory Injection
- Tool: `tools/agent/agent_memory.py --inject`
- Load relevant agent memories for each subtask agent
- Inject as system prompt context

### Step 4: Parallel Execution
- Tool: `tools/agent/team_orchestrator.py --execute`
- Execute subtasks respecting DAG dependencies
- Independent subtasks run in parallel via ThreadPoolExecutor
- Each subtask dispatched via A2A protocol

### Step 5: Collaboration Patterns
- Tool: `tools/agent/collaboration.py`
- Apply reviewer_pattern for security/compliance review
- Apply veto_pattern for domain authority enforcement
- Apply debate_pattern for architectural decisions

### Step 6: Result Aggregation
- Collect all subtask outputs
- Record collaboration outcomes
- Store lessons learned in agent memory
- Generate final workflow report

## Architecture Decisions
- D36: boto3 + ThreadPoolExecutor (no asyncio)
- D37: Model fallback chain (Opus 4.6 -> Sonnet 4.5 -> Sonnet 3.5)
- D40: graphlib.TopologicalSorter for DAG
- D41: SQLite mailbox with HMAC signing
- D42: YAML authority matrix
- D43: Project-scoped agent memory

## Edge Cases
- If Bedrock is unavailable: fall back to sequential execution with CLI
- If an agent is stale: skill_router finds alternative or escalates
- If a hard veto is issued: workflow pauses, creates approval_workflow entry
- If a subtask fails: mark workflow as partially_completed, continue other branches

## Success Criteria
- All subtasks completed (or explicitly handled failures)
- All required domain authority reviews passed
- Collaboration history recorded
- Token usage tracked
- Audit trail complete
