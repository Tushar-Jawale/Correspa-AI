"""Run from correspa-ai with: python deploy_api.py (local use, one worker)."""
import os
import sys
from pathlib import Path
import uvicorn
from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

ROOT = Path(__file__).resolve().parent
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')
os.chdir(ROOT)
load_dotenv(ROOT / '.env')
from dashboard_api import router

app = FastAPI(title='Correspa AI', version='2.0', description='Customer support workspace API')
app.add_middleware(CORSMiddleware, allow_origins=['http://localhost:5173', 'http://127.0.0.1:5173'], allow_methods=['GET', 'POST'], allow_headers=['Content-Type'])
app.include_router(router)

# Preserve the LangServe interface without loading models or opening OAuth at startup.
from langserve import add_routes
from langchain_core.runnables import RunnableLambda

def invoke_workflow(state):
    from src.graph import Workflow
    return Workflow().app.invoke(state, {'recursion_limit': 100})

add_routes(app, RunnableLambda(invoke_workflow))

frontend = ROOT.parent / 'frontend' / 'dist'
if frontend.exists():
    app.mount('/workspace', StaticFiles(directory=frontend, html=True), name='workspace')

if __name__ == '__main__':
    uvicorn.run(app, host='127.0.0.1', port=8000)
