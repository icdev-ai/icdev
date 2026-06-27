import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import uuid
import pytest
from tools.migration_canvas.db import init_db as init_db_mod
from tools.migration_canvas import network_migration as nm


@pytest.fixture
def session_id(tmp_path, monkeypatch):
    db_path = tmp_path / "migration_canvas_coa.db"
    monkeypatch.setenv("MC_DB_PATH", str(db_path))
    monkeypatch.setattr(init_db_mod, "DB_PATH", db_path)
    monkeypatch.setattr(nm, "_MC_DB_PATH", db_path)
    init_db_mod.init_db()
    sid = f"nmig-coa-test-{uuid.uuid4().hex[:8]}"
    with init_db_mod.get_connection() as conn:
        conn.execute(
            "INSERT INTO mc_net_sessions (id, src_model, tgt_model) VALUES (?,?,?)",
            (sid, "Juniper MX204", "Cisco ASR-9901"),
        )
        conn.commit()
    return sid


def test_save_and_context(session_id):
    before = nm.get_coa_questions(session_id)
    print("BEFORE", [(q["question_key"], q["user_answer"]) for q in before])
    nm.save_coa_answers(
        session_id,
        {
            "spare_ports_available": 0,
            "same_mgmt_vlan_ok": 0,
        },
    )
    after = nm.get_coa_questions(session_id)
    print("AFTER", [(q["question_key"], q["user_answer"]) for q in after])
    assert any(q["question_key"] == "spare_ports_available" and q["user_answer"] == 0 for q in after)
