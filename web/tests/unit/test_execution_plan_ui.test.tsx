import { describe, it, expect, vi, beforeEach } from "vitest";
import React from "react";
import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { CaseDetailView } from "../../src/views/CaseDetailView";

describe("Phase 5.3.1 Exact External Action Planning UI Dossier", () => {
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

  const mockPackage = {
    package_id: "pkg-101",
    case_id: "case-001",
    artifact_set_id: "set-001",
    source_report_id: "rep-001",
    user_id: "alice",
    status: "approved",
    artifact_ids: ["art-1"],
    artifact_versions: { "art-1": 1 },
    artifact_snapshots: [],
    claim_snapshots: [],
    rendered_payload_hashes: {},
    verification_status: "passed",
    verification_findings: [
      {
        check_name: "package_grounding_verified",
        passed: true,
        severity: "info",
        message: "All 1 package claims grounded",
        details: {},
      },
    ],
    frozen_at: "2026-10-16T10:05:00Z",
    package_fingerprint: "pkg_fp_1234567890abcdef",
    schema_version: "prep.package.v1",
    verifier_version: "verifier.v1",
    created_at: "2026-10-16T10:05:00Z",
    updated_at: "2026-10-16T10:05:00Z",
    version: 2,
  };

  const mockPlan = {
    plan_id: "plan-501",
    case_id: "case-001",
    opportunity_id: "opp-001",
    package_id: "pkg-101",
    package_fingerprint: "pkg_fp_1234567890abcdef",
    user_id: "alice",
    status: "ready_for_authorization",
    destination: {
      destination_type: "ats_form",
      destination_provenance: "OPPORTUNITY_APPLICATION_URL",
      canonical_url: "https://acme.greenhouse.io/jobs/123/apply",
      organization: "Acme Corp",
      channel_metadata: {},
      is_exact_destination: true,
    },
    target_schema: {
      schema_id: "greenhouse-v1",
      destination_type: "ats_form",
      fields: [],
      upload_slots: [],
      screening_questions: [],
      final_submit_control: null,
      schema_hash: "schema_hash_123",
    },
    field_mappings: [
      {
        target_field_id: "first_name",
        field_name: "First Name",
        field_value: "Alice",
        source_fact_id: "fact_name",
        source_fact_version: 1,
        verification_status: "USER_VERIFIED",
        required: true,
      },
      {
        target_field_id: "email",
        field_name: "Email Address",
        field_value: "alice@example.com",
        source_fact_id: "fact_email",
        source_fact_version: 1,
        verification_status: "SOURCE_VERIFIED",
        required: true,
      },
    ],
    upload_mappings: [
      {
        asset_id: "asset_resume",
        role: "RESUME",
        source_artifact_id: "art-1",
        source_artifact_version: 1,
        filename: "Alice_Resume.pdf",
        mime_type: "application/pdf",
        content_sha256: "9876543210fedcba9876543210fedcba",
        byte_size: 4096,
        materialized: true,
      },
    ],
    screening_responses: [
      {
        question_id: "q_legally_authorized",
        question: "Are you authorized to work in the country?",
        answer: "Yes, I am authorized.",
        source_artifact_id: "art-1",
        grounding_claim_ids: ["claim-1"],
      },
    ],
    findings: [
      {
        check_name: "destination_resolved",
        passed: true,
        severity: "info",
        code: "OK",
        message: "Exact destination resolved with provenance OPPORTUNITY_APPLICATION_URL",
        details: {},
      },
      {
        check_name: "upload_assets_materialized",
        passed: true,
        severity: "info",
        code: "OK",
        message: "All upload assets verified and materialized",
        details: {},
      },
    ],
    execution_input_hash: "input_hash_abcdef",
    execution_plan_fingerprint: "plan_fp_abcdef0123456789",
    schema_version: "execution.plan.v1",
    planner_version: "action.planner.v1",
    created_at: "2026-10-16T10:10:00Z",
    updated_at: "2026-10-16T10:10:00Z",
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

  it("renders Phase 5.3.1 external action plan dossier with zero-transmission guarantee", async () => {
    vi.spyOn(globalThis, "fetch").mockImplementation((input: any) => {
      const url = String(input);
      if (url.includes("/cases/case-001/execution/plan/latest")) {
        return Promise.resolve(new Response(JSON.stringify(mockPlan), { status: 200 }));
      }
      if (url.includes("/cases/case-001/preparation/packages/latest")) {
        return Promise.resolve(new Response(JSON.stringify(mockPackage), { status: 200 }));
      }
      if (url.includes("/cases/case-001/preparation/artifacts")) {
        return Promise.resolve(new Response(JSON.stringify({ artifacts: mockArtifacts, total: 1 }), { status: 200 }));
      }
      if (url.includes("/cases/case-001/actions")) {
        return Promise.resolve(new Response(JSON.stringify([]), { status: 200 }));
      }
      if (url.includes("/cases/case-001/claims")) {
        return Promise.resolve(new Response(JSON.stringify([]), { status: 200 }));
      }
      if (url.includes("/cases/case-001/preparation/report/latest")) {
        return Promise.resolve(new Response(JSON.stringify({}), { status: 200 }));
      }
      if (url.includes("/cases/case-001")) {
        return Promise.resolve(new Response(JSON.stringify(mockCase), { status: 200 }));
      }
      if (url.includes("/events")) {
        return Promise.resolve(new Response(JSON.stringify({ events: [], next_cursor: null }), { status: 200 }));
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

    // Verify header and plan status
    await waitFor(() => {
      expect(screen.getByText(/PHASE 5.3.1 \/\/ EXACT EXTERNAL ACTION PLANNING/i)).toBeInTheDocument();
    });

    expect(screen.getByText(/READY FOR AUTHORIZATION · ZERO TRANSMISSION/i)).toBeInTheDocument();

    // Verify Destination details
    expect(screen.getByText("Acme Corp")).toBeInTheDocument();
    expect(screen.getByText("https://acme.greenhouse.io/jobs/123/apply")).toBeInTheDocument();
    expect(screen.getByText("OPPORTUNITY_APPLICATION_URL")).toBeInTheDocument();
    expect(screen.getByText("EXACT DESTINATION")).toBeInTheDocument();

    // Verify Field Mappings exist and are masked by default
    expect(screen.getByText("First Name")).toBeInTheDocument();
    expect(screen.getByText("Email Address")).toBeInTheDocument();
    const maskedValues = screen.getAllByText("••••••••••••");
    expect(maskedValues.length).toBeGreaterThanOrEqual(2);

    // Click reveal button on first_name
    const revealBtns = screen.getAllByTitle("Reveal personal value");
    fireEvent.click(revealBtns[0]);
    await waitFor(() => {
      expect(screen.getByText("Alice")).toBeInTheDocument();
    });

    // Verify Materialized Upload Asset
    expect(screen.getByText("Alice_Resume.pdf")).toBeInTheDocument();
    expect(screen.getByText("MATERIALIZED")).toBeInTheDocument();

    // Verify Screening Response
    expect(screen.getByText("Are you authorized to work in the country?")).toBeInTheDocument();

    // Verify Cryptographic Binding collapsible
    expect(screen.getByText(/INSPECT PLAN BINDING & CRYPTOGRAPHIC PROVENANCE/i)).toBeInTheDocument();
    fireEvent.click(screen.getByText(/INSPECT PLAN BINDING & CRYPTOGRAPHIC PROVENANCE/i));
    await waitFor(() => {
      expect(screen.getByText("plan_fp_abcdef0123456789")).toBeInTheDocument();
    });

    // Verify Constitutional Zero-Transmission Policy notice
    expect(screen.getByText(/Constitutional Zero-Transmission Policy/i)).toBeInTheDocument();

    // CRITICAL: Ensure NO external transmit or submit buttons exist
    expect(screen.queryByRole("button", { name: /Submit to Acme/i })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Authorize Submission/i })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Send Application/i })).not.toBeInTheDocument();
  });
});
