"""HTTP-level verification of the scoped model registry endpoints.

Run with a live DATABASE_URL (host venv -> container MySQL):
    DATABASE_URL=mysql+pymysql://weknora:weknora@172.20.0.5:3306/knowledge \
    DB_TYPE=mysql uv run python scripts/verify_models_http.py
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from api.main import app
from api.services.identity import Identity, encode_identity_cookie

client = TestClient(app)

ident = Identity(
    login_id="huqiang",
    user_id="huqiang",
    user_name="huqiang",
    team_name="????",
    team_id="aa0330c67ffb4d4082c6f44d14fd8b27",
)
cookie = encode_identity_cookie(ident)  # raw value; the browser round-trips encoding
headers = {"Cookie": f"x-next-identity={cookie}"}

# 1. anon -> 401
r = client.get("/api/v1/models")
print("anon GET /models:", r.status_code, r.json().get("message"))

# 2. logged-in list
r = client.get("/api/v1/models", headers=headers)
print("login GET /models:", r.status_code, "items:", len(r.json()["data"]["items"]),
      "is_admin:", r.json()["data"]["is_admin"])
assert r.json()["data"]["is_admin"] is True

# 3. create system model (admin)
r = client.post(
    "/api/v1/models", headers=headers,
    json={"scope": "system", "name": "http-sys", "type": "chat", "provider": "generic",
          "base_url": "https://y/v1", "api_key": "sk-http-1", "is_default": False},
)
print("create system:", r.status_code, r.json().get("data", {}).get("item", {}).get("name"))
assert r.status_code == 200
sid = r.json()["data"]["item"]["id"]

# 4. providers
r = client.get("/api/v1/models/providers", headers=headers)
print("providers:", r.status_code, "count:", len(r.json()["data"]["items"]))

# 5. debug (no live network -> ok false, shape correct)
r = client.post(f"/api/v1/models/{sid}/debug", headers=headers, json={"input": "hello"})
body = r.json().get("data", {}) or {}
print("debug:", r.status_code, "ok:", body.get("ok"), "error:", str(body.get("error", ""))[:40])

# 6. set default
r = client.post(f"/api/v1/models/{sid}/default", headers=headers)
print("set default:", r.status_code, "is_default:", r.json()["data"]["item"]["is_default"])
assert r.json()["data"]["item"]["is_default"] is True

# 7. update with masked key keeps existing
r = client.put(f"/api/v1/models/{sid}", headers=headers, json={"api_key": "****keep"})
print("update masked:", r.status_code, "configured:", r.json()["data"]["item"]["api_key_configured"])
assert r.json()["data"]["item"]["api_key_configured"] is True

# 8. delete
r = client.delete(f"/api/v1/models/{sid}", headers=headers)
print("delete:", r.status_code, r.json()["data"])
assert r.json()["data"].get("deleted") is True

print("HTTP VERIFICATION OK")