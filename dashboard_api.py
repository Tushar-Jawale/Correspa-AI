"""Local dashboard API. Credentials and email contents remain on the backend."""
import logging
import os
import time
from collections import OrderedDict
from pathlib import Path
from threading import Lock
from datetime import datetime, timezone
from fastapi import APIRouter, HTTPException, Response, Query
from pydantic import BaseModel, Field

ROOT = Path(__file__).resolve().parent
router = APIRouter(prefix='/api')
log = logging.getLogger(__name__)
operation_lock = Lock()
message_cache = OrderedDict()
cache_lock = Lock()
provider_retry_at = 0.0


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
    global provider_retry_at
    if isinstance(exc, HTTPException):
        raise exc
    if getattr(getattr(exc, 'resp', None), 'status', None) == 429 or 'quota exceeded' in str(getattr(exc, 'reason', '')).lower():
        provider_retry_at = time.monotonic() + 120
        raise HTTPException(429, 'Gmail rate limit reached. Automatic sync will retry in two minutes.', headers={'Retry-After':'120'}) from exc
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
def inbox(response: Response, page_token: str | None = Query(default=None, max_length=2048)):
    try:
        if time.monotonic() < provider_retry_at:
            raise HTTPException(429, 'Gmail is cooling down after a rate limit. Sync will retry automatically.', headers={'Retry-After':str(max(1, int(provider_retry_at-time.monotonic())))})
        client = gmail()
        response.headers['Cache-Control'] = 'no-store, private'
        mailbox = client.service.users().getProfile(userId='me').execute()['emailAddress']
        messages = client.service.users().messages().list(userId='me', labelIds=['INBOX'], maxResults=50, pageToken=page_token).execute()
        drafts = client.service.users().drafts().list(userId='me', maxResults=500).execute()
        draft_threads = {d['message']['threadId'] for d in drafts.get('drafts', [])}
        result = []
        failures = []
        def collect(message_id, message, exception):
            if exception:
                failures.append(exception)
                return
            email = client._parse_email_message(message, message_id)
            with cache_lock:
                message_cache[(mailbox, message_id)] = dict(email)
                while len(message_cache) > 500:
                    message_cache.popitem(last=False)
            email['has_draft'] = email['threadId'] in draft_threads
            result.append(email)
        # One Gmail batch avoids 50 sequential HTTP round trips per refresh.
        if messages.get('messages'):
            batch = client.service.new_batch_http_request(callback=collect)
            pending = 0
            for message in messages['messages']:
                with cache_lock:
                    cached = message_cache.get((mailbox, message['id']))
                if cached is not None:
                    email = dict(cached)
                    email['has_draft'] = email['threadId'] in draft_threads
                    result.append(email)
                    continue
                pending += 1
                batch.add(client.service.users().messages().get(userId='me', id=message['id'], format='full'), request_id=message['id'])
            if pending:
                batch.execute()
        if failures:
            raise failures[0]
        result.sort(key=lambda email: int(email.get('received_at', 0)), reverse=True)
        return {'emails': result, 'has_more': bool(messages.get('nextPageToken')),
                'next_page_token': messages.get('nextPageToken'),
                'synced_at': datetime.now(timezone.utc).isoformat(), 'source': 'gmail', 'mailbox': mailbox}
    except Exception as exc:
        failure(exc)


class GenerateRequest(BaseModel):
    email_id: str = Field(min_length=1, max_length=256)
    draft_anyway: bool = False


@router.post('/generate')
def generate(request: GenerateRequest):
    if not operation_lock.acquire(blocking=False):
        raise HTTPException(409, 'Another draft is being generated. Please wait for it to finish.')
    try:
        from src.agents import Agents
        client = gmail()
        email = client._get_email_info(request.email_id)
        agents = Agents()
        message_context = f"Subject: {email['subject']}\nFrom: {email['sender']}\nMessage:\n{email['body']}"
        classification = agents.categorize_email.invoke({'email': message_context})
        category = classification.category.value
        reason = getattr(classification, 'reason', '') or 'The classifier did not identify a customer support request in this message.'
        if category == 'unrelated' and not request.draft_anyway:
            return {'category': category, 'classification_reason': reason, 'draft': '', 'approved': False, 'attempts': 0, 'context': '', 'feedback': reason, 'steps': ['Categorized', 'Skipped unrelated email']}
        context = ''
        steps = ['Categorized']
        if category == 'product_enquiry':
            queries = agents.design_rag_queries.invoke({'email': email['body']}).queries
            context = '\n\n'.join(f'{q}\n{agents.generate_rag_answer.invoke(q)}' for q in queries[:3])
            steps.append('Knowledge retrieved')
        history = []
        manual_instruction = '\nMANUAL REPLY: The mailbox owner explicitly requests a reply despite automatic triage. Write from the recipient\'s perspective, responding to this actual message. Do not pretend the sender is a customer or that the recipient represents the sender\'s company. Use a neutral sign-off and do not invent commitments or personal facts.' if category == 'unrelated' and request.draft_anyway else ''
        for attempt in range(1, 4):
            draft = agents.email_writer.invoke({'email_information': f"Category: {category}\n{message_context}\nKnowledge: {context}{manual_instruction}", 'history': history}).email
            review = agents.email_proofreader.invoke({'initial_email': email['body'], 'generated_email': draft})
            steps.append(f'Draft {attempt} reviewed')
            if review.send:
                break
            history.extend([('assistant', draft), ('human', review.feedback)])
        return {'category': category, 'classification_reason': reason, 'manual_reply': bool(manual_instruction), 'draft': draft, 'approved': review.send, 'feedback': review.feedback, 'attempts': attempt, 'context': context, 'steps': steps}
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
