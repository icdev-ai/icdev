# CUI // SP-CTI
"""Seed FA Module 3 / Module 5 mission steps.

Run: python tools/kanban/seed_m03_m05.py
"""
import json
import sys
from pathlib import Path

_BASE = Path(__file__).resolve().parents[2]
if str(_BASE) not in sys.path:
    sys.path.insert(0, str(_BASE))

from tools.db.storage import get_connection  # noqa: E402

conn = get_connection()

def seed_steps(slug, steps):
    m = conn.execute('SELECT id FROM fa_missions WHERE slug=?', (slug,)).fetchone()
    if not m:
        print(f'Mission {slug} not found')
        return
    mid = m[0]
    for s in steps:
        existing = conn.execute('SELECT id FROM fa_mission_steps WHERE mission_id=? AND step_num=?',
                                (mid, s['step_num'])).fetchone()
        if existing:
            print(f'  {slug} step {s["step_num"]} exists, skipping')
            continue
        conn.execute('''INSERT INTO fa_mission_steps
            (mission_id, step_num, title, step_type, content_path, starter_code_path,
             test_code_path, config_schema_json, xp_partial, skill_tag)
            VALUES (?,?,?,?,?,?,?,?,?,?)''',
            (mid, s['step_num'], s['title'], s['step_type'],
             s.get('content_path'), s.get('starter_code_path'), s.get('test_code_path'),
             s.get('config_schema_json'), s.get('xp_partial', 60), s.get('skill_tag', '')))
        print(f'  Seeded {slug} step {s["step_num"]}: {s["title"]}')
    conn.commit()

# M03 RAG Basics
seed_steps('m03-rag-basics', [
    {'step_num': 1, 'title': 'What is RAG?', 'step_type': 'coding', 'xp_partial': 70, 'skill_tag': 'rag',
     'content_path': 'tier1/m03-rag-basics/steps/step1_what_is_rag.md',
     'starter_code_path': 'tier1/m03-rag-basics/steps/step1_starter.py',
     'test_code_path': 'tier1/m03-rag-basics/steps/step1_test.py'},
    {'step_num': 2, 'title': 'Chunking Strategy', 'step_type': 'reflect', 'xp_partial': 50, 'skill_tag': 'rag',
     'config_schema_json': json.dumps({
         'question': 'Your RAG pipeline retrieves 5 full PDF pages (avg 800 tokens each) for every query. Users report vague, unfocused answers. What is the most likely fix?',
         'options': [
             {'text': 'Use a larger LLM with more parameters.', 'correct': False},
             {'text': 'Chunk documents into smaller overlapping segments (200-500 tokens) before embedding.', 'correct': True},
             {'text': 'Add more documents to the vector database.', 'correct': False},
             {'text': 'Lower the temperature to make answers more deterministic.', 'correct': False},
         ],
         'explanation': 'Large chunks = noisy retrieval. Smaller overlapping chunks (200-500 tokens with 50-token overlap) make retrieval precise. This is the #1 RAG tuning move.',
     })},
    {'step_num': 3, 'title': 'Embedding & Similarity', 'step_type': 'coding', 'xp_partial': 80, 'skill_tag': 'rag'},
    {'step_num': 4, 'title': 'ICDEV RAG Pipeline', 'step_type': 'watch', 'xp_partial': 50, 'skill_tag': 'rag',
     'config_schema_json': json.dumps({'demo_url': '/rag/search'})},
    {'step_num': 5, 'title': 'Corrective RAG', 'step_type': 'coding', 'xp_partial': 90, 'skill_tag': 'rag'},
])

# M04 First Agent
seed_steps('m04-first-agent', [
    {'step_num': 1, 'title': 'The Agent Loop', 'step_type': 'coding', 'xp_partial': 80, 'skill_tag': 'agents',
     'content_path': 'tier1/m04-first-agent/steps/step1_agent_loop.md',
     'starter_code_path': 'tier1/m04-first-agent/steps/step1_starter.py',
     'test_code_path': 'tier1/m04-first-agent/steps/step1_test.py'},
    {'step_num': 2, 'title': 'Tool Design Patterns', 'step_type': 'reflect', 'xp_partial': 50, 'skill_tag': 'agents',
     'config_schema_json': json.dumps({
         'question': 'Which of these is the BEST design for an agent tool that runs a STIG compliance scan?',
         'options': [
             {'text': 'A single tool scan_everything() that checks all 200 controls and returns a 50-page report.', 'correct': False},
             {'text': 'A tool scan_control(control_id) that checks one control and returns structured JSON.', 'correct': True},
             {'text': 'A tool that opens a browser and fills out a web form.', 'correct': False},
             {'text': 'A tool that calls another agent to run the scan.', 'correct': False},
         ],
         'explanation': 'Good agent tools are atomic: one job, one result, structured output. Monolithic tools are opaque, produce too much output (context window overflow), and cannot be retried on partial failure.',
     })},
    {'step_num': 3, 'title': 'Build a Security Agent', 'step_type': 'coding', 'xp_partial': 100, 'skill_tag': 'agents'},
    {'step_num': 4, 'title': 'Deploy Document-QA Pattern', 'step_type': 'deploy', 'xp_partial': 100, 'skill_tag': 'agents',
     'config_schema_json': json.dumps({'pattern_id': 'document-qa'})},
])

# M05 MCP Protocol
seed_steps('m05-mcp-protocol', [
    {'step_num': 1, 'title': 'What is MCP?', 'step_type': 'coding', 'xp_partial': 70, 'skill_tag': 'mcp',
     'content_path': 'tier1/m05-mcp-protocol/steps/step1_what_is_mcp.md',
     'starter_code_path': 'tier1/m05-mcp-protocol/steps/step1_starter.py',
     'test_code_path': 'tier1/m05-mcp-protocol/steps/step1_test.py'},
    {'step_num': 2, 'title': 'MCP Tool Registration', 'step_type': 'coding', 'xp_partial': 80, 'skill_tag': 'mcp'},
    {'step_num': 3, 'title': 'MCP vs REST vs Function Calling', 'step_type': 'reflect', 'xp_partial': 50, 'skill_tag': 'mcp',
     'config_schema_json': json.dumps({
         'question': 'An air-gapped DoD system needs to let its AI model call a STIG scanner tool securely, with no network access. Which transport should the MCP server use?',
         'options': [
             {'text': 'HTTP/SSE (Server-Sent Events) over port 8080.', 'correct': False},
             {'text': 'stdio — process stdin/stdout on the local machine.', 'correct': True},
             {'text': 'WebSocket over a VPN tunnel.', 'correct': False},
             {'text': 'REST API with JWT authentication.', 'correct': False},
         ],
         'explanation': 'stdio transport requires no network, no ports, and no authentication infrastructure. The model spawns the server as a child process and communicates via stdin/stdout. The OS process boundary provides isolation. Perfect for air-gap DoD systems.',
     })},
    {'step_num': 4, 'title': 'ICDEV MCP Servers', 'step_type': 'watch', 'xp_partial': 50, 'skill_tag': 'mcp',
     'config_schema_json': json.dumps({'demo_url': '/api/mcp/tools/list'})},
])

print('Done seeding M03, M04, M05.')
