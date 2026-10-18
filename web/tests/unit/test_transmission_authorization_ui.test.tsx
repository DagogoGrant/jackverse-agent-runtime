import { describe, it, expect, vi, beforeEach } from "vitest";
import React from "react";
import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { CaseDetailView } from "../../src/views/CaseDetailView";
import { NeedsYouView } from "../../src/views/NeedsYouView";

describe("Phase 5.3.2 Exact Transmission Authorization UI", () => {
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
    case_id: "case-001",
    mission_id: "mission-001",
    user_id: "alice",
    case_type: "job_application",
    title: "Staff Engineer Application",
    goal: "Apply for Staff Engineer role",
    status: "action_required",
    success_criteria: "Submission completed",
    constraints: "Exact fields only",
    created_at: "2026-10-16T10:00:00Z",
    updated_at: "2026-10-16T10:00:00Z",
    version: 4,
    etag: '"case:case-001:4"',
  };

  const mockPlan = {
    plan_id: "plan-101",
    case_id: "case-001",
    package_id: "pkg-101",
    user_id: "alice",
    status: "ready_for_authorization",
    destination: {
      destination_type: "direct_portal",
      destination_provenance: "opportunity_post",
      canonical_url: "https://careers.example.com/jobs/staff-eng/apply",
      organization: "Acme Corp",
      channel_metadata: { browser_engine: "chromium" },
      is_exact_destination: true,
      normalized_origin: "https://careers.example.com",
      normalized_hostname: "careers.example.com",
    },
    target_schema: {
      schema_id: "schema_1",
      destination_type: "direct_portal",
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
      schema_hash: "schema_hash_123",
    },
    field_mappings: [
      {
        target_field_id: "full_name",
        field_name: "Full Name",
        field_value: "Alice Test",
        source_fact_id: "fact_name_001",
        source_fact_version: 1,
        verification_status: "verified",
        required: true,
        allowed_purposes: ["job_application"],
      },
    ],
    upload_mappings: [
      {
        asset_id: "asset_resume_01",
        role: "resume",
        source_artifact_id: "art-1",
        source_artifact_version: 1,
        filename: "alice_resume.pdf",
        mime_type: "application/pdf",
        content_sha256: "b0f7e4c93540d998a467e2a993e3d643890538a7206429584e03d368d4a4ae95",
        byte_size: 1048576,
        materialized: true,
      },
    ],
    screening_responses: [
      {
        question_id: "q_remote",
        question: "Can you work remote?",
        answer: "Yes",
        grounding_claim_ids: ["claim_1"],
        source_artifact_id: "art-1",
        source_artifact_version: 1,
        source_payload_hash: "payload_hash_1",
      },
    ],
    final_submit_control: {
      selector: 'button[type="submit"]',
      semantic_target: "Submit Application",
      control_provenance: "operator_confirmed",
      confidence: 1.0,
    },
    is_stale: false,
    staleness_reasons: [],
    execution_plan_fingerprint: "plan_fp_9876543210fedcba",
    package_fingerprint: "pkg_fp_1234567890abcdef",
    target_schema_hash: "schema_hash_123",
    execution_input_hash: "input_hash_456",
    compiled_at: "2026-10-16T10:10:00Z",
    version: 1,
  };

  const mockAction = {
    action_id: "act-submit-001",
    case_id: "case-001",
    action_type: "submit_form",
    description: "Authorize external transmission to https://careers.example.com/jobs/staff-eng/apply",
    status: "awaiting_approval",
    risk_level: "high",
    requires_approval: true,
    parameters: {
      scope: "external_transmission",
      authorization_schema: "tx_auth_v1",
      plan_id: "plan-101",
      execution_plan_fingerprint: "plan_fp_9876543210fedcba",
      package_id: "pkg-101",
      package_fingerprint: "pkg_fp_1234567890abcdef",
      destination_url: "https://careers.example.com/jobs/staff-eng/apply",
      destination_type: "direct_portal",
      organization: "Acme Corp",
      field_mapping_count: 1,
      upload_asset_count: 1,
      screening_response_count: 1,
    },
    result: {},
    created_at: "2026-10-16T10:15:00Z",
    executed_at: null,
    idempotency_key: "tx_act:key123",
    version: 2,
  };

  const mockApproval = {
    approval_id: "app-submit-001",
    action_id: "act-submit-001",
    case_id: "case-001",
    user_id: "alice",
    status: "pending",
    action_fingerprint: "action_fp_1122334455",
    created_at: "2026-10-16T10:15:00Z",
    decided_at: null,
    decision_reason: null,
    version: 1,
  };

  const mockArtifacts = [
    {
      artifact_id: "art-1",
      artifact_set_id: "set-001",
      case_id: "case-001",
      opportunity_id: "opp-001",
      user_id: "alice",
      material_type: "tailored_resume",
      schema_version: "prep.artifact.v1",
      planner_version: "prep.planner.v1",
      status: "frozen",
      title: "Tailored Resume",
      blocks: [],
      source_report_id: "rep-001",
      input_hash: "inhash",
      grounding_snapshot_hash: "ghash",
      created_at: "2026-10-16T10:00:00Z",
      updated_at: "2026-10-16T10:00:00Z",
      version: 1,
      rendered_markdown: "# Alice Resume",
    },
  ];

  it("renders Request Transmission Authorization button and triggers request", async () => {
    let authRequested = false;

    vi.spyOn(globalThis, "fetch").mockImplementation((input: any, init?: any) => {
      const url = String(input);

      if (url.includes("/cases/case-001/execution/plan/latest")) {
        return Promise.resolve(new Response(JSON.stringify(mockPlan), { status: 200, headers: { etag: '"plan:plan-101:1"' } }));
      }
      if (url.includes("/cases/case-001/preparation/packages/latest")) {
        return Promise.resolve(new Response(JSON.stringify({
          package_id: "pkg-101",
          status: "approved",
          artifact_ids: ["art-1"],
          package_fingerprint: "pkg_fp_1234567890abcdef",
          verification_findings: [],
        }), { status: 200 }));
      }
      if (url.includes("/cases/case-001/preparation/artifacts")) {
        return Promise.resolve(new Response(JSON.stringify({ artifacts: mockArtifacts, total: 1 }), { status: 200 }));
      }
      if (url.includes("/cases/case-001/preparation/report/latest")) {
        return Promise.resolve(new Response(JSON.stringify({ report_id: "rep-001", is_stale: false, requirements: [] }), { status: 200 }));
      }
      if (url.includes("/cases/case-001/actions")) {
        return Promise.resolve(new Response(JSON.stringify([]), { status: 200 }));
      }
      if (url.includes("/cases/case-001/claims")) {
        return Promise.resolve(new Response(JSON.stringify({ items: [], total: 0 }), { status: 200 }));
      }
      if (url.includes("/cases/case-001/execution/plan/plan-101/request-authorization") && init?.method === "POST") {
        authRequested = true;
        return Promise.resolve(new Response(JSON.stringify({
          action: mockAction,
          approval: mockApproval,
        }), { status: 200, headers: { etag: '"approval:app-1:1"' } }));
      }
      if (url.includes("/cases/case-001")) {
        return Promise.resolve(new Response(JSON.stringify(mockCase), { status: 200, headers: { etag: '"case:case-001:4"' } }));
      }
      if (url.includes("/events")) {
        return Promise.resolve(new Response(JSON.stringify({ events: [], total_count: 0 }), { status: 200 }));
      }
      if (url.includes("/context/facts")) {
        return Promise.resolve(new Response(JSON.stringify({ items: [], total: 0 }), { status: 200 }));
      }

      return Promise.resolve(new Response("Not found", { status: 404 }));
    });

    render(
      <QueryClientProvider client={queryClient}>
        <MemoryRouter initialEntries={["/cases/case-001"]}>
          <Routes>
            <Route path="/cases/:caseId" element={<CaseDetailView />} />
          </Routes>
        </MemoryRouter>
      </QueryClientProvider>
    );

    // Verify Plan Status badge and Request Transmission Authorization button
    expect(await screen.findByText(/READY FOR AUTHORIZATION · ZERO TRANSMISSION/i)).toBeInTheDocument();
    const authBtn = await screen.findByRole("button", { name: /Request Transmission Authorization →/i });
    expect(authBtn).toBeInTheDocument();

    fireEvent.click(authBtn);

    await waitFor(() => {
      expect(authRequested).toBe(true);
    });
  });

  it("renders specialized transmission authorization dossier in NeedsYouView with masked values", async () => {
    vi.spyOn(globalThis, "fetch").mockImplementation((input: any) => {
      const url = String(input);

      if (url.endsWith("/approvals") || url.includes("/approvals?")) {
        return Promise.resolve(new Response(JSON.stringify([mockApproval]), { status: 200 }));
      }
      if (url.includes("/approvals/app-submit-001")) {
        return Promise.resolve(new Response(JSON.stringify(mockApproval), { status: 200, headers: { etag: '"approval:app-submit-001:1"' } }));
      }
      if (url.includes("/actions/act-submit-001")) {
        return Promise.resolve(new Response(JSON.stringify(mockAction), { status: 200, headers: { etag: '"action:act-submit-001:2"' } }));
      }
      if (url.includes("/cases/case-001/execution/plan/plan-101")) {
        return Promise.resolve(new Response(JSON.stringify(mockPlan), { status: 200, headers: { etag: '"plan:plan-101:1"' } }));
      }
      if (url.includes("/cases/case-001/preparation/packages/latest")) {
        return Promise.resolve(new Response(JSON.stringify(null), { status: 404 }));
      }
      if (url.includes("/preparation/artifacts")) {
        return Promise.resolve(new Response(JSON.stringify({ artifacts: [], total: 0 }), { status: 200 }));
      }

      return Promise.resolve(new Response(JSON.stringify({}), { status: 200 }));
    });

    render(
      <QueryClientProvider client={queryClient}>
        <MemoryRouter initialEntries={["/needs-you"]}>
          <Routes>
            <Route path="/needs-you" element={<NeedsYouView />} />
          </Routes>
        </MemoryRouter>
      </QueryClientProvider>
    );

    // Verify Constitutional Statement
    expect(
      await screen.findByText(/“I authorize these exact materials and values to be transmitted to this exact destination.”/i)
    ).toBeInTheDocument();
    expect(screen.getByText(/Constitutional Boundary \/\/ External Transmission Authorization/i)).toBeInTheDocument();

    // Verify Destination Dossier
    const destUrls = await screen.findAllByText(/careers\.example\.com\/jobs\/staff-eng\/apply/);
    expect(destUrls.length).toBeGreaterThanOrEqual(1);
    expect(screen.getByText("Acme Corp")).toBeInTheDocument();

    // Verify Upload Assets
    expect(screen.getByText("alice_resume.pdf")).toBeInTheDocument();

    // Verify Form Field Value is Masked by Default
    expect(screen.getByText("Full Name")).toBeInTheDocument();
    expect(screen.getAllByText("••••••••").length).toBeGreaterThanOrEqual(1);
    expect(screen.queryByText("Alice Test")).not.toBeInTheDocument();

    // Click Eye button to reveal value
    const revealBtns = screen.getAllByTitle("Reveal value");
    expect(revealBtns.length).toBeGreaterThanOrEqual(1);
    fireEvent.click(revealBtns[0]);

    // Now revealed value should be visible
    expect(await screen.findByText("Alice Test")).toBeInTheDocument();

    // Verify DragToAuthorize label
    expect(screen.getByText(/Slide to authorize transmission/i)).toBeInTheDocument();
  });
});
