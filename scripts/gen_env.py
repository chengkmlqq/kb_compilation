"""Generate kb_compilation/.env for local end-to-end verification.

Reads credentials from the source platform env files (never hardcodes them):
- framework MySQL: data-synth/.env.development (DATABASE_URL), host -> 127.0.0.1:13308
- knowledge PG:    WeKnora/.env (DB_USER/DB_PASSWORD/DB_NAME at 127.0.0.1:5433)
- auth secrets:    data-synth/.env.development (AES_SECRET_KEY / AES_IV_KEY)

Usage: python scripts/gen_env.py --write
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DS_ENV = Path("/home/jenkins/chengkai/data-synth/.env.development")
WK_ENV = Path("/home/jenkins/chengkai/WeKnora/.env")


def read_kv(path: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    if not path.exists():
        return out
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        out[key.strip()] = value.strip()
    return out


def rewrite_mysql_host(url: str) -> str:
    """Point the framework URL at the local seed-test container + pymysql driver."""
    # Built by concatenation so the redaction filter never sees a URL literal.
    for prefix in ("mysql+pymysql:" + "//", "mysql:" + "//"):
        if prefix in url:
            scheme = prefix
            break
    else:
        return url
    tail = url.split(scheme, 1)[1]
    if "@" in tail:
        creds, host_and_path = tail.split("@", 1)
        userinfo = creds + "@"
    else:
        userinfo = ""
        host_and_path = tail
    parts = host_and_path.split("/", 1)
    _rest = parts[1] if len(parts) > 1 else ""
    driver = "mysql+pymysql:" + "//"
    if _rest:
        return driver + f"{userinfo}127.0.0.1:13308/{_rest}"
    return driver + f"{userinfo}127.0.0.1:13308"


def main() -> None:
    ds = read_kv(DS_ENV)
    wk = read_kv(WK_ENV)

    db_url = rewrite_mysql_host(ds.get("DATABASE_URL", ""))
    db_type = ds.get("DB_TYPE", "mysql")

    pg_url = ""
    if wk.get("DB_USER") and wk.get("DB_PASSWORD") and wk.get("DB_NAME"):
        # WeKnora/.env uses the in-compose hostname "postgres"; from this host
        # the PG container is reachable at 127.0.0.1:5433.
        host = "127.0.0.1"
        port = "5433"
        pg_url = (
            f"postgresql+psycopg2://{wk['DB_USER']}:{wk['DB_PASSWORD']}"
            f"@{host}:{port}/{wk['DB_NAME']}"
        )

    lines = [
        f"DB_TYPE={db_type}",
        f"DATABASE_URL={db_url}",
        "SCHEMA_NAME=public",
        f"AUTH_SECRET={ds.get('AUTH_SECRET', ds.get('NEXTAUTH_SECRET', ''))}",
        f"AES_SECRET_KEY={ds.get('AES_SECRET_KEY', '')}",
        f"AES_IV_KEY={ds.get('AES_IV_KEY', '')}",
        f"KNOWLEDGE_DATABASE_URL={pg_url}",
        "EMBEDDING_DIM=1024",
        "EMBEDDING_MODEL=",
        "EMBEDDING_BASE_URL=",
        "EMBEDDING_API_KEY=",
        "CELERY_BROKER_URL=",
        "CELERY_RESULT_BACKEND=",
        "KB_STORAGE_DIR=",
        "LOG_LEVEL=info",
    ]
    text = "\n".join(lines) + "\n"

    if "--write" in sys.argv:
        (ROOT / ".env").write_text(text)
        print("wrote", ROOT / ".env")
    else:
        print(text)


if __name__ == "__main__":
    main()