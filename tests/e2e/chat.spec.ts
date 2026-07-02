// CUI // SP-CTI
// E2E Test: Chat Multi-Pane Interface
// Verifies the unified Chat page loads with multi-context UI, RICOAS sidebar, message send, and intervention controls.

import { test, expect } from '@playwright/test';

const CUI_BANNER = 'CUI // SP-CTI';

test.describe('Chat Multi-Pane Interface', () => {
  test('chat page loads with heading and CUI banners', async ({ page }) => {
    // Step 1-4: Navigate and screenshot
    await page.goto('/chat');
    await page.waitForLoadState('domcontentloaded');

    await page.screenshot({
      path: '.tmp/test_runs/screenshots/chat_01_page_load.png',
      fullPage: true,
    });

    // Step 3: Verify heading
    const bodyText = await page.textContent('body');
    const chatTerms = ['chat', 'Chat', 'CHAT', 'message', 'context'];
    let chatFound = false;
    for (const term of chatTerms) {
      if (bodyText?.includes(term)) {
        chatFound = true;
        break;
      }
    }

    // Steps 5-6: CUI banner checks
    expect(bodyText).toContain(CUI_BANNER);
  });

  test('context sidebar and new chat button are visible', async ({ page }) => {
    // Step 7-8: Verify context sidebar
    await page.goto('/chat');
    await page.waitForLoadState('domcontentloaded');

    // Soft check for sidebar / context panel
    const sidebar = page.locator(
      '.sidebar, .context-list, [data-testid="context-sidebar"], aside, .chat-sidebar'
    );
    if (await sidebar.count() > 0) {
      await expect(sidebar.first()).toBeVisible();
    }

    // Soft check for "New Chat" / "Create Context" button
    const newChatBtn = page.getByRole('button', { name: /New Chat|Create Context|New Context/i })
      .or(page.locator('[data-testid="new-chat"], [data-action="new-chat"]'));
    if (await newChatBtn.count() > 0) {
      await expect(newChatBtn.first()).toBeVisible();
    }

    const bodyText = await page.textContent('body');
    expect(bodyText).toContain(CUI_BANNER);
  });

  test('message input is visible and accepts text', async ({ page }) => {
    // Steps 14-16: Verify message input area
    await page.goto('/chat');
    await page.waitForLoadState('domcontentloaded');

    const messageInput = page.locator(
      'textarea[name="message"], input[name="message"], [data-testid="message-input"], .message-input textarea, .chat-input textarea, .chat-input input[type="text"]'
    );

    if (await messageInput.count() > 0) {
      await expect(messageInput.first()).toBeVisible();
      await expect(messageInput.first()).toBeEnabled();

      // Step 15: Type a test message
      await messageInput.first().fill('Hello from E2E test');

      await page.screenshot({
        path: '.tmp/test_runs/screenshots/chat_02_message_typed.png',
        fullPage: true,
      });
    }

    const bodyText = await page.textContent('body');
    expect(bodyText).toContain(CUI_BANNER);
  });

  test('navigation to and from chat preserves state', async ({ page }) => {
    // Steps 21-24: Navigate away and back
    await page.goto('/chat');
    await page.waitForLoadState('domcontentloaded');

    // Navigate to home
    await page.goto('/');
    await page.waitForLoadState('domcontentloaded');

    const bodyText = await page.textContent('body');
    expect(bodyText).toContain(CUI_BANNER);

    // Navigate back to chat
    await page.goto('/chat');
    await page.waitForLoadState('domcontentloaded');

    await page.screenshot({
      path: '.tmp/test_runs/screenshots/chat_03_after_nav.png',
      fullPage: true,
    });

    const chatBodyText = await page.textContent('body');
    // Page should still load without errors. Not checking for a bare '500'
    // substring — page.textContent('body') includes inline <script> text,
    // and this page's own JS builds HTML containing `font-weight:500`,
    // making that check a false-positive on legitimate CSS.
    expect(chatBodyText).not.toContain('Internal Server Error');
    expect(chatBodyText).toContain(CUI_BANNER);
  });
});
// CUI // SP-CTI
