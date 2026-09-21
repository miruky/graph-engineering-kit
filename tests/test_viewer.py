import http.client
import json
import threading
from agentkit import viewer
from test_core import ProjectCase


class WorkbenchContracts(ProjectCase):
    def test_metadata_cannot_terminate_the_json_script_element(self):
        payload = '</script><script>alert(1)</script>'
        graph = {'nodes':[{'id':'node','title':payload,'kind':'command','status':'pending'}],'edges':[]}
        result = viewer.export(self.root, graph, '<unsafe title>')
        page = (self.root / result['html']).read_text()
        self.assertNotIn(payload, page)
        self.assertIn('&lt;unsafe title&gt;', page)
        self.assertIn('\\u003c/script>', page)
        stored = json.loads((self.root / result['directory'] / 'graph.json').read_text())
        self.assertEqual(stored, graph)

    def test_loopback_routes_host_origin_and_write_rejection(self):
        result = viewer.export(self.root, {'nodes':[],'edges':[]})
        server = viewer.serve(self.root, result['directory'])
        thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
        def request(path='/', headers=None, method='GET'):
            connection = http.client.HTTPConnection('127.0.0.1', server.server_port, timeout=5)
            connection.request(method, path, headers=headers or {})
            response = connection.getresponse()
            status, body, response_headers = response.status, response.read(), dict(response.getheaders())
            connection.close()
            return status, body, response_headers
        try:
            status, body, headers = request()
            self.assertEqual(status, 200)
            self.assertIn(b'Workbench', body)
            self.assertEqual(headers['X-Content-Type-Options'], 'nosniff')
            self.assertEqual(request('/graph.json')[0], 200)
            self.assertEqual(request('/../../.git/config')[0], 404)
            self.assertEqual(request('/project/docs/policy.md')[0], 404)
            self.assertEqual(request(headers={'Host':'untrusted.invalid'})[0], 403)
            self.assertEqual(request(headers={'Origin':'https://untrusted.invalid'})[0], 403)
            self.assertEqual(request(method='POST')[0], 405)
        finally:
            server.shutdown(); server.server_close(); thread.join(timeout=5)
