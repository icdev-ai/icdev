# CUI // SP-PROPIN
"""Initialize GovProposal SQLite DB schema in ICDev's data directory."""
import os
import sqlite3
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent.parent.parent
_GP_DB_PATH = Path(os.environ.get(
    "GOVPROPOSAL_DB_PATH", str(BASE_DIR / "data" / "govproposal.db")
))

SCHEMA = """
CREATE TABLE IF NOT EXISTS opportunities (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    agency TEXT,
    solicitation_number TEXT,
    naics_code TEXT,
    set_aside TEXT,
    response_deadline TEXT,
    posted_date TEXT,
    estimated_value REAL,
    fit_score REAL DEFAULT 0,
    status TEXT DEFAULT 'identified',
    source TEXT DEFAULT 'manual',
    raw_synopsis TEXT,
    notes TEXT,
    discovered_at TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
    updated_at TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now'))
);

CREATE TABLE IF NOT EXISTS opportunity_scores (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    opportunity_id TEXT NOT NULL REFERENCES opportunities(id),
    dimension TEXT NOT NULL,
    score REAL,
    rationale TEXT,
    scored_at TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now'))
);

CREATE TABLE IF NOT EXISTS pipeline_stages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    opportunity_id TEXT NOT NULL REFERENCES opportunities(id),
    stage TEXT NOT NULL,
    entered_at TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
    notes TEXT
);

CREATE TABLE IF NOT EXISTS proposals (
    id TEXT PRIMARY KEY,
    opportunity_id TEXT REFERENCES opportunities(id),
    title TEXT NOT NULL,
    status TEXT DEFAULT 'draft',
    cag_status TEXT DEFAULT 'clear',
    due_date TEXT,
    assigned_pm TEXT,
    result TEXT,
    notes TEXT,
    created_at TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
    updated_at TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now'))
);

CREATE TABLE IF NOT EXISTS proposal_sections (
    id TEXT PRIMARY KEY,
    proposal_id TEXT NOT NULL REFERENCES proposals(id),
    volume TEXT,
    section_number TEXT,
    title TEXT,
    content TEXT,
    status TEXT DEFAULT 'draft',
    word_count INTEGER DEFAULT 0,
    hitl_status TEXT DEFAULT 'pending',
    created_at TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
    updated_at TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now'))
);

CREATE TABLE IF NOT EXISTS proposal_reviews (
    id TEXT PRIMARY KEY,
    proposal_id TEXT NOT NULL REFERENCES proposals(id),
    review_type TEXT,
    reviewer TEXT,
    score REAL,
    findings TEXT,
    action_items TEXT,
    status TEXT DEFAULT 'scheduled',
    created_at TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now'))
);

CREATE TABLE IF NOT EXISTS cag_alerts (
    id TEXT PRIMARY KEY,
    proposal_id TEXT REFERENCES proposals(id),
    alert_type TEXT,
    severity TEXT DEFAULT 'medium',
    description TEXT,
    status TEXT DEFAULT 'open',
    resolution TEXT,
    created_at TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now'))
);

CREATE TABLE IF NOT EXISTS compliance_matrices (
    id TEXT PRIMARY KEY,
    proposal_id TEXT NOT NULL REFERENCES proposals(id),
    requirement_id TEXT,
    requirement_text TEXT,
    compliance_status TEXT DEFAULT 'tbd',
    response_reference TEXT,
    notes TEXT
);

CREATE TABLE IF NOT EXISTS win_themes (
    id TEXT PRIMARY KEY,
    proposal_id TEXT NOT NULL REFERENCES proposals(id),
    theme TEXT,
    discriminator TEXT,
    customer_hot_button TEXT,
    proof_point TEXT
);

CREATE TABLE IF NOT EXISTS kb_entries (
    id TEXT PRIMARY KEY,
    entry_type TEXT,
    title TEXT,
    content TEXT,
    tags TEXT,
    source TEXT,
    is_active INTEGER DEFAULT 1,
    created_at TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
    updated_at TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now'))
);

CREATE TABLE IF NOT EXISTS employees (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    email TEXT,
    title TEXT,
    lcat TEXT,
    clearance_level TEXT,
    skills TEXT,
    certifications TEXT,
    hourly_rate REAL,
    location TEXT,
    status TEXT DEFAULT 'active',
    created_at TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now'))
);

CREATE TABLE IF NOT EXISTS lcat_rates (
    id TEXT PRIMARY KEY,
    lcat TEXT NOT NULL,
    level TEXT,
    min_rate REAL,
    max_rate REAL,
    typical_rate REAL,
    effective_date TEXT,
    notes TEXT
);

CREATE TABLE IF NOT EXISTS contacts (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    email TEXT,
    phone TEXT,
    organization TEXT,
    title TEXT,
    relationship_type TEXT DEFAULT 'prospect',
    clearance_level TEXT,
    notes TEXT,
    last_contacted TEXT,
    created_at TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now'))
);

CREATE TABLE IF NOT EXISTS crm_interactions (
    id TEXT PRIMARY KEY,
    contact_id TEXT NOT NULL REFERENCES contacts(id),
    interaction_type TEXT,
    summary TEXT,
    outcome TEXT,
    follow_up_date TEXT,
    logged_at TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now'))
);

CREATE TABLE IF NOT EXISTS rfx_documents (
    id TEXT PRIMARY KEY,
    filename TEXT,
    doc_type TEXT DEFAULT 'rfp',
    file_path TEXT,
    vectorized INTEGER DEFAULT 0,
    uploaded_at TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now'))
);

CREATE TABLE IF NOT EXISTS rfx_requirements (
    id TEXT PRIMARY KEY,
    document_id TEXT REFERENCES rfx_documents(id),
    proposal_id TEXT REFERENCES proposals(id),
    requirement_text TEXT,
    requirement_type TEXT,
    section_ref TEXT,
    compliance_status TEXT DEFAULT 'tbd',
    extracted_at TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now'))
);

CREATE TABLE IF NOT EXISTS rfx_exclusions (
    id TEXT PRIMARY KEY,
    term TEXT NOT NULL,
    replacement TEXT,
    reason TEXT,
    added_at TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now'))
);

CREATE TABLE IF NOT EXISTS rfx_ai_sections (
    id TEXT PRIMARY KEY,
    proposal_id TEXT REFERENCES proposals(id),
    section_title TEXT,
    section_type TEXT,
    generated_content TEXT,
    hitl_status TEXT DEFAULT 'pending',
    reviewer_notes TEXT,
    generation_params TEXT,
    created_at TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
    updated_at TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now'))
);

CREATE TABLE IF NOT EXISTS rfx_finetune_jobs (
    id TEXT PRIMARY KEY,
    model_name TEXT,
    dataset_path TEXT,
    status TEXT DEFAULT 'queued',
    progress REAL DEFAULT 0,
    error_msg TEXT,
    started_at TEXT,
    completed_at TEXT,
    created_at TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now'))
);

CREATE TABLE IF NOT EXISTS contracts (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    contract_number TEXT,
    agency TEXT,
    value REAL,
    start_date TEXT,
    end_date TEXT,
    status TEXT DEFAULT 'active',
    contract_type TEXT,
    notes TEXT,
    created_at TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now'))
);

CREATE TABLE IF NOT EXISTS deliverable_reminders (
    id TEXT PRIMARY KEY,
    contract_id TEXT REFERENCES contracts(id),
    title TEXT,
    due_date TEXT,
    reminder_date TEXT,
    status TEXT DEFAULT 'pending',
    notes TEXT
);

CREATE TABLE IF NOT EXISTS sbir_proposals (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    program TEXT,
    phase TEXT,
    topic TEXT,
    agency TEXT,
    trl INTEGER DEFAULT 1,
    status TEXT DEFAULT 'draft',
    submission_date TEXT,
    created_at TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now'))
);

CREATE TABLE IF NOT EXISTS idiq_vehicles (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    contract_number TEXT,
    agency TEXT,
    ceiling_value REAL,
    start_date TEXT,
    end_date TEXT,
    status TEXT DEFAULT 'active',
    notes TEXT
);

CREATE TABLE IF NOT EXISTS recompetes (
    id TEXT PRIMARY KEY,
    contract_id TEXT REFERENCES contracts(id),
    title TEXT,
    agency TEXT,
    estimated_recompete_date TEXT,
    incumbent TEXT,
    displacement_score REAL DEFAULT 0,
    strategy TEXT,
    status TEXT DEFAULT 'monitoring',
    created_at TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now'))
);

CREATE TABLE IF NOT EXISTS pricing_scenarios (
    id TEXT PRIMARY KEY,
    name TEXT,
    scenario_data TEXT,
    total_price REAL,
    created_at TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now'))
);
"""


def init_db(db_path: Path | None = None) -> None:
    path = db_path or _GP_DB_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.executescript(SCHEMA)
    conn.commit()
    conn.close()


if __name__ == "__main__":
    init_db()
    print(f"GovProposal DB initialized at {_GP_DB_PATH}")
