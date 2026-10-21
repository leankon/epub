import { describe, expect, it } from "vitest";
import { flatten, nest } from "@/components/BookmarkEditor";
import { displayToPdf, pdfToDisplay } from "@/components/RedactEditor";
import { formatBytes, progressPercent } from "@/lib/format";
import { duplicate, initialPages, isUnchanged, move, remove, rotate, toOrderPayload } from "@/lib/organizer";
import { parseRanges } from "@/lib/ranges";

describe("page ranges", () => {
  it("parses ranges, open ends and single pages", () => {
    expect(parseRanges("1-3, 5, 8-", 10)).toEqual({ ok: true, groups: [[0, 1, 2], [4], [7, 8, 9]] });
  });
  it("rejects invalid, reversed and out-of-range input", () => {
    expect(parseRanges("", 5).ok).toBe(false);
    expect(parseRanges("4-2", 5).ok).toBe(false);
    expect(parseRanges("0", 5).ok).toBe(false);
    expect(parseRanges("6", 5).ok).toBe(false);
    expect(parseRanges("a-b", 5).ok).toBe(false);
    expect(parseRanges("1,,2", 5).ok).toBe(false);
  });
});

describe("organizer", () => {
  it("reorders, rotates, duplicates and removes pages", () => {
    let p = initialPages(3);
    expect(isUnchanged(p, 3)).toBe(true);
    p = move(p, 0, 2);
    expect(toOrderPayload(p).map((x) => x.page)).toEqual([1, 2, 0]);
    p = rotate(p, 0, 90);
    p = rotate(p, 0, 90);
    p = rotate(p, 1, -90);
    expect(toOrderPayload(p)).toEqual([{ page: 1, rotate: 180 }, { page: 2, rotate: 270 }, { page: 0, rotate: 0 }]);
    p = duplicate(p, 2, 1);
    expect(p).toHaveLength(4);
    expect(new Set(p.map((x) => x.key)).size).toBe(4);
    p = remove(p, 0);
    expect(toOrderPayload(p).map((x) => x.page)).toEqual([2, 0, 0]);
    expect(isUnchanged(p, 3)).toBe(false);
  });
  it("ignores invalid moves", () => {
    const p = initialPages(2);
    expect(move(p, 0, 5)).toBe(p);
  });
});

describe("format helpers", () => {
  it("formats bytes", () => {
    expect(formatBytes(512)).toBe("512 B");
    expect(formatBytes(1536)).toBe("1.50 KB");
    expect(formatBytes(null)).toBe("—");
  });
  it("never invents progress without a total", () => {
    expect(progressPercent(3, null)).toBeNull();
    expect(progressPercent(null, 10)).toBeNull();
    expect(progressPercent(5, 10)).toBe(50);
    expect(progressPercent(20, 10)).toBe(100);
  });
});

describe("redaction coordinate mapping", () => {
  for (const rot of [0, 90, 180, 270]) {
    it(`round-trips display <-> PDF space at ${rot}°`, () => {
      const r = displayToPdf(0.1, 0.2, 0.4, 0.3, 612, 792, rot);
      const d = pdfToDisplay(r, 612, 792, rot);
      expect(d.left).toBeCloseTo(0.1, 3);
      expect(d.top).toBeCloseTo(0.2, 3);
      expect(d.width).toBeCloseTo(0.3, 3);
      expect(d.height).toBeCloseTo(0.1, 3);
    });
  }
  it("maps the top-left corner of an unrotated page to high y in PDF space", () => {
    const [x0, y0, x1, y1] = displayToPdf(0, 0, 0.5, 0.5, 600, 800, 0);
    expect([x0, y0, x1, y1]).toEqual([0, 400, 300, 800]);
  });
});

describe("bookmark nesting", () => {
  it("round-trips flat and nested forms", () => {
    const tree = [{ title: "A", page: 0, children: [{ title: "A.1", page: 1, children: [] }] }, { title: "B", page: 3, children: [] }];
    const flat = flatten(tree);
    expect(flat).toEqual([{ title: "A", page: 1, level: 0 }, { title: "A.1", page: 2, level: 1 }, { title: "B", page: 4, level: 0 }]);
    expect(nest(flat)).toEqual(tree);
  });
});
