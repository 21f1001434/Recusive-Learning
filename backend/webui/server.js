import { join, extname, normalize } from "node:path";

const ROOT = import.meta.dir;
const API_BASE = (process.env.HIP_API_BASE || "http://127.0.0.1:8000").replace(/\/$/, "");
const argPort = process.argv.find((arg) => arg.startsWith("--port="));
const PORT = Number(argPort?.split("=")[1] || process.env.HIP_UI_PORT || 8501);

const MIME = {
  ".html": "text/html; charset=utf-8",
  ".js": "text/javascript; charset=utf-8",
  ".css": "text/css; charset=utf-8",
  ".json": "application/json; charset=utf-8",
  ".svg": "image/svg+xml",
  ".png": "image/png",
  ".ico": "image/x-icon",
};

function staticPath(pathname) {
  const requested = pathname === "/" ? "index.html" : pathname.replace(/^\/+/, "");
  const safe = normalize(requested).replace(/^(\.\.(\/|\\|$))+/, "");
  return join(ROOT, safe);
}

async function proxy(req, url) {
  const upstream = new URL(url.pathname + url.search, API_BASE);
  const headers = new Headers(req.headers);
  headers.delete("host");
  try {
    return await fetch(upstream, {
      method: req.method,
      headers,
      body: ["GET", "HEAD"].includes(req.method) ? undefined : req.body,
      redirect: "manual",
    });
  } catch (error) {
    return Response.json({
      detail: `HIP backend is unavailable at ${API_BASE}`,
      error: String(error),
    }, { status: 503 });
  }
}

const server = Bun.serve({
  port: PORT,
  hostname: "127.0.0.1",
  async fetch(req) {
    const url = new URL(req.url);
    if (url.pathname === "/healthz") {
      return Response.json({ status: "ok", service: "hip-javascript-ui", api: API_BASE });
    }
    if (url.pathname.startsWith("/api/") || url.pathname === "/health") {
      return proxy(req, url);
    }
    const path = staticPath(url.pathname);
    const file = Bun.file(path);
    if (await file.exists()) {
      return new Response(file, { headers: { "Content-Type": MIME[extname(path).toLowerCase()] || "application/octet-stream" } });
    }
    // SPA fallback.
    return new Response(Bun.file(join(ROOT, "index.html")), { headers: { "Content-Type": MIME[".html"] } });
  },
});

console.log(`HIP JavaScript UI: http://localhost:${server.port}`);
console.log(`HIP API proxy: ${API_BASE}`);
