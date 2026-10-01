import React from "react";
import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { render, screen, fireEvent, waitFor, within } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { ContextView } from "../../src/views/ContextView";

describe("Context Vault Privacy, Personal Facts & On-Demand Masking", () => {
  let queryClient: QueryClient;
  const originalFetch = global.fetch;

  beforeEach(() => {
    localStorage.clear();
    sessionStorage.clear();
    queryClient = new QueryClient({
      defaultOptions: {
        queries: { retry: false },
      },
    });
  });

  afterEach(() => {
    global.fetch = originalFetch;
    vi.restoreAllMocks();
  });

  const mockFactsSummary = {
    items: [
      {
        fact_id: "fact-public-01",
        namespace: "profile",
        key: "github",
        sensitivity: "public",
        verification_status: "user_verified",
        preview: "github.com/alexmercer",
        version: 1,
        created_at: "2026-10-01T00:00:00Z",
        updated_at: "2026-10-01T00:00:00Z",
      },
      {
        fact_id: "fact-personal-01",
        namespace: "career",
        key: "title",
        sensitivity: "personal",
        verification_status: "user_verified",
        preview: null,
        version: 1,
        created_at: "2026-10-01T00:00:00Z",
        updated_at: "2026-10-01T00:00:00Z",
      },
      {
        fact_id: "fact-sensitive-01",
        namespace: "finance",
        key: "iban",
        sensitivity: "sensitive",
        verification_status: "user_verified",
        preview: null,
        version: 1,
        created_at: "2026-10-01T00:00:00Z",
        updated_at: "2026-10-01T00:00:00Z",
      },
    ],
    total: 3,
  };

  const mockReadiness = {
    purpose: "job_application",
    title: "Job Application Readiness",
    is_ready: true,
    completeness_ratio: 1.0,
    satisfied_count: 4,
    missing_count: 0,
    unverifiable_count: 0,
    expired_count: 0,
    satisfied: [],
    missing: [],
    unverifiable: [],
    expired: [],
  };

  it("handles PUBLIC, PERSONAL, and SENSITIVE fact visibility and zero-retention on demand", async () => {
    global.fetch = vi.fn().mockImplementation((url: string) => {
      if (url.includes("/context/facts/fact-sensitive-01")) {
        return Promise.resolve({
          ok: true,
          status: 200,
          headers: new Headers({ ETag: '"fact:fact-sensitive-01:v1"' }),
          json: async () => ({
            fact_id: "fact-sensitive-01",
            namespace: "finance",
            key: "iban",
            sensitivity: "sensitive",
            verification_status: "user_verified",
            value: "DE89370400440532013000",
            version: 1,
          }),
        });
      }
      if (url.includes("/context/facts/fact-personal-01")) {
        return Promise.resolve({
          ok: true,
          status: 200,
          headers: new Headers({ ETag: '"fact:fact-personal-01:v1"' }),
          json: async () => ({
            fact_id: "fact-personal-01",
            namespace: "career",
            key: "title",
            sensitivity: "personal",
            verification_status: "user_verified",
            value: "Staff Software Engineer",
            version: 1,
          }),
        });
      }
      if (url.includes("/context/facts")) {
        return Promise.resolve({
          ok: true,
          status: 200,
          headers: new Headers(),
          json: async () => mockFactsSummary,
        });
      }
      if (url.includes("/context/readiness")) {
        return Promise.resolve({
          ok: true,
          status: 200,
          headers: new Headers(),
          json: async () => mockReadiness,
        });
      }
      return Promise.reject(new Error("Unknown route: " + url));
    });

    const { unmount } = render(
      <QueryClientProvider client={queryClient}>
        <ContextView />
      </QueryClientProvider>
    );

    // 1. Wait for facts to render
    await waitFor(() => {
      expect(screen.getByText("profile.github")).toBeInTheDocument();
      expect(screen.getByText("career.title")).toBeInTheDocument();
      expect(screen.getByText("finance.iban")).toBeInTheDocument();
    });

    // 2. PUBLIC fact: preview is visible immediately
    expect(screen.getByText("github.com/alexmercer")).toBeInTheDocument();

    // 3. PERSONAL fact: raw value is NOT in the page initially
    expect(screen.getByText(/Personal record · Detail on demand/i)).toBeInTheDocument();
    expect(screen.queryByText("Staff Software Engineer")).not.toBeInTheDocument();

    // 4. SENSITIVE fact: masked by default
    expect(screen.getByText(/PRIVATE ••••••••••••/i)).toBeInTheDocument();
    expect(screen.queryByText("DE89370400440532013000")).not.toBeInTheDocument();

    // 5. Test PERSONAL on-demand detail fetch: Click "View"
    const viewButton = screen.getByRole("button", { name: /view/i });
    fireEvent.click(viewButton);

    await waitFor(() => {
      expect(screen.getByText("Staff Software Engineer")).toBeInTheDocument();
    });
    // Detail is temporarily cached while viewed
    expect(queryClient.getQueryData(["context", "facts", "detail", "fact-personal-01"])).toBeDefined();

    // Click "Hide": purges detail query from cache
    const hideButton = screen.getByRole("button", { name: /hide/i });
    fireEvent.click(hideButton);

    await waitFor(() => {
      expect(screen.queryByText("Staff Software Engineer")).not.toBeInTheDocument();
      expect(screen.getByText(/Personal record · Detail on demand/i)).toBeInTheDocument();
    });
    expect(queryClient.getQueryData(["context", "facts", "detail", "fact-personal-01"])).toBeUndefined();

    // 6. Test SENSITIVE on-demand reveal: Click "Reveal"
    const revealButton = screen.getByRole("button", { name: /reveal/i });
    fireEvent.click(revealButton);

    await waitFor(() => {
      expect(screen.getByText("DE89370400440532013000")).toBeInTheDocument();
    });
    expect(queryClient.getQueryData(["context", "facts", "detail", "fact-sensitive-01"])).toBeDefined();

    // Click "Mask": purges sensitive detail query from cache
    const maskButton = screen.getByRole("button", { name: /mask/i });
    fireEvent.click(maskButton);

    await waitFor(() => {
      expect(screen.queryByText("DE89370400440532013000")).not.toBeInTheDocument();
      expect(screen.getByText(/PRIVATE ••••••••••••/i)).toBeInTheDocument();
    });
    expect(queryClient.getQueryData(["context", "facts", "detail", "fact-sensitive-01"])).toBeUndefined();

    // 7. Verify localStorage and sessionStorage never contain sensitive or personal values
    expect(JSON.stringify(localStorage)).not.toContain("DE89370400440532013000");
    expect(JSON.stringify(localStorage)).not.toContain("Staff Software Engineer");
    expect(JSON.stringify(sessionStorage)).not.toContain("DE89370400440532013000");
    expect(JSON.stringify(sessionStorage)).not.toContain("Staff Software Engineer");

    // 8. Re-open details and test unmount purge
    fireEvent.click(screen.getByRole("button", { name: /view/i }));
    fireEvent.click(screen.getByRole("button", { name: /reveal/i }));
    await waitFor(() => {
      expect(screen.getByText("Staff Software Engineer")).toBeInTheDocument();
      expect(screen.getByText("DE89370400440532013000")).toBeInTheDocument();
    });

    unmount();
    expect(queryClient.getQueryData(["context", "facts", "detail", "fact-personal-01"])).toBeUndefined();
    expect(queryClient.getQueryData(["context", "facts", "detail", "fact-sensitive-01"])).toBeUndefined();
  });

  it("exposes inline Update action, calls supersede with ETag, and handles 412 concurrency", async () => {
    let supersedeCallHeaders: HeadersInit | undefined;
    let supersedeCallBody: any;
    let return412 = false;

    global.fetch = vi.fn().mockImplementation((url: string, init: any) => {
      if (url.includes("/context/facts/fact-personal-01/supersede")) {
        supersedeCallHeaders = init?.headers;
        supersedeCallBody = JSON.parse(init?.body || "{}");

        if (return412) {
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
          });
        }

        return Promise.resolve({
          ok: true,
          status: 200,
          headers: new Headers({ ETag: '"fact:fact-personal-02:v1"' }),
          json: async () => ({
            fact_id: "fact-personal-02",
            namespace: "career",
            key: "title",
            sensitivity: "personal",
            verification_status: "unverified",
            value: "Principal Systems Architect",
            version: 1,
          }),
        });
      }
      if (url.includes("/context/facts")) {
        return Promise.resolve({
          ok: true,
          status: 200,
          headers: new Headers(),
          json: async () => mockFactsSummary,
        });
      }
      if (url.includes("/context/readiness")) {
        return Promise.resolve({
          ok: true,
          status: 200,
          headers: new Headers(),
          json: async () => mockReadiness,
        });
      }
      return Promise.reject(new Error("Unknown route: " + url));
    });

    render(
      <QueryClientProvider client={queryClient}>
        <ContextView />
      </QueryClientProvider>
    );

    await waitFor(() => {
      expect(screen.getByText("career.title")).toBeInTheDocument();
    });

    // Click "Update" on career.title
    const careerRow = screen.getByText("career.title").closest("div[class*='py-4']");
    expect(careerRow).not.toBeNull();
    const updateButton = within(careerRow as HTMLElement).getByRole("button", { name: /^update$/i });
    fireEvent.click(updateButton);

    // Verify inline editor form appears
    await waitFor(() => {
      expect(
        screen.getByText(/SUPERSEDE FACT \/\/ HISTORICAL LINEAGE PRESERVED/i)
      ).toBeInTheDocument();
    });

    // Fill in new replacement value and reason
    const replacementInput = screen.getByPlaceholderText(/Enter updated replacement value/i);
    fireEvent.change(replacementInput, { target: { value: "Principal Systems Architect" } });

    const reasonInput = screen.getByPlaceholderText(/e\.g\. Promotion, corrected title/i);
    fireEvent.change(reasonInput, { target: { value: "Annual Promotion 2026" } });

    // Submit update
    const saveButton = screen.getByRole("button", { name: /save update →/i });
    fireEvent.click(saveButton);

    await waitFor(() => {
      expect(supersedeCallBody).toBeDefined();
    });

    expect(supersedeCallBody.new_value).toBe("Principal Systems Architect");
    expect(supersedeCallBody.reason).toBe("Annual Promotion 2026");
    expect(supersedeCallBody.new_confidence).toBe(1);

    const ifMatch = supersedeCallHeaders instanceof Headers
      ? supersedeCallHeaders.get("If-Match")
      : (supersedeCallHeaders as any)["If-Match"];
    expect(ifMatch).toBe('"fact:fact-personal-01:v1"');

    // Now test 412 concurrency handling
    return412 = true;
    fireEvent.click(updateButton);
    const input2 = screen.getByPlaceholderText(/Enter updated replacement value/i);
    fireEvent.change(input2, { target: { value: "Chief Architect" } });
    fireEvent.click(screen.getByRole("button", { name: /save update →/i }));

    await waitFor(() => {
      expect(
        screen.getByText(/This fact was modified elsewhere\. We've loaded the latest version\. Please review before updating\./i)
      ).toBeInTheDocument();
    });
  });
});
