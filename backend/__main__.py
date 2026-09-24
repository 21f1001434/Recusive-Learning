from __future__ import annotations

import os


def main() -> None:
    import uvicorn
    host = os.getenv("HIP_BACKEND_HOST", "127.0.0.1")
    port = int(os.getenv("HIP_BACKEND_PORT", "8000"))
    uvicorn.run("backend.app:app", host=host, port=port, reload=False)


if __name__ == "__main__":
    main()
