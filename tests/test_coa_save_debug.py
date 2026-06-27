import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest
from tools.migration_canvas.db import init_db as init_db_mod
from tools.migration_canvas import network_migration as nm


@pytest.fixture
def db(tmp_path, monkeypatch):
    p = tmp_path / "x.db"
    monkeypatch.setenv("MC_DB_PATH", str(p))
    monkeypatch.setattr(init_db_mod, "DB_PATH", p)
    monkeypatch.setattr(nm, "_MC_DB_PATH", p)
    init_db_mod.init_db()
    return p


def test_save(db):
    sid = "s-1"
    with init_db_mod.get_connection() as conn:
        conn.execute("INSERT INTO mc_net_sessions (id, src_model, tgt_model) VALUES (?,?,?)", (sid, "a", "b"))
        conn.commit()
    nm.seed_coa_questions(sid)
    before = nm.get_coa_questions(sid)
    print("BEFORE", [(q["question_key"], q["user_answer"]) for q in before])
    nm.save_coa_answers(sid, {"spare_ports_available": 0, "same_mgmt_vlan_ok": 0})
    after = nm.get_coa_questions(sid)
    print("AFTER", [(q["question_key"], q["user_answer"]) for q in after])
    assert any(q["question_key"] == "spare_ports_available" and q["user_answer"] == 0 for q in after)
