import { describe, expect, it } from "vitest";

import { isBrowserDownloadUri } from "./download";

describe("isBrowserDownloadUri", () => {
  it.each(["/api/v1/assets/downloads/token", "https://objects.example/draft", "http://localhost:8000/draft"])("allows a supported browser URI: %s", (uri) => {
    expect(isBrowserDownloadUri(uri)).toBe(true);
  });

  it.each([undefined, "", "memory:tenant/asset", "javascript:alert(1)", "/untrusted/path"])("rejects an unsupported URI: %s", (uri) => {
    expect(isBrowserDownloadUri(uri)).toBe(false);
  });
});
