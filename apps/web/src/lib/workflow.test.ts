import { describe, expect, it } from "vitest";

import {
  createPaletteNode,
  isWorkflowConnected,
  paletteItems,
  parsePaletteItem,
  serializePaletteItem,
  workflowEdges,
  workflowNodes,
} from "./workflow";

describe("enterprise document workflow", () => {
  it("contains exactly 13 uniquely identified nodes", () => {
    expect(workflowNodes).toHaveLength(13);
    expect(new Set(workflowNodes.map(({ id }) => id)).size).toBe(13);
  });

  it("connects every node from the trigger", () => {
    expect(workflowNodes[0].id).toBe("trigger");
    expect(isWorkflowConnected(workflowNodes, workflowEdges)).toBe(true);
  });

  it("keeps the enterprise asset archive in the publication path", () => {
    expect(workflowEdges).toEqual(expect.arrayContaining([
      expect.objectContaining({ source: "document", target: "asset" }),
      expect.objectContaining({ source: "asset", target: "notify" }),
    ]));
  });

  it("round-trips palette drag data and creates a canvas node at the drop position", () => {
    const item = paletteItems[1];
    expect(parsePaletteItem(serializePaletteItem(item))).toEqual(item);
    expect(parsePaletteItem("not-json")).toBeNull();

    expect(createPaletteNode(item, { x: 128, y: 256 }, "new-node")).toEqual(expect.objectContaining({
      id: "new-node",
      type: "agent",
      position: { x: 128, y: 256 },
      data: expect.objectContaining({ label: "知识检索", status: "idle" }),
    }));
  });
});
