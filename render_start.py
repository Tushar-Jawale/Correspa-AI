"""Start Correspa API on Render: build Chroma index if missing, then run uvicorn."""
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
os.chdir(ROOT)

if not (ROOT / "db" / "chroma.sqlite3").exists():
    print("Building knowledge index...", flush=True)
    subprocess.check_call([sys.executable, "create_index.py"], cwd=ROOT)

port = os.environ.get("PORT", "8000")
os.execvp(
    sys.executable,
    [sys.executable, "-m", "uvicorn", "deploy_api:app", "--host", "0.0.0.0", "--port", port],
)
