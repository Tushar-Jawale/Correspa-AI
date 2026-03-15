"""Start API immediately; copy Render secret files; build index in background if needed."""
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
os.chdir(ROOT)

# Render Secret Files are mounted under /etc/secrets/<filename>
secrets = Path("/etc/secrets")
for name in ("token.json", "credentials.json"):
    src = secrets / name
    if src.exists():
        shutil.copy(src, ROOT / name)
        print(f"Loaded secret: {name}", flush=True)

if not (ROOT / "db" / "chroma.sqlite3").exists():
    print("Building knowledge index in background...", flush=True)
    subprocess.Popen([sys.executable, "create_index.py"], cwd=ROOT)

port = os.environ.get("PORT", "8000")
os.execvp(
    sys.executable,
    [sys.executable, "-m", "uvicorn", "deploy_api:app", "--host", "0.0.0.0", "--port", port],
)
