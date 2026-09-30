"""Live verification: RAG QA streaming against a mock OpenAI SSE endpoint.

Not part of the unit test suite (needs a local server) — run manually:
    .venv/bin/python scripts/verify_chat_stream.py

Verifies the streaming/context path end-to-end: build_messages embeds the
retrieval context into the system prompt, ChatClient streams deltas over SSE,
and the mock endpoint asserts it received the RAG context.
"""

import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

sys.path.insert(0, ".")

from api.services.chat import ChatClient, ChatConfig, ChatMessage, build_messages  # noqa: E402
from api.services.retrieval import ChunkHit  # noqa: E402

REPLY = ["这是", "根据", "检索", "内容", "的回答"]


class MockOpenAIHandler(BaseHTTPRequestHandler):
    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        payload = json.loads(self.rfile.read(length))
        assert "/chat/completions" in self.path, self.path
        msgs = payload["messages"]
        # RAG context must be embedded in the system prompt
        assert "search_results" in msgs[0]["content"], "system prompt 缺少检索上下文"

        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        for token in REPLY:
            line = f"data: {json.dumps({'choices': [{'delta': {'content': token}}]})}\n\n"
            self.wfile.write(line.encode())
            self.wfile.flush()
        self.wfile.write(b"data: [DONE]\n\n")
        self.wfile.flush()

    def log_message(self, *args):
        pass


def main() -> int:
    server = HTTPServer(("127.0.0.1", 18999), MockOpenAIHandler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        hits = [
            ChunkHit(
                chunk_id="c1",
                content="数据中台是统一数据管理平台",
                document_id="d1",
                kb_id="kb1",
                score=0.9,
            )
        ]
        messages = build_messages(
            "什么是数据中台？",
            hits,
            history=[ChatMessage(role="user", content="前置对话")],
        )
        client = ChatClient(ChatConfig(base_url="http://127.0.0.1:18999/v1", api_key="", model="mock"))
        deltas = list(client.stream(messages))
        full = "".join(deltas)
        print("流式回复:", full)
        assert full == "".join(REPLY), f"流式内容不完整: {full}"
        print("PASS: RAG QA 流式链路（上下文注入 → SSE 流式 → [DONE]）验证通过")
        return 0
    finally:
        server.shutdown()


if __name__ == "__main__":
    sys.exit(main())
