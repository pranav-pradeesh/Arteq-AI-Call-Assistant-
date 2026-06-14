import { describe, it, expect, vi, beforeEach } from "vitest";
import { api, setAuthToken, ApiError } from "@/lib/api";

// Helper to create a fake Response object
function fakeResponse(status: number, body: unknown): Response {
  return {
    ok: status >= 200 && status < 300,
    status,
    statusText: status === 500 ? "Internal Server Error" : String(status),
    json: async () => body,
  } as unknown as Response;
}

describe("api", () => {
  beforeEach(() => {
    vi.resetAllMocks();
    // Set a cached token so resolveToken() returns it directly without calling getSession()
    setAuthToken("test-token");
    vi.stubGlobal("fetch", vi.fn());
  });

  describe("listHospitals", () => {
    it("calls fetch with correct URL and Authorization header", async () => {
      const mockFetch = vi.mocked(global.fetch);
      mockFetch.mockResolvedValueOnce(fakeResponse(200, []));

      await api.listHospitals();

      expect(mockFetch).toHaveBeenCalledOnce();
      const [url, opts] = mockFetch.mock.calls[0];
      expect(String(url)).toMatch(/\/admin\/api\/hospitals$/);
      const headers = (opts as RequestInit).headers as Record<string, string>;
      expect(headers["Authorization"]).toBe("Bearer test-token");
    });

    it("uses GET method (no explicit method means GET)", async () => {
      const mockFetch = vi.mocked(global.fetch);
      mockFetch.mockResolvedValueOnce(fakeResponse(200, []));

      await api.listHospitals();

      const [, opts] = mockFetch.mock.calls[0];
      // GET requests have no explicit method set in the implementation
      const method = (opts as RequestInit).method;
      expect(method === undefined || method === "GET").toBe(true);
    });
  });

  describe("createHospital", () => {
    it("issues POST with correct URL and JSON body", async () => {
      const mockFetch = vi.mocked(global.fetch);
      mockFetch.mockResolvedValueOnce(fakeResponse(200, { id: "1", name: "X" }));

      await api.createHospital({ name: "X" });

      expect(mockFetch).toHaveBeenCalledOnce();
      const [url, opts] = mockFetch.mock.calls[0];
      expect(String(url)).toMatch(/\/admin\/api\/hospitals$/);
      expect((opts as RequestInit).method).toBe("POST");
      expect((opts as RequestInit).body).toBe('{"name":"X"}');
    });
  });

  describe("error handling", () => {
    it("rejects with ApiError on 500 response", async () => {
      const mockFetch = vi.mocked(global.fetch);
      mockFetch.mockResolvedValueOnce(fakeResponse(500, { detail: "boom" }));

      await expect(api.listHospitals()).rejects.toSatisfy((err: unknown) => {
        return err instanceof ApiError && err.status === 500 && err.message === "boom";
      });
    });

    it("ApiError has correct status and message", async () => {
      const mockFetch = vi.mocked(global.fetch);
      mockFetch.mockResolvedValueOnce(fakeResponse(500, { detail: "boom" }));

      let caught: ApiError | undefined;
      try {
        await api.listHospitals();
      } catch (e) {
        caught = e as ApiError;
      }

      expect(caught).toBeInstanceOf(ApiError);
      expect(caught!.status).toBe(500);
      expect(caught!.message).toBe("boom");
    });
  });

  describe("204 No Content", () => {
    it("resolves to undefined for a 204 response", async () => {
      const mockFetch = vi.mocked(global.fetch);
      mockFetch.mockResolvedValueOnce({
        ok: true,
        status: 204,
        statusText: "No Content",
        json: async () => { throw new Error("no body"); },
      } as unknown as Response);

      const result = await api.deleteDepartment("h1", "d1");

      expect(result).toBeUndefined();
    });

    it("calls the correct URL for deleteDepartment", async () => {
      const mockFetch = vi.mocked(global.fetch);
      mockFetch.mockResolvedValueOnce({
        ok: true,
        status: 204,
        statusText: "No Content",
        json: async () => { throw new Error("no body"); },
      } as unknown as Response);

      await api.deleteDepartment("hosp1", "dept2");

      const [url] = mockFetch.mock.calls[0];
      expect(String(url)).toMatch(/\/admin\/api\/hospitals\/hosp1\/departments\/dept2$/);
    });
  });
});
