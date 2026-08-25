import { act, renderHook } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { useWorkspaceState } from "./useWorkspaceState";

describe("useWorkspaceState keyboard focus", () => {
  it("sends slash to node search while the left drawer is open", () => {
    const { result } = renderHook(() => useWorkspaceState());
    const search = document.createElement("input");
    const command = document.createElement("input");
    document.body.append(search, command);
    result.current.nodeSearchRef.current = search;
    result.current.commandRef.current = command;

    act(() => window.dispatchEvent(new KeyboardEvent("keydown", { key: "/", cancelable: true })));
    expect(search).toHaveFocus();

    search.remove();
    command.remove();
  });

  it("sends slash to the command bar when the node drawer is closed", () => {
    const { result } = renderHook(() => useWorkspaceState());
    const search = document.createElement("input");
    const command = document.createElement("input");
    document.body.append(search, command);
    result.current.nodeSearchRef.current = search;
    result.current.commandRef.current = command;

    act(() => result.current.dispatch({ type: "toggle-left" }));
    act(() => window.dispatchEvent(new KeyboardEvent("keydown", { key: "/", cancelable: true })));
    expect(command).toHaveFocus();

    search.remove();
    command.remove();
  });
});
