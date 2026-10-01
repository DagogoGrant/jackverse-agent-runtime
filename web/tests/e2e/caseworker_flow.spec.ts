import { test, expect } from "@playwright/test";

test.describe("Caseworker Web End-to-End User Flow", () => {
  test("Desktop navigation across all numbered editorial views", async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 900 });

    // 01 Home
    await page.goto("/");
    await expect(page.locator("label[for='home-prompt']")).toContainText("What do you want JackVerse to move forward?");

    // 02 Missions
    await page.locator("a[href='/missions']").click();
    await expect(page).toHaveURL(/.*\/missions/);
    await expect(page.locator("h1")).toContainText("Missions");

    // 03 Opportunities
    await page.locator("a[href='/opportunities']").click();
    await expect(page).toHaveURL(/.*\/opportunities/);
    await expect(page.locator("h1")).toContainText("Opportunity Inbox");

    // 04 Needs You
    await page.locator("a[href='/approvals']").click();
    await expect(page).toHaveURL(/.*\/approvals/);
    await expect(page.locator("h1")).toContainText("Needs You");

    // 05 My Context
    await page.locator("a[href='/context']").click();
    await expect(page).toHaveURL(/.*\/context/);
    await expect(page.locator("h1")).toContainText("My Context");

    // 06 Activity
    await page.locator("a[href='/activity']").click();
    await expect(page).toHaveURL(/.*\/activity/);
    await expect(page.locator("h1")).toContainText("Activity Log");
  });

  test("Mobile responsive drawer navigation (375x812)", async ({ page }) => {
    await page.setViewportSize({ width: 375, height: 812 });
    await page.goto("/");

    // Fixed desktop sidebar should be hidden
    const desktopRail = page.locator("aside.hidden");
    await expect(desktopRail).toBeHidden();

    // Mobile menu toggle should be visible
    const menuToggle = page.locator("button", { hasText: "☰ 01-06" });
    await expect(menuToggle).toBeVisible();

    // Open mobile menu
    await menuToggle.click();
    await expect(page.locator("button", { hasText: "✕ CLOSE" })).toBeVisible();

    // Select 02 MISSIONS from open drawer
    const missionLink = page.locator(".fixed nav a", { hasText: "MISSIONS" });
    await missionLink.click();

    // URL should change and drawer close
    await expect(page).toHaveURL(/.*\/missions/);
    await expect(page.locator("button", { hasText: "☰ 01-06" })).toBeVisible();
  });

  test("Tactile DragToAuthorize interaction on Prototype C", async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 900 });
    await page.goto("/prototypes/c");

    // Header visible
    await expect(page.locator("text=Requires Your Authorization")).toBeVisible();

    // Expand parameters
    const inspectorButton = page.locator("button", { hasText: "INSPECT SUBMISSION PAYLOAD" });
    await expect(inspectorButton).toBeVisible();
    await expect(page.locator("text=Synthetic_CV.pdf")).toBeVisible();

    // Keyboard trigger on slider
    const slider = page.locator("div[role='slider']");
    await slider.focus();
    await page.keyboard.press("Space");

    // Success state
    await expect(page.locator("text=Authorization recorded")).toBeVisible();
  });
});
