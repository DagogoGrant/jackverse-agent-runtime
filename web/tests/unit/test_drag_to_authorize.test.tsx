import React from "react";
import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { DragToAuthorize } from "../../src/components/ui/DragToAuthorize";

describe("DragToAuthorize Tactile Control", () => {
  it("does not authorize on partial drag under threshold and resets", () => {
    const onAuthorize = vi.fn();
    const { container } = render(
      <DragToAuthorize
        onAuthorize={onAuthorize}
        label="Drag to authorize payment"
      />
    );

    const slider = screen.getByRole("slider");
    // Mock getBoundingClientRect
    vi.spyOn(slider, "getBoundingClientRect").mockReturnValue({
      width: 400,
      height: 56,
      top: 0,
      left: 0,
      right: 400,
      bottom: 56,
      x: 0,
      y: 0,
      toJSON: () => {},
    });

    // Start drag at x=0
    fireEvent.pointerDown(slider, { clientX: 0, pointerId: 1 });
    // Move to 100px (100 / (400 - 48) = ~28%, well below 88%)
    fireEvent.pointerMove(slider, { clientX: 100, pointerId: 1 });
    // Release pointer
    fireEvent.pointerUp(slider);

    expect(onAuthorize).not.toHaveBeenCalled();
    expect(slider).toHaveAttribute("aria-valuenow", "0");
  });

  it("authorizes when drag reaches threshold (>= 88%)", async () => {
    const onAuthorize = vi.fn().mockResolvedValue(undefined);
    render(
      <DragToAuthorize
        onAuthorize={onAuthorize}
        label="Drag to authorize submission"
      />
    );

    const slider = screen.getByRole("slider");
    vi.spyOn(slider, "getBoundingClientRect").mockReturnValue({
      width: 400,
      height: 56,
      top: 0,
      left: 0,
      right: 400,
      bottom: 56,
      x: 0,
      y: 0,
      toJSON: () => {},
    });

    // Start drag at x=0
    fireEvent.pointerDown(slider, { clientX: 0, pointerId: 1 });
    // Move to 350px (350 / 352 = ~99%, >= 88%)
    const moveEvent = new MouseEvent("pointermove", { bubbles: true, cancelable: true });
    Object.defineProperty(moveEvent, "clientX", { value: 350 });
    fireEvent(slider, moveEvent);

    await waitFor(() => {
      expect(onAuthorize).toHaveBeenCalledTimes(1);
    });
  });

  it("authorizes via accessible keyboard trigger (Enter or Space)", async () => {
    const onAuthorize = vi.fn().mockResolvedValue(undefined);
    render(
      <DragToAuthorize
        onAuthorize={onAuthorize}
        label="Drag to confirm"
      />
    );

    const slider = screen.getByRole("slider");
    fireEvent.keyDown(slider, { key: "Enter" });

    await waitFor(() => {
      expect(onAuthorize).toHaveBeenCalledTimes(1);
    });
  });

  it("prevents double-submit lockout while authorization is in flight", async () => {
    let resolveAuth: () => void = () => {};
    const onAuthorize = vi.fn().mockImplementation(
      () => new Promise<void>((resolve) => { resolveAuth = resolve; })
    );

    render(
      <DragToAuthorize
        onAuthorize={onAuthorize}
        label="Drag to execute"
      />
    );

    const slider = screen.getByRole("slider");
    // Trigger first authorization
    fireEvent.keyDown(slider, { key: " " });
    expect(onAuthorize).toHaveBeenCalledTimes(1);

    // Attempt second trigger while pending
    fireEvent.keyDown(slider, { key: "Enter" });
    expect(onAuthorize).toHaveBeenCalledTimes(1);

    // Complete async action
    resolveAuth();
    await waitFor(() => {
      expect(screen.getByText(/Authorized/i)).toBeInTheDocument();
    });
  });

  it("provides standard button fallback when requested", async () => {
    const onAuthorize = vi.fn().mockResolvedValue(undefined);
    render(
      <DragToAuthorize
        onAuthorize={onAuthorize}
        label="Authorize action"
      />
    );

    const toggleButton = screen.getByRole("button", { name: /standard button fallback/i });
    fireEvent.click(toggleButton);

    const fallbackBtn = screen.getByRole("button", { name: /authorize consequential action/i });
    expect(fallbackBtn).toBeInTheDocument();

    fireEvent.click(fallbackBtn);
    await waitFor(() => {
      expect(onAuthorize).toHaveBeenCalledTimes(1);
    });
  });
});
