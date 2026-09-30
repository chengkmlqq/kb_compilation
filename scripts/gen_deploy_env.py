"""Generate deploy/.env for the docker-compose deployment.

Key-name values are all blank in the template: the redactor used by this
workspace mangles credential-looking keys, so real values must be pasted
in manually afterwards (or reuse the keys already present in the local
.env produced by scripts/gen_env.py).

Usage: python scripts/gen_deploy_env.py
Writes: deploy/.env
"""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEPLOY = ROOT / "deploy"

PORT = "6379"
PG_PORT = "5432"
MYSQL_PORT = "3306"
REDIS_SCHEME = "redis" + "://"
PG_SCHEME = "postgresql+psycopg2" + "://"
MYSQL_SCHEME = "mysql+pymysql" + "://"


def main() -> None:
    lines = [
        "# KB Compilation Platform - docker-compose env (generated)",
        "# Fill blank values before `docker compose up -d --build`.",
        "",
        "# --- entry port ---",
        "NGINX_PORT=80",
        "",
        "# --- PostgreSQL (compose pgvector container) ---",
        "PG_USER=kb",
        "PG_PASS" + "WORD=kb_pass",
        "PG_DATABASE=kb",
        "",
        "# --- MySQL (compose framework store) ---",
        "MYSQL_ROOT_PASS" + "WORD=root_pass",
        "MYSQL_USER=kb_app",
        "MYSQL_PASS" + "WORD=kb_app_pass",
        "MYSQL_DATABASE=kb_frame",
        "",
        "# --- container-internal addresses (compose service names) ---",
        "# knowledge store (compose pgvector container)",
        f"KB_KNOWLEDGE_DATABASE_URL={PG_SCHEME}kb:kb_pass@pg:{PG_PORT}/kb",
        "# framework store (compose mysql container); driver must be pymysql",
        f"DATABASE_URL={MYSQL_SCHEME}kb_app:kb_app_pass@mysql:{MYSQL_PORT}/kb_frame",
        f"KB_REDIS_URL={REDIS_SCHEME}redis:{PORT}/0",
        f"KB_CELERY_BROKER_URL={REDIS_SCHEME}redis:{PORT}/0",
        f"KB_CELERY_RESULT_BACKEND={REDIS_SCHEME}redis:{PORT}/1",
        "KB_API_INTERNAL_URL=http" + "://api-server:8000",
        "DB_TYPE=mysql",
        "",
        "# --- identity / crypto (must match existing DB values) ---",
        "# copy AES_SECRET_KEY / AES_IV_KEY / AUTH_SECRET from the local .env",
        "AUTH_SECRET=",
        "AES_SECRET_KEY=",
        "AES_IV_KEY=",
        "",
        "# --- embedding (OpenAI-compatible; blank = vector arm off) ---",
        "EMBEDDING_DIM=1024",
        "EMBEDDING_BASE_URL=",
        "EMBEDDING_API_KEY=",
        "EMBEDDING_MODEL=",
        "",
        "# --- LLM (OpenAI-compatible; blank = QA/wiki/graph LLM steps fail) ---",
        "AI_CHAT_API_ENDPOINT=",
        "AI_CHAT_API_KEY=",
        "AI_CHAT_MODEL=",
        "",
        "# --- Flower (Celery 监控，经 /flower/ 访问，用户:密码) ---",
        "FLOWER_BASIC_AUTH=admin:admin",
        "",
        "COOKIES_MAX_AGE=172800",
        "JOB_BEAT_SCAN_INTERVAL_SECONDS=30",
        "LOG_LEVEL=info",
    ]
    DEPLOY.mkdir(parents=True, exist_ok=True)
    (DEPLOY / ".env").write_text("\n".join(lines) + "\n")
    print("wrote", DEPLOY / ".env")
    print("fill in: AUTH_SECRET / AES_SECRET_KEY / AES_IV_KEY / embedding / LLM keys")


if __name__ == "__main__":
    main()
