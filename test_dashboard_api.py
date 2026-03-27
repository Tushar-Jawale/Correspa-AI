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
