import { describe, it, expect, vi, beforeEach } from "vitest";
import React from "react";
import { render, screen, waitFor, fireEvent, renderHook, act } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { NeedsYouView } from "../../src/views/NeedsYouView";
import { useRequestActionApproval } from "../../src/hooks/useCaseworker";

describe("Approval Workflow & ETag Governance", () => {
  let queryClient: QueryClient;

  beforeEach(() => {
    queryClient = new QueryClient({
      defaultOptions: {
        queries: { retry: false },
      },
    });
    vi.restoreAllMocks();
  });

  const mockApprovalsList = [
    {
      approval_id: "app-101",
      action_id: "act-501",
      case_id: "case-001",
      user_id: "alice",
      action_fingerprint: "sha256:abcd1234ef5678",
      status: "pending",
      requested_at: "2026-10-01T10:00:00Z",
      version: 1,
    },
  ];

  const mockApprovalDetail = {
    approval_id: "app-101",
    action_id: "act-501",
    case_id: "case-001",
    user_id: "alice",
    action_fingerprint: "sha256:abcd1234ef5678",
    status: "pending",
    requested_at: "2026-10-01T10:00:00Z",
    version: 1,
  };

  const mockActionDetail = {
    action_id: "act-501",
    case_id: "case-001",
    action_type: "submit_form",
    description: "Submit verified application packet to external portal",
    parameters: { target_url: "https://example.com/apply" },
    status: "awaiting_approval",
    risk_level: "high",
    requires_approval: true,
    fingerprint: "sha256:abcd1234ef5678",
    created_at: "2026-10-01T09:59:00Z",
    version: 1,
  };

  it("fetches Approval and Action independently, rendering Action parameters before decision", async () => {
    const fetchSpy = vi.spyOn(globalThis, "fetch").mockImplementation((input: any) => {
      const url = String(input);
      if (url.endsWith("/approvals") || url.includes("/approvals?")) {
        return Promise.resolve({
          ok: true,
          status: 200,
          headers: new Headers({ ETag: '"approval:app-101:v1"' }),
          json: async () => mockApprovalsList,
        } as Response);
      }
      if (url.includes("/approvals/app-101")) {
        return Promise.resolve({
          ok: true,
          status: 200,
          headers: new Headers({ ETag: '"approval:app-101:v1"' }),
          json: async () => mockApprovalDetail,
        } as Response);
      }
      if (url.includes("/actions/act-501")) {
        return Promise.resolve({
          ok: true,
          status: 200,
          headers: new Headers({ ETag: '"action:act-501:v1"' }),
          json: async () => mockActionDetail,
        } as Response);
      }
      return Promise.reject(new Error("Unexpected route: " + url));
    });

    render(
      <QueryClientProvider client={queryClient}>
        <NeedsYouView />
      </QueryClientProvider>
    );

    // Verify Action description and risk level are rendered from independent Action fetch
    await waitFor(() => {
      expect(
        screen.getByText("Submit verified application packet to external portal")
      ).toBeInTheDocument();
    });

    expect(screen.getByText(/RISK: HIGH/i)).toBeInTheDocument();
    expect(screen.getByText(/sha256:abcd1234ef5678/i)).toBeInTheDocument();

    // Verify independent calls were made
    const calledUrls = fetchSpy.mock.calls.map((c) => String(c[0]));
    expect(calledUrls.some((u) => u.includes("/approvals/app-101"))).toBe(true);
    expect(calledUrls.some((u) => u.includes("/actions/act-501"))).toBe(true);
  });

  it("sends Approval ETag in If-Match header upon approval mutation and handles 412 concurrency", async () => {
    let approveCallHeaders: HeadersInit | undefined;

    vi.spyOn(globalThis, "fetch").mockImplementation((input: any, init: any) => {
      const url = String(input);
      if (url.endsWith("/approvals") || url.includes("/approvals?")) {
        return Promise.resolve({
          ok: true,
          status: 200,
          headers: new Headers(),
          json: async () => mockApprovalsList,
        } as Response);
      }
      if (url.includes("/approvals/app-101/approve")) {
        approveCallHeaders = init?.headers;
        // Simulate concurrent modification 412
        return Promise.resolve({
          ok: false,
          status: 412,
          headers: new Headers(),
          json: async () => ({
            type: "https://httpstatuses.com/412",
            title: "Precondition Failed",
            status: 412,
            detail: "ETag precondition failed: version mismatch",
          }),
        } as Response);
      }
      if (url.includes("/approvals/app-101")) {
        return Promise.resolve({
          ok: true,
          status: 200,
          headers: new Headers({ ETag: '"approval:app-101:v1"' }),
          json: async () => mockApprovalDetail,
        } as Response);
      }
      if (url.includes("/actions/act-501")) {
        return Promise.resolve({
          ok: true,
          status: 200,
          headers: new Headers({ ETag: '"action:act-501:v1"' }),
          json: async () => mockActionDetail,
        } as Response);
      }
      return Promise.reject(new Error("Unexpected route: " + url));
    });

    render(
      <QueryClientProvider client={queryClient}>
        <NeedsYouView />
      </QueryClientProvider>
    );

    await waitFor(() => {
      expect(
        screen.getByText("Submit verified application packet to external portal")
      ).toBeInTheDocument();
    });

    // Authorize via accessible keyboard trigger on DragToAuthorize
    const slider = screen.getByRole("slider");
    slider.focus();
    fireEvent.keyDown(slider, { key: "Enter" });

    // Verify 412 displays user notification without crashing or silent retry
    await waitFor(() => {
      expect(
        screen.getByText("This changed elsewhere. We've loaded the latest version.")
      ).toBeInTheDocument();
    });

    // Verify If-Match sent was the Approval ETag, NOT the Action ETag
    expect(approveCallHeaders).toBeDefined();
    const ifMatch = approveCallHeaders instanceof Headers
      ? approveCallHeaders.get("If-Match")
      : (approveCallHeaders as any)["If-Match"];
    expect(ifMatch).toBe('"approval:app-101:v1"');
  });

  it("useRequestActionApproval sends Action ETag, returns ActionResponse, and invalidates queries", async () => {
    let requestHeaders: HeadersInit | undefined;
    let requestMethod: string | undefined;

    const mockActionResponse = {
      action_id: "act-501",
      case_id: "case-001",
      action_type: "submit_form",
      description: "Submit candidate credentials to external portal",
      parameters: { portal: "greenhouse" },
      status: "awaiting_approval",
      risk_level: "high",
      requires_approval: true,
      fingerprint: "sha256:abcd1234ef5678",
      created_at: "2026-10-01T09:59:00Z",
      version: 2,
    };

    vi.spyOn(globalThis, "fetch").mockImplementation((input: any, init: any) => {
      const url = String(input);
      if (url.includes("/actions/act-501/request-approval")) {
        requestMethod = init?.method;
        requestHeaders = init?.headers;
        return Promise.resolve({
          ok: true,
          status: 200,
          headers: new Headers({ ETag: '"action:act-501:v2"' }),
          json: async () => mockActionResponse,
        } as Response);
      }
      return Promise.reject(new Error("Unexpected route: " + url));
    });

    const invalidateSpy = vi.spyOn(queryClient, "invalidateQueries");

    const wrapper = ({ children }: { children: React.ReactNode }) => (
      <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
    );

    const { result } = renderHook(() => useRequestActionApproval(), { wrapper });

    let mutationResult: any;
    await act(async () => {
      mutationResult = await result.current.mutateAsync({
        actionId: "act-501",
        etag: '"action:act-501:v1"',
      });
    });

    expect(requestMethod).toBe("POST");
    const ifMatch = requestHeaders instanceof Headers
      ? requestHeaders.get("If-Match")
      : (requestHeaders as any)["If-Match"];
    expect(ifMatch).toBe('"action:act-501:v1"');

    // Contract: returns action (ActionResponse) and Action ETag, NOT approval
    expect(mutationResult.action).toBeDefined();
    expect(mutationResult.action.action_id).toBe("act-501");
    expect(mutationResult.action.status).toBe("awaiting_approval");
    expect(mutationResult.etag).toBe('"action:act-501:v2"');
    expect(mutationResult.approval).toBeUndefined();

    // Invalidation check
    const invalidatedKeys = invalidateSpy.mock.calls.map((c) => c[0]?.queryKey);
    expect(invalidatedKeys.some((k) => k?.[0] === "approvals")).toBe(true);
    expect(invalidatedKeys.some((k) => k?.[0] === "action" && k?.[1] === "act-501")).toBe(true);
    expect(invalidatedKeys.some((k) => k?.[0] === "actions")).toBe(true);
    expect(invalidatedKeys.some((k) => k?.[0] === "events")).toBe(true);
  });
});
