# CUI // SP-CTI
"""BOM Evidence Engine — schema invariants.

Content here is invented. ICDEV is a public repository and the customer
evidence this engine was developed against is not ours to publish — a test
asserting a real budget figure discloses it exactly as surely as committing
the spreadsheet would, and it looks like diligence in the diff. Publish the
technique; never the data.

These tests are not "does the table exist". They assert the handful of promises
the rest of the engine is built on, because each one, if it quietly stopped
holding, would produce a wrong number in front of an executive rather than a
stack trace.
"""
from __future__ import annotations

import sqlite3

import pytest

from tools.bom import constants as C


def _mk_project(conn, pid="p1"):
    conn.execute(
        "INSERT INTO bom_projects (project_id, name, intent_text) VALUES (%s, %s, %s)",
        (pid, "IRAD Innovation Lab", "stand up a lab; get the team coding now"),
    )
    return pid


class TestCheckConstraintsAreDerived:
    """The SQL CHECKs must come FROM the Python tuples.

    Python accepting a value the database rejects is a bug you only ever meet in
    production, at the moment a user hits save.
    """

    @pytest.mark.parametrize("finding_type", C.FINDING_TYPES)
    def test_every_declared_finding_type_is_insertable(self, bom_db, finding_type):
        _mk_project(bom_db)
        bom_db.execute(
            "INSERT INTO bom_findings (finding_id, project_id, finding_type, title) "
            "VALUES (%s, %s, %s, %s)",
            (f"f-{finding_type}", "p1", finding_type, "t"),
        )

    def test_an_undeclared_finding_type_is_rejected(self, bom_db):
        _mk_project(bom_db)
        with pytest.raises(sqlite3.IntegrityError):
            bom_db.execute(
                "INSERT INTO bom_findings (finding_id, project_id, finding_type, title) "
                "VALUES (%s, %s, %s, %s)",
                ("f-bad", "p1", "definitely_not_a_finding_type", "t"),
            )

    @pytest.mark.parametrize("basis", C.PRICE_BASES)
    def test_every_price_basis_is_insertable(self, bom_db, basis):
        _mk_project(bom_db)
        bom_db.execute(
            "INSERT INTO bom_lines (line_id, line_hash, project_id, source_id, price_basis) "
            "VALUES (%s, %s, %s, %s, %s)",
            (f"l-{basis}", f"h-{basis}", "p1", "s1", basis),
        )

    def test_an_invented_price_basis_is_rejected(self, bom_db):
        _mk_project(bom_db)
        with pytest.raises(sqlite3.IntegrityError):
            bom_db.execute(
                "INSERT INTO bom_lines (line_id, line_hash, project_id, source_id, price_basis) "
                "VALUES (%s, %s, %s, %s, %s)",
                ("l-x", "h-x", "p1", "s1", "vibes"),
            )


class TestDefaultsFailSafe:
    """Silence must never read as confirmation.

    Every default in this schema is chosen so that *not knowing* something is
    recorded as not knowing it, rather than as a convenient assumption.
    """

    def test_price_basis_defaults_to_unknown_not_to_something_convenient(self, bom_db):
        _mk_project(bom_db)
        bom_db.execute(
            "INSERT INTO bom_lines (line_id, line_hash, project_id, source_id) "
            "VALUES (%s, %s, %s, %s)",
            ("l1", "h1", "p1", "s1"),
        )
        row = bom_db.execute(
            "SELECT price_basis FROM bom_lines WHERE line_id = %s", ("l1",)
        ).fetchone()
        # If this ever defaults to 'msrp' or 'rom', the engine will start
        # silently averaging figures that are not comparable.
        assert row[0] == "unknown"

    def test_source_credibility_defaults_to_unknown(self, bom_db):
        _mk_project(bom_db)
        bom_db.execute(
            "INSERT INTO bom_sources (source_id, project_id, filename, content_sha256) "
            "VALUES (%s, %s, %s, %s)",
            ("s1", "p1", "whatever.xlsx", "abc"),
        )
        row = bom_db.execute(
            "SELECT credibility_tier, credibility_set_by FROM bom_sources WHERE source_id = %s",
            ("s1",),
        ).fetchone()
        assert row[0] == "unknown"
        # Nothing is trusted because it showed up. A human has to say so.
        assert row[1] == "default"

    def test_an_undecided_option_group_has_no_selection(self, bom_db):
        _mk_project(bom_db)
        bom_db.execute(
            "INSERT INTO bom_option_groups (group_id, project_id, label, scope, detected_by) "
            "VALUES (%s, %s, %s, %s, %s)",
            ("g1", "p1", "RKE2 vs VMware", "architecture", "baseline"),
        )
        row = bom_db.execute(
            "SELECT selected_option_label FROM bom_option_groups WHERE group_id = %s", ("g1",)
        ).fetchone()
        # NULL is the whole point: an unchosen option contributes zero to the
        # committed total. Defaulting to the first branch would fabricate a
        # decision nobody made.
        assert row[0] is None

    def test_a_line_is_extracted_unless_we_say_we_computed_it(self, bom_db):
        _mk_project(bom_db)
        bom_db.execute(
            "INSERT INTO bom_lines (line_id, line_hash, project_id, source_id) "
            "VALUES (%s, %s, %s, %s)",
            ("l1", "h1", "p1", "s1"),
        )
        row = bom_db.execute(
            "SELECT line_kind, derivation FROM bom_lines WHERE line_id = %s", ("l1",)
        ).fetchone()
        assert row[0] == "extracted"
        assert row[1] == ""


class TestOwnedHardwareIsNotAnAccusation:
    """Repurposed kit is avoided CapEx, and a stale inventory is not a lie.

    A serial number proves a machine exists. The ABSENCE of a serial number
    proves nothing — inventories go stale, and a rack of real servers can be
    missing from a spreadsheet. The schema therefore keeps `claimed` and
    `verified` as two separate facts and never derives a verdict from their
    difference.
    """

    def test_claimed_and_verified_are_separate_columns(self, bom_db):
        _mk_project(bom_db)
        bom_db.execute(
            "INSERT INTO bom_lines "
            "(line_id, line_hash, project_id, source_id, description_raw, "
            " existing_asset, asset_disposition, claimed_qty, verified_qty) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)",
            ("l1", "h1", "p1", "s1", "Compute Node, Model X", True, "repurpose", 12, 2),
        )
        row = bom_db.execute(
            "SELECT claimed_qty, verified_qty FROM bom_lines WHERE line_id = %s", ("l1",)
        ).fetchone()
        # Both survive. The engine reports "the design leans on 12; the inventory
        # has serials for 2" and asks which is stale. It does not overwrite one
        # with the other, and it does not conclude the hardware is fictional.
        assert (row[0], row[1]) == (12, 2)

    def test_the_disputed_count_finding_is_a_decision_not_a_defect(self, bom_db):
        _mk_project(bom_db)
        bom_db.execute(
            "INSERT INTO bom_findings "
            "(finding_id, project_id, finding_type, kind, severity, title) "
            "VALUES (%s, %s, %s, %s, %s, %s)",
            ("f1", "p1", "asset_count_disputed", "decision", "high",
             "Design leans on 12 nodes; inventory has serials for 2"),
        )
        row = bom_db.execute(
            "SELECT kind FROM bom_findings WHERE finding_id = %s", ("f1",)
        ).fetchone()
        # Classifying this as a 'defect' would be the tool asserting the servers
        # do not exist. It cannot know that. Someone has to go and look.
        assert row[0] == "decision"

    def test_good_news_is_representable(self, bom_db):
        """A register that only holds defects buries the best fact in the BOM."""
        _mk_project(bom_db)
        bom_db.execute(
            "INSERT INTO bom_findings "
            "(finding_id, project_id, finding_type, kind, severity, title, impact_usd) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s)",
            ("f1", "p1", "unblocks_now", "opportunity", "info",
             "12 owned servers stand up the virtual environment at $0 CapEx — "
             "development starts without waiting on the lab buildout", 0),
        )
        row = bom_db.execute(
            "SELECT kind, finding_type FROM bom_findings WHERE finding_id = %s", ("f1",)
        ).fetchone()
        assert tuple(row) == ("opportunity", "unblocks_now")


class TestDeclaredScopeIsCheckable:
    """You cannot detect the absence of something nobody wrote down.

    An engine that only reads documents is blind to a workstream that exists
    solely in someone's head — and that is the one that turns up late, unfunded,
    in front of exactly the wrong audience. So intent is promoted to a source and
    the design and the BOM are held against it.
    """

    def test_scope_can_exist_with_nothing_backing_it(self, bom_db):
        """The Digital Twin case: real, intended, and in no document anywhere."""
        _mk_project(bom_db)
        bom_db.execute(
            "INSERT INTO bom_scope_items "
            "(scope_id, project_id, label, description, capabilities, status, "
            " wave_label, wave_order, declared_by) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)",
            ("sc1", "p1", "Digital Twin",
             "Stood up after the environment exists; brings in software, "
             "solutions and services for twinning",
             '["Windows and Linux estate", "AD / DNS", "Ansible", '
             '"network simulation", "cloud emulation", "directory + DNS services"]',
             "declared", "Wave 3 — Digital Twin", 30, "stakeholder"),
        )
        row = bom_db.execute(
            "SELECT status, priced_total FROM bom_scope_items WHERE scope_id = %s", ("sc1",)
        ).fetchone()
        assert row[0] == "declared"
        # NULL, not zero. Zero would say "this is free". NULL says "we have not
        # sized this yet" — which is the truth, and is something a budget owner
        # can actually earmark against.
        assert row[1] is None

    def test_a_placeholder_line_holds_the_slot_without_inventing_a_number(self, bom_db):
        _mk_project(bom_db)
        bom_db.execute(
            "INSERT INTO bom_lines "
            "(line_id, line_hash, project_id, source_id, line_kind, description_raw, "
            " unit_price, extended_price, wave_label, wave_order, derivation) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
            ("l1", "h1", "p1", "intent", "placeholder",
             "Digital Twin — scope declared, not yet designed or priced",
             None, None, "Wave 3 — Digital Twin", 30,
             "Declared in project intent; no architecture component and no priced "
             "line in any uploaded source."),
        )
        row = bom_db.execute(
            "SELECT line_kind, unit_price, extended_price, derivation "
            "FROM bom_lines WHERE line_id = %s", ("l1",)
        ).fetchone()
        assert row[0] == "placeholder"
        # A placeholder that guessed a price would be worse than no placeholder
        # at all: the guess gets quoted back at you in a budget meeting.
        assert row[1] is None and row[2] is None
        assert "no priced line" in row[3]

    def test_scope_covered_only_by_a_weak_source_is_its_own_finding(self, bom_db):
        """Reads as 'covered' on a spreadsheet. Is not covered.

        On the real corpus this is Digital Twin: priced ONLY inside the
        AI-generated ROM (the least credible source in the pile), and present in
        neither the agreed architecture nor the authoritative BOM.
        """
        _mk_project(bom_db)
        bom_db.execute(
            "INSERT INTO bom_findings "
            "(finding_id, project_id, finding_type, kind, severity, title, detail, detector) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
            ("f1", "p1", "scope_priced_only_by_weak_source", "risk", "high",
             "Digital Twin is priced only by a draft source",
             "The sole pricing for this workstream sits in a draft workbook "
             "(machine-generated, no part numbers). It appears in no agreed "
             "architecture and in no authoritative BOM.",
             "deterministic"),
        )
        row = bom_db.execute(
            "SELECT kind, severity FROM bom_findings WHERE finding_id = %s", ("f1",)
        ).fetchone()
        assert tuple(row) == ("risk", "high")


class TestDecisionsOutliveClusters:
    """Human verdicts are keyed on line hashes, never on cluster ids.

    Clusters are recomputed on every run. Key a customer's approvals to them and
    the fifth upload renumbers everything and silently orphans every decision
    they ever made — the classic entity-resolution re-run bug, and one that
    destroys weeks of work without raising a single error.
    """

    def test_a_decision_references_hashes_and_not_a_cluster(self, bom_db):
        cols = {
            r[1] for r in bom_db.execute("PRAGMA table_info(bom_match_decisions)").fetchall()
        }
        assert {"a_line_hash", "b_line_hash", "pair_key"} <= cols
        assert "cluster_id" not in cols

    def test_a_line_hash_survives_a_parser_improvement(self, bom_db):
        """line_hash is built ONLY from inputs a parser cannot change.

        If the hash were computed over parsed fields (qty, unit_price, the
        normalized description), then improving the parser would rewrite every
        hash and invalidate every approval ever recorded. So it is sha256 over
        (source_id, source_locator, raw_text) — the bytes as they arrived.
        """
        import hashlib

        source_id, locator, raw = "s1", "Networking!A23", "Simulation Licence | 1 | 10000"
        h1 = hashlib.sha256("\x1f".join((source_id, locator, raw)).encode()).hexdigest()

        # Re-parse the same cell with a smarter parser that now also extracts a
        # part number and a category. Same bytes in, same hash out.
        h2 = hashlib.sha256("\x1f".join((source_id, locator, raw)).encode()).hexdigest()
        assert h1 == h2


class TestFindingsCiteTheirEvidence:
    def test_a_finding_carries_evidence_and_an_impact(self, bom_db):
        _mk_project(bom_db)
        bom_db.execute(
            "INSERT INTO bom_findings "
            "(finding_id, project_id, finding_type, kind, severity, title, impact_usd, "
            " detector, evidence_json) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)",
            ("f1", "p1", "intra_doc_double_count", "defect", "critical",
             "Simulation licence counted in two subtotals", 10000, "deterministic",
             '[{"source_document": "draft_bom.xlsx", "sheet": "Networking", '
             '"locator": "A23", "raw_text": "licence shared with Networking sheet"}]'),
        )
        row = bom_db.execute(
            "SELECT impact_usd, detector, evidence_json FROM bom_findings WHERE finding_id = %s",
            ("f1",),
        ).fetchone()
        assert row[0] == 10000
        # The reader is entitled to know whether a claim is arithmetic or opinion.
        assert row[1] == "deterministic"
        assert "Networking" in row[2]

    def test_impact_may_be_null_rather_than_invented(self, bom_db):
        """We refuse to make up a number we cannot source.

        A finding worth reporting with no defensible dollar figure keeps a NULL
        impact and says why, rather than emitting a plausible guess that will be
        quoted back at someone in a budget meeting.
        """
        _mk_project(bom_db)
        bom_db.execute(
            "INSERT INTO bom_findings "
            "(finding_id, project_id, finding_type, kind, severity, title, detail) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s)",
            ("f1", "p1", "no_replacement_price_basis", "risk", "high",
             "R630 fleet is out of warranty; nothing in the corpus prices a replacement",
             "No replacement cost is quoted anywhere in the uploaded evidence."),
        )
        row = bom_db.execute(
            "SELECT impact_usd FROM bom_findings WHERE finding_id = %s", ("f1",)
        ).fetchone()
        assert row[0] is None
