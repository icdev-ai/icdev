#!/usr/bin/env python3
# CUI // SP-CTI
"""Unit tests for the hardened `new_page_completeness` coherence check (TCH).

Background (tch-gate-*): the original completeness gate only globbed
``tools/dashboard/templates/*/page.html``. Canvases whose templates are named
differently (``slides/{index,detail,new}.html``, ``mfa/{enroll,challenge}.html``,
``zta/lac_simulator.html``) shipped with no ``icdev/`` mirror at all and slipped
through entirely — and ~44/56 canvases were never checked. ACE slipped exactly
this way.

The hardened ``check_new_page_completeness`` adds a full mirror-parity sub-check
that set-diffs every ``*.html`` in each canvas dir against its ``icdev/`` mirror,
so a canvas with NO ``page.html`` is now examined too.

These tests build fully synthetic temp-canvas fixtures under a patched
``PROJECT_ROOT`` and exercise the gate's behaviour directly — no live DB, no
real ``args/`` whitelist. Patching is shim-aware: we import the canonical
``tools.workflow.coherence_checker`` module object and ``monkeypatch.setattr``
its module globals, then call the function off that same object, so the
function's ``__globals__`` lookups see the patched values (the ``tools.*`` and
``icdev.tools.*`` shim modules are distinct objects).
"""

from pathlib import Path


# ---------------------------------------------------------------------------
# Fixture builders
# ---------------------------------------------------------------------------

# Marker the gate scans for as IQE-widget component #7.
PAGE_WIDGET = "{% include 'includes/iqe_query_widget.html' %}"

# Minimal blueprint that satisfies the @route + render_template checks.
BLUEPRINT_PY = (
    "from flask import Blueprint, render_template\n"
    "bp = Blueprint('x', __name__)\n\n\n"
    "@bp.route('/x')\n"
    "def x():\n"
    "    return render_template('x/page.html')\n"
)


def _write(path: Path, text: str = "") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _norm(s: str) -> str:
    """Normalize OS path separators so assertions are platform-agnostic."""
    return s.replace("\\", "/")


def make_root(tmp_path: Path) -> Path:
    """Create an empty synthetic project root with the dirs the gate reads."""
    root = tmp_path / "proj"
    (root / "tools" / "dashboard" / "templates").mkdir(parents=True)
    (root / "icdev" / "tools" / "dashboard" / "templates").mkdir(parents=True)
    (root / "tools" / "iqe" / "adapters").mkdir(parents=True)
    (root / "context" / "iqe" / "queries").mkdir(parents=True)
    return root


def write_base_html(root: Path, nav_slugs=()) -> None:
    """Write base.html containing nav links for the given canvas slugs."""
    links = "".join(f'<a href="/{s}">{s}</a>\n' for s in nav_slugs)
    _write(root / "tools" / "dashboard" / "templates" / "base.html", f"<nav>\n{links}</nav>\n")


def build_page_canvas(root: Path, name: str, *, complete: bool = True, mirror: bool = True) -> None:
    """Build a page.html-keyed canvas.

    When ``complete`` is True all seven sibling components are created so the
    8-component gate passes (the caller must still add the nav link via
    ``write_base_html``). When False, only an incomplete page.html is written.
    """
    page_text = f"<html><body>{PAGE_WIDGET if complete else ''}</body></html>"
    _write(root / "tools" / "dashboard" / "templates" / name / "page.html", page_text)
    if mirror:
        _write(
            root / "icdev" / "tools" / "dashboard" / "templates" / name / "page.html",
            page_text,
        )
    if complete:
        _write(root / "tools" / name / "blueprint.py", BLUEPRINT_PY)
        _write(root / "tools" / name / "service.py", "def go():\n    return 1\n")
        _write(root / "tools" / "iqe" / "adapters" / f"{name}.py", "# iqe adapter\n")
        _write(root / "context" / "iqe" / "queries" / name / "seed.yaml", "queries: []\n")


# Split-blueprint assembler (cvx-net-01 pattern): a thin file whose
# create_*_blueprint() delegates to register_<group>_routes(bp) helpers imported
# from a sibling routes/ subpackage. It carries NO @route decorator itself.
SPLIT_ASSEMBLER_PY = (
    "from flask import Blueprint\n"
    "from tools.{name}.routes.pages import register_pages_routes\n\n\n"
    "def create_{name}_blueprint():\n"
    "    bp = Blueprint('{name}', __name__)\n"
    "    register_pages_routes(bp)\n"
    "    return bp\n"
)

# Route-group module living under routes/ — this is where the @route lives.
SPLIT_ROUTE_MODULE_PY = (
    "from flask import render_template\n\n\n"
    "def register_pages_routes(bp):\n"
    "    @bp.route('/{name}')\n"
    "    def {name}_page():\n"
    "        return render_template('{name}/page.html')\n"
)

# Route-group module with NO @route anywhere — genuine failure case.
SPLIT_ROUTE_MODULE_NO_ROUTE_PY = (
    "def register_pages_routes(bp):\n"
    "    # forgot to declare any routes here\n"
    "    return None\n"
)


def build_split_blueprint_canvas(
    root: Path, name: str, *, route_in_routes: bool = True, mirror: bool = True
) -> None:
    """Build a complete page.html canvas whose blueprint is a SPLIT assembler.

    The blueprint.py file references ``register_*_routes`` / imports from a
    sibling ``routes/`` subpackage and carries no ``@route`` decorator itself.
    When ``route_in_routes`` is True the routes/ module declares an ``@bp.route``
    (gate should pass); when False no module declares any route (gate should
    fail with "blueprint has no @route decorator").
    """
    build_page_canvas(root, name, complete=True, mirror=mirror)
    # Overwrite the classic single-file blueprint with a thin split assembler.
    _write(root / "tools" / name / "blueprint.py", SPLIT_ASSEMBLER_PY.format(name=name))
    _write(root / "tools" / name / "routes" / "__init__.py", "")
    route_body = SPLIT_ROUTE_MODULE_PY if route_in_routes else SPLIT_ROUTE_MODULE_NO_ROUTE_PY
    _write(root / "tools" / name / "routes" / "pages.py", route_body.format(name=name))


def build_template_only_canvas(root: Path, name: str, template_names, *, mirror: bool = True) -> None:
    """Build a canvas with arbitrary template names and NO page.html.

    This is the slides/mfa/zta class the original gate was blind to.
    """
    for t in template_names:
        text = f"<html><!-- {name}/{t} --></html>"
        _write(root / "tools" / "dashboard" / "templates" / name / t, text)
        if mirror:
            _write(root / "icdev" / "tools" / "dashboard" / "templates" / name / t, text)


def run_check(root: Path, monkeypatch, whitelist: Path = None, exemptions: Path = None):
    """Invoke check_new_page_completeness against the synthetic root.

    Shim-aware: patch + call on the SAME canonical module object so the
    function's global lookups (PROJECT_ROOT, whitelist config) resolve to the
    fixtures, not the real repo.
    """
    from tools.workflow import coherence_checker as cc

    monkeypatch.setattr(cc, "PROJECT_ROOT", root)
    # Default both config paths to non-existent files → loader returns an empty
    # set (nothing grandfathered/exempt). These module constants are bound to the
    # REAL repo root at import time, so they must be patched explicitly to keep
    # synthetic tests isolated from the live args/ files.
    monkeypatch.setattr(
        cc,
        "_PAGE_COMPLETENESS_WHITELIST_CONFIG",
        whitelist if whitelist is not None else (root / "no_such_whitelist.yaml"),
    )
    monkeypatch.setattr(
        cc,
        "_COMPLETION_EXEMPTIONS_CONFIG",
        exemptions if exemptions is not None else (root / "no_such_exemptions.yaml"),
    )
    return cc.check_new_page_completeness()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestHardenedCompleteness:
    """Behavioural tests for the hardened new_page_completeness gate."""

    def test_non_page_canvas_is_now_checked(self, tmp_path, monkeypatch):
        """Regression for the blind spot: a canvas with only index.html (no
        page.html) and no icdev/ mirror must now be flagged. The original gate
        globbed only page.html and never saw such a canvas."""
        root = make_root(tmp_path)
        write_base_html(root)
        build_template_only_canvas(root, "solo", ["index.html"], mirror=False)

        result = run_check(root, monkeypatch)

        assert result.status == "fail"
        missing = [_norm(m) for m in result.missing]
        assert any(
            "solo/index.html" in m and "mirror missing" in m for m in missing
        ), missing

    def test_complete_canvas_passes(self, tmp_path, monkeypatch):
        """A canvas with all 8 components + icdev mirror produces no violations."""
        root = make_root(tmp_path)
        build_page_canvas(root, "alpha", complete=True, mirror=True)
        write_base_html(root, ["alpha"])

        result = run_check(root, monkeypatch)

        assert result.status == "pass", result.missing
        assert result.missing == []
        assert "complete" in result.message.lower()

    def test_missing_icdev_mirror_fails(self, tmp_path, monkeypatch):
        """slides/mfa/zta class: differently-named templates with no icdev/
        mirror must each be flagged as a mirror gap (the exact case the
        hardened sub-check was added to catch)."""
        root = make_root(tmp_path)
        write_base_html(root)
        build_template_only_canvas(
            root, "slides", ["index.html", "detail.html", "new.html"], mirror=False
        )

        result = run_check(root, monkeypatch)

        assert result.status == "fail"
        missing = [_norm(m) for m in result.missing]
        for tmpl in ("index.html", "detail.html", "new.html"):
            assert any(
                f"slides/{tmpl}" in m and "mirror missing" in m for m in missing
            ), (tmpl, missing)

    def test_partial_mirror_flags_only_unmirrored(self, tmp_path, monkeypatch):
        """When some templates are mirrored and others are not, only the
        unmirrored ones are reported — set-diff, not all-or-nothing."""
        root = make_root(tmp_path)
        write_base_html(root)
        # mirror index.html but NOT challenge.html
        build_template_only_canvas(root, "mfa", ["enroll.html"], mirror=True)
        _write(root / "tools" / "dashboard" / "templates" / "mfa" / "challenge.html", "<html></html>")

        result = run_check(root, monkeypatch)

        assert result.status == "fail"
        missing = [_norm(m) for m in result.missing]
        assert any("mfa/challenge.html" in m and "mirror missing" in m for m in missing), missing
        assert not any("mfa/enroll.html" in m for m in missing), missing

    def test_adapter_name_differs_from_dir_passes(self, tmp_path, monkeypatch):
        """dic/sdc style: template dir name (document_intelligence) differs from
        the IQE adapter file name (dic.py), the canvas has no page.html, and all
        templates are mirrored. The gate must NOT false-positive on it."""
        root = make_root(tmp_path)
        write_base_html(root)
        build_template_only_canvas(
            root, "document_intelligence", ["index.html", "search.html"], mirror=True
        )
        # adapter deliberately named differently from the directory
        _write(root / "tools" / "iqe" / "adapters" / "dic.py", "# adapter name differs from dir\n")

        result = run_check(root, monkeypatch)

        assert result.status == "pass", result.missing
        assert result.missing == []

    def test_whitelisted_canvas_is_skipped(self, tmp_path, monkeypatch):
        """A broken canvas listed in the whitelist is skipped by BOTH the
        8-component loop and the mirror-parity loop, flipping fail → pass."""
        root = make_root(tmp_path)
        write_base_html(root)
        # page.html canvas missing every sibling component and its mirror
        build_page_canvas(root, "broken", complete=False, mirror=False)
        # plus a template-only canvas with no mirror, to exercise mirror-skip
        build_template_only_canvas(root, "broken", ["extra.html"], mirror=False)

        # 1) Without a whitelist the fixture fails.
        result_no_wl = run_check(root, monkeypatch)
        assert result_no_wl.status == "fail", result_no_wl.actual

        # 2) Whitelisting the canvas skips it entirely → pass.
        wl = tmp_path / "whitelist.yaml"
        wl.write_text("whitelisted_canvases:\n  - broken\n", encoding="utf-8")
        result_wl = run_check(root, monkeypatch, whitelist=wl)

        assert result_wl.status == "pass", result_wl.missing
        assert result_wl.missing == []
        # the whitelist note is surfaced in the actual/expected lines
        assert "whitelisted" in result_wl.actual[0]

    def test_completion_exemptions_buckets_skip_canvas(self, tmp_path, monkeypatch):
        """args/completion_exemptions.yaml (tch-fix-04): canvases in the
        iqe_exempt / iqe_wired_via_alias / iqe_partial buckets are skipped, but
        iqe_required canvases are NOT — they keep failing the gate."""
        root = make_root(tmp_path)
        write_base_html(root)
        # Three broken canvases, one per skip bucket, plus one required.
        for name in ("util_page", "alias_page", "partial_page", "needs_iqe"):
            build_page_canvas(root, name, complete=False, mirror=False)
            build_template_only_canvas(root, name, ["extra.html"], mirror=False)

        # Without exemptions every broken canvas fails.
        result_none = run_check(root, monkeypatch)
        assert result_none.status == "fail", result_none.actual

        exemptions = tmp_path / "completion_exemptions.yaml"
        exemptions.write_text(
            "iqe_exempt:\n"
            "  util_page: utility\n"
            "iqe_wired_via_alias:\n"
            "  alias_page: wired via xyz\n"
            "iqe_partial:\n"
            "  partial_page: needs dispatch\n"
            "iqe_required:\n"
            "  needs_iqe: data-bearing backfill target\n",
            encoding="utf-8",
        )
        result = run_check(root, monkeypatch, exemptions=exemptions)

        # Still fails because the required canvas is NOT skipped.
        assert result.status == "fail", result.actual
        missing = [_norm(m) for m in result.missing]
        # The three skip-bucket canvases are gone from the violations.
        for skipped in ("util_page", "alias_page", "partial_page"):
            assert not any(skipped in m for m in missing), (skipped, missing)
        # The required canvas is still flagged.
        assert any("needs_iqe" in m for m in missing), missing

    def test_completion_exemptions_list_form_supported(self, tmp_path, monkeypatch):
        """A bucket given as a plain list (not a mapping) is still honoured."""
        root = make_root(tmp_path)
        write_base_html(root)
        build_page_canvas(root, "listed", complete=False, mirror=False)

        exemptions = tmp_path / "completion_exemptions.yaml"
        exemptions.write_text("iqe_exempt:\n  - listed\n", encoding="utf-8")
        result = run_check(root, monkeypatch, exemptions=exemptions)

        assert result.status == "pass", result.missing

    def test_no_canvases_passes(self, tmp_path, monkeypatch):
        """An empty templates tree is vacuously complete (guards the
        checked_count == 0 branch)."""
        root = make_root(tmp_path)
        write_base_html(root)

        result = run_check(root, monkeypatch)

        assert result.status == "pass"
        assert result.missing == []


class TestSplitBlueprintDetection:
    """Regression for cvx-net-03: the completeness gate must recognize SPLIT
    blueprints. After the network blueprint split (PR #397) the module file is a
    thin assembler that calls register_<group>_routes(bp); the @route decorators
    live under routes/*.py. The gate previously asserted the module file itself
    contained @route and falsely reported network "has no @route"."""

    def test_split_blueprint_with_route_in_routes_passes(self, tmp_path, monkeypatch):
        """(a) Assembler + routes/ dir with an @route → passes."""
        root = make_root(tmp_path)
        build_split_blueprint_canvas(root, "netlike", route_in_routes=True, mirror=True)
        write_base_html(root, ["netlike"])

        result = run_check(root, monkeypatch)

        assert result.status == "pass", result.missing
        assert result.missing == []

    def test_split_blueprint_without_any_route_fails(self, tmp_path, monkeypatch):
        """(b) Assembler + routes/ dir with NO @route anywhere → still fails.

        The check stays genuine: delegating to routes/ that declare no route is
        indistinguishable from a truly routeless blueprint."""
        root = make_root(tmp_path)
        build_split_blueprint_canvas(root, "netlike", route_in_routes=False, mirror=True)
        write_base_html(root, ["netlike"])

        result = run_check(root, monkeypatch)

        assert result.status == "fail"
        missing = [_norm(m) for m in result.missing]
        assert any("no @route decorator" in m for m in missing), missing

    def test_classic_single_file_blueprint_still_passes(self, tmp_path, monkeypatch):
        """(c) Classic single-file blueprint with an inline @route → still passes
        (no regression from the split-aware helper)."""
        root = make_root(tmp_path)
        build_page_canvas(root, "classic", complete=True, mirror=True)
        write_base_html(root, ["classic"])

        result = run_check(root, monkeypatch)

        assert result.status == "pass", result.missing
        assert result.missing == []


class TestBlueprintHasRouteHelper:
    """Direct unit tests for the _blueprint_has_route helper shared by both
    detection sites (check_new_page_completeness + check_canvas_completeness)."""

    def test_inline_route_detected(self, tmp_path):
        from tools.workflow import coherence_checker as cc

        bp = tmp_path / "tools" / "c" / "blueprint.py"
        _write(bp, BLUEPRINT_PY)
        assert cc._blueprint_has_route(bp) is True

    def test_split_route_in_routes_detected(self, tmp_path):
        from tools.workflow import coherence_checker as cc

        bp = tmp_path / "tools" / "c" / "blueprint.py"
        _write(bp, SPLIT_ASSEMBLER_PY.format(name="c"))
        _write(tmp_path / "tools" / "c" / "routes" / "__init__.py", "")
        _write(
            tmp_path / "tools" / "c" / "routes" / "pages.py",
            SPLIT_ROUTE_MODULE_PY.format(name="c"),
        )
        assert cc._blueprint_has_route(bp) is True

    def test_split_without_route_is_false(self, tmp_path):
        from tools.workflow import coherence_checker as cc

        bp = tmp_path / "tools" / "c" / "blueprint.py"
        _write(bp, SPLIT_ASSEMBLER_PY.format(name="c"))
        _write(tmp_path / "tools" / "c" / "routes" / "__init__.py", "")
        _write(
            tmp_path / "tools" / "c" / "routes" / "pages.py",
            SPLIT_ROUTE_MODULE_NO_ROUTE_PY.format(name="c"),
        )
        assert cc._blueprint_has_route(bp) is False

    def test_routeless_no_routes_dir_is_false(self, tmp_path):
        from tools.workflow import coherence_checker as cc

        bp = tmp_path / "tools" / "c" / "blueprint.py"
        _write(bp, "def create_c_blueprint():\n    return None\n")
        assert cc._blueprint_has_route(bp) is False


class TestRegistryNavKindAgnostic:
    """A registry nav link renders via nav_tree for ANY component with
    nav.section — not only kind=canvas. Regression for the standards_catalog
    false-positive (kind=core_extension, nav.section=Platform)."""

    def _write_registry(self, root: Path) -> None:
        _write(
            root / "args" / "component_registry.yaml",
            "components:\n"
            "  - key: my_canvas\n"
            "    kind: canvas\n"
            "    nav:\n"
            "      section: Canvases\n"
            "      label: My Canvas\n"
            "    completeness:\n"
            "      template: tools/dashboard/templates/my_canvas/page.html\n"
            "  - key: my_core_ext\n"
            "    kind: core_extension\n"
            "    nav:\n"
            "      section: Platform\n"
            "      label: My Core Ext\n"
            "    completeness:\n"
            "      template: tools/dashboard/templates/my_core_ext/page.html\n"
            "  - key: no_nav_ext\n"
            "    kind: core_extension\n",
        )

    def test_core_extension_with_nav_section_is_recognized(self, tmp_path, monkeypatch):
        root = make_root(tmp_path)
        self._write_registry(root)
        from tools.workflow import coherence_checker as cc

        monkeypatch.setattr(cc, "PROJECT_ROOT", root)
        dirs = cc._load_registry_nav_dirs()

        assert "my_canvas" in dirs          # canvas kind still recognized
        assert "my_core_ext" in dirs        # core_extension with nav.section now recognized
        assert "no_nav_ext" not in dirs     # no nav.section → not a nav dir


class TestSlugTemplateDirAliasesLive:
    """cvx-nav-02: canvases whose URL slug differs from their template-dir name
    (aiify→/ai-ify, workflow_canvas→/workflow-canvas, canvas_health→/health/
    canvases) must pass the 8-component gate on the registry-nav alias mechanism
    ALONE — with NO entry in args/page_completeness_whitelist.yaml.

    These run against the LIVE repo (no PROJECT_ROOT patch): they assert the real
    whitelist no longer lists the three canvases AND that the real completeness
    check reports zero violations for any of them. The overall gate may still fail
    on unrelated pre-existing debt, so we assert on the per-canvas absence of
    violations, not on the aggregate status.
    """

    ALIASED = ("aiify", "workflow_canvas", "canvas_health")

    def test_aliased_canvases_not_whitelisted(self):
        from tools.workflow import coherence_checker as cc

        whitelist = cc._load_page_completeness_whitelist()
        for canvas in self.ALIASED:
            assert canvas not in whitelist, (
                f"{canvas} must resolve via the registry-nav alias, not a "
                f"page_completeness_whitelist.yaml entry (cvx-nav-02)"
            )

    def test_aliased_canvases_have_no_completeness_violations(self):
        from tools.workflow import coherence_checker as cc

        result = cc.check_new_page_completeness()
        offenders = [
            m for m in result.missing
            if any(
                f"templates{sep}{canvas}{sep}" in m or f"{canvas}-" in m
                for canvas in self.ALIASED
                for sep in ("/", "\\")
            )
        ]
        assert not offenders, (
            "aliased canvases must pass the 8-component gate without a "
            f"whitelist entry; violations: {offenders}"
        )

    def test_aiify_registry_template_points_at_real_dir(self):
        """The aiify fix: completeness.template must reference the on-disk
        'aiify/' dir (not the ghost 'ai-ify/'), so _load_registry_nav_dirs keys
        it under 'aiify' and the nav-link alias resolves."""
        import yaml
        from tools.workflow import coherence_checker as cc

        registry = cc.PROJECT_ROOT / "args" / "component_registry.yaml"
        data = yaml.safe_load(registry.read_text(encoding="utf-8"))
        aiify = next(c for c in data["components"] if c.get("key") == "aiify")
        tpl = aiify["completeness"]["template"]
        assert tpl == "tools/dashboard/templates/aiify/page.html", tpl
        assert (cc.PROJECT_ROOT / tpl).exists(), f"{tpl} must exist on disk"
        assert "aiify" in cc._load_registry_nav_dirs()


class TestCompletenessWhitelistRetiredLive:
    """cvx-tch-01/02/03: the legacy args/page_completeness_whitelist.yaml
    grandfather list is fully retired (emptied). The final 10 canvases were
    resolved three ways:

      CLEARED     — brought to full 8-component compliance (ai_observatory,
                    cache_savings, aisg, strategos) or already passing after
                    earlier merged work (demo_runner, ontology). These must be
                    absent from the ENTIRE skip set (no legacy entry AND no
                    completion_exemptions.yaml entry).
      RECLASSIFIED — utility pages with no query-worthy data model
                    (forge_academy, il5, safety_monitor, system_graph). Removed
                    from the legacy whitelist but still correctly excused via
                    completion_exemptions.yaml :: iqe_exempt.

    Runs against the LIVE repo (no PROJECT_ROOT patch). The overall gate may
    still fail on unrelated pre-existing debt (e.g. the security_canvas/sdc
    registry gap), so we assert on per-canvas absence of violations, not on the
    aggregate status — mirroring TestSlugTemplateDirAliasesLive (cvx-nav-02).
    """

    CLEARED = (
        "ai_observatory", "cache_savings", "aisg", "strategos",
        "demo_runner", "ontology",
    )
    RECLASSIFIED = ("forge_academy", "il5", "safety_monitor", "system_graph")

    @staticmethod
    def _legacy_entries():
        import yaml
        from tools.workflow import coherence_checker as cc

        path = cc._PAGE_COMPLETENESS_WHITELIST_CONFIG
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        return list(data.get("whitelisted_canvases") or [])

    @staticmethod
    def _violations_for(names):
        from tools.workflow import coherence_checker as cc

        result = cc.check_new_page_completeness()
        return [
            m for m in result.missing
            if any(
                f"templates{sep}{canvas}{sep}" in m or f"{canvas}-" in m
                for canvas in names
                for sep in ("/", "\\")
            )
        ]

    def test_legacy_whitelist_file_is_fully_retired(self):
        assert self._legacy_entries() == [], (
            "args/page_completeness_whitelist.yaml must be emptied "
            f"(cvx-tch-01/02/03); still lists: {self._legacy_entries()}"
        )

    def test_cleared_canvases_not_in_any_skip_set(self):
        from tools.workflow import coherence_checker as cc

        skip = cc._load_page_completeness_whitelist()
        for canvas in self.CLEARED:
            assert canvas not in skip, (
                f"{canvas} was brought to full 8-component compliance (or already "
                "passes) and must not be skipped by the whitelist or exemptions"
            )

    def test_cleared_canvases_have_no_completeness_violations(self):
        offenders = self._violations_for(self.CLEARED)
        assert not offenders, (
            "cleared canvases must pass the 8-component gate with no whitelist "
            f"entry; violations: {offenders}"
        )

    def test_reclassified_removed_from_legacy_but_still_exempt(self):
        from tools.workflow import coherence_checker as cc

        legacy = set(self._legacy_entries())
        skip = cc._load_page_completeness_whitelist()
        for canvas in self.RECLASSIFIED:
            assert canvas not in legacy, (
                f"{canvas} must be removed from the legacy whitelist file"
            )
            assert canvas in skip, (
                f"{canvas} must remain excused via completion_exemptions.yaml "
                "(iqe_exempt utility page — no query-worthy data model)"
            )

    def test_reclassified_canvases_have_no_completeness_violations(self):
        offenders = self._violations_for(self.RECLASSIFIED)
        assert not offenders, (
            "reclassified utility canvases must not surface completeness "
            f"violations; got: {offenders}"
        )


class TestSeedQueryConsolidationLive:
    """cvx-nav-02: the duplicate seed-query dirs (ccc+ccc_canvas, dsoc+dsoc_canvas,
    pmc+pmc_canvas) are consolidated onto the loaded `<key>_canvas/*.iqe` dirs;
    the unread bare `<key>/seed.yaml` orphans are removed. Assert the loaded dir
    is populated, the orphan is gone, and the registry seed_queries path points
    at the surviving dir."""

    CANVASES = ("ccc", "dsoc", "pmc")

    def test_loaded_canvas_dirs_populated_and_orphans_removed(self):
        from tools.workflow import coherence_checker as cc

        root = cc.PROJECT_ROOT
        for key in self.CANVASES:
            loaded = root / "context" / "iqe" / "queries" / f"{key}_canvas"
            orphan = root / "context" / "iqe" / "queries" / key
            assert loaded.is_dir(), f"{loaded} must exist"
            assert list(loaded.glob("*.iqe")), f"{loaded} must hold .iqe seeds"
            assert not orphan.exists(), f"orphan {orphan} must be removed"

    def test_registry_seed_queries_points_at_surviving_dir(self):
        import yaml
        from tools.workflow import coherence_checker as cc

        registry = cc.PROJECT_ROOT / "args" / "component_registry.yaml"
        data = yaml.safe_load(registry.read_text(encoding="utf-8"))
        by_key = {c.get("key"): c for c in data["components"]}
        for key in self.CANVASES:
            seed = by_key[key]["completeness"]["seed_queries"]
            assert seed == f"context/iqe/queries/{key}_canvas/", seed
            assert (cc.PROJECT_ROOT / seed).is_dir()
