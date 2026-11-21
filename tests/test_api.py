from __future__ import annotations
import json
import threading
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from backend.server import Handler

MODEL = json.loads((Path(__file__).resolve().parents[1] / 'data/factory_demo_v3.json').read_text('utf-8'))


class ApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        cls.host = 'http://127.0.0.1:' + str(cls.server.server_address[1])
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=2)

    def request(self, route, data=None):
        headers = {'Content-Type': 'application/json'} if data is not None else {}
        req = Request(self.host + route, headers=headers,
                      data=json.dumps(data, ensure_ascii=False).encode('utf-8') if data is not None else None)
        try:
            with urlopen(req, timeout=3) as resp:
                return resp.status, json.loads(resp.read())
        except HTTPError as e:
            return e.code, json.loads(e.read())

    def test_health(self):
        status, body = self.request('/api/v1/health')
        self.assertEqual(status, 200)
        self.assertEqual(body['status'], 'ok')

    def test_demo_data(self):
        status, body = self.request('/api/v1/demo')
        self.assertEqual(status, 200)
        self.assertEqual(body['version'], 3)
        self.assertEqual(len(body['edges']), 14)

    def test_validate(self):
        status, body = self.request('/api/v1/validate', {'model': MODEL})
        self.assertEqual(status, 200)
        self.assertTrue(body['valid'])

    def test_simulate(self):
        status, body = self.request('/api/v1/simulate', {'model': MODEL})
        self.assertEqual(status, 200)
        self.assertEqual(body['accepted'], 9)
        self.assertIn('methodology', body)

    def test_compare(self):
        status, body = self.request('/api/v1/compare', {'baseline': MODEL, 'candidate': MODEL})
        self.assertEqual(status, 200)
        self.assertEqual(body['delta']['accepted']['change'], 0)

    def test_invalid_model_422(self):
        status, body = self.request('/api/v1/simulate', {'model': {'nodes': []}})
        self.assertEqual(status, 422)
        self.assertEqual(body['error'], 'invalid_model')

    def test_static_page_exists(self):
        with urlopen(self.host + '/', timeout=3) as resp:
            body = resp.read().decode('utf-8')
            self.assertIn('PROCESS LAB', body)
            self.assertIn('backend_sync.js', body)

    def test_static_traversal_blocked(self):
        status, body = self.request('/../../backend/engine.py')
        self.assertEqual(status, 404)


if __name__ == '__main__':
    unittest.main()
