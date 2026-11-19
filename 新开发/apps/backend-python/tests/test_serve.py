import unittest
from unittest.mock import patch

from baoxian_api.serve import serve


class ServeTests(unittest.TestCase):
    def test_uses_api_host_port_and_application_factory(self):
        with patch('baoxian_api.serve.uvicorn.run') as run:
            serve({'API_HOST': '127.0.0.8', 'API_PORT': '4123'})
        run.assert_called_once_with('baoxian_api.main:create_application', factory=True, host='127.0.0.8', port=4123)

    def test_uses_existing_api_defaults(self):
        with patch('baoxian_api.serve.uvicorn.run') as run:
            serve({})
        run.assert_called_once_with('baoxian_api.main:create_application', factory=True, host='127.0.0.1', port=3001)
