import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import {
  apiRequest,
  ApiError,
  PreconditionFailedError,
  PreconditionRequiredError,
  setDevUser,
} from "../../src/api/client";

describe("API Client & ETag Management", () => {
  const originalFetch = global.fetch;

  beforeEach(() => {
    localStorage.clear();
  });

  afterEach(() => {
    global.fetch = originalFetch;
    vi.restoreAllMocks();
  });

  it("extracts ETag from response headers and returns data", async () => {
    const mockHeaders = new Headers();
    mockHeaders.set("ETag", '"case:123:v2"');

    global.fetch = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      headers: mockHeaders,
      json: async () => ({ case_id: "123", status: "active" }),
    } as any);

    const result = await apiRequest<{ case_id: string; status: string }>("/cases/123");

    expect(result.data).toEqual({ case_id: "123", status: "active" });
    expect(result.etag).toBe('"case:123:v2"');
  });

  it("sends If-Match header when expectedETag is passed", async () => {
    const mockHeaders = new Headers();
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      headers: mockHeaders,
      json: async () => ({ success: true }),
    } as any);
    global.fetch = fetchMock;

    await apiRequest("/cases/123/transition", {
      method: "POST",
      body: JSON.stringify({ to_status: "resolved" }),
    }, '"case:123:v2"');

    expect(fetchMock).toHaveBeenCalled();
    const callArgs = fetchMock.mock.calls[0];
    const headers = callArgs[1].headers as Headers;
    expect(headers.get("If-Match")).toBe('"case:123:v2"');
  });

  it("throws PreconditionFailedError on 412 status code", async () => {
    const mockHeaders = new Headers();
    global.fetch = vi.fn().mockResolvedValue({
      ok: false,
      status: 412,
      headers: mockHeaders,
      json: async () => ({
        type: "about:blank",
        title: "Precondition Failed",
        status: 412,
        detail: "Resource was modified by another transaction",
      }),
    } as any);

    await expect(
      apiRequest("/cases/123", { method: "PATCH" }, '"case:123:v1"')
    ).rejects.toThrow(PreconditionFailedError);
  });

  it("throws PreconditionRequiredError on 428 status code", async () => {
    const mockHeaders = new Headers();
    global.fetch = vi.fn().mockResolvedValue({
      ok: false,
      status: 428,
      headers: mockHeaders,
      json: async () => ({
        type: "about:blank",
        title: "Precondition Required",
        status: 428,
        detail: "Precondition required. An ETag must be supplied.",
      }),
    } as any);

    await expect(
      apiRequest("/cases/123", { method: "PATCH" })
    ).rejects.toThrow(PreconditionRequiredError);
  });

  it("throws generic ApiError on other HTTP errors", async () => {
    const mockHeaders = new Headers();
    global.fetch = vi.fn().mockResolvedValue({
      ok: false,
      status: 404,
      headers: mockHeaders,
      json: async () => ({
        title: "Not Found",
        detail: "Case not found",
        status: 404,
      }),
    } as any);

    await expect(apiRequest("/cases/999")).rejects.toThrow(ApiError);
  });
});
