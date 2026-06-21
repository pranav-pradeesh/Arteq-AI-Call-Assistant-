"use client";

export default function GlobalError({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  return (
    <html lang="en">
      <body
        style={{
          margin: 0,
          minHeight: "100vh",
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          fontFamily:
            "system-ui, -apple-system, Segoe UI, Roboto, Helvetica, Arial, sans-serif",
          background: "#f9fafb",
          color: "#111827",
        }}
      >
        <div style={{ textAlign: "center", padding: "24px", maxWidth: "28rem" }}>
          <h1 style={{ margin: "0 0 8px", fontSize: "1.25rem", fontWeight: 600 }}>
            Something went wrong
          </h1>
          <p style={{ margin: "0 0 20px", fontSize: "0.875rem", color: "#6b7280" }}>
            A critical error occurred and the page could not be displayed.
          </p>
          <button
            type="button"
            onClick={() => reset()}
            style={{
              cursor: "pointer",
              padding: "8px 16px",
              fontSize: "0.875rem",
              fontWeight: 500,
              color: "#111827",
              background: "#ffffff",
              border: "1px solid #d1d5db",
              borderRadius: "0.5rem",
            }}
          >
            Try again
          </button>
        </div>
      </body>
    </html>
  );
}
