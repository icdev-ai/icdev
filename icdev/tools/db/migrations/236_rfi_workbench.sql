-- 236_rfi_workbench.sql: RFI Response Workbench — sessions, sections, exports

CREATE TABLE IF NOT EXISTS rfi_workbench_sessions (
    id TEXT PRIMARY KEY,
    rfi_number TEXT,
    rfi_title TEXT,
    profile_name TEXT DEFAULT 'own_company',
    upload_filename TEXT,
    parsed_data TEXT,
    status TEXT NOT NULL DEFAULT 'draft'
        CHECK (status IN ('draft','in_progress','complete','exported')),
    total_sections INTEGER DEFAULT 0,
    approved_sections INTEGER DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS rfi_workbench_sections (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL REFERENCES rfi_workbench_sessions(id) ON DELETE CASCADE,
    part TEXT NOT NULL,
    item_number TEXT NOT NULL,
    title TEXT NOT NULL,
    topic TEXT,
    question_text TEXT,
    content TEXT DEFAULT '',
    ai_draft TEXT DEFAULT '',
    status TEXT NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending','ai_draft_ready','hitl_approved','hitl_rejected','accepted')),
    hitl_action TEXT CHECK (hitl_action IN ('approve','reject','comment',NULL)),
    hitl_comment TEXT DEFAULT '',
    writeguard_result TEXT,
    writeguard_score REAL,
    generation_count INTEGER DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS rfi_workbench_exports (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL REFERENCES rfi_workbench_sessions(id) ON DELETE CASCADE,
    export_format TEXT NOT NULL CHECK (export_format IN ('pdf','docx','md')),
    file_path TEXT,
    exported_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_rfi_sections_session ON rfi_workbench_sections(session_id);
CREATE INDEX IF NOT EXISTS idx_rfi_exports_session ON rfi_workbench_exports(session_id);
