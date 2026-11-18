import unittest
from unittest.mock import patch, MagicMock
from types import SimpleNamespace
from fastapi import FastAPI
from fastapi.testclient import TestClient
import dashboard_api as api

app = FastAPI()
app.include_router(api.router)
client = TestClient(app)

class DashboardTests(unittest.TestCase):
    def setUp(self):
        api.message_cache.clear()
        api.provider_retry_at = 0

    def test_quota_error_stops_repeated_provider_requests(self):
        error = RuntimeError('quota')
        error.reason = 'Quota exceeded'
        error.resp = SimpleNamespace(status=403)
        with patch.object(api, 'gmail', side_effect=error) as provider:
            first = client.get('/api/inbox')
            second = client.get('/api/inbox')
        self.assertEqual(first.status_code, 429)
        self.assertEqual(second.status_code, 429)
        self.assertEqual(provider.call_count, 1)
        self.assertEqual(first.headers['retry-after'], '120')

    def test_inbox_live_pagination_self_mail_and_order(self):
        fake = MagicMock()
        fake.service.users().getProfile().execute.return_value = {'emailAddress': 'actual@example.com'}
        fake.service.users().messages().list().execute.return_value = {'messages':[{'id':'older'}, {'id':'new'}], 'nextPageToken':'next'}
        fake.service.users().drafts().list().execute.return_value = {}
        fake._parse_email_message.side_effect = [
            {'id':'older','threadId':'a','sender':'me@example.com','received_at':100},
            {'id':'new','threadId':'b','sender':'someone@example.com','received_at':200}]
        def batch_factory(callback):
            batch = MagicMock()
            batch.execute.side_effect = lambda: [callback('older', {}, None), callback('new', {}, None)]
            return batch
        fake.service.new_batch_http_request.side_effect = batch_factory
        with patch.object(api, 'gmail', return_value=fake), patch.dict(api.os.environ, {'MY_EMAIL':'me@example.com'}):
            response = client.get('/api/inbox?page_token=previous')
            refreshed = client.get('/api/inbox?page_token=previous')
        self.assertEqual(response.status_code, 200)
        self.assertEqual([e['id'] for e in response.json()['emails']], ['new', 'older'])
        self.assertEqual(response.json()['mailbox'], 'actual@example.com')
        self.assertEqual(response.json()['next_page_token'], 'next')
        self.assertEqual(response.headers['cache-control'], 'no-store, private')
        fake.service.users().messages().list.assert_called_with(userId='me', labelIds=['INBOX'], maxResults=50, pageToken='previous')
        self.assertEqual(refreshed.status_code, 200)
        self.assertEqual(fake.service.new_batch_http_request.call_count, 2)
        self.assertEqual(fake._parse_email_message.call_count, 2)

    def test_mime_alternatives_and_safe_formatted_email(self):
        import base64
        from src.tools.GmailTools import GmailToolsClass
        tool = GmailToolsClass.__new__(GmailToolsClass)
        def part(kind, text):
            return {'mimeType': kind, 'body': {'data': base64.urlsafe_b64encode(text.encode()).decode().rstrip('=')}}
        payload = {'parts': [part('text/html', '<h2>Hello</h2><p>First paragraph.</p><p>Second paragraph.</p><script>alert(1)</script><a href="javascript:alert(1)" onclick="bad()">Bad link</a><img src="https://tracker.test/pixel"><div style="display:none">Hidden</div><!-- comment -->'), {'parts': [part('text/plain', 'Preferred plain text\n\nSecond paragraph.')]}]}
        self.assertEqual(tool._get_email_body(payload), 'Preferred plain text\n\nSecond paragraph.')
        html = tool._safe_email_html(payload)
        self.assertIn('<h2>Hello</h2>', html)
        self.assertIn('<p>Second paragraph.</p>', html)
        for unsafe in ['<script', 'onclick', 'javascript:', 'Hidden', '<!--']:
            self.assertNotIn(unsafe, html)
        from bs4 import BeautifulSoup
        image = BeautifulSoup(html, 'html.parser').find('img')
        self.assertFalse(image.has_attr('src'))
        self.assertEqual(image['data-remote-src'], 'https://tracker.test/pixel')

    def test_original_email_design_is_preserved(self):
        import base64
        from bs4 import BeautifulSoup
        from src.tools.GmailTools import GmailToolsClass
        tool = GmailToolsClass.__new__(GmailToolsClass)
        original = '<html><head><style>.hero{background:#101010}@media(max-width:500px){.hero{width:100%}}</style></head><body style="background:#eee"><table width="600" cellpadding="0" cellspacing="0"><tr><td colspan="2" bgcolor="#222"><img src="https://example.com/hero.jpg" width="600" height="400" alt="Collection" style="display:block" onload="bad()"></td></tr></table></body></html>'
        payload = {'mimeType':'text/html','body':{'data':base64.urlsafe_b64encode(original.encode()).decode()}}
        result = BeautifulSoup(tool._safe_email_html(payload), 'html.parser')
        self.assertIn('@media', result.style.string)
        self.assertEqual(result.table['width'], '600')
        self.assertEqual(result.td['colspan'], '2')
        self.assertEqual(result.img['style'], 'display:block')
        self.assertEqual(result.img['height'], '400')
        self.assertFalse(result.img.has_attr('onload'))
        self.assertFalse(result.img.has_attr('src'))

    def test_nested_hidden_email_content(self):
        import base64
        from src.tools.GmailTools import GmailToolsClass
        tool = GmailToolsClass.__new__(GmailToolsClass)
        html = '<html><head><style>p{color:red}</style></head><body><div style="display:none"><table><tr><td>Hidden</td></tr></table></div><p>Readable message</p></body></html>'
        payload = {'mimeType':'text/html','body':{'data':base64.urlsafe_b64encode(html.encode()).decode()}}
        self.assertIn('Readable message', tool._safe_email_html(payload))
        self.assertNotIn('Hidden', tool._safe_email_html(payload))

    def test_reply_escapes_html_and_keeps_plain_text(self):
        from src.tools.GmailTools import GmailToolsClass
        tool = GmailToolsClass.__new__(GmailToolsClass)
        message = tool._create_html_email_message('a@example.com', 'Help', '<b>Hello</b>\nThanks')
        parts = message.get_payload()
        self.assertEqual(parts[0].get_content_type(), 'text/plain')
        self.assertIn('&lt;b&gt;Hello&lt;/b&gt;<br>Thanks', parts[1].get_payload(decode=True).decode())
        self.assertEqual(tool._clean_body_text('Hello\n\nWorld'), 'Hello\n\nWorld')

    def test_status_does_not_initialize_providers(self):
        with patch.object(api, 'gmail', side_effect=AssertionError('Must stay offline')):
            self.assertEqual(client.get('/api/status').status_code, 200)

    def test_provider_failure_is_not_an_empty_inbox(self):
        with patch.object(api, 'gmail', side_effect=RuntimeError('secret-key')):
            response = client.get('/api/inbox')
        self.assertEqual(response.status_code, 502)
        self.assertNotIn('secret-key', response.text)

    def test_validation(self):
        self.assertEqual(client.post('/api/generate', json={'email_id': ''}).status_code, 422)
        self.assertEqual(client.post('/api/drafts', json={'email_id': 'id', 'body': ' '}).status_code, 422)

    def test_concurrent_generation(self):
        api.operation_lock.acquire()
        try:
            self.assertEqual(client.post('/api/generate', json={'email_id':'id'}).status_code, 409)
        finally:
            api.operation_lock.release()

    def test_duplicate_draft(self):
        fake = MagicMock()
        fake._get_email_info.return_value = {'threadId':'t'}
        fake.service.users().drafts().list().execute.return_value = {'drafts':[{'message':{'threadId':'t'}}]}
        with patch.object(api, 'gmail', return_value=fake):
            response = client.post('/api/drafts',json={'email_id':'id','body':'Reply'})
        self.assertEqual(response.status_code,409)
        fake.create_draft_reply.assert_not_called()

    def test_successful_save(self):
        fake = MagicMock()
        fake._get_email_info.return_value = {'id':'id','threadId':'t','messageId':None,'references':'','sender':'customer@example.com','subject':'Help','body':'Help please'}
        fake.service.users().drafts().list().execute.return_value = {}
        fake.create_draft_reply.return_value = {'id':'draft1'}
        with patch.object(api, 'gmail', return_value=fake):
            response = client.post('/api/drafts',json={'email_id':'id','body':'Reply'})
        self.assertEqual(response.json(),{'id':'draft1','status':'saved'})

    def test_generation_retries_and_does_not_save(self):
        import sys
        fake = MagicMock()
        fake._get_email_info.return_value = {'subject':'Help','sender':'A','body':'Help'}
        agents = MagicMock()
        agents.categorize_email.invoke.return_value = SimpleNamespace(category=SimpleNamespace(value='customer_complaint'))
        agents.email_writer.invoke.return_value = SimpleNamespace(email='Draft reply')
        agents.email_proofreader.invoke.side_effect = [SimpleNamespace(send=False,feedback='Try again'),SimpleNamespace(send=True,feedback='Good')]
        module = SimpleNamespace(Agents=lambda:agents)
        with patch.dict(sys.modules, {'src.agents':module}), patch.object(api,'gmail',return_value=fake):
            response = client.post('/api/generate',json={'email_id':'id'})
        self.assertEqual(response.status_code,200)
        self.assertEqual(response.json()['attempts'],2)
        self.assertTrue(response.json()['approved'])
        fake.create_draft_reply.assert_not_called()
        fake.send_reply.assert_not_called()

    def test_unrelated_reason_and_explicit_manual_reply(self):
        import sys
        fake = MagicMock()
        fake._get_email_info.return_value = {'subject':'Weekend plans','sender':'Alex','body':'Want to catch up this weekend?'}
        agents = MagicMock()
        agents.categorize_email.invoke.return_value = SimpleNamespace(category=SimpleNamespace(value='unrelated'), reason='Alex is asking about personal weekend plans, not product support.')
        agents.email_writer.invoke.return_value = SimpleNamespace(email='Hi Alex, what day did you have in mind?')
        agents.email_proofreader.invoke.return_value = SimpleNamespace(send=True,feedback='Appropriate personal reply.')
        with patch.dict(sys.modules, {'src.agents':SimpleNamespace(Agents=lambda:agents)}), patch.object(api,'gmail',return_value=fake):
            skipped = client.post('/api/generate',json={'email_id':'id'})
            agents.email_writer.invoke.assert_not_called()
            manual = client.post('/api/generate',json={'email_id':'id','draft_anyway':True})
        self.assertIn('weekend plans', skipped.json()['classification_reason'])
        self.assertEqual(manual.status_code,200)
        self.assertTrue(manual.json()['manual_reply'])
        self.assertTrue(manual.json()['draft'])
        self.assertIn('Subject: Weekend plans', agents.categorize_email.invoke.call_args.args[0]['email'])
        self.assertIn('MANUAL REPLY', agents.email_writer.invoke.call_args.args[0]['email_information'])
        fake.create_draft_reply.assert_not_called()
        fake.send_reply.assert_not_called()

if __name__=='__main__':
    unittest.main()
