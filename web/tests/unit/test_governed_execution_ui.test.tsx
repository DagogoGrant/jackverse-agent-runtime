import { describe, it, expect, vi, beforeEach } from "vitest";
import React from "react";
import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { CaseDetailView } from "../../src/views/CaseDetailView";

describe("Phase 5.3.3 Governed External Execution UI", () => {
  let queryClient: QueryClient;

  beforeEach(() => {
    queryClient = new QueryClient({
      defaultOptions: {
        queries: { retry: false },
      },
    });
    vi.restoreAllMocks();
  });

  const mockCase = {
    case_id: "case-exec-001",
    mission_id: "mission-001",
    user_id: "grant",
    case_type: "job_application",
    title: "Senior AI Engineer Application",
    goal: "Apply for Senior AI Engineer role",
    status: "action_required",
    success_criteria: "Application submitted and receipt verified",
    constraints: "Exact fields only",
    created_at: "2026-10-18T12:00:00Z",
    updated_at: "2026-10-18T12:00:00Z",
    version: 5,
    etag: '"case:case-exec-001:5"',
  };

  const mockArtifacts = [
    {
      artifact_id: "art-1",
      artifact_set_id: "set-001",
      case_id: "case-exec-001",
      opportunity_id: "opp-001",
      user_id: "grant",
      material_type: "tailored_resume",
      schema_version: "prep.artifact.v1",
      planner_version: "prep.planner.v1",
      status: "frozen",
      title: "Tailored Resume",
      blocks: [],
      source_report_id: "rep-001",
      input_hash: "inhash",
      grounding_snapshot_hash: "ghash",
      created_at: "2026-10-18T12:00:00Z",
      updated_at: "2026-10-18T12:00:00Z",
      version: 1,
      rendered_markdown: "# Grant Resume",
    },
  ];

  const mockPlan = {
    plan_id: "plan-exec-101",
    case_id: "case-exec-001",
    package_id: "pkg-101",
    user_id: "grant",
    status: "ready_for_authorization",
    destination: {
      destination_type: "ats_form",
      destination_provenance: "opportunity_post",
      canonical_url: "https://jobs.example.com/apply/ai-engineer",
      organization: "Apex Systems",
      channel_metadata: { browser_engine: "chromium" },
      is_exact_destination: true,
      normalized_origin: "https://jobs.example.com",
      normalized_hostname: "jobs.example.com",
    },
    target_schema: {
      schema_id: "schema_apex",
      destination_type: "ats_form",
      target_schema_provenance: "declared_exact",
      fields: [
        {
          target_field_id: "full_name",
          label: "Full Name",
          field_type: "text",
          required: true,
          allowed_options: [],
        },
      ],
      upload_slots: [],
      screening_questions: [],
      final_submit_control: {
        selector: 'button[type="submit"]',
        semantic_target: "Submit Application",
        control_provenance: "operator_confirmed",
        confidence: 1.0,
      },
      schema_hash: "schema_hash_999",
    },
    field_mappings: [
      {
        target_field_id: "full_name",
        field_name: "Full Name",
        field_value: "Grant Jack",
        source_fact_id: "fact_name_001",
        source_fact_version: 1,
        verification_status: "verified",
        required: true,
        allowed_purposes: ["job_application"],
      },
    ],
    upload_mappings: [],
    screening_responses: [],
    final_submit_control: {
      selector: 'button[type="submit"]',
      semantic_target: "Submit Application",
      control_provenance: "operator_confirmed",
      confidence: 1.0,
    },
    is_stale: false,
    staleness_reasons: [],
    execution_plan_fingerprint: "plan_fp_apex_1234567890",
    package_fingerprint: "pkg_fp_apex_abcdef1234",
    target_schema_hash: "schema_hash_999",
    execution_input_hash: "input_hash_999",
    compiled_at: "2026-10-18T12:05:00Z",
    version: 1,
  };

  const mockApprovedAction = {
    action_id: "act-exec-001",
    case_id: "case-exec-001",
    action_type: "submit_form",
    description: "Authorize external transmission to jobs.example.com (ats_form) for case case-exec-001",
    status: "approved",
    risk_level: "high",
    requires_approval: true,
    parameters: {
      scope: "external_transmission",
      authorization_schema: "tx_auth_v1",
      plan_id: "plan-exec-101",
      execution_plan_fingerprint: "plan_fp_apex_1234567890",
      package_id: "pkg-101",
      package_fingerprint: "pkg_fp_apex_abcdef1234",
      destination_url: "https://jobs.example.com/apply/ai-engineer",
      destination_type: "ats_form",
      destination_hostname: "jobs.example.com",
      field_mapping_count: 1,
      upload_asset_count: 0,
      screening_response_count: 0,
    },
    result: {},
    created_at: "2026-10-18T12:10:00Z",
    executed_at: null,
    idempotency_key: "tx_act:key999",
    version: 2,
    etag: '"action:act-exec-001:v2"',
  };

  const mockApproval = {
    approval_id: "app-exec-001",
    action_id: "act-exec-001",
    case_id: "case-exec-001",
    user_id: "grant",
    status: "approved",
    action_fingerprint: "action_fp_approved_112233",
    created_at: "2026-10-18T12:10:00Z",
    decided_at: "2026-10-18T12:15:00Z",
    decision_reason: "Approved by human operator",
    version: 2,
  };

  const mockReceipt = {
    receipt_id: "rcpt-apex-001",
    attempt_id: "att-apex-001",
    action_id: "act-exec-001",
    approval_id: "app-exec-001",
    plan_id: "plan-exec-101",
    case_id: "case-exec-001",
    user_id: "grant",
    status: "verified_success",
    destination_hostname: "jobs.example.com",
    destination_url: "https://jobs.example.com/apply/ai-engineer",
    destination_type: "ats_form",
    execution_plan_fingerprint: "plan_fp_apex_1234567890",
    authorized_action_fingerprint: "action_fp_approved_112233",
    package_fingerprint: "pkg_fp_apex_abcdef1234",
    executed_at: "2026-10-18T12:20:00Z",
    external_reference_id: "TX-APEX-998877",
    field_count: 1,
    upload_asset_ids: [],
    upload_asset_shas: [],
    screening_response_count: 0,
    safe_provider_metadata: {
      http_status: 200,
      duration_ms: 540,
      response_category: "success",
    },
    receipt_hash: "a1b2c3d4e5f67890123456789abcdef0123456789abcdef0123456789abcdef0",
    version: 1,
  };

  const mockAmbiguousReceipt = {
    ...mockReceipt,
    receipt_id: "rcpt-ambig-002",
    status: "ambiguous_submission",
    external_reference_id: null,
    receipt_hash: "f1e2d3c4b5a67890123456789abcdef0123456789abcdef0123456789abcdef0",
  };

  function setupFetchMock(options: {
    receipt?: any | null;
    action?: any;
    onExecute?: (body: any, headers: any) => void;
  }) {
    const currentAction = options.action ?? mockApprovedAction;
    const currentReceipt = options.receipt !== undefined ? options.receipt : null;

    vi.spyOn(globalThis, "fetch").mockImplementation((input: any, init?: any) => {
      const url = String(input);

      if (url.includes("/cases/case-exec-001/execution/plan/latest")) {
        return Promise.resolve(new Response(JSON.stringify(mockPlan), { status: 200, headers: { etag: '"plan:plan-exec-101:1"' } }));
      }
      if (url.includes("/cases/case-exec-001/preparation/packages/latest")) {
        return Promise.resolve(new Response(JSON.stringify({
          package_id: "pkg-101",
          status: "approved",
          artifact_ids: ["art-1"],
          package_fingerprint: "pkg_fp_apex_abcdef1234",
          verification_findings: [],
        }), { status: 200 }));
      }
      if (url.includes("/cases/case-exec-001/preparation/artifacts")) {
        return Promise.resolve(new Response(JSON.stringify({ artifacts: mockArtifacts, total: 1 }), { status: 200 }));
      }
      if (url.includes("/cases/case-exec-001/preparation/report/latest")) {
        return Promise.resolve(new Response(JSON.stringify({ report_id: "rep-001", is_stale: false, requirements: [] }), { status: 200 }));
      }
      if (url.includes("/cases/case-exec-001/claims")) {
        return Promise.resolve(new Response(JSON.stringify({ items: [], total: 0 }), { status: 200 }));
      }
      if (url.includes("/cases/case-exec-001/actions")) {
        return Promise.resolve(new Response(JSON.stringify([currentAction]), { status: 200 }));
      }
      if (url.includes("/cases/case-exec-001/approvals")) {
        return Promise.resolve(new Response(JSON.stringify([mockApproval]), { status: 200 }));
      }
      if (url.includes("/cases/case-exec-001/execution/receipt")) {
        if (!currentReceipt) {
          return Promise.resolve(new Response(JSON.stringify({ detail: "Not found" }), { status: 404 }));
        }
        return Promise.resolve(new Response(JSON.stringify(currentReceipt), { status: 200, headers: { etag: '"receipt:1"' } }));
      }
      if (url.includes("/cases/case-exec-001/execution/attempts")) {
        return Promise.resolve(new Response(JSON.stringify({ attempts: [] }), { status: 200 }));
      }
      if (url.includes("/execute")) {
        if (options.onExecute) {
          options.onExecute(init?.body ? JSON.parse(String(init.body)) : null, init?.headers);
        }
        return Promise.resolve(
          new Response(
            JSON.stringify({
              action: { ...currentAction, status: "succeeded" },
              attempt: {
                attempt_id: "att-exec-001",
                action_id: currentAction.action_id,
                approval_id: mockApproval.approval_id,
                plan_id: mockPlan.plan_id,
                case_id: mockCase.case_id,
                user_id: "grant",
                lease_token: "lease_123",
                fencing_token: 1,
                status: "succeeded",
                provider_kind: "simulated_test",
                started_at: "2026-10-18T12:19:00Z",
                completed_at: "2026-10-18T12:20:00Z",
                irreversible_action_taken: true,
                staged_field_count: 1,
                staged_upload_count: 0,
                attempt_number: 1,
              },
              receipt: mockReceipt,
              case_status: "waiting_external",
            }),
            { status: 200 }
          )
        );
      }
      if (url.includes("/cases/case-exec-001")) {
        return Promise.resolve(new Response(JSON.stringify(mockCase), { status: 200, headers: { etag: mockCase.etag } }));
      }
      if (url.includes("/events")) {
        return Promise.resolve(new Response(JSON.stringify({ events: [], total_count: 0 }), { status: 200 }));
      }
      if (url.includes("/context/facts")) {
        return Promise.resolve(new Response(JSON.stringify({ items: [], total: 0 }), { status: 200 }));
      }

      return Promise.resolve(new Response(JSON.stringify({}), { status: 200 }));
    });
  }

  function renderView() {
    return render(
      <QueryClientProvider client={queryClient}>
        <MemoryRouter initialEntries={["/cases/case-exec-001"]}>
          <Routes>
            <Route path="/cases/:caseId" element={<CaseDetailView />} />
          </Routes>
        </MemoryRouter>
      </QueryClientProvider>
    );
  }

  it("renders Execute Authorized Transmission button when action is approved", async () => {
    setupFetchMock({ receipt: null });
    renderView();

    await waitFor(() => {
      expect(screen.getByText("Execute Authorized Transmission →")).toBeInTheDocument();
    });
  });

  it("opens Governed External Execution confirmation modal with two-barrier safety notice", async () => {
    setupFetchMock({ receipt: null });
    renderView();

    const executeBtn = await screen.findByText("Execute Authorized Transmission →");
    fireEvent.click(executeBtn);

    await waitFor(() => {
      expect(screen.getByText("Execute Governed Transmission")).toBeInTheDocument();
      expect(screen.getByText("IRREVERSIBLE EXTERNAL ACTION")).toBeInTheDocument();
      expect(screen.getByText("TWO-BARRIER LIVE INTEGRITY (A & B)")).toBeInTheDocument();
    });
  });

  it("submits external execution with If-Match action etag on modal confirmation", async () => {
    let capturedHeaders: any = null;
    setupFetchMock({
      receipt: null,
      onExecute: (_, headers) => {
        capturedHeaders = headers;
      },
    });
    renderView();

    const executeBtn = await screen.findByText("Execute Authorized Transmission →");
    fireEvent.click(executeBtn);

    const confirmBtn = await screen.findByText("Confirm & Execute →");
    fireEvent.click(confirmBtn);

    await waitFor(() => {
      expect(capturedHeaders).not.toBeNull();
      const ifMatch = capturedHeaders.get ? capturedHeaders.get("If-Match") : (capturedHeaders["If-Match"] || capturedHeaders["if-match"]);
      expect(ifMatch).toBe('"action:act-exec-001:v2"');
    });
  });

  it("renders Verified Execution Receipt card with confirmation ref and copyable receipt hash", async () => {
    setupFetchMock({ receipt: mockReceipt });
    renderView();

    await waitFor(() => {
      expect(screen.getByText("Verified Execution Receipt")).toBeInTheDocument();
      expect(screen.getByText("TX-APEX-998877")).toBeInTheDocument();
      expect(screen.getByText("jobs.example.com")).toBeInTheDocument();
      expect(screen.getByText("a1b2c3d4e5f67890123456789abcdef0123456789abcdef0123456789abcdef0")).toBeInTheDocument();
    });
  });

  it("renders Ambiguous External Transmission alert when transmission timed out past irreversible barrier", async () => {
    setupFetchMock({ receipt: mockAmbiguousReceipt });
    renderView();

    await waitFor(() => {
      expect(screen.getByText("CRITICAL: Ambiguous External Transmission")).toBeInTheDocument();
      expect(screen.getByText(/MANUAL OPERATOR RESOLUTION REQUIRED/i)).toBeInTheDocument();
    });
  });
});
