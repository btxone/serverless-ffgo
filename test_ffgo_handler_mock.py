import unittest
from unittest.mock import MagicMock, patch
import json
import os
import sys

# Add directory to path to import handler
sys.path.append(os.path.join(os.getcwd(), 'ffgo-worker'))

# Mock runpod before importing handler
sys.modules['runpod'] = MagicMock()
sys.modules['runpod.serverless'] = MagicMock()
sys.modules['runpod.serverless.utils'] = MagicMock()

import handler

class TestFFGOHandler(unittest.TestCase):
    def setUp(self):
        # Load the template locally to simulate client sending it
        with open('ffgo-worker/src/workflow_ffgo.json', 'r', encoding='utf-8') as f:
            self.workflow = json.load(f)

    @patch('handler.requests')
    @patch('handler.websocket')
    @patch('handler.check_server')
    def test_handler_success(self, mock_check_server, mock_websocket, mock_requests):
        # Setup Mocks
        mock_check_server.return_value = True
        mock_requests.post.return_value.status_code = 200
        
        mock_ws_instance = MagicMock()
        mock_websocket.WebSocket.return_value = mock_ws_instance
        mock_ws_instance.recv.side_effect = [
            json.dumps({"type": "executing", "data": {"node": None, "prompt_id": "test-id"}}),
        ]
        
        mock_queue_resp = MagicMock()
        mock_queue_resp.json.return_value = {"prompt_id": "test-id"}
        mock_queue_resp.status_code = 200
        
        mock_history_resp = MagicMock()
        mock_history_resp.json.return_value = {
            "test-id": {
                "outputs": {
                    "211": { 
                        "videos": [{"filename": "out.mp4", "subfolder": "", "type": "output"}]
                    }
                }
            }
        }
        mock_history_resp.status_code = 200
        
        mock_view_resp = MagicMock()
        mock_view_resp.content = b"fake-video-bytes"
        mock_view_resp.status_code = 200
        
        def requests_post_side_effect(url, **kwargs):
            if "prompt" in url:
                # IMPORTANT: Verify the handler received passed-through workflow
                data = json.loads(kwargs['data'])
                prompt = data['prompt']
                # We expect whatever we sent in 'workflow'
                assert prompt['6']['inputs']['text'] == "TEST PROMPT CLIENT SIDE"
                return mock_queue_resp
            return MagicMock()

        def requests_get_side_effect(url, **kwargs):
            if "history" in url: return mock_history_resp
            if "view" in url: return mock_view_resp
            return MagicMock()

        mock_requests.post.side_effect = requests_post_side_effect
        mock_requests.get.side_effect = requests_get_side_effect

        # MODIFY WORKFLOW CLIENT SIDE (SIMULATION)
        self.workflow['6']['inputs']['text'] = "TEST PROMPT CLIENT SIDE"

        # Input
        job_input = {
            "input": {
                "workflow": self.workflow, # Sending full workflow
                "images": [{"name": "img1.png", "image": "aGVsbG8="}]
            },
            "id": "job-123"
        }

        # Run Handler
        result = handler.handler(job_input)
        
        # Verify
        self.assertIn("output", result)
        self.assertEqual(result["output"][0]["filename"], "out.mp4")
        print("\n[TEST] Handler execution successful (Client-Side Injection Mode)!")

if __name__ == '__main__':
    unittest.main()
