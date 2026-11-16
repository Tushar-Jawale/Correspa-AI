"""Run from correspa-ai with: python deploy_api.py"""
import os
import sys
from pathlib import Path

import uvicorn
from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

ROOT = Path(__file__).resolve().parent
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
os.chdir(ROOT)
load_dotenv(ROOT / ".env")
from dashboard_api import router

app = FastAPI(title="Correspa AI", version="2.0", description="Customer support workspace API")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(router)

from langserve import add_routes
from langchain_core.runnables import RunnableLambda


def invoke_workflow(state):
    from src.graph import Workflow

    return Workflow().app.invoke(state, {"recursion_limit": 100})


add_routes(app, RunnableLambda(invoke_workflow))


@app.get("/")
def root():
    return {"service": "Correspa AI", "docs": "/docs", "status": "/api/status"}


if __name__ == "__main__":
    host = "0.0.0.0" if os.getenv("PORT") else "127.0.0.1"
    port = int(os.getenv("PORT", "8000"))
    uvicorn.run(app, host=host, port=port)
