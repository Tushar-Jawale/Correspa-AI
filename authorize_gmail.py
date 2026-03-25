"""Explicit, local-only Gmail authorization helper."""
import os
from pathlib import Path
from dotenv import load_dotenv
from src.tools.GmailTools import GmailToolsClass

if __name__ == '__main__':
    os.chdir(Path(__file__).resolve().parent)
    load_dotenv()
    GmailToolsClass()
    print('Gmail authorization is ready. Start python deploy_api.py next.')
