import { test, expect, Page } from '@playwright/test';
import path from 'path';

const outDir = path.resolve(process.cwd(), '../docs/prototypes/v2');

async function setThemeAndNavigate(page: Page, url: string, theme: 'paper' | 'ink', width: number, height: number) {
  await page.setViewportSize({ width, height });
  await page.goto(url);
  await page.evaluate((t) => {
    localStorage.setItem('jv_theme', t);
    document.documentElement.dataset.theme = t;
  }, theme);
  // Wait briefly for theme wash/rendering to settle
  await page.waitForTimeout(300);
}

test.describe('JackVerse Caseworker Design V2 - Four-Surface Prototype Gate (16 Screenshots)', () => {
  let missionId = '';

  test.beforeAll(async ({ request }) => {
    // Seed real synthetic data into FastAPI instance for Alice
    const missionRes = await request.post('http://127.0.0.1:8089/api/v1/missions', {
      headers: { 'X-JackVerse-User': 'alice', 'Content-Type': 'application/json' },
      data: {
        title: 'Autonomous Verification Infrastructure',
        goal: 'Design, verify, and deploy a verifiable editorial supervisor for agent runtimes',
        kind: 'opportunity_pursuit',
      },
    });
    const mission = await missionRes.json();
    missionId = mission.mission_id;

    // Transition Mission to ACTIVE
    const missionEtag = missionRes.headers()['etag'];
    if (missionEtag) {
      await request.post(`http://127.0.0.1:8089/api/v1/missions/${missionId}/transition`, {
        headers: { 'X-JackVerse-User': 'alice', 'If-Match': missionEtag, 'Content-Type': 'application/json' },
        data: { new_status: 'active' },
      });
    }

    // Create 2 concrete Cases
    const case1Res = await request.post(`http://127.0.0.1:8089/api/v1/missions/${missionId}/cases`, {
      headers: { 'X-JackVerse-User': 'alice', 'Content-Type': 'application/json' },
      data: {
        title: 'Principal Infrastructure Engineer Dossier',
        goal: 'Coordinate credential presentation and deterministic audit review',
        case_type: 'job_application',
      },
    });
    const case1 = await case1Res.json();

    await request.post(`http://127.0.0.1:8089/api/v1/missions/${missionId}/cases`, {
      headers: { 'X-JackVerse-User': 'alice', 'Content-Type': 'application/json' },
      data: {
        title: 'Research Fellowship Grant Proposal',
        goal: 'Secure exploratory research funding for formal verification of LLM sandboxes',
        case_type: 'grant_submission',
      },
    });

    // Propose high-consequential action on Case 1 and request approval
    const actionRes = await request.post(`http://127.0.0.1:8089/api/v1/cases/${case1.case_id}/actions`, {
      headers: { 'X-JackVerse-User': 'alice', 'Content-Type': 'application/json' },
      data: {
        action_type: 'submit_application',
        description: 'Authorize Transmission of Verified Technical Dossier',
        parameters: {
          recipient: 'Fraunhofer AI Research Governance',
          credentials_pack: 'dossier-sha256:e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855',
          channel: 'cryptographic_carrier_protocol',
        },
      },
    });
    const action = await actionRes.json();
    const actionEtag = actionRes.headers()['etag'];

    if (actionEtag) {
      await request.post(`http://127.0.0.1:8089/api/v1/actions/${action.action_id}/request-approval`, {
        headers: { 'X-JackVerse-User': 'alice', 'If-Match': actionEtag, 'Content-Type': 'application/json' },
      });
    }

    // Seed Context facts: Public, Personal, Sensitive
    const fact1Res = await request.post('http://127.0.0.1:8089/api/v1/context/facts', {
      headers: { 'X-JackVerse-User': 'alice', 'Content-Type': 'application/json' },
      data: {
        namespace: 'skills',
        key: 'primary_domain',
        value: 'Formal Verification & Distributed Systems',
        sensitivity: 'public',
        allowed_purposes: ['job_application'],
      },
    });
    const fact1 = await fact1Res.json();
    const f1Etag = fact1Res.headers()['etag'];
    if (f1Etag) {
      await request.post(`http://127.0.0.1:8089/api/v1/context/facts/${fact1.fact_id}/verify`, {
        headers: { 'X-JackVerse-User': 'alice', 'If-Match': f1Etag, 'Content-Type': 'application/json' },
        data: {},
      });
    }

    await request.post('http://127.0.0.1:8089/api/v1/context/facts', {
      headers: { 'X-JackVerse-User': 'alice', 'Content-Type': 'application/json' },
      data: {
        namespace: 'identity',
        key: 'legal_name',
        value: 'Elena Rostova',
        sensitivity: 'personal',
        allowed_purposes: ['job_application'],
      },
    });

    await request.post('http://127.0.0.1:8089/api/v1/context/facts', {
      headers: { 'X-JackVerse-User': 'alice', 'Content-Type': 'application/json' },
      data: {
        namespace: 'finance',
        key: 'tax_identifier',
        value: 'DE-893041928-V',
        sensitivity: 'sensitive',
        allowed_purposes: ['job_application'],
      },
    });
  });

  test.beforeEach(async ({ page }) => {
    await page.goto('/');
    await page.evaluate(() => {
      localStorage.setItem('jv_dev_user', 'alice');
    });
  });

  // Surface A: Home / Mission Index (4 screenshots)
  test('Capture Surface A - Home / Mission Index (1440 & 375, Paper & Ink)', async ({ page }) => {
    // 1440x900 Paper
    await setThemeAndNavigate(page, '/', 'paper', 1440, 900);
    await expect(page.locator('#home-prompt')).toBeVisible();
    await expect(page.locator('text=Autonomous Verification Infrastructure').first()).toBeVisible();
    await page.screenshot({ path: path.join(outDir, 'home_paper_1440.png'), fullPage: false });

    // 1440x900 Ink
    await setThemeAndNavigate(page, '/', 'ink', 1440, 900);
    await expect(page.locator('#home-prompt')).toBeVisible();
    await page.screenshot({ path: path.join(outDir, 'home_ink_1440.png'), fullPage: false });

    // 375x812 Paper
    await setThemeAndNavigate(page, '/', 'paper', 375, 812);
    await expect(page.locator('#home-prompt')).toBeVisible();
    await page.screenshot({ path: path.join(outDir, 'home_paper_375.png'), fullPage: false });

    // 375x812 Ink
    await setThemeAndNavigate(page, '/', 'ink', 375, 812);
    await expect(page.locator('#home-prompt')).toBeVisible();
    await page.screenshot({ path: path.join(outDir, 'home_ink_375.png'), fullPage: false });
  });

  // Surface B: Mission Dossier (4 screenshots)
  test('Capture Surface B - Mission Dossier (1440 & 375, Paper & Ink)', async ({ page }) => {
    const url = `/missions/${missionId}`;

    // 1440x900 Paper
    await setThemeAndNavigate(page, url, 'paper', 1440, 900);
    await expect(page.locator('text=Active Cases Dossier')).toBeVisible();
    await expect(page.locator('text=Principal Infrastructure Engineer Dossier')).toBeVisible();
    await page.screenshot({ path: path.join(outDir, 'mission_paper_1440.png'), fullPage: false });

    // 1440x900 Ink
    await setThemeAndNavigate(page, url, 'ink', 1440, 900);
    await expect(page.locator('text=Active Cases Dossier')).toBeVisible();
    await page.screenshot({ path: path.join(outDir, 'mission_ink_1440.png'), fullPage: false });

    // 375x812 Paper
    await setThemeAndNavigate(page, url, 'paper', 375, 812);
    await expect(page.locator('text=Active Cases Dossier')).toBeVisible();
    await page.screenshot({ path: path.join(outDir, 'mission_paper_375.png'), fullPage: false });

    // 375x812 Ink
    await setThemeAndNavigate(page, url, 'ink', 375, 812);
    await expect(page.locator('text=Active Cases Dossier')).toBeVisible();
    await page.screenshot({ path: path.join(outDir, 'mission_ink_375.png'), fullPage: false });
  });

  // Surface C: Needs You (4 screenshots)
  test('Capture Surface C - Needs You Approval (1440 & 375, Paper & Ink)', async ({ page }) => {
    const url = '/approvals';

    // 1440x900 Paper
    await setThemeAndNavigate(page, url, 'paper', 1440, 900);
    await expect(page.locator('h1')).toContainText('Needs You');
    await expect(page.locator('text=Authorize Transmission of Verified Technical Dossier')).toBeVisible();
    await expect(page.locator('div[role="slider"]')).toBeVisible();
    await page.screenshot({ path: path.join(outDir, 'needs_you_paper_1440.png'), fullPage: false });

    // 1440x900 Ink
    await setThemeAndNavigate(page, url, 'ink', 1440, 900);
    await expect(page.locator('h1')).toContainText('Needs You');
    await expect(page.locator('text=Authorize Transmission of Verified Technical Dossier')).toBeVisible();
    await page.screenshot({ path: path.join(outDir, 'needs_you_ink_1440.png'), fullPage: false });

    // 375x812 Paper
    await setThemeAndNavigate(page, url, 'paper', 375, 812);
    await expect(page.locator('h1')).toContainText('Needs You');
    await page.screenshot({ path: path.join(outDir, 'needs_you_paper_375.png'), fullPage: false });

    // 375x812 Ink
    await setThemeAndNavigate(page, url, 'ink', 375, 812);
    await expect(page.locator('h1')).toContainText('Needs You');
    await page.screenshot({ path: path.join(outDir, 'needs_you_ink_375.png'), fullPage: false });
  });

  // Surface D: My Context (4 screenshots)
  test('Capture Surface D - My Context (1440 & 375, Paper & Ink)', async ({ page }) => {
    const url = '/context';

    // 1440x900 Paper
    await setThemeAndNavigate(page, url, 'paper', 1440, 900);
    await expect(page.locator('h1')).toContainText('My Context');
    await expect(page.locator('text=skills.primary_domain')).toBeVisible();
    await page.screenshot({ path: path.join(outDir, 'context_paper_1440.png'), fullPage: false });

    // 1440x900 Ink
    await setThemeAndNavigate(page, url, 'ink', 1440, 900);
    await expect(page.locator('h1')).toContainText('My Context');
    await page.screenshot({ path: path.join(outDir, 'context_ink_1440.png'), fullPage: false });

    // 375x812 Paper
    await setThemeAndNavigate(page, url, 'paper', 375, 812);
    await expect(page.locator('h1')).toContainText('My Context');
    await page.screenshot({ path: path.join(outDir, 'context_paper_375.png'), fullPage: false });

    // 375x812 Ink
    await setThemeAndNavigate(page, url, 'ink', 375, 812);
    await expect(page.locator('h1')).toContainText('My Context');
    await page.screenshot({ path: path.join(outDir, 'context_ink_375.png'), fullPage: false });
  });
});
