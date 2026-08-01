# CUI // SP-CTI
"""The confidence sampler audits what nothing else will ever surface.

Items above the gate flow through automatically and nobody re-reads them; items
below it are dropped silently. So errors in the trusted band are not rare — they
are structurally unobservable. Random sampling is the only thing that forces a
look.

Measured on the live corpus before this existed: oracle_predictions joined to
kanban_verifications gave claimed 1.0 -> observed 0.89 and claimed 0.9 ->
observed 0.33, off 19 and 3 labels. Nobody had run that join.

No band below 0.7 exists to compare against — but NOT because the gate truncates
the table (the write is unconditional; the filter is at read time). It is because
`confidence` is a per-rule constant from awareness_config.yaml and every enabled
rule is hand-assigned 0.70..0.95. 423 live predictions carry 7 distinct values.
That makes a band mostly a single rule wearing a number, which is what
test_a_band_is_mostly_one_rule pins down.
"""

import importlib
import random
from pathlib import Path

import yaml

from tools.genesis.reflexes import confidence_sampler as cs

REPO_ROOT = Path(__file__).resolve().parents[1]

# `import tools.db.storage as storage` raises ImportError: tools.* is a shim over
# icdev.tools.*, and the submodule is not an attribute of the shimmed package.
# importlib resolves it properly — the same trap as the DIC inbox tests.
_storage = importlib.import_module("tools.db.storage")


def _cand(i, conf):
    return {"id": f"p{i}", "confidence": conf, "prediction_type": "risk",
            "prediction_text": f"prediction {i}", "lens_name": "lens"}


BANDS = [0.5, 0.6, 0.7, 0.8, 0.9, 1.0]


class TestRegistration:
    """Three points or it silently never runs."""

    def test_in_daemon_reflex_names(self):
        from tools.genesis.daemon import REFLEX_NAMES
        assert "confidence_sampler" in REFLEX_NAMES

    def test_in_genesis_config(self):
        cfg = yaml.safe_load((REPO_ROOT / "args" / "genesis_config.yaml").read_text(encoding="utf-8"))
        entry = cfg["reflexes"]["confidence_sampler"]
        assert entry["enabled"] is True
        assert entry["risk_tier"] == "green"

    def test_module_exposes_the_dispatch_contract(self):
        assert callable(cs.run)


class TestStratification:
    def test_draws_from_every_band_not_just_the_biggest(self):
        """The live shape: 173 rows at 1.0 vs 41 at 0.7. A flat random draw is
        ~41% 1.0s and starves the bands that need estimating."""
        cands = ([_cand(i, 1.0) for i in range(173)]
                 + [_cand(200 + i, 0.9) for i in range(163)]
                 + [_cand(400 + i, 0.7) for i in range(41)])
        drawn = cs.draw_sample(cands, set(), BANDS, per_band=3, width=0.1,
                               rng=random.Random(1))
        by_band = {}
        for d in drawn:
            by_band[d["band"]] = by_band.get(d["band"], 0) + 1
        assert by_band == {0.7: 3, 0.9: 3, 1.0: 3}, "each band is its own question"

    def test_a_thin_band_yields_what_it_has_not_an_error(self):
        cands = [_cand(1, 0.9)] + [_cand(2 + i, 1.0) for i in range(10)]
        drawn = cs.draw_sample(cands, set(), BANDS, per_band=5, width=0.1,
                               rng=random.Random(1))
        assert sum(1 for d in drawn if d["band"] == 0.9) == 1

    def test_samples_below_the_gate(self):
        """Every live oracle_prediction is >=0.7 — because no enabled rule claims
        less, not because the gate truncates the table. If a sub-threshold rule is
        ever enabled (stale_code, 0.50, is merely `enabled: false`), its rows are
        already written and must be auditable the moment they appear."""
        cands = [_cand(1, 0.55), _cand(2, 0.65)] + [_cand(3 + i, 0.9) for i in range(5)]
        drawn = cs.draw_sample(cands, set(), BANDS, per_band=2, width=0.1,
                               rng=random.Random(1))
        bands = {d["band"] for d in drawn}
        assert 0.5 in bands and 0.6 in bands, "sub-threshold items must be auditable"

    def test_band_outside_the_configured_list_is_ignored(self):
        drawn = cs.draw_sample([_cand(1, 0.1)], set(), BANDS, per_band=3, width=0.1,
                               rng=random.Random(1))
        assert drawn == []


class TestABandIsMostlyOneRule:
    """The thing that makes 'calibration by band' misleading here.

    `confidence` is never computed per prediction. gap_detector writes
    `float(rule_config["confidence"])` — a constant copied out of
    awareness_config.yaml. So the number identifies the RULE, not this
    prediction's likelihood of being right, and 423 live predictions carry 7
    distinct values:

        0.95  tool_not_in_manifest   165      0.85  orphan_db_table   82
        0.90  broken_test_reference   54      0.70  route_no_e2e      41

    Read that way, "the 0.9 band is right a third of the time" is a claim about
    broken_test_reference (54 of the 55 rows in that band) and not about high
    confidence at all. The per-rule cut is the one a human can act on, so the
    rule must survive into the recorded row.
    """

    def test_every_enabled_rule_claims_at_least_the_gate(self):
        """Not a style check — this is *why* the sub-threshold bands are empty.
        If it ever fails, the sampler starts drawing there and the explanation in
        the module docstring is out of date."""
        cfg = yaml.safe_load((REPO_ROOT / "args" / "awareness_config.yaml").read_text(encoding="utf-8"))
        gate = float(cfg["oracle"]["promotion_threshold"])
        rules = cfg["gaps"]
        below = {k: v["confidence"] for k, v in rules.items()
                 if v.get("enabled") and float(v.get("confidence", 1.0)) < gate}
        assert below == {}, f"an enabled rule now claims less than the gate: {below}"

    def test_the_rule_survives_into_the_recorded_row(self):
        """decision carries prediction_type, so calibration can be cut per rule
        rather than per band. Lose it and every band collapses into an average
        over rules that have nothing to do with each other."""
        drawn = cs.draw_sample([_cand(1, 0.9)], set(), BANDS, per_band=1, width=0.1,
                               rng=random.Random(1))
        assert drawn[0]["prediction_type"] == "risk"


class TestNeverRedraws:
    def test_an_already_sampled_item_is_not_drawn_again(self):
        """A re-drawn item votes twice, and keeps re-asking the same easy cases
        while the rest of the population stays unaudited."""
        cands = [_cand(i, 0.9) for i in range(5)]
        already = {"p0", "p1", "p2"}
        drawn = cs.draw_sample(cands, already, BANDS, per_band=5, width=0.1,
                               rng=random.Random(1))
        assert {d["id"] for d in drawn} == {"p3", "p4"}

    def test_everything_audited_means_an_empty_draw_not_a_repeat(self):
        cands = [_cand(i, 0.9) for i in range(3)]
        drawn = cs.draw_sample(cands, {"p0", "p1", "p2"}, BANDS, per_band=5, width=0.1,
                               rng=random.Random(1))
        assert drawn == []


class TestRandomness:
    def test_successive_draws_differ(self):
        """A sampler that picks the same rows every run audits the same rows
        forever, which is not an audit. Production must not be seeded."""
        cands = [_cand(i, 0.9) for i in range(50)]
        a = {d["id"] for d in cs.draw_sample(cands, set(), BANDS, 5, 0.1, rng=random.Random(1))}
        b = {d["id"] for d in cs.draw_sample(cands, set(), BANDS, 5, 0.1, rng=random.Random(2))}
        assert a != b

    def test_draw_is_pure_and_does_not_mutate_candidates(self):
        cands = [_cand(i, 0.9) for i in range(5)]
        before = [dict(c) for c in cands]
        cs.draw_sample(cands, set(), BANDS, 2, 0.1, rng=random.Random(1))
        assert cands == before, "an audit must not change its subject"


class TestReflexContract:
    def _ctx(self, **kw):
        ctx = {"surface": "oracle_triage", "per_band": 2}
        ctx.update(kw)
        return ctx

    def test_failure_to_build_the_candidate_set_is_reported(self, monkeypatch):
        """Not `success: True` regardless — the flaw in doc_modernization_sweep,
        which collects errors then returns success anyway."""
        def boom(*a, **k):
            raise RuntimeError("db down")

        monkeypatch.setattr(_storage, "get_connection", boom)
        out = cs.run(self._ctx())
        assert out["success"] is False
        assert "db down" in out["details"]["error"]

    def test_sample_reflex_name_is_namespaced(self):
        """Sampled rows must not pool with organic ones: organic labels exist
        only where the system chose to surface something, so combining an
        unbiased sample with a biased one inherits the bias while looking like
        more evidence."""
        assert cs.sample_reflex_name("oracle_triage") == "sampled:oracle_triage"

    def test_unsupported_surface_is_refused_not_silently_empty(self, monkeypatch):
        """An unknown surface must fail loudly. Returning an empty draw would
        look identical to "everything is already audited"."""
        class FakeConn:
            def execute(self, sql, *a, **k):
                # _already_sampled runs first and legitimately queries.
                class _C:
                    def fetchall(self):
                        return []
                return _C()

            def close(self):
                pass

        monkeypatch.setattr(_storage, "get_connection", lambda *a, **k: FakeConn())
        out = cs.run(self._ctx(surface="not_a_surface"))
        assert out["success"] is False
        assert "not_a_surface" in out["details"]["error"]


class TestTombstonesAreNotAudited:
    """Retired / dismissed predictions are tombstones and must not be re-drawn.

    A ``dismissed*`` row is a killed prediction (operator ruled it a false
    positive, or a retired rule left it behind); a ``promoted:*`` row already
    has real ground truth via the organic kanban_verifications join. Drawing
    either into the audit sample folds a stale/duplicate observation into the
    sampled calibration aggregate — the exact "stale tombstone rows keep
    influencing aggregate statistics" defect. The candidate query must restrict
    to still-open rows, matching suggested_card_writer._load_pending_predictions.
    """

    class _RecordingConn:
        def __init__(self):
            self.queries = []

        def execute(self, sql, *args, **kwargs):
            self.queries.append(sql)

            class _C:
                def fetchall(self_inner):
                    return []

            return _C()

        def close(self):
            pass

    def test_candidate_query_filters_out_closed_outcomes(self):
        conn = self._RecordingConn()
        cs._candidates(conn, "oracle_triage")
        sql = " ".join(self.conn_sql(conn)).lower()
        # The tombstone guard: only NULL / '' / 'pending' outcomes are eligible.
        assert "outcome is null" in sql
        assert "outcome = ''" in sql
        assert "outcome = 'pending'" in sql

    @staticmethod
    def conn_sql(conn):
        # The candidate query is the one that mentions oracle_predictions.
        return [q for q in conn.queries if "oracle_predictions" in q.lower()]


class TestPendingRowsAreInert:
    def test_a_pending_sample_cannot_move_a_metric(self):
        """The sampler writes rows with outcome NULL. _compute_ece drops
        unlabelled rows, so a pending sample cannot shift calibration until a
        human answers it."""
        from tools.genesis.harness.eval_harness import _compute_ece
        pending = [{"confidence": 0.9, "actual_outcome": None, "decision": "x"} for _ in range(10)]
        assert _compute_ece(pending) is None
