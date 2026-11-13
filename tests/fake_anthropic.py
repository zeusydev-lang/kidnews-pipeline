"""A fake Anthropic Messages API on localhost, for tests. No key, no cost.

It records every request body litellm sends and answers with queued replies
(the last reply repeats once the queue runs out).
"""

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer


def text_reply(text: str, input_tokens: int = 100, output_tokens: int = 20) -> dict:
    return {
        "id": "msg_test",
        "type": "message",
        "role": "assistant",
        "model": "claude-haiku-5-5",
        "content": [{"type": "text", "text": text}],
        "stop_reason": "end_turn",
        "stop_sequence": None,
        "usage": {"input_tokens": input_tokens, "output_tokens": output_tokens},
    }


def tool_reply(
    calls: list[tuple[str, str, dict]],
    text: str | None = None,
    input_tokens: int = 100,
    output_tokens: int = 20,
) -> dict:
    reply = text_reply("", input_tokens, output_tokens)
    content = [{"type": "text", "text": text}] if text else []
    content += [
        {"type": "tool_use", "id": cid, "name": name, "input": args}
        for cid, name, args in calls
    ]
    reply.update(content=content, stop_reason="tool_use")
    return reply


class FakeAnthropic:
    def __init__(self):
        self.requests: list[dict] = []
        self.replies: list[dict] = [text_reply("Hello!")]
        fake = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                length = int(self.headers.get("content-length", 0))
                fake.requests.append(json.loads(self.rfile.read(length)))
                reply = (
                    fake.replies.pop(0) if len(fake.replies) > 1 else fake.replies[0]
                )
                body = json.dumps(reply).encode()
                self.send_response(200)
                self.send_header("content-type", "application/json")
                self.send_header("content-length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args):
                pass

        self.server = HTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_port}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def reset(self, *replies: dict) -> None:
        self.requests.clear()
        self.replies = list(replies) or [text_reply("Hello!")]

    def close(self) -> None:
        self.server.shutdown()
