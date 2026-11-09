# Entrypoint for Railway / Cloud deployment
import os
import sys
from pathlib import Path
import uvicorn

# Ensure root and backend paths are available
ROOT_DIR = Path(__file__).resolve().parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

BACKEND_DIR = ROOT_DIR / "dashboard" / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

# Import the FastAPI application from dashboard.backend.main
from dashboard.backend.main import app  # noqa: F401

if __name__ == "__main__":
    port = int(os.getenv("PORT", "8080"))
    uvicorn.run("dashboard.backend.main:app", host="0.0.0.0", port=port, log_level="info")
