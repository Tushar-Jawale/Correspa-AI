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
        for unsafe in ['script', 'onclick', 'javascript:', 'tracker.test', 'Hidden', '<!--']:
            self.assertNotIn(unsafe, html)

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

if __name__=='__main__':
    unittest.main()
