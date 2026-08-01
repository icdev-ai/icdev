# CUI // SP-CTI
"""E2E Test: Fine-Tuning Dashboard.

Verifies the Fine-Tuning dashboard pages at /finetune render correctly,
display system status, and support dataset/job/model management.
Ported from .claude/commands/e2e/finetune.md.

Prerequisites:
  - Dashboard running on port 5000
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest
from selenium.webdriver.common.by import By

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from tools.browser.driver_manager import get_driver  # noqa: E402
from tests.e2e_selenium.pages.base import BasePage  # noqa: E402

BASE_URL = os.environ.get("ICDEV_DASHBOARD_URL", "http://localhost:5050")
CUI_BANNER = "CUI // SP-CTI"

FINETUNE_SUB_PAGES = [
    ("/finetune/datasets", ["dataset", "training", "data"]),
    ("/finetune/jobs",     ["training jobs", "jobs", "job"]),
    ("/finetune/models",   ["model versions", "models", "model"]),
    ("/finetune/label",    ["label", "labeling", "annotation"]),
    ("/finetune/evaluate", ["evaluation", "evaluate", "eval"]),
]


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def driver():
    """Yield a headless WebDriver for the entire module, then quit."""
    drv = get_driver(headless=True, window_size=(1920, 1080))
    drv.implicitly_wait(5)
    yield drv
    drv.quit()


@pytest.fixture(scope="module")
def page(driver):
    """Return a BasePage bound to the dashboard base URL."""
    return BasePage(driver, BASE_URL)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestFinetuneDashboard:
    """Fine-Tuning Dashboard — ported from finetune.md."""

    def test_finetune_overview_loads_with_cui_banner(self, page: BasePage):
        """Steps 8-10: Navigate to /finetune and verify heading + CUI."""
        page.navigate("/finetune")
        page.screenshot("finetune_01_overview")

        body_text = page.driver.find_element(By.TAG_NAME, "body").text
        assert body_text, "Fine-tune overview page body is empty"
        assert CUI_BANNER in body_text, f"CUI banner '{CUI_BANNER}' not found on finetune page"

        # Step 9-10: Page heading contains "Fine-Tuning"
        fine_tune_found = "fine-tun" in body_text.lower() or "finetun" in body_text.lower()
        assert fine_tune_found, "No 'Fine-Tuning' heading found on finetune page"

    def test_stat_grid_is_visible(self, page: BasePage):
        """Steps 11-15: Verify stat grid with 4 expected cards."""
        page.navigate("/finetune")

        body_text = page.driver.find_element(By.TAG_NAME, "body").text.lower()

        # Step 12-15: Check for stat card labels
        stat_labels = ["datasets", "training jobs", "models", "active overrides"]
        found_stats = [label for label in stat_labels if label in body_text]

        stat_cards = page.driver.find_elements(
            By.CSS_SELECTOR,
            ".stat-card, .metric-card, .summary-card, .card",
        )

        page.screenshot("finetune_02_stat_grid")
        assert found_stats or stat_cards, (
            "No stat cards or stat labels found on finetune overview page"
        )

    def test_recent_jobs_section_is_visible(self, page: BasePage):
        """Step 16-17: Verify Recent Training Jobs section."""
        page.navigate("/finetune")

        body_text = page.driver.find_element(By.TAG_NAME, "body").text

        recent_jobs_found = (
            "recent training" in body_text.lower()
            or "recent jobs" in body_text.lower()
            or "training job" in body_text.lower()
        )

        tables_or_empty = page.driver.find_elements(
            By.CSS_SELECTOR,
            "table, .empty-state, .no-data, .table-container",
        )

        page.screenshot("finetune_03_recent_jobs")
        assert recent_jobs_found or tables_or_empty, (
            "No recent training jobs section found on finetune overview"
        )

    def test_sub_page_navigation_links(self, page: BasePage):
        """Step 18: Verify links to Datasets, Jobs, Models sub-pages."""
        page.navigate("/finetune")

        body_text = page.driver.find_element(By.TAG_NAME, "body").text

        # Step 18: Links to sub-pages present
        nav_terms = ["datasets", "jobs", "models"]
        nav_links_found = page.driver.find_elements(
            By.XPATH,
            "//a[contains(@href, '/finetune/')]",
        )
        nav_text_found = any(term in body_text.lower() for term in nav_terms)

        assert nav_links_found or nav_text_found, (
            "No sub-page navigation links found on finetune overview"
        )

    def test_datasets_sub_page_loads(self, page: BasePage):
        """Steps 19-22: Navigate to /finetune/datasets."""
        page.navigate("/finetune/datasets")
        page.screenshot("finetune_04_datasets")

        body_text = page.driver.find_element(By.TAG_NAME, "body").text.lower()
        assert "dataset" in body_text, "No 'Datasets' heading on datasets sub-page"

        # Step 21: Create Dataset button
        create_btns = page.driver.find_elements(
            By.XPATH,
            "//*[contains(translate(text(),'abcdefghijklmnopqrstuvwxyz','ABCDEFGHIJKLMNOPQRSTUVWXYZ'),'CREATE DATASET') "
            "or contains(translate(text(),'abcdefghijklmnopqrstuvwxyz','ABCDEFGHIJKLMNOPQRSTUVWXYZ'),'NEW DATASET')]",
        )
        assert create_btns or "create" in body_text, (
            "No 'Create Dataset' button found on datasets page"
        )

    def test_jobs_sub_page_loads(self, page: BasePage):
        """Steps 23-25: Navigate to /finetune/jobs."""
        page.navigate("/finetune/jobs")
        page.screenshot("finetune_05_jobs")

        body_text = page.driver.find_element(By.TAG_NAME, "body").text.lower()
        assert "job" in body_text or "training" in body_text, (
            "No 'Training Jobs' heading on jobs sub-page"
        )

    def test_models_sub_page_loads(self, page: BasePage):
        """Steps 26-28: Navigate to /finetune/models."""
        page.navigate("/finetune/models")
        page.screenshot("finetune_06_models")

        body_text = page.driver.find_element(By.TAG_NAME, "body").text.lower()
        assert "model" in body_text, "No 'Model Versions' heading on models sub-page"

    def test_label_sub_page_loads(self, page: BasePage):
        """Steps 29-32: Navigate to /finetune/label."""
        page.navigate("/finetune/label")
        page.screenshot("finetune_07_label")

        body_text = page.driver.find_element(By.TAG_NAME, "body").text.lower()
        assert "label" in body_text or "annotati" in body_text, (
            "No 'Labeling' heading on label sub-page"
        )

    def test_evaluate_sub_page_loads(self, page: BasePage):
        """Steps 33-35: Navigate to /finetune/evaluate."""
        page.navigate("/finetune/evaluate")
        page.screenshot("finetune_08_evaluate")

        body_text = page.driver.find_element(By.TAG_NAME, "body").text.lower()
        assert "evaluat" in body_text, "No 'Evaluations' heading on evaluate sub-page"

    def test_responsive_screenshots(self, page: BasePage):
        """Steps 36-41: Capture screenshots at desktop, tablet, mobile viewports."""
        # Desktop 1920x1080
        page.driver.set_window_size(1920, 1080)
        page.navigate("/finetune")
        page.screenshot("finetune_09_desktop_1920x1080")

        # Tablet 768x1024
        page.driver.set_window_size(768, 1024)
        page.navigate("/finetune")
        page.screenshot("finetune_10_tablet_768x1024")

        # Mobile 375x812
        page.driver.set_window_size(375, 812)
        page.navigate("/finetune")
        page.screenshot("finetune_11_mobile_375x812")

        # Restore desktop
        page.driver.set_window_size(1920, 1080)

    def test_cui_banner_on_all_finetune_pages(self, page: BasePage):
        """Step 5-7 (CUI): Verify CUI banner is present on all finetune pages."""
        paths_to_check = ["/finetune"] + [p for p, _ in FINETUNE_SUB_PAGES]
        for path in paths_to_check:
            page.navigate(path)
            body_text = page.driver.find_element(By.TAG_NAME, "body").text
            assert CUI_BANNER in body_text, (
                f"CUI banner '{CUI_BANNER}' not found on {path}"
            )
# CUI // SP-CTI
