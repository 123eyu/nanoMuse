import { describe, expect, it } from "vitest";
import { isBlobRef, partsOf, shaOf, toolArgs } from "./turns";

const REF = "blob:sha256:" + "a".repeat(64);

describe("the record's messages", () => {
  it("tells a blob reference from any other string", () => {
    expect(isBlobRef(REF)).toBe(true);
    expect(isBlobRef("blob:sha256:short")).toBe(false);
    expect(isBlobRef("data:image/png;base64,AAAA")).toBe(false);
    expect(isBlobRef(42)).toBe(false);
    expect(shaOf(REF)).toBe("a".repeat(64));
  });

  it("finds the text and every file in a message's content", () => {
    expect(partsOf("hello")).toEqual({ text: "hello", files: [] });
    expect(partsOf(null)).toEqual({ text: "", files: [] });
    const content = [
      { type: "text", text: "look" },
      { type: "image_url", image_url: { url: REF } },
      { type: "image_url", image_url: "https://example.invalid/a.png" },
      { type: "input_audio", input_audio: { data: REF, format: "wav" } },
      { type: "video", video: [REF, REF] },
      { type: "text", text: "at this" },
      { type: "image_url", image_url: { url: "data:image/png;base64,AAAA" } },
    ];
    expect(partsOf(content)).toEqual({ text: "look\nat this", files: [REF, "https://example.invalid/a.png", REF, REF, REF] });
  });

  it("lays out a tool call's arguments", () => {
    expect(toolArgs('{"path":"/tmp/a","n":2}')).toBe('{\n "path": "/tmp/a",\n "n": 2\n}');
    expect(toolArgs("not json")).toBe("not json");
    expect(toolArgs(undefined)).toBe("");
  });
});
