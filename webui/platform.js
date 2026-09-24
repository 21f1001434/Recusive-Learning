const python = process.env.PYTHON || "python";
const API_BASE = (process.env.HIP_API_BASE || "http://127.0.0.1:8000").replace(/\/$/, "");
const HEALTH_URL = `${API_BASE}/health`;
const STARTUP_TIMEOUT_MS = Number(process.env.HIP_BACKEND_STARTUP_TIMEOUT_MS || 30000);
const POLL_MS = 250;

async function backendHealthy() {
  try {
    const response = await fetch(HEALTH_URL, { signal: AbortSignal.timeout(1500) });
    return response.ok;
  } catch {
    return false;
  }
}

let backend = null;
if (await backendHealthy()) {
  console.log(`HIP FastAPI already healthy at ${API_BASE}; reusing it.`);
} else {
  backend = Bun.spawn([
    python, "-m", "uvicorn", "backend.app:app",
    "--host", "127.0.0.1", "--port", "8000",
  ], {
    cwd: `${import.meta.dir}/..`,
    stdout: "inherit",
    stderr: "inherit",
    stdin: "inherit",
    env: { ...process.env, PYTHONUNBUFFERED: "1", PYTHONUTF8: "1", PYTHONIOENCODING: "utf-8", PYTHONLEGACYWINDOWSSTDIO: "0" },
  });

  const deadline = Date.now() + STARTUP_TIMEOUT_MS;
  let healthy = false;
  while (Date.now() < deadline) {
    if (backend.exitCode !== null) {
      throw new Error(`HIP FastAPI exited before becoming healthy (exit=${backend.exitCode}).`);
    }
    if (await backendHealthy()) {
      healthy = true;
      break;
    }
    await Bun.sleep(POLL_MS);
  }
  if (!healthy) {
    try { backend.kill(); } catch {}
    throw new Error(`HIP FastAPI did not become healthy at ${HEALTH_URL} within ${STARTUP_TIMEOUT_MS}ms.`);
  }
  console.log(`HIP FastAPI healthy at ${API_BASE}.`);
}

let stopped = false;
async function shutdown() {
  if (stopped) return;
  stopped = true;
  if (backend) {
    try { backend.kill(); } catch {}
  }
  setTimeout(() => process.exit(0), 150);
}
process.on("SIGINT", shutdown);
process.on("SIGTERM", shutdown);

await import("./server.js");
