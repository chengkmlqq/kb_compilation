"""docreader package — document-to-markdown parsing (migrated from WeKnora).

License: Apache-2.0 (WeKnora/docreader). See LICENSE in this directory.

Migrated from the WeKnora repository:
- parser/     — format-specific parsers (md/pdf/docx/xlsx/pptx/epub/mhtml/...) 
                with a registry + fallback engine chain
- models/     — Document / Chunk pydantic models
- splitter/   — chunking (TextSplitter + header hooks)
- utils/      — endecode / request / ssrf / tempfile helpers
- config.py   — env-driven DocReaderConfig (CONFIG singleton)

Removed vs upstream: the gRPC service shell (main.py / auth.py / proto/ /
client/) — in this Python-only stack the parsers are called directly by
Celery tasks, so no RPC layer is needed.
"""
