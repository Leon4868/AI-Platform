import { describe, expect, it } from "vitest";

import { isWorkflowConnected, workflowEdges, workflowNodes } from "./workflow";

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
});
