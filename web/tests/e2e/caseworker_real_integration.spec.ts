import { test, expect, Page } from '@playwright/test';

async function createMission(page: Page, promptText: string, kind?: string) {
  await page.goto('/');
  const promptInput = page.locator('#home-prompt');
  await promptInput.fill(promptText);

  if (kind) {
    const kindSelect = page.locator('select').first();
    await kindSelect.selectOption(kind);
  }

  const submitBtn = page.locator('button[type="submit"]:has-text("Initialize Mission")');
  if (await submitBtn.isVisible()) {
    await submitBtn.click();
  } else {
    await promptInput.press('Enter');
  }

  await expect(page).toHaveURL(/\/missions\/[a-zA-Z0-9_-]+/);
}

test.describe('Caseworker Real Integration Suite (Live FastAPI + Temporary SQLite)', () => {
  test.beforeEach(async ({ page }) => {
    // Navigate to / and ensure default user is alice
    await page.goto('/');
    await page.evaluate(() => {
      localStorage.setItem('jv_dev_user', 'alice');
    });
  });

  // FLOW A: Identity Isolation
  test('Flow A: Identity Isolation between Alice and Bob', async ({ page }) => {
    // 1. Alice creates a confidential mission
    await createMission(page, 'Alice Confidential Strategy');

    const aliceUrl = page.url();
    const aliceMissionId = aliceUrl.split('/missions/')[1];

    // Verify Alice sees the mission in Missions index by title
    await page.locator('a[href="/missions"]').click();
    await expect(page.locator('h2', { hasText: 'Alice Confidential Strategy' })).toBeVisible();

    // 2. Switch identity to Bob via header dev switcher
    const bobSwitcher = page.locator('button', { hasText: 'bob' });
    await expect(bobSwitcher).toBeVisible();
    await bobSwitcher.click();

    // Bob visits /missions -> Alice's mission MUST be absent
    await page.goto('/missions');
    await expect(page.locator('h2', { hasText: 'Alice Confidential Strategy' })).toHaveCount(0);

    // Bob directly navigates to Alice's mission -> 404 Not Found error state
    await page.goto(`/missions/${aliceMissionId}`);
    await expect(page.locator('text=HTTP 404 // NOT FOUND')).toBeVisible();
    await expect(page.locator('text=Mission Dossier Not Found')).toBeVisible();

    // Switch back to Alice
    const aliceSwitcher = page.locator('button', { hasText: 'alice' });
    await aliceSwitcher.click();
  });

  // FLOW B: Mission Flow (All 3 valid MissionKinds & ETag transitions)
  test('Flow B: Mission Flow for all 3 valid MissionKinds and lifecycle transitions', async ({ page }) => {
    const kinds = [
      { kind: 'opportunity_pursuit', label: 'Opportunity Pursuit' },
      { kind: 'problem_resolution', label: 'Problem Resolution' },
      { kind: 'general_goal', label: 'General Goal' },
    ] as const;

    for (const { kind } of kinds) {
      await createMission(page, `Testing kind ${kind}`, kind);

      // Verify authoritative classification kind is rendered
      await expect(page.locator(`text=KIND: ${kind.toUpperCase()}`)).toBeVisible();

      // Mission starts in DRAFT -> Activate
      const activateBtn = page.locator('button', { hasText: 'Activate Mission →' });
      await expect(activateBtn).toBeVisible();
      await activateBtn.click();

      // Active state transitions via ETag
      // Pause
      const pauseBtn = page.locator('button', { hasText: 'Pause Mission' });
      await expect(pauseBtn).toBeVisible();
      await pauseBtn.click();

      // Resume
      const resumeBtn = page.locator('button', { hasText: 'Resume Mission →' });
      await expect(resumeBtn).toBeVisible();
      await resumeBtn.click();

      // Mark Completed
      const completeBtn = page.locator('button', { hasText: 'Mark Completed ■' });
      await expect(completeBtn).toBeVisible();
      await completeBtn.click();

      // Completed is terminal; verify complete state
      await expect(page.locator('button', { hasText: 'Pause Mission' })).toHaveCount(0);
      await expect(page.locator('button', { hasText: 'Activate Mission →' })).toHaveCount(0);
    }
  });

  // FLOW C: Case Flow (Create Case under Mission, Transition Statuses)
  test('Flow C: Case creation under Mission and state machine progression', async ({ page }) => {
    // 1. Create a parent mission
    await createMission(page, 'Parent Mission for Case Flow');

    // 2. Open inline Case creation form
    await page.locator('button', { hasText: '+ New Case' }).click();

    // Fill Case form
    await page.locator('form input[type="text"]').fill('Software Engineer Case');
    await page.locator('form textarea').fill('Execute intake and technical verification');
    await page.locator('form select').selectOption('job_application');

    await page.locator('form button[type="submit"]:has-text("Create Case")').click();

    // Verify Case card appears in list
    const caseCard = page.locator('text=Software Engineer Case');
    await expect(caseCard).toBeVisible();

    // 3. Navigate into Case dossier
    await caseCard.click();
    await expect(page).toHaveURL(/\/cases\/[a-zA-Z0-9_-]+/);

    // Verify initial status: new, version: v1
    await expect(page.locator('text=STATUS: NEW')).toBeVisible();
    await expect(page.locator('text=VERSION: v1')).toBeVisible();

    // Transition NEW -> INTAKE
    const intakeBtn = page.locator('button', { hasText: 'Initiate Intake →' });
    await expect(intakeBtn).toBeVisible();
    await intakeBtn.click();
    await expect(page.locator('text=STATUS: INTAKE')).toBeVisible();
    await expect(page.locator('text=VERSION: v2')).toBeVisible();

    // Transition INTAKE -> INVESTIGATING
    const investBtn = page.locator('button', { hasText: 'Begin Investigation →' });
    await expect(investBtn).toBeVisible();
    await investBtn.click();
    await expect(page.locator('text=STATUS: INVESTIGATING')).toBeVisible();
    await expect(page.locator('text=VERSION: v3')).toBeVisible();
  });

  // FLOW D: Opportunity Flow (Add Opportunity, Render Schema Fields, State Transitions)
  test('Flow D: Opportunity schema compliance, manual ingress, and state transitions', async ({ page }) => {
    await page.goto('/opportunities');

    // 1. Open manual creation modal
    await page.locator('button', { hasText: '+ Add Opportunity' }).click();

    // Fill form with synthetic data
    await page.locator('input[placeholder="e.g. Senior Machine Learning Engineer"]').fill('Senior Systems Engineer');
    await page.locator('input[placeholder="e.g. Acme Research Labs"]').fill('HyperScale Infrastructure');
    await page.locator('input[placeholder="e.g. Remote / Berlin, Germany"]').fill('Remote (UK / London)');
    await page.locator('input[placeholder="e.g. LinkedIn, Direct Board"]').fill('Curated Tech Feed');
    await page.locator('input[placeholder="https://..."]').fill('https://careers.example.test/jobs/8821');
    await page.locator('form textarea').fill('Python 3.11\nDistributed Systems\nFastAPI');

    await page.locator('button[type="submit"]:has-text("Register Opportunity")').click();

    // 2. Redirected to Opportunity Detail
    await expect(page).toHaveURL(/\/opportunities\/[a-zA-Z0-9_-]+/);

    // Verify genuine schema fields rendered
    await expect(page.locator('h1')).toContainText('Senior Systems Engineer');
    await expect(page.locator('text=HyperScale Infrastructure').first()).toBeVisible();
    await expect(page.locator('text=Remote (UK / London)').first()).toBeVisible();
    await expect(page.locator('text=Curated Tech Feed')).toBeVisible();
    await expect(page.locator('text=STATUS: DISCOVERED')).toBeVisible();
    await expect(page.locator('text=Distributed Systems')).toBeVisible();

    // 3. Autoritative domain transitions: DISCOVERED -> EVALUATING -> SHORTLISTED
    const evalBtn = page.locator('button', { hasText: 'Mark Evaluating →' });
    await expect(evalBtn).toBeVisible();
    await evalBtn.click();
    await expect(page.locator('text=STATUS: EVALUATING')).toBeVisible();

    const shortlistBtn = page.locator('button', { hasText: 'Shortlist Opportunity ★' });
    await expect(shortlistBtn).toBeVisible();
    await shortlistBtn.click();
    await expect(page.locator('text=STATUS: SHORTLISTED')).toBeVisible();
  });

  // FLOW E: Context Flow (Sensitive Masking, Reveal, Mask, and Lifecycle without value reveal)
  test('Flow E: Context Fact sensitivity masking, on-demand reveal, and non-revealing lifecycle', async ({ page }) => {
    await page.goto('/context');

    // 1. Record sensitive fact
    await page.locator('button', { hasText: '+ Add Fact' }).click();
    await page.locator('form select').first().selectOption('identity');
    await page.locator('input[placeholder*="legal_name"]').fill('national_identity_number');
    await page.locator('form select').nth(1).selectOption('sensitive');
    await page.locator('input[placeholder*="John Doe"]').fill('ID-SECRET-99412');

    await page.locator('button[type="submit"]:has-text("Record Fact →")').click();

    // Locate fact row
    const factRow = page.locator('div', { hasText: 'national_identity_number' }).first();
    await expect(factRow).toBeVisible();

    // Sensitive value should be masked initially
    await expect(factRow.locator('text=PRIVATE ••••••••••••')).toBeVisible();
    await expect(page.locator('text=ID-SECRET-99412')).toHaveCount(0);

    // Reveal value
    const revealBtn = factRow.locator('button', { hasText: 'Reveal' });
    await revealBtn.click();
    await expect(page.locator('text=ID-SECRET-99412')).toBeVisible();

    // Mask value
    const maskBtn = factRow.locator('button', { hasText: 'Mask' });
    await maskBtn.click();
    await expect(factRow.locator('text=PRIVATE ••••••••••••')).toBeVisible();
    await expect(page.locator('text=ID-SECRET-99412')).toHaveCount(0);

    // Verify fact without revealing raw value
    const verifyBtn = factRow.locator('button[title="Verify fact assertion"]');
    await expect(verifyBtn).toBeVisible();
    await verifyBtn.click();

    // Status should become VERIFIED
    await expect(factRow.locator('text=VERIFIED')).toBeVisible();
    // Raw value must still be unrevealed
    await expect(factRow.locator('text=PRIVATE ••••••••••••')).toBeVisible();
  });

  // FLOW F: Claim Flow (Verified Fact -> Propose Claim -> Evaluate -> Supported)
  test('Flow F: Claim evaluation adhering to deterministic claim verification policy', async ({ page, request }) => {
    // 1. Record a PUBLIC fact via API directly for Alice to get its fact_id
    const factRes = await request.post('http://127.0.0.1:8089/api/v1/context/facts', {
      headers: {
        'X-JackVerse-User': 'alice',
        'Content-Type': 'application/json',
      },
      data: {
        namespace: 'skills',
        key: 'python_years',
        value: '7_years',
        sensitivity: 'public',
        allowed_purposes: ['job_application'],
      },
    });
    expect(factRes.status()).toBe(201);
    const factData = await factRes.json();
    const factId = factData.fact_id;
    const factEtag = factRes.headers()['etag'];

    // Verify the fact to satisfy minimum verification policy (USER_VERIFIED)
    const verifyRes = await request.post(`http://127.0.0.1:8089/api/v1/context/facts/${factId}/verify`, {
      headers: {
        'X-JackVerse-User': 'alice',
        'If-Match': factEtag,
        'Content-Type': 'application/json',
      },
      data: {},
    });
    expect(verifyRes.status()).toBe(200);

    // 2. Create parent mission and case via UI
    await createMission(page, 'Mission for Claim Support');

    await page.locator('button', { hasText: '+ New Case' }).click();
    await page.locator('form input[type="text"]').fill('Claim Evaluation Case');
    await page.locator('form textarea').fill('Test deterministic claim validation');
    await page.locator('form button[type="submit"]:has-text("Create Case")').click();

    await page.locator('text=Claim Evaluation Case').click();
    await expect(page).toHaveURL(/\/cases\/[a-zA-Z0-9_-]+/);

    // 3. Propose claim bound to verified fact
    await page.locator('button', { hasText: '+ Propose Claim' }).click();
    await page.locator('form select').first().selectOption('job_application');
    await page.locator('input[placeholder*="fact_abc123"]').fill(factId);
    await page.locator('textarea[placeholder*="State claim assertion"]').fill('7+ years of Python engineering expertise');
    await page.locator('button[type="submit"]:has-text("Propose Claim")').click();

    // 4. Assert claim is listed and status evaluates to supported
    const claimEntry = page.locator('text=7+ years of Python engineering expertise').first();
    await expect(claimEntry).toBeVisible();

    // If evaluate claim button appears (or automatically evaluated), ensure status is supported
    const evalClaimBtn = page.locator('button', { hasText: 'Evaluate Claim →' });
    if (await evalClaimBtn.isVisible()) {
      await evalClaimBtn.click();
    }

    await expect(page.locator('text=SUPPORTED')).toBeVisible();
  });

  // FLOW G: Approval Flow (Action Proposed -> Request Approval -> Needs You -> DragToAuthorize -> Approved)
  test('Flow G: Consequential Action governance separation and slide authorization', async ({ page, request }) => {
    // 1. Create parent Mission and Case
    const missionRes = await request.post('http://127.0.0.1:8089/api/v1/missions', {
      headers: { 'X-JackVerse-User': 'alice', 'Content-Type': 'application/json' },
      data: { title: 'Governance Test Mission', goal: 'Test approval boundaries', kind: 'opportunity_pursuit' },
    });
    const mission = await missionRes.json();

    const caseRes = await request.post(`http://127.0.0.1:8089/api/v1/missions/${mission.mission_id}/cases`, {
      headers: { 'X-JackVerse-User': 'alice', 'Content-Type': 'application/json' },
      data: { title: 'High Risk Action Case', goal: 'Execute consequential submission', case_type: 'job_application' },
    });
    const caseItem = await caseRes.json();

    // 2. Propose a HIGH risk action (governance separation: does NOT create approval yet)
    const actionRes = await request.post(`http://127.0.0.1:8089/api/v1/cases/${caseItem.case_id}/actions`, {
      headers: { 'X-JackVerse-User': 'alice', 'Content-Type': 'application/json' },
      data: {
        action_type: 'submit_form',
        description: 'Submit candidate credentials to external portal',
        parameters: { portal: 'greenhouse', candidate_email: 'alex@example.test' },
      },
    });
    expect(actionRes.status()).toBe(201);
    const action = await actionRes.json();
    const actionEtag = actionRes.headers()['etag'];
    expect(action.status).toBe('proposed');
    expect(action.risk_level).toBe('high');

    // 3. Request approval under Action ETag (transitions action to awaiting_approval and creates pending Approval)
    const reqApprovalRes = await request.post(`http://127.0.0.1:8089/api/v1/actions/${action.action_id}/request-approval`, {
      headers: { 'X-JackVerse-User': 'alice', 'If-Match': actionEtag },
    });
    expect(reqApprovalRes.status()).toBe(200);

    // 4. Open Needs You view in browser
    await page.goto('/approvals');
    await expect(page.locator('h1')).toContainText('Needs You');

    // Verify approval appears in queue and click it
    const approvalCard = page.locator(`text=${caseItem.case_id}`).first();
    await expect(approvalCard).toBeVisible();
    await approvalCard.click();

    // Verify action description and risk level
    await expect(page.locator('text=Submit candidate credentials to external portal')).toBeVisible();
    await expect(page.locator('text=RISK: HIGH')).toBeVisible();

    // Verify parameters JSON
    await expect(page.locator('text=greenhouse')).toBeVisible();
    await expect(page.locator('text=alex@example.test')).toBeVisible();

    // 5. Authorize using DragToAuthorize slider
    const slider = page.locator('div[role="slider"]');
    await expect(slider).toBeVisible();
    await slider.focus();
    await page.keyboard.press('Space');

    // Truthful execution messaging
    await expect(
      page.locator('text=Authorization recorded. Execution is not connected in this phase.')
    ).toBeVisible();

    // Verify approval leaves pending queue and appears in All Decisions
    await expect(page.locator('button', { hasText: 'Pending (0)' })).toBeVisible();
    await page.locator('button', { hasText: 'All Decisions' }).click();
    await expect(page.locator('text=DECISION RECORDED // STATUS: APPROVED')).toBeVisible();
  });

  // FLOW H: Stale ETag Concurrency (412 Precondition Failed Handling)
  test('Flow H: Stale ETag concurrency notice and refresh handling without silent retry', async ({ page, request }) => {
    // 1. Create a mission and activate it
    await createMission(page, 'Concurrency ETag Test Mission');

    const missionUrl = page.url();
    const missionId = missionUrl.split('/missions/')[1];

    // Activate mission DRAFT -> ACTIVE
    const activateBtn = page.locator('button', { hasText: 'Activate Mission →' });
    await expect(activateBtn).toBeVisible();
    await activateBtn.click();

    // Wait until Pause Mission button is visible (state is ACTIVE)
    const pauseBtn = page.locator('button', { hasText: 'Pause Mission' });
    await expect(pauseBtn).toBeVisible();

    // Get current authoritative ETag from backend (now at ACTIVE version)
    const getRes = await request.get(`http://127.0.0.1:8089/api/v1/missions/${missionId}`, {
      headers: { 'X-JackVerse-User': 'alice' },
    });
    const currentEtag = getRes.headers()['etag'];

    // 2. Out-of-band mutation: Transition the mission to 'paused' directly via API
    // This bumps the version on the backend while the browser still holds the previous ETag
    const mutateRes = await request.post(`http://127.0.0.1:8089/api/v1/missions/${missionId}/transition`, {
      headers: {
        'X-JackVerse-User': 'alice',
        'If-Match': currentEtag,
        'Content-Type': 'application/json',
      },
      data: { new_status: 'paused' },
    });
    expect(mutateRes.status()).toBe(200);

    // 3. User in browser attempts mutation using their stale ETag
    // The page was loaded when status was active. User clicks "Pause Mission".
    await pauseBtn.click();

    // 4. UI receives 412 and displays honest concurrency notice
    await expect(
      page.locator("text=This changed elsewhere. We've loaded the latest version.")
    ).toBeVisible();

    // Page refetches and updates to reflect new state 'Resume Mission →' (which corresponds to PAUSED)
    await expect(page.locator('button', { hasText: 'Resume Mission →' })).toBeVisible();
  });
});
