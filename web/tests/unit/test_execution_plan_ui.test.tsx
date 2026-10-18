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
      target_schema_provenance: "user_provided",
      fields: [
        { target_field_id: "first_name", label: "First Name", field_type: "text", required: true, allowed_options: [] },
        { target_field_id: "email", label: "Email Address", field_type: "email", required: true, allowed_options: [] },
      ],
      upload_slots: [
        { slot_id: "resume", role: "RESUME", label: "Resume", required: true, accepted_mime_types: ["application/pdf"] },
      ],
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

  it("opens resolve plan inputs modal, binds Vault facts to target fields, and compiles plan with exact submit locator", async () => {
    let capturedCompilePayload: any = null;

    const mockFacts = [
      {
        fact_id: "fact_name",
        key: "personal.legal_name",
        namespace: "profile",
        user_id: "alice",
        preview: "Alice Smith",
        confidence: 1.0,
        verification_status: "USER_VERIFIED",
        sensitivity: "MEDIUM",
        source_type: "USER_INPUT",
        updated_at: "2026-10-16T10:00:00Z",
        version: 1,
        has_value: true,
      },
      {
        fact_id: "fact_email",
        key: "contact.email",
        namespace: "profile",
        user_id: "alice",
        preview: "alice@example.com",
        confidence: 1.0,
        verification_status: "SOURCE_VERIFIED",
        sensitivity: "MEDIUM",
        source_type: "USER_INPUT",
        updated_at: "2026-10-16T10:00:00Z",
        version: 1,
        has_value: true,
      },
    ];

    vi.spyOn(globalThis, "fetch").mockImplementation((input: any, init?: any) => {
      const url = String(input);
      if (url.includes("/cases/case-001/execution/plan") && init?.method === "POST") {
        capturedCompilePayload = JSON.parse(init.body);
        return Promise.resolve(new Response(JSON.stringify(mockPlan), { status: 200 }));
      }
      if (url.includes("/cases/case-001/execution/plan/latest")) {
        return Promise.resolve(new Response(JSON.stringify(mockPlan), { status: 200 }));
      }
      if (url.includes("/context/facts")) {
        return Promise.resolve(new Response(JSON.stringify({ items: mockFacts, total: 2, limit: 100, offset: 0 }), { status: 200 }));
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

    await waitFor(() => {
      expect(screen.getByText(/Resolve plan inputs →/i)).toBeInTheDocument();
    });

    // Open Resolution Modal
    fireEvent.click(screen.getByText(/Resolve plan inputs →/i));

    await waitFor(() => {
      expect(screen.getByText(/Resolve Action Plan Inputs/i)).toBeInTheDocument();
      expect(screen.getByText(/External ATS Form Field Bindings/i)).toBeInTheDocument();
    });

    // Select fact for email field
    const emailSelect = screen.getByLabelText(/Vault Fact for Email Address/i);
    fireEvent.change(emailSelect, { target: { value: "fact_email" } });

    // Set submit selector and semantic target
    const selectorInput = screen.getByPlaceholderText(/button\[data-automation-id='submit-button'\]/i);
    fireEvent.change(selectorInput, { target: { value: "button#submit-application" } });

    const labelInput = screen.getByPlaceholderText(/Submit Application/i);
    fireEvent.change(labelInput, { target: { value: "Submit Application" } });

    // Submit modal
    const compileBtn = screen.getByText(/Compile Action Plan with Resolved Bindings →/i);
    fireEvent.click(compileBtn);

    await waitFor(() => {
      expect(capturedCompilePayload).not.toBeNull();
    });

    expect(capturedCompilePayload.field_bindings).toContainEqual({
      target_field_id: "email",
      source_fact_id: "fact_email",
    });
    expect(capturedCompilePayload.final_submit_control).toMatchObject({
      selector: "button#submit-application",
      semantic_target: "Submit Application",
      control_provenance: "user_provided",
    });
  });

  it("renders staleness alert with reasons and exact final submit control card when present", async () => {
    const mockPlanStale = {
      ...mockPlan,
      is_stale: true,
      staleness_reasons: [
        "Target form field 'email' source fact fact_email was modified since plan was sealed",
      ],
      final_submit_control: {
        control_id: "submit-btn-1",
        selector: "button#submit-application",
        semantic_target: "Submit Application",
        control_provenance: "user_provided",
        control_type: "button_submit",
        method: "POST",
        requires_double_confirmation: true,
      },
    };

    vi.spyOn(globalThis, "fetch").mockImplementation((input: any) => {
      const url = String(input);
      if (url.includes("/cases/case-001/execution/plan/latest")) {
        return Promise.resolve(new Response(JSON.stringify(mockPlanStale), { status: 200 }));
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

    // Verify staleness banner and reasons
    await waitFor(() => {
      expect(screen.getByText(/Action Plan is Stale/i)).toBeInTheDocument();
      expect(
        screen.getByText(/Target form field 'email' source fact fact_email was modified/i)
      ).toBeInTheDocument();
    });

    // Verify Exact Final Submit Control Card
    expect(screen.getByText(/Exact Final Submit Control/i)).toBeInTheDocument();
    expect(screen.getByText("button#submit-application")).toBeInTheDocument();
    expect(screen.getByText("SEALED LOCATOR")).toBeInTheDocument();
  });

  it("displays notice when target interaction schema is not declared and avoids guessing fields or upload slots", async () => {
    const mockPlanNoSchema = {
      ...mockPlan,
      status: "draft",
      target_schema: null,
      field_mappings: [],
      upload_mappings: [],
      findings: [
        {
          check_name: "target_schema_verification",
          passed: false,
          severity: "error",
          code: "TARGET_SCHEMA_REQUIRED",
          message: "Target interaction schema is absent.",
          details: {},
        },
      ],
    };

    vi.spyOn(globalThis, "fetch").mockImplementation((input: any) => {
      const url = String(input);
      if (url.includes("/cases/case-001/execution/plan/latest")) {
        return Promise.resolve(new Response(JSON.stringify(mockPlanNoSchema), { status: 200 }));
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

    await waitFor(() => {
      expect(screen.getByText(/Resolve plan inputs →/i)).toBeInTheDocument();
    });

    fireEvent.click(screen.getByText(/Resolve plan inputs →/i));

    await waitFor(() => {
      expect(screen.getByText(/NO TARGET INTERACTION SCHEMA DECLARED/i)).toBeInTheDocument();
      expect(screen.getByText(/No upload slots declared in target schema/i)).toBeInTheDocument();
    });
  });

  it("allows explicit schema construction when schema is absent and compiles user-entered schema with no invented values", async () => {
    let capturedCompilePayload: any = null;
    const mockPlanNoSchema = {
      ...mockPlan,
      status: "draft",
      destination: null,
      target_schema: null,
      field_mappings: [],
      upload_mappings: [],
      findings: [
        {
          check_name: "target_schema_verification",
          passed: false,
          severity: "error",
          code: "TARGET_SCHEMA_REQUIRED",
          message: "Target interaction schema is absent.",
          details: {},
        },
      ],
    };

    vi.spyOn(globalThis, "fetch").mockImplementation((input: any, init?: any) => {
      const url = String(input);
      if (url.includes("/cases/case-001/execution/plan") && init?.method === "POST") {
        capturedCompilePayload = JSON.parse(init.body);
        return Promise.resolve(new Response(JSON.stringify(mockPlan), { status: 200 }));
      }
      if (url.includes("/cases/case-001/execution/plan/latest")) {
        return Promise.resolve(new Response(JSON.stringify(mockPlanNoSchema), { status: 200 }));
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

    await waitFor(() => {
      expect(screen.getByText(/Resolve plan inputs →/i)).toBeInTheDocument();
    });

    fireEvent.click(screen.getByText(/Resolve plan inputs →/i));

    await waitFor(() => {
      expect(screen.getByText(/NO TARGET INTERACTION SCHEMA DECLARED/i)).toBeInTheDocument();
    });

    // 1. Operator adds a field but forgets destination_type -> client-side validation error shown
    const addFieldBtns = screen.getAllByText(/\+ Add Target Field/i);
    fireEvent.click(addFieldBtns[0]);

    const fieldIdInput = screen.getByPlaceholderText(/Field ID \(e\.g\. first_name\)/i);
    fireEvent.change(fieldIdInput, { target: { value: "custom_first_name" } });

    const fieldLabelInput = screen.getByPlaceholderText(/Label \(e\.g\. First Name\)/i);
    fireEvent.change(fieldLabelInput, { target: { value: "First Name" } });

    const fieldTypeSelect = screen.getByLabelText(/Field Type for First Name/i);
    fireEvent.change(fieldTypeSelect, { target: { value: "text" } });

    const compileBtn = screen.getByText(/Compile Action Plan with Resolved Bindings →/i);
    fireEvent.click(compileBtn);

    await waitFor(() => {
      expect(screen.getByText(/Destination type \(ats_form or direct_portal\) is required/i)).toBeInTheDocument();
    });
    expect(capturedCompilePayload).toBeNull();

    // 2. Operator selects destination_type = direct_portal and adds an upload slot
    const channelSelect = screen.getByLabelText(/Channel Type/i);
    fireEvent.change(channelSelect, { target: { value: "direct_portal" } });

    const addSlotBtns = screen.getAllByText(/\+ Add Upload Slot/i);
    fireEvent.click(addSlotBtns[0]);

    const slotIdInput = screen.getByPlaceholderText(/Slot ID \(e\.g\. resume\)/i);
    fireEvent.change(slotIdInput, { target: { value: "cv_doc" } });

    const slotRoleInput = screen.getByPlaceholderText(/Role \(e\.g\. RESUME\)/i);
    fireEvent.change(slotRoleInput, { target: { value: "RESUME" } });

    const slotLabelInput = screen.getByPlaceholderText(/Label \(e\.g\. Resume \/ CV\)/i);
    fireEvent.change(slotLabelInput, { target: { value: "Curriculum Vitae" } });

    // Set canonical url to test destinationOverride
    const urlInput = screen.getByPlaceholderText(/https:\/\/company\.greenhouse\.io\/jobs\/123\/apply/i);
    fireEvent.change(urlInput, { target: { value: "https://portal.example.com/apply" } });

    fireEvent.click(compileBtn);

    await waitFor(() => {
      expect(capturedCompilePayload).not.toBeNull();
    });

    expect(capturedCompilePayload.target_schema).not.toBeNull();
    expect(capturedCompilePayload.target_schema.destination_type).toBe("direct_portal");
    expect(capturedCompilePayload.target_schema.fields).toHaveLength(1);
    expect(capturedCompilePayload.target_schema.fields[0]).toMatchObject({
      target_field_id: "custom_first_name",
      label: "First Name",
      field_type: "text",
      required: true,
      allowed_options: [],
    });
    expect(capturedCompilePayload.target_schema.upload_slots).toHaveLength(1);
    expect(capturedCompilePayload.target_schema.upload_slots[0]).toMatchObject({
      slot_id: "cv_doc",
      role: "RESUME",
      label: "Curriculum Vitae",
      required: true,
      accepted_mime_types: [],
    });
    expect(capturedCompilePayload.destination_override).toMatchObject({
      canonical_url: "https://portal.example.com/apply",
      destination_type: "direct_portal",
      is_exact_destination: true,
    });
  });

  it("compiles with targetSchema: null and destination_override: null when no schema or destination is entered", async () => {
    let capturedCompilePayload: any = null;
    const mockPlanNoSchema = {
      ...mockPlan,
      status: "draft",
      destination: null,
      target_schema: null,
      field_mappings: [],
      upload_mappings: [],
      findings: [],
    };

    vi.spyOn(globalThis, "fetch").mockImplementation((input: any, init?: any) => {
      const url = String(input);
      if (url.includes("/cases/case-001/execution/plan") && init?.method === "POST") {
        capturedCompilePayload = JSON.parse(init.body);
        return Promise.resolve(new Response(JSON.stringify(mockPlan), { status: 200 }));
      }
      if (url.includes("/cases/case-001/execution/plan/latest")) {
        return Promise.resolve(new Response(JSON.stringify(mockPlanNoSchema), { status: 200 }));
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

    await waitFor(() => {
      expect(screen.getByText(/Resolve plan inputs →/i)).toBeInTheDocument();
    });

    fireEvent.click(screen.getByText(/Resolve plan inputs →/i));

    await waitFor(() => {
      expect(screen.getByText(/NO TARGET INTERACTION SCHEMA DECLARED/i)).toBeInTheDocument();
    });

    const compileBtn = screen.getByText(/Compile Action Plan with Resolved Bindings →/i);
    fireEvent.click(compileBtn);

    await waitFor(() => {
      expect(capturedCompilePayload).not.toBeNull();
    });
    expect(capturedCompilePayload.target_schema).toBeNull();
    expect(capturedCompilePayload.destination_override).toBeNull();
  });
});
