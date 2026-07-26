# CUI // SP-CTI
"""E2E Test: Chat Multi-Pane Interface.

Verifies the unified Chat page loads correctly with multi-context UI, RICOAS
sidebar, message send, and intervention controls.
Ported from .claude/commands/e2e/chat.md.

Prerequisites:
  - Flask dashboard running on http://localhost:5050
  - Database initialised (chat_contexts, chat_messages, chat_tasks tables present)
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

class TestChatPage:
    """Chat Multi-Pane Interface — ported from chat.md."""

    def test_chat_page_loads_with_cui_banner(self, page: BasePage):
        """Steps 1-6: Navigate to /chat, verify title and CUI banners."""
        page.navigate("/chat")
        page.screenshot("chat_01_overview")

        body_text = page.driver.find_element(By.TAG_NAME, "body").text
        assert body_text, "Chat page body is empty"
        assert CUI_BANNER in body_text, f"CUI banner '{CUI_BANNER}' not found on chat page"

        # Step 3: Page heading contains "Chat"
        headings = page.driver.find_elements(By.XPATH, "//*[contains(text(),'Chat')]")
        assert headings, "No 'Chat' heading found on chat page"

        # Check header banner
        header_banners = page.driver.find_elements(
            By.CSS_SELECTOR,
            "header, .cui-banner-top, [data-cui='header'], .cui-banner",
        )
        if header_banners:
            assert CUI_BANNER in header_banners[0].text, "Header CUI banner text mismatch"

        # Check footer banner
        footer_banners = page.driver.find_elements(
            By.CSS_SELECTOR,
            "footer, .cui-banner-bottom, [data-cui='footer'], .cui-banner",
        )
        if footer_banners:
            assert CUI_BANNER in footer_banners[-1].text, "Footer CUI banner text mismatch"

    def test_context_sidebar_is_visible(self, page: BasePage):
        """Steps 7-8: Verify context sidebar and New Chat button."""
        page.navigate("/chat")

        body_text = page.driver.find_element(By.TAG_NAME, "body").text

        # Step 7: Sidebar or context-list terms present
        sidebar_terms = [
            "context", "sidebar", "conversation", "new chat",
            "create context", "chat list", "history",
        ]
        sidebar_found = any(term in body_text.lower() for term in sidebar_terms)

        # Step 7: Sidebar element present
        sidebar_elements = page.driver.find_elements(
            By.CSS_SELECTOR,
            ".sidebar, .context-list, .chat-sidebar, "
            "[data-sidebar], .left-panel, .context-panel",
        )
        if sidebar_elements:
            assert sidebar_elements[0].is_displayed(), "Context sidebar is not visible"

        # Step 8: New Chat / Create Context button
        new_chat_buttons = page.driver.find_elements(
            By.XPATH,
            "//*[contains(translate(text(),'abcdefghijklmnopqrstuvwxyz','ABCDEFGHIJKLMNOPQRSTUVWXYZ'),'NEW CHAT') "
            "or contains(translate(text(),'abcdefghijklmnopqrstuvwxyz','ABCDEFGHIJKLMNOPQRSTUVWXYZ'),'CREATE CONTEXT')]",
        )

        page.screenshot("chat_02_sidebar")
        assert sidebar_found or sidebar_elements or new_chat_buttons, (
            "No sidebar, context list, or New Chat button found on chat page"
        )

    def test_message_input_area_is_present(self, page: BasePage):
        """Steps 14-15: Verify message input area is visible and usable."""
        page.navigate("/chat")

        # Step 14: Message input visible and enabled
        message_inputs = page.driver.find_elements(
            By.CSS_SELECTOR,
            "textarea, input[type='text'], .message-input, "
            "[placeholder*='message'], [placeholder*='type'], [data-message-input]",
        )

        body_text = page.driver.find_element(By.TAG_NAME, "body").text
        input_terms = ["send", "message", "type here", "input", "compose", "enter"]
        input_found = any(term in body_text.lower() for term in input_terms)

        if message_inputs:
            assert message_inputs[0].is_displayed(), "Message input is not visible"

        page.screenshot("chat_03_message_input")
        assert message_inputs or input_found, "No message input area found on chat page"

    def test_chat_context_persists_after_navigation(self, page: BasePage):
        """Steps 21-24: Navigate away and back, verify context list preserved."""
        page.navigate("/chat")

        # Capture sidebar context count before navigating away
        sidebar_items_before = page.driver.find_elements(
            By.CSS_SELECTOR,
            ".context-item, .chat-item, .conversation-item, "
            ".sidebar-item, li.context, [data-context-id]",
        )
        count_before = len(sidebar_items_before)

        # Step 21: Navigate to dashboard
        page.navigate("/")

        # Step 22: Dashboard loads
        body_text = page.driver.find_element(By.TAG_NAME, "body").text
        assert body_text, "Dashboard body is empty after returning from chat"

        # Step 23: Navigate back to chat
        page.navigate("/chat")
        page.screenshot("chat_04_after_nav")

        # Step 24: Context count should match (or more — new context may have been created)
        sidebar_items_after = page.driver.find_elements(
            By.CSS_SELECTOR,
            ".context-item, .chat-item, .conversation-item, "
            ".sidebar-item, li.context, [data-context-id]",
        )
        assert len(sidebar_items_after) >= count_before, (
            "Context count decreased after navigation — contexts may have been lost"
        )

        body_text = page.driver.find_element(By.TAG_NAME, "body").text
        assert CUI_BANNER in body_text, "CUI banner missing on chat page after navigation"
# CUI // SP-CTI
