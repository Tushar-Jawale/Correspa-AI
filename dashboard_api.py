"""Local dashboard API. Credentials and email contents remain on the backend."""
import logging
import os
from pathlib import Path
from threading import Lock
from email.utils import parseaddr
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

ROOT = Path(__file__).resolve().parent
router = APIRouter(prefix='/api')
log = logging.getLogger(__name__)
operation_lock = Lock()


def gmail():
    # The browser dashboard never launches an interactive OAuth flow on a request.
    if not (ROOT / 'token.json').exists():
        raise HTTPException(503, 'Authorize Gmail locally first: run python authorize_gmail.py in correspa-ai.')
    from google.oauth2.credentials import Credentials
    from google.auth.transport.requests import Request
    from googleapiclient.discovery import build
    from src.tools.GmailTools import GmailToolsClass, SCOPES
    creds = Credentials.from_authorized_user_file(str(ROOT / 'token.json'), SCOPES)
    if not creds.valid:
        if not creds.refresh_token:
            raise HTTPException(503, 'Gmail authorization expired. Run python authorize_gmail.py again.')
        creds.refresh(Request())
        (ROOT / 'token.json').write_text(creds.to_json(), encoding='utf-8')
    client = GmailToolsClass.__new__(GmailToolsClass)
    client.service = build('gmail', 'v1', credentials=creds)
    return client


def failure(exc):
    if isinstance(exc, HTTPException):
        raise exc
    log.error('Dashboard operation failed (%s)', type(exc).__name__)
    raise HTTPException(502, 'Provider request failed. Check Gmail authorization, API keys, model availability, and backend logs.') from exc


@router.get('/status')
def status():
    return {'api': 'online', 'gmail_configured': (ROOT / 'token.json').exists(),
            'groq_configured': bool(os.getenv('GROQ_API_KEY')),
            'google_configured': bool(os.getenv('GOOGLE_API_KEY')),
            'knowledge_indexed': (ROOT / 'db' / 'chroma.sqlite3').exists(),
            'email': os.getenv('MY_EMAIL', '')}


@router.get('/inbox')
def inbox():
    try:
        client = gmail()
        messages = client.service.users().messages().list(userId='me', q='in:inbox newer_than:7d', maxResults=50).execute()
        drafts = client.service.users().drafts().list(userId='me', maxResults=500).execute()
        draft_threads = {d['message']['threadId'] for d in drafts.get('drafts', [])}
        result = []
        for message in messages.get('messages', []):
            email = client._get_email_info(message['id'])
            if parseaddr(email['sender'])[1].lower() == os.getenv('MY_EMAIL', '').lower():
                continue
            email['has_draft'] = email['threadId'] in draft_threads
            result.append(email)
        return {'emails': result, 'has_more': bool(messages.get('nextPageToken'))}
    except Exception as exc:
        failure(exc)


class GenerateRequest(BaseModel):
    email_id: str = Field(min_length=1, max_length=256)


@router.post('/generate')
def generate(request: GenerateRequest):
    if not operation_lock.acquire(blocking=False):
        raise HTTPException(409, 'Another draft is being generated. Please wait for it to finish.')
    try:
        from src.agents import Agents
        client = gmail()
        email = client._get_email_info(request.email_id)
        agents = Agents()
        category = agents.categorize_email.invoke({'email': email['body']}).category.value
        if category == 'unrelated':
            return {'category': category, 'draft': '', 'approved': False, 'attempts': 0, 'context': '', 'feedback': 'This message is unrelated to customer support.', 'steps': ['Categorized', 'Skipped unrelated email']}
        context = ''
        steps = ['Categorized']
        if category == 'product_enquiry':
            queries = agents.design_rag_queries.invoke({'email': email['body']}).queries
            context = '\n\n'.join(f'{q}\n{agents.generate_rag_answer.invoke(q)}' for q in queries[:3])
            steps.append('Knowledge retrieved')
        history = []
        for attempt in range(1, 4):
            draft = agents.email_writer.invoke({'email_information': f"Category: {category}\nSubject: {email['subject']}\nSender: {email['sender']}\nEmail: {email['body']}\nKnowledge: {context}", 'history': history}).email
            review = agents.email_proofreader.invoke({'initial_email': email['body'], 'generated_email': draft})
            steps.append(f'Draft {attempt} reviewed')
            if review.send:
                break
            history.extend([('assistant', draft), ('human', review.feedback)])
        return {'category': category, 'draft': draft, 'approved': review.send, 'feedback': review.feedback, 'attempts': attempt, 'context': context, 'steps': steps}
    except Exception as exc:
        failure(exc)
    finally:
        operation_lock.release()


class SaveRequest(GenerateRequest):
    body: str = Field(min_length=1, max_length=50000)


@router.post('/drafts')
def save_draft(request: SaveRequest):
    if not request.body.strip():
        raise HTTPException(422, 'Draft cannot be empty.')
    if not operation_lock.acquire(blocking=False):
        raise HTTPException(409, 'Another operation is running. Please try again shortly.')
    try:
        from src.state import Email
        client = gmail()
        email = client._get_email_info(request.email_id)
        drafts = client.service.users().drafts().list(userId='me', maxResults=500).execute()
        if any(d['message']['threadId'] == email['threadId'] for d in drafts.get('drafts', [])):
            raise HTTPException(409, 'This conversation already has a Gmail draft. Review it in Gmail.')
        email['messageId'] = email.get('messageId') or ''
        result = client.create_draft_reply(Email(**email), request.body)
        if not result:
            raise HTTPException(502, 'Gmail could not save the draft. Please try again.')
        return {'id': result['id'], 'status': 'saved'}
    except Exception as exc:
        failure(exc)
    finally:
        operation_lock.release()


@router.get('/knowledge')
def knowledge():
    return {'documents': [{'name': p.name, 'content': p.read_text(encoding='utf-8'), 'size': p.stat().st_size} for p in sorted((ROOT / 'data').glob('*.txt'))]}
