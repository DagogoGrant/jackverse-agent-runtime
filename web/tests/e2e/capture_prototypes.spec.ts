import { test } from '@playwright/test';
import path from 'path';

const outDir = path.resolve(process.cwd(), '../docs/prototypes');

test.describe('Phase 4 Visual Prototype Screenshots', () => {
  // Prototype A: Dashboard
  test('Capture Prototype A - Dashboard (Desktop 1440 & Mobile 375)', async ({ page }) => {
    // Desktop 1440x900
    await page.setViewportSize({ width: 1440, height: 900 });
    await page.goto('/prototypes/a');
    await page.waitForSelector('text=What do you want JackVerse to move forward?');
    await page.screenshot({ path: path.join(outDir, 'prototype_a_desktop.png'), fullPage: true });

    // Mobile 375x812
    await page.setViewportSize({ width: 375, height: 812 });
    await page.goto('/prototypes/a');
    await page.waitForSelector('text=What do you want JackVerse to move forward?');
    await page.screenshot({ path: path.join(outDir, 'prototype_a_mobile.png'), fullPage: true });
  });

  // Prototype B: Mission Workspace
  test('Capture Prototype B - Mission Workspace (Desktop 1440 & Mobile 375)', async ({ page }) => {
    // Desktop 1440x900
    await page.setViewportSize({ width: 1440, height: 900 });
    await page.goto('/prototypes/b');
    await page.waitForSelector('text=Active Cases Dossier');
    await page.screenshot({ path: path.join(outDir, 'prototype_b_desktop.png'), fullPage: true });

    // Mobile 375x812
    await page.setViewportSize({ width: 375, height: 812 });
    await page.goto('/prototypes/b');
    await page.waitForSelector('text=Active Cases Dossier');
    await page.screenshot({ path: path.join(outDir, 'prototype_b_mobile.png'), fullPage: true });
  });

  // Prototype C: Needs You
  test('Capture Prototype C - Needs You Approval (Desktop 1440 & Mobile 375)', async ({ page }) => {
    // Desktop 1440x900
    await page.setViewportSize({ width: 1440, height: 900 });
    await page.goto('/prototypes/c');
    await page.waitForSelector('text=Requires Your Authorization');
    await page.screenshot({ path: path.join(outDir, 'prototype_c_desktop.png'), fullPage: true });

    // Mobile 375x812
    await page.setViewportSize({ width: 375, height: 812 });
    await page.goto('/prototypes/c');
    await page.waitForSelector('text=Requires Your Authorization');
    await page.screenshot({ path: path.join(outDir, 'prototype_c_mobile.png'), fullPage: true });
  });
});
