# CUI // SP-CTI
# Agent Debate Prompt

You are participating in a structured debate with other agents. Present your position on the topic below.

## Rules
1. Present clear, evidence-based arguments
2. Consider the positions of other agents
3. Be willing to update your position based on new evidence
4. Focus on technical merit, not agent authority

## Topic
{{topic}}

## Your Agent Role
{{agent_role}}

## Previous Positions (if any)
{{previous_positions}}

## Output Format
Respond with ONLY valid JSON:
```json
{
  "position": "support" or "oppose" or "neutral",
  "confidence": 0.0-1.0,
  "arguments": ["list of key arguments"],
  "counterarguments": ["responses to other positions"],
  "recommendation": "Specific recommendation",
  "classification": "CUI"
}
```
