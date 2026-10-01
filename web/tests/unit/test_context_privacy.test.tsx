import React from "react";
import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { ContextView } from "../../src/views/ContextView";

describe("Context Vault Privacy & On-Demand Masking", () => {
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

  it("masks sensitive facts by default and only reveals them on-demand without local storage leaks", async () => {
    // Mock facts list returning a sanitized FactSummaryResponse (omitting value)
    const mockFactsSummary = {
      items: [
        {
          fact_id: "fact-sensitive-01",
          namespace: "finance",
          key: "iban",
          sensitivity: "sensitive",
          verification_status: "user_verified",
          preview: "DE89••••••••••••",
          created_at: "2026-10-01T00:00:00Z",
          updated_at: "2026-10-01T00:00:00Z",
        },
      ],
      total: 1,
    };

    // Mock readiness response
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

    // Mock detail endpoint returning the decrypted value
    const mockFactDetail = {
      fact_id: "fact-sensitive-01",
      namespace: "finance",
      key: "iban",
      sensitivity: "sensitive",
      verification_status: "user_verified",
      value: "DE89370400440532013000",
      source_reference: "bank_statement_2026.pdf",
      created_at: "2026-10-01T00:00:00Z",
      updated_at: "2026-10-01T00:00:00Z",
    };

    global.fetch = vi.fn().mockImplementation((url: string) => {
      if (url.includes("/context/facts/fact-sensitive-01")) {
        return Promise.resolve({
          ok: true,
          status: 200,
          headers: new Headers(),
          json: async () => mockFactDetail,
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

    // Verify sensitive value is NOT rendered initially
    await waitFor(() => {
      expect(screen.getByText("finance.iban")).toBeInTheDocument();
    });
    expect(screen.getByText(/PRIVATE ••••••••••••/i)).toBeInTheDocument();
    expect(screen.queryByText("DE89370400440532013000")).not.toBeInTheDocument();

    // Verify localStorage and sessionStorage contain no sensitive fact values
    expect(localStorage.getItem("finance.iban")).toBeNull();
    expect(sessionStorage.getItem("finance.iban")).toBeNull();
    expect(JSON.stringify(localStorage)).not.toContain("DE89370400440532013000");

    // Click "Reveal" button
    const revealButton = screen.getByRole("button", { name: /reveal/i });
    fireEvent.click(revealButton);

    // Verify the decrypted value appears on demand
    await waitFor(() => {
      expect(screen.getByText("DE89370400440532013000")).toBeInTheDocument();
    });

    // Verify even after reveal, sensitive values are NEVER persisted to localStorage or sessionStorage
    expect(JSON.stringify(localStorage)).not.toContain("DE89370400440532013000");
    expect(JSON.stringify(sessionStorage)).not.toContain("DE89370400440532013000");

    // Click "Mask" to hide again
    const maskButton = screen.getByRole("button", { name: /mask/i });
    fireEvent.click(maskButton);

    await waitFor(() => {
      expect(screen.getByText(/PRIVATE ••••••••••••/i)).toBeInTheDocument();
      expect(screen.queryByText("DE89370400440532013000")).not.toBeInTheDocument();
    });
  });
});
