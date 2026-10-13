import { describe, expect, it } from "vitest";
import { checkSitemapUrl, parseSitemapUrlsCsv } from "./sitemapUrls";

const SITE = "https://www.example.com";

describe("checkSitemapUrl", () => {
  it("adds a scheme and accepts same-site URLs", () => {
    expect(checkSitemapUrl("example.com/a", SITE)).toEqual({ ok: true, url: "https://example.com/a" });
    expect(checkSitemapUrl("https://shop.example.com/b", SITE).ok).toBe(true);
    expect(checkSitemapUrl("http://www.example.com/c", SITE).ok).toBe(true);
  });

  it("rejects malformed and off-domain URLs", () => {
    expect(checkSitemapUrl("not a url", SITE)).toEqual({ ok: false, reason: "invalid" });
    expect(checkSitemapUrl("ftp://example.com/x", SITE)).toEqual({ ok: false, reason: "invalid" });
    expect(checkSitemapUrl("https://other.com/x", SITE)).toEqual({ ok: false, reason: "off-domain" });
    expect(checkSitemapUrl("https://notexample.com/x", SITE)).toEqual({ ok: false, reason: "off-domain" });
  });
});

describe("parseSitemapUrlsCsv", () => {
  it("reads a URL column from a header row", () => {
    const csv = "Title,URL\nHome,https://example.com/\nAbout,https://example.com/about\n";
    const res = parseSitemapUrlsCsv(csv, SITE);
    expect(res.urls).toEqual(["https://example.com/", "https://example.com/about"]);
    expect(res.warnings).toEqual([]);
    expect(res.error).toBeUndefined();
  });

  it("reads the first column when there is no header", () => {
    const res = parseSitemapUrlsCsv("﻿example.com/a\r\nexample.com/b", SITE);
    expect(res.urls).toEqual(["https://example.com/a", "https://example.com/b"]);
  });

  it("warns on invalid and off-domain rows and counts duplicates", () => {
    const csv = [
      "URL",
      "https://example.com/a",
      "https://example.com/a",
      "https://competitor.com/x",
      "bad url",
      "https://example.com/existing",
    ].join("\n");
    const res = parseSitemapUrlsCsv(csv, SITE, ["https://example.com/existing"]);
    expect(res.urls).toEqual(["https://example.com/a"]);
    expect(res.alreadyListed).toEqual(["https://example.com/existing"]);
    expect(res.duplicates).toBe(2);
    expect(res.warnings).toHaveLength(2);
    expect(res.warnings[0]).toContain("Row 4");
    expect(res.warnings[0]).toContain("does not match the website domain (example.com)");
    expect(res.warnings[1]).toContain("is not a valid URL");
  });

  it("errors when nothing valid remains", () => {
    expect(parseSitemapUrlsCsv("", SITE).error).toBe("CSV file is empty.");
    const res = parseSitemapUrlsCsv("URL\nhttps://other.com/", SITE);
    expect(res.error).toBe("No valid URLs found in the CSV.");
    expect(res.warnings).toHaveLength(1);
  });
});
