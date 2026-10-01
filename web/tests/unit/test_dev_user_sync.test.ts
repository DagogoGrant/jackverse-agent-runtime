import { describe, it, expect, beforeEach, vi } from "vitest";
import { renderHook, act } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import React from "react";
import { useDevUserSync } from "../../src/hooks/useCaseworker";
import { setDevUser, getDevUser } from "../../src/api/client";

// Set env var so isDevAuthEnabled returns true
vi.stubEnv("VITE_JACKVERSE_DEV_AUTH", "true");

describe("Dev User Sync & Cache Discipline", () => {
  let queryClient: QueryClient;

  beforeEach(() => {
    localStorage.clear();
    queryClient = new QueryClient();
  });

  it("updates local dev user state and emits change event", () => {
    const listener = vi.fn();
    window.addEventListener("jv:dev_user_change", listener);

    setDevUser("bob");

    expect(localStorage.getItem("jv_dev_user")).toBe("bob");
    expect(getDevUser()).toBe("bob");
    expect(listener).toHaveBeenCalled();

    window.removeEventListener("jv:dev_user_change", listener);
  });

  it("clears queryClient cache immediately when dev user is switched", () => {
    const wrapper = ({ children }: { children: React.ReactNode }) =>
      React.createElement(QueryClientProvider, { client: queryClient }, children);

    renderHook(() => useDevUserSync(), { wrapper });

    // Seed query cache with dummy data
    queryClient.setQueryData(["missions"], [{ mission_id: "m-1", title: "Alice Mission" }]);
    queryClient.setQueryData(["claims"], [{ claim_id: "c-1", user_id: "alice" }]);
    expect(queryClient.getQueryData(["missions"])).toBeDefined();

    // Clear spy
    const clearSpy = vi.spyOn(queryClient, "clear");

    // Switch dev user
    act(() => {
      setDevUser("bob");
    });

    expect(clearSpy).toHaveBeenCalledTimes(1);
    expect(queryClient.getQueryData(["missions"])).toBeUndefined();
    expect(queryClient.getQueryData(["claims"])).toBeUndefined();
  });
});
