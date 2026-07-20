/**
 * Task 11.5 — Literature search contract test.
 *
 * HOSTILE-AUDITOR PASS (v129, TM11): verifies the /api/literature/search
 * route accepts BOTH query contracts (?q=… and ?drug=…&disease=…)
 * and returns the structured fields the V1 criterion requires
 * (count, pmids, abstracts).
 *
 * The test mocks the pubmed service (searchPubMed + getAbstract) so
 * it does NOT hit the real NCBI API. The mock returns canned data
 * that matches the real PubMed response shape — so the test verifies
 * the route's query-handling + response-shaping logic, not PubMed's
 * availability.
 */
import { searchPubMed, getAbstract } from "@/lib/services/pubmed";

jest.mock("@/lib/services/pubmed", () => ({
  searchPubMed: jest.fn(),
  getAbstract: jest.fn(),
  truncateAbstract: jest.fn((text) => ({ text, truncated: false, fullLength: text?.length ?? 0 })),
}));

jest.mock("@/lib/auth/api-proxy-guard", () => ({
  requireAuthAndRateLimit: jest.fn().mockResolvedValue({
    user: { userId: "u1", orgId: "org1", email: "test@test", role: "researcher" },
    ip: "127.0.0.1",
    response: null,
  }),
  recordApiRequestForUser: jest.fn(),
}));

// Mock NextRequest construction.
function buildReq(url: string): any {
  const u = new URL(url);
  return {
    nextUrl: {
      searchParams: u.searchParams,
      origin: u.origin,
    },
    headers: new Headers(),
  };
}

describe("Task 11.5: /api/literature/search contract", () => {
  beforeEach(() => {
    jest.clearAllMocks();
    (searchPubMed as jest.Mock).mockResolvedValue({
      total: 2,
      articles: [
        { pmid: "12345", title: "Aspirin in cancer", journal: "J Oncol", authors: ["Smith"], pubDate: "2024", url: "https://pubmed.ncbi.nlm.nih.gov/12345/" },
        { pmid: "67890", title: "Aspirin mechanisms", journal: "Nature", authors: ["Jones"], pubDate: "2023", url: "https://pubmed.ncbi.nlm.nih.gov/67890/" },
      ],
    });
    (getAbstract as jest.Mock).mockResolvedValue("Aspirin shows promise in cancer treatment...");
  });

  test("Accepts ?q=<free-text> (backwards-compat contract)", async () => {
    const { GET } = require("@/app/api/literature/search/route");
    const req = buildReq("http://localhost:3000/api/literature/search?q=aspirin%20cancer");
    const res = await GET(req);
    expect(res.status).toBe(200);
    const body = await res.json();
    expect(body.items).toHaveLength(2);
    expect(body.total).toBe(2);
    expect(body.pmids).toEqual(["12345", "67890"]);
    expect(body.count).toBe(2);
    expect(body.querySource).toBe("free_text");
    // Abstracts are NOT fetched for free-text queries (only drug+disease).
    expect(body.abstracts).toEqual([]);
    expect(getAbstract).not.toHaveBeenCalled();
  });

  test("Accepts ?drug=…&disease=… AND fetches top-5 abstracts (V1 criterion support)", async () => {
    const { GET } = require("@/app/api/literature/search/route");
    const req = buildReq("http://localhost:3000/api/literature/search?drug=aspirin&disease=cancer");
    const res = await GET(req);
    expect(res.status).toBe(200);
    const body = await res.json();
    expect(body.items).toHaveLength(2);
    expect(body.pmids).toEqual(["12345", "67890"]);
    expect(body.count).toBe(2);
    expect(body.querySource).toBe("drug_disease");
    // The query should be built from drug+disease as a Title/Abstract phrase query.
    expect(body.query).toContain('"aspirin"[Title/Abstract]');
    expect(body.query).toContain('"cancer"[Title/Abstract]');
    expect(body.query).toContain(" AND ");
    // Top-5 abstracts should be fetched via getAbstract.
    expect(getAbstract).toHaveBeenCalledTimes(2); // 2 articles in mock
    expect(body.abstracts).toHaveLength(2);
    expect(body.abstracts[0].pmid).toBe("12345");
    expect(body.abstracts[0].abstract).toContain("Aspirin shows promise");
  });

  test("Returns 400 when neither ?q=… nor ?drug=…&disease=… is provided", async () => {
    const { GET } = require("@/app/api/literature/search/route");
    const req = buildReq("http://localhost:3000/api/literature/search");
    const res = await GET(req);
    expect(res.status).toBe(400);
    const body = await res.json();
    expect(body.error).toBe("bad_request");
  });

  test("Returns 400 when ?q= is less than 2 chars", async () => {
    const { GET } = require("@/app/api/literature/search/route");
    const req = buildReq("http://localhost:3000/api/literature/search?q=a");
    const res = await GET(req);
    expect(res.status).toBe(400);
  });

  test("Sanitizes PubMed query syntax in drug/disease names (injection guard)", async () => {
    const { GET } = require("@/app/api/literature/search/route");
    // An attacker passes a drug name with PubMed query syntax chars
    // (double-quotes + boolean OR). The route strips the double-quotes
    // and wraps the result in its OWN double-quotes — so the attacker's
    // `OR` becomes a LITERAL phrase inside the quoted search term, not
    // a PubMed boolean operator. PubMed will search for the literal
    // phrase "aspirin OR ibuprofen" (which will return ~0 results)
    // instead of returning aspirin OR ibuprofen articles.
    const req = buildReq(
      "http://localhost:3000/api/literature/search?drug=aspirin%22%20OR%20%22ibuprofen&disease=cancer",
    );
    const res = await GET(req);
    expect(res.status).toBe(200);
    const body = await res.json();
    // Security guarantee: the attacker's boolean OR must be INSIDE a
    // quoted phrase (so it's a literal, not an operator). The query
    // should be a single quoted phrase for the drug + a single quoted
    // phrase for the disease, joined by AND.
    //
    // The drug phrase should be: "aspirin OR ibuprofen"[Title/Abstract]
    // (the attacker's OR is now literal text inside the phrase).
    expect(body.query).toContain('"aspirin OR ibuprofen"[Title/Abstract]');
    expect(body.query).toContain('"cancer"[Title/Abstract]');
    // The query should have exactly ONE ` AND ` (the joiner) — NOT
    // multiple ANDs that would indicate the attacker's input was
    // parsed as multiple clauses.
    const andCount = (body.query.match(/\bAND\b/g) || []).length;
    expect(andCount).toBe(1);
    // The legitimate "cancer" term should be present.
    expect(body.query).toContain("cancer");
  });

  test("Accepts ?drug=… alone (disease optional)", async () => {
    const { GET } = require("@/app/api/literature/search/route");
    const req = buildReq("http://localhost:3000/api/literature/search?drug=aspirin");
    const res = await GET(req);
    expect(res.status).toBe(200);
    const body = await res.json();
    expect(body.querySource).toBe("drug_disease");
    expect(body.query).toContain('"aspirin"[Title/Abstract]');
    expect(body.query).not.toContain("AND");
  });

  test("Accepts ?disease=… alone (drug optional)", async () => {
    const { GET } = require("@/app/api/literature/search/route");
    const req = buildReq("http://localhost:3000/api/literature/search?disease=cancer");
    const res = await GET(req);
    expect(res.status).toBe(200);
    const body = await res.json();
    expect(body.querySource).toBe("drug_disease");
    expect(body.query).toContain('"cancer"[Title/Abstract]');
  });

  test("?q= takes precedence over ?drug=&disease= when both are provided", async () => {
    const { GET } = require("@/app/api/literature/search/route");
    const req = buildReq(
      "http://localhost:3000/api/literature/search?q=metformin&drug=aspirin&disease=cancer",
    );
    const res = await GET(req);
    expect(res.status).toBe(200);
    const body = await res.json();
    expect(body.querySource).toBe("free_text");
    expect(body.query).toBe("metformin");
  });
});
