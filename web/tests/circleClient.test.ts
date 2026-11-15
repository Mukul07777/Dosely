import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

// A minimal browser: localStorage and fetch, so the care circle client can run under Node.
class MemoryStorage {
  private data = new Map<string, string>();
  getItem(k: string) { return this.data.has(k) ? this.data.get(k)! : null; }
  setItem(k: string, v: string) { this.data.set(k, String(v)); }
  removeItem(k: string) { this.data.delete(k); }
  clear() { this.data.clear(); }
}

type Call = { url: string; init: RequestInit };
let calls: Call[] = [];
let reply: (url: string) => { status: number; body: unknown };

beforeEach(() => {
  calls = [];
  reply = () => ({ status: 200, body: {} });
  vi.stubGlobal("localStorage", new MemoryStorage());
  vi.stubGlobal("fetch", vi.fn(async (url: string, init: RequestInit = {}) => {
    calls.push({ url, init });
    const r = reply(url);
    return new Response(JSON.stringify(r.body), { status: r.status, headers: { "Content-Type": "application/json" } });
  }));
});
afterEach(() => vi.unstubAllGlobals());

const headersOf = (c: Call) => new Headers(c.init.headers as HeadersInit);

describe("passcode session token", () => {
  it("is stored per profile, case-insensitively, and can be cleared", async () => {
    const { getToken, setToken } = await import("@/lib/circle");
    setToken("kc-abc123", "tok-1");
    expect(getToken("KC-ABC123")).toBe("tok-1");
    expect(getToken("KC-OTHER1")).toBeNull();
    setToken("KC-ABC123", null);
    expect(getToken("kc-abc123")).toBeNull();
  });

  it("survives a browser that blocks storage (private mode)", async () => {
    vi.stubGlobal("localStorage", { getItem: () => { throw new Error("blocked"); }, setItem: () => { throw new Error("blocked"); } });
    const { getToken, setToken } = await import("@/lib/circle");
    expect(() => setToken("KC-ABC123", "t")).not.toThrow();
    expect(getToken("KC-ABC123")).toBeNull();
  });
});

describe("care circle requests", () => {
  it("unlock saves the token, then every profile call sends it with the CSRF header", async () => {
    reply = (url) => (url.endsWith("/api/circle/unlock")
      ? { status: 200, body: { token: "secret-token", profile: "KC-ABC123" } }
      : { status: 200, body: { ok: true } });
    const { unlock, tick, getToken } = await import("@/lib/circle");
    const code = await unlock({ code: "kc-abc123", passcode: "482913", name: "Rahul", role: "family" });
    expect(code).toBe("KC-ABC123");
    expect(getToken("KC-ABC123")).toBe("secret-token");

    await tick("KC-ABC123", { med_id: 7, slot: "morning", taken: true });
    const last = calls.at(-1)!;
    expect(last.url).toBe("/api/circle/p/KC-ABC123/doses");
    expect(last.init.method).toBe("POST");
    expect(headersOf(last).get("X-Circle-Token")).toBe("secret-token");
    expect(headersOf(last).get("X-KinCare")).toBe("1");
    expect(JSON.parse(String(last.init.body))).toEqual({ med_id: 7, slot: "morning", taken: true });
  });

  it("never sends one profile's token to another profile", async () => {
    const { setToken, view } = await import("@/lib/circle");
    setToken("KC-AAAAAA", "token-a");
    await view("KC-BBBBBB");
    expect(headersOf(calls.at(-1)!).get("X-Circle-Token")).toBeNull();
  });

  it("shows the server's own message when a request is refused", async () => {
    reply = () => ({ status: 403, body: { detail: "Savitri has given you view-only access." } });
    const { addRoutine } = await import("@/lib/circle");
    await expect(addRoutine("KC-ABC123", { title: "Walk", time: "07:00" })).rejects.toThrow("view-only access");
  });

  it("uses PUT for an edit and POST for a new medicine", async () => {
    const { saveMed } = await import("@/lib/circle");
    await saveMed("KC-ABC123", { name: "Telma" });
    await saveMed("KC-ABC123", { strength: "80 mg" }, 5);
    expect(calls.map((c) => [c.init.method, c.url])).toEqual([
      ["POST", "/api/circle/p/KC-ABC123/meds"],
      ["PUT", "/api/circle/p/KC-ABC123/meds/5"],
    ]);
  });

  it("leaving the circle forgets the token even if the server is unreachable", async () => {
    const { leave, setToken, getToken } = await import("@/lib/circle");
    setToken("KC-ABC123", "t");
    vi.stubGlobal("fetch", vi.fn(async () => { throw new TypeError("Failed to fetch"); }));
    await expect(leave("KC-ABC123")).rejects.toThrow();
    expect(getToken("KC-ABC123")).toBeNull();
  });
});
