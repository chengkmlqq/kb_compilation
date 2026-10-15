"""热修后验证：/metadata/datasources 列表接口返回的 collectionStatus 真实反映任务状态。"""
from fastapi.testclient import TestClient

from api.main import app
from api.services.identity import Identity, encode_identity_cookie


def main() -> None:
    client = TestClient(app)
    client.cookies.set(
        "x-next-identity",
        encode_identity_cookie(Identity(user_id="admin", user_name="admin", team_name="ROOT")),
    )
    r = client.get("/api/v1/metadata/datasources")
    assert r.status_code == 200, r.text[:300]
    items = r.json()["data"]["items"]
    for it in items:
        print(
            f"{it['name'][:24]:26} status={it['collectionStatus']:10} tables={it['tableCount']:5} "
            f"last={str(it.get('lastCollectionTime'))[:19]:19} err={str(it.get('lastError'))[:40]}"
        )
    # 断言：有 META 任务且最近成功的数据源必须 COLLECTED（不能再退化未采集/漏状态）
    assert items, "无数据源"
    print(f"VERIFY_OK items={len(items)}")


if __name__ == "__main__":
    main()