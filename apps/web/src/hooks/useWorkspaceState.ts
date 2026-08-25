import { useEffect, useReducer, useRef } from "react";

export type CommandMode = "idle" | "focused" | "loading";

type WorkspaceState = {
  leftDrawerOpen: boolean;
  rightDrawerOpen: boolean;
  focusMode: boolean;
  selectedNodeId: string;
  commandMode: CommandMode;
};

type Action =
  | { type: "toggle-left" }
  | { type: "toggle-right" }
  | { type: "toggle-focus" }
  | { type: "select-node"; nodeId: string }
  | { type: "command-mode"; mode: CommandMode }
  | { type: "close-topmost" };

const initialState: WorkspaceState = {
  leftDrawerOpen: true,
  rightDrawerOpen: true,
  focusMode: false,
  selectedNodeId: "model",
  commandMode: "idle",
};

function reducer(state: WorkspaceState, action: Action): WorkspaceState {
  switch (action.type) {
    case "toggle-left": return { ...state, leftDrawerOpen: !state.leftDrawerOpen };
    case "toggle-right": return { ...state, rightDrawerOpen: !state.rightDrawerOpen };
    case "toggle-focus": return { ...state, focusMode: !state.focusMode };
    case "select-node": return { ...state, selectedNodeId: action.nodeId, rightDrawerOpen: true };
    case "command-mode": return { ...state, commandMode: action.mode };
    case "close-topmost":
      if (state.rightDrawerOpen) return { ...state, rightDrawerOpen: false };
      if (state.leftDrawerOpen) return { ...state, leftDrawerOpen: false };
      return state;
  }
}

export function useWorkspaceState() {
  const [state, dispatch] = useReducer(reducer, initialState);
  const commandRef = useRef<HTMLInputElement>(null);
  const nodeSearchRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      const target = event.target as HTMLElement | null;
      if (event.key === "/" && target?.tagName !== "INPUT" && target?.tagName !== "TEXTAREA") {
        event.preventDefault();
        if (state.leftDrawerOpen) nodeSearchRef.current?.focus();
        else commandRef.current?.focus();
      }
      if (event.key === "Escape") dispatch({ type: "close-topmost" });
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "k") {
        event.preventDefault();
        commandRef.current?.focus();
      }
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [state.leftDrawerOpen]);

  return { state, dispatch, commandRef, nodeSearchRef };
}
