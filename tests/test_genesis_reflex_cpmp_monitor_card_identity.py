# CUI // SP-CTI
"""Card identity for tools/genesis/reflexes/cpmp_monitor.py.

The reflex used a random uuid for the card id and deduped on
``title + dispatch_source + status NOT IN (done, dismissed)``. Because card
titles embedded ``contract_number`` — '' on real cpmp_contracts rows — every
contract produced the identical title, so the dedup discarded all but the
first finding; and because promoting a card rewrites ``dispatch_source`` to
'genesis_scheduler', the dedup then stopped matching its own card and
re-created it every cycle. These tests pin both directions.
"""
from __future__ import annotations

import pytest


@pytest.fixture
def reflex():
    import tools.genesis.reflexes.cpmp_monitor as rx
    return rx


class FakeRow(dict):
    """dict that also supports row["col"] style access used by the reflex."""


class FakeBoard:
    """Minimal stand-in for kanban_tasks: records INSERTs, answers SELECT by id."""

    def __init__(self, contracts=None):
        self.rows: dict[str, dict] = {}
        self.contracts = [FakeRow(c) for c in (contracts or [])]

    def connection(self):
        board = self

        class _Conn:
            def set_security_context(self, _ctx):
                pass

            def execute(self, sql, params=()):
                if "FROM cpmp_contracts" in sql:
                    return _Result(None, rows=board.contracts)
                if sql.lstrip().upper().startswith("SELECT"):
                    task_id = params[0]
                    row = board.rows.get(task_id)
                    return _Result(FakeRow(row) if row else None)
                if "INSERT INTO kanban_tasks" in sql:
                    task_id = params[0]
                    assert task_id not in board.rows, (
                        f"duplicate INSERT for card id {task_id}"
                    )
                    board.rows[task_id] = {
                        "id": task_id,
                        "title": params[2],
                        "status": "suggested",
                        "dispatch_source": params[7],
                    }
                    return _Result(None)
                raise AssertionError(f"unexpected SQL: {sql}")

            def commit(self):
                pass

            def close(self):
                pass

        class _Result:
            def __init__(self, row, rows=None):
                self._row = row
                self._rows = rows or []

            def fetchone(self):
                return self._row

            def fetchall(self):
                return self._rows

        return _Conn()


@pytest.fixture
def board(monkeypatch):
    fake = FakeBoard()
    from tools.db import storage as _stor
    monkeypatch.setattr(_stor, "get_connection", fake.connection)
    return fake


class TestContractLabel:
    def test_blank_contract_number_falls_back_to_title(self, reflex):
        """'' is a present-but-empty VALUE — .get(k, default) never fires on it."""
        label = reflex._contract_label(
            {"id": "abc12345-dead", "contract_number": "", "title": "GCPL Seed Contract"}
        )
        assert label == "GCPL Seed Contract"

    def test_null_contract_number_falls_back_to_title(self, reflex):
        label = reflex._contract_label(
            {"id": "abc12345-dead", "contract_number": None, "title": "GCPL Seed Contract"}
        )
        assert label == "GCPL Seed Contract"

    def test_contract_number_wins_when_present(self, reflex):
        label = reflex._contract_label(
            {"id": "abc12345", "contract_number": "W912-24-C-0001", "title": "Whatever"}
        )
        assert label == "W912-24-C-0001"

    def test_all_blank_falls_back_to_short_id(self, reflex):
        """Worst case still yields something that identifies ONE contract."""
        label = reflex._contract_label(
            {"id": "bff20029-94e2-442c", "contract_number": "", "title": ""}
        )
        assert label == "contract bff20029"

    def test_label_never_blank(self, reflex):
        assert reflex._contract_label({}).strip()

    def test_placeholder_title_is_treated_as_absent(self, reflex):
        """create_contract() stamps this on any contract created without a
        title, so it is identical across rows and names no contract at all."""
        label = reflex._contract_label(
            {"id": "8143e17a-0a01-452f", "contract_number": "", "title": "Untitled Contract"}
        )
        assert label == "contract 8143e17a"

    def test_placeholder_check_ignores_case_and_padding(self, reflex):
        label = reflex._contract_label(
            {"id": "df32ba49-9c39", "contract_number": "", "title": "  untitled contract  "}
        )
        assert label == "contract df32ba49"

    def test_placeholder_tracks_the_contract_manager_default(self, reflex):
        """Sourced from the constant, so changing the default cannot silently
        re-admit a placeholder as an identifier."""
        from tools.govcon.contract_manager import DEFAULT_CONTRACT_TITLE
        label = reflex._contract_label(
            {"id": "6d67ff20-a9", "contract_number": "", "title": DEFAULT_CONTRACT_TITLE}
        )
        assert label == "contract 6d67ff20"

    def test_placeholder_still_caught_when_contract_manager_is_absent(self, reflex, monkeypatch):
        """contract_manager has no icdev/ mirror, so in a packaged install the
        constant import raises and the literal fallback is the ONLY thing
        keeping the placeholder out of card titles."""
        import builtins
        real_import = builtins.__import__

        def _no_govcon(name, *a, **kw):
            if "contract_manager" in name:
                raise ImportError("no icdev/tools/govcon/contract_manager.py")
            return real_import(name, *a, **kw)

        monkeypatch.setattr(builtins, "__import__", _no_govcon)
        label = reflex._contract_label(
            {"id": "719d5e59-bd", "contract_number": "", "title": "Untitled Contract"}
        )
        assert label == "contract 719d5e59"

    def test_real_title_still_wins_over_the_id(self, reflex):
        """Only the placeholder is discarded — a genuine title is the label."""
        label = reflex._contract_label(
            {"id": "bff20029-94e2", "contract_number": "", "title": "GCPL Seed Contract"}
        )
        assert label == "GCPL Seed Contract"

    def test_placeholder_title_does_not_beat_a_real_number(self, reflex):
        label = reflex._contract_label(
            {"id": "8143e17a", "contract_number": "W912-24-C-0001", "title": "Untitled Contract"}
        )
        assert label == "W912-24-C-0001"


class TestCardIdentity:
    def _emit(self, reflex, cid, label, issue_type="subcontractor_compliance"):
        reflex._suggest_kanban_card(
            title=f"[CPMP] {label}: Subcontractor Compliance",
            description="subs have gaps",
            priority="high",
            context_data={"contract_id": cid, "contract_number": ""},
            created_by="cpmp_monitor",
            dedup_key=f"{cid}:{issue_type}",
        )

    def test_distinct_contracts_get_distinct_cards(self, reflex, board):
        """The collapse bug: 5 contracts with blank numbers produced 1 card.

        Labels are passed in here, so this pins the id scheme only; that two of
        them are the same placeholder is the subject of TestContractLabel and
        of test_no_card_title_is_left_unidentifiable.
        """
        contracts = [
            ("bff20029-94e2", "GCPL Seed Contract"),
            ("0f28acca-ee28", "bypass probe"),
            ("e8d124a9-8d5b", "probe"),
            ("df32ba49-9c39", "Untitled Contract"),
            ("8143e17a-0a01", "Untitled Contract"),
        ]
        for cid, label in contracts:
            self._emit(reflex, cid, label)

        assert len(board.rows) == 5, "one card per contract, not one card total"
        assert len({r["id"] for r in board.rows.values()}) == 5

    def test_same_finding_twice_is_one_card(self, reflex, board):
        """Idempotent across cycles — the reflex runs every 3 hours."""
        for _ in range(4):
            self._emit(reflex, "bff20029-94e2", "GCPL Seed Contract")
        assert len(board.rows) == 1

    def test_promotion_rewriting_dispatch_source_does_not_duplicate(self, reflex, board):
        """The duplication bug: promotion rewrites dispatch_source, and the old
        dedup keyed on it, so the reflex re-created its own in-progress card."""
        self._emit(reflex, "bff20029-94e2", "GCPL Seed Contract")
        (card_id,) = board.rows
        board.rows[card_id]["dispatch_source"] = "genesis_scheduler"
        board.rows[card_id]["status"] = "in_progress"

        self._emit(reflex, "bff20029-94e2", "GCPL Seed Contract")

        assert len(board.rows) == 1, "re-created a card it had already filed"

    def test_closed_card_is_not_resurrected(self, reflex, board):
        """A reflex that reopens deliberately-closed work is a nag loop."""
        self._emit(reflex, "bff20029-94e2", "GCPL Seed Contract")
        (card_id,) = board.rows
        board.rows[card_id]["status"] = "done"

        self._emit(reflex, "bff20029-94e2", "GCPL Seed Contract")

        assert len(board.rows) == 1

    def test_different_issue_types_on_one_contract_are_separate_cards(self, reflex, board):
        cid = "bff20029-94e2"
        self._emit(reflex, cid, "GCPL", issue_type="subcontractor_compliance")
        self._emit(reflex, cid, "GCPL", issue_type="overdue_deliverables")
        assert len(board.rows) == 2

    def test_card_id_is_stable_across_processes(self, reflex, board):
        """sha256-derived, not uuid4 — a fresh run must recompute the same id."""
        self._emit(reflex, "bff20029-94e2", "GCPL Seed Contract")
        first = set(board.rows)
        board.rows.clear()
        self._emit(reflex, "bff20029-94e2", "GCPL Seed Contract")
        assert set(board.rows) == first

    def test_title_carries_an_identifiable_label(self, reflex, board):
        self._emit(reflex, "bff20029-94e2", "GCPL Seed Contract")
        (row,) = board.rows.values()
        assert row["title"] == "[CPMP] GCPL Seed Contract: Subcontractor Compliance"
        assert "[CPMP] :" not in row["title"]


class TestRunEndToEnd:
    """Drives run() so the card TITLE is built by the reflex, not the test.

    This is the case that shipped: PG holds 5 active contracts that each have
    noncompliant subcontractors and each have contract_number = ''.
    """

    CONTRACTS = [
        {"id": "bff20029-94e2-442c", "contract_number": "", "title": "GCPL Seed Contract"},
        {"id": "0f28acca-ee28-47b1", "contract_number": "", "title": "bypass probe"},
        {"id": "e8d124a9-8d5b-40e7", "contract_number": "", "title": "probe"},
        {"id": "df32ba49-9c39-4760", "contract_number": "", "title": "Untitled Contract"},
        {"id": "8143e17a-0a01-452f", "contract_number": "", "title": "Untitled Contract"},
    ]

    @pytest.fixture
    def wired(self, reflex, monkeypatch):
        board = FakeBoard(contracts=self.CONTRACTS)
        from tools.db import storage as _stor
        monkeypatch.setattr(_stor, "get_connection", board.connection)

        from tools.govcon import pmo_ai_advisor
        monkeypatch.setattr(
            pmo_ai_advisor,
            "auto_detect_issues",
            lambda cid: {
                "issues": [{
                    "type": "subcontractor_compliance",
                    "severity": "high",
                    "description": "4 subcontractor(s) have incomplete flow-down or cybersecurity gaps.",
                    "suggested_action": "Issue cure notice to non-compliant subs.",
                }]
            },
        )
        # Passes 2–4 quiet: this test is about pass 1's card identity.
        from tools.govcon import cpars_predictor, subcontractor_tracker
        monkeypatch.setattr(cpars_predictor, "predict_cpars", lambda cid: {"predicted_score": 1.0})
        monkeypatch.setattr(cpars_predictor, "get_cpars_trend", lambda cid: {"trend": []})
        monkeypatch.setattr(subcontractor_tracker, "detect_noncompliance", lambda cid: {"noncompliance": []})
        monkeypatch.setattr(reflex, "_write_memory_log", lambda results: None)
        return board

    def test_every_contract_gets_its_own_card(self, reflex, wired, monkeypatch):
        from tools.govcon import cdrl_generator
        monkeypatch.setattr(cdrl_generator, "generate_all_due", lambda cid, days_ahead=14: {"generated": 0})

        result = reflex.run({}, None)

        assert result["status"] == "ok"
        assert result["contracts_scanned"] == 5
        assert result["cards_created"] == 5, (
            "each contract's finding must reach the board; blank contract_number "
            "used to collapse all five into one identical title"
        )
        assert len(wired.rows) == 5

    def test_no_card_title_is_left_unidentifiable(self, reflex, wired, monkeypatch):
        from tools.govcon import cdrl_generator
        monkeypatch.setattr(cdrl_generator, "generate_all_due", lambda cid, days_ahead=14: {"generated": 0})

        reflex.run({}, None)

        titles = sorted(r["title"] for r in wired.rows.values())
        assert not any(t.startswith("[CPMP] :") for t in titles), titles
        assert "[CPMP] GCPL Seed Contract: Subcontractor Compliance" in titles
        assert len(wired.rows) == 5

        # Distinct rows are NOT enough. Two of these contracts are titled
        # 'Untitled Contract' — the placeholder create_contract() stamps on any
        # untitled contract — so falling back to `title` gave them one identical
        # card title and a human could not tell which contract a card meant.
        # Every title must name exactly one contract.
        assert len(set(titles)) == 5, f"two cards cannot share a title: {titles}"
        assert "[CPMP] Untitled Contract: Subcontractor Compliance" not in titles
        assert "[CPMP] contract df32ba49: Subcontractor Compliance" in titles
        assert "[CPMP] contract 8143e17a: Subcontractor Compliance" in titles

    def test_second_cycle_adds_nothing(self, reflex, wired, monkeypatch):
        from tools.govcon import cdrl_generator
        monkeypatch.setattr(cdrl_generator, "generate_all_due", lambda cid, days_ahead=14: {"generated": 0})

        reflex.run({}, None)
        reflex.run({}, None)

        assert len(wired.rows) == 5, "3-hourly reflex must be idempotent"

    def test_cards_created_counts_writes_not_attempts(self, reflex, wired, monkeypatch):
        """The counter is persisted by _write_memory_log — an attempt-counter
        reports steady card creation forever while the dedup writes nothing."""
        from tools.govcon import cdrl_generator
        monkeypatch.setattr(cdrl_generator, "generate_all_due", lambda cid, days_ahead=14: {"generated": 0})

        assert reflex.run({}, None)["cards_created"] == 5
        second = reflex.run({}, None)
        assert second["cards_created"] == 0, "reported cards it did not write"
        assert len(wired.rows) == 5


class TestCparsEscalation:
    """A CAT2 page must accompany a NEW card, not a standing condition."""

    CONTRACT = [{"id": "bff20029-94e2", "contract_number": "", "title": "GCPL Seed Contract"}]

    @pytest.fixture
    def wired(self, reflex, monkeypatch):
        board = FakeBoard(contracts=self.CONTRACT)
        pages: list[str] = []
        from tools.db import storage as _stor
        monkeypatch.setattr(_stor, "get_connection", board.connection)

        from tools.govcon import cdrl_generator, cpars_predictor, pmo_ai_advisor, subcontractor_tracker
        monkeypatch.setattr(pmo_ai_advisor, "auto_detect_issues", lambda cid: {"issues": []})
        monkeypatch.setattr(subcontractor_tracker, "detect_noncompliance", lambda cid: {"noncompliance": []})
        monkeypatch.setattr(cdrl_generator, "generate_all_due", lambda cid, days_ahead=14: {"generated": 0})
        monkeypatch.setattr(
            cpars_predictor, "predict_cpars",
            lambda cid: {"predicted_score": 0.40, "predicted_rating": "marginal"},
        )
        monkeypatch.setattr(
            cpars_predictor, "get_cpars_trend",
            lambda cid: {"trend": [{"predicted_score": 0.62}, {"predicted_score": 0.40}]},
        )

        # alert_service exports escalate_cat1_FINDING, not escalate_cat1 — the
        # reflex's import has never resolved. Supply the intended API so the
        # once-per-finding behaviour is testable; raising=False because the
        # attribute genuinely does not exist.
        from tools.notification_service import alert_service
        monkeypatch.setattr(
            alert_service, "escalate_cat1",
            lambda finding_title, severity, details: pages.append(finding_title),
            raising=False,
        )
        monkeypatch.setattr(reflex, "_write_memory_log", lambda results: None)
        return board, pages

    def test_standing_trajectory_pages_cat2_once(self, reflex, wired):
        board, pages = wired
        for _ in range(3):
            reflex.run({}, None)
        assert len(board.rows) == 1
        assert len(pages) == 1, f"re-paged CAT2 every cycle: {pages}"

    def test_cat2_page_names_the_contract(self, reflex, wired):
        """Not '[...] alert: ' — the page has to say which contract."""
        _board, pages = wired
        reflex.run({}, None)
        assert pages == ["CPARS trajectory alert: GCPL Seed Contract"]


class TestEscalationFailureIsVisible:
    """A silent `except: pass` is why nobody noticed the CAT2 channel was dead."""

    def test_missing_escalation_api_is_reported_not_swallowed(self, reflex, monkeypatch):
        board = FakeBoard(
            contracts=[{"id": "bff20029", "contract_number": "", "title": "GCPL Seed Contract"}]
        )
        from tools.db import storage as _stor
        monkeypatch.setattr(_stor, "get_connection", board.connection)

        from tools.govcon import cdrl_generator, cpars_predictor, pmo_ai_advisor, subcontractor_tracker
        monkeypatch.setattr(pmo_ai_advisor, "auto_detect_issues", lambda cid: {"issues": []})
        monkeypatch.setattr(subcontractor_tracker, "detect_noncompliance", lambda cid: {"noncompliance": []})
        monkeypatch.setattr(cdrl_generator, "generate_all_due", lambda cid, days_ahead=14: {"generated": 0})
        monkeypatch.setattr(
            cpars_predictor, "predict_cpars",
            lambda cid: {"predicted_score": 0.40, "predicted_rating": "marginal"},
        )
        monkeypatch.setattr(
            cpars_predictor, "get_cpars_trend",
            lambda cid: {"trend": [{"predicted_score": 0.62}, {"predicted_score": 0.40}]},
        )
        monkeypatch.setattr(reflex, "_write_memory_log", lambda results: None)

        # escalate_cat1 is NOT patched in — this is the real production state.
        result = reflex.run({}, None)

        assert result["cpars_alerts"] == 1, "the card itself must still be filed"
        assert any("CAT2 escalation unavailable" in e for e in result["errors"]), result["errors"]


class TestCdrlEventSemantics:
    def test_new_batch_size_gets_its_own_card(self, reflex, board):
        """CDRL generation is an EVENT, not a condition — a second batch must
        still be reported rather than swallowed as a duplicate."""
        for generated in (3, 3, 5):
            reflex._suggest_kanban_card(
                title=f"[CDRL] GCPL: {generated} deliverable(s) auto-generated",
                description="review before submission",
                priority="medium",
                context_data={"contract_id": "bff20029", "generated": generated},
                created_by="cpmp_monitor_cdrl",
                dedup_key=f"bff20029:cdrl_generated:{generated}",
            )
        assert len(board.rows) == 2
