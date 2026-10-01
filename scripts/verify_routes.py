"""Verify new routes register correctly against a live DB."""

from fastapi.testclient import TestClient

from api.main import app

client = TestClient(app)

checks = [
    ("/api/v1/models", 401),   # anon -> 401 (auth required)
    ("/api/v1/mcps", 401),
    ("/api/v1/skills", 401),
    ("/api/v1/kbs", 200),      # kbs list is open (identity optional)
]
for path, expected in checks:
    r = client.get(path)
    ok = "PASS" if r.status_code == expected else "FAIL"
    print(f"{ok} GET {path} -> {r.status_code} (expected {expected})")

# kbs create with identity (scope personal)
import urllib.parse
from api.services.identity import Identity, encode_identity_cookie

ident = Identity(login_id="huqiang", user_id="huqiang", user_name="huqiang", team_name="????", team_id="x")
cookie = encode_identity_cookie(ident)
headers = {"Cookie": f"x-next-identity={cookie}"}
r = client.post("/api/v1/kbs", headers=headers, json={"name": "verify-kb-scope", "scope": "personal"})
print("PASS" if r.status_code == 200 else "FAIL", "POST /api/v1/kbs personal ->", r.status_code, r.json())

# cleanup the test KB
if r.status_code == 200:
    kb_id = r.json()["data"]["id"]
    r2 = client.delete(f"/api/v1/kbs/{kb_id}", headers=headers)
    print("PASS" if r2.status_code == 200 else "FAIL", "DELETE test kb ->", r2.status_code, r2.json())

print("ROUTE VERIFICATION DONE")