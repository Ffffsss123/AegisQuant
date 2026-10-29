import unittest
from unittest.mock import patch,MagicMock
from trading.ai_analysis import endpoint,analyze,NoRedirect

class ReadOnlyAITests(unittest.TestCase):
    def test_only_https_or_loopback_without_url_credentials(self):
        self.assertEqual(endpoint('https://provider.example/v1'),'https://provider.example/v1/chat/completions')
        for url in ('http://provider.example/v1','https://user:secret@provider.example/v1','https://provider.example/v1?key=secret'):
            with self.assertRaises(ValueError):endpoint(url)
        with self.assertRaises(ValueError):NoRedirect().redirect_request(None,None,None,None,None,'https://other.example')
    def test_one_explicit_request_contains_only_supplied_research(self):
        response=MagicMock();response.__enter__.return_value.read.return_value=b'{"choices":[{"message":{"content":"Research result"}}]}'
        with patch('trading.ai_analysis.build_opener') as op:
            op.return_value.open.return_value=response
            self.assertEqual(analyze('https://provider.example/v1','fixture','secret','Public research'),'Research result')
            self.assertEqual(op.return_value.open.call_count,1)
            request=op.return_value.open.call_args.args[0]
            self.assertIn(b'Public research',request.data)
            self.assertNotIn(b'secret',request.data)
    def test_failure_does_not_echo_key_or_retry(self):
        with patch('trading.ai_analysis.build_opener') as op:
            op.return_value.open.side_effect=RuntimeError('secret')
            with self.assertRaises(ValueError) as error:analyze('https://provider.example/v1','fixture','secret','Public')
            self.assertNotIn('secret',str(error.exception));self.assertEqual(op.return_value.open.call_count,1)
