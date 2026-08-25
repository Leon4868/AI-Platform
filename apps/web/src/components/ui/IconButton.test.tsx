import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { IconButton } from "./IconButton";

describe("IconButton", () => {
  it.each([
    ["top", "bottom-[calc(100%+8px)]"],
    ["right", "left-[calc(100%+8px)]"],
    ["bottom", "top-[calc(100%+8px)]"],
    ["left", "right-[calc(100%+8px)]"],
  ] as const)("positions its tooltip on the %s side", (placement, expectedClass) => {
    render(<IconButton label={`提示-${placement}`} icon={<span>图标</span>} tooltipPlacement={placement} />);

    const tooltip = screen.getByRole("tooltip");
    expect(tooltip).toHaveAttribute("data-placement", placement);
    expect(tooltip).toHaveClass(expectedClass);
  });

  it("defaults to a right-side tooltip", () => {
    render(<IconButton label="默认提示" icon={<span>图标</span>} />);

    expect(screen.getByRole("tooltip")).toHaveAttribute("data-placement", "right");
  });
});
