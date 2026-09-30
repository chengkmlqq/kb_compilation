"""Auth/identity tests: crypto interop + cookie roundtrip + RBAC logic."""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from api.db import Base
from api.lib.crypto import aes_decrypt, aes_encrypt, des_decrypt, des_encrypt
from api.services.identity import (
    Identity,
    check_path_permission,
    decode_identity_cookie,
    encode_identity_cookie,
    normalize_identity_payload,
)
from api.models.framework import (
    Menu,
    RoleMenuRela,
    Team,
    TeamMember,
    User,
    UserRole,
    UserRoleRela,
)

# Golden values produced by the source platform's crypto-js (Node) with default keys.
NODE_AES_SAMPLES = [
    ("admin123", "0ul8acflRvZxbGbFANJoHA=="),
    ("Test@2024", "kNutP9CPm2aLPNfzuY00Ow=="),
    ("中文密码测试!!", "DGL0AR8bqVPCfhPML/R784DQUvNc0emBcquIT6Ttdwk="),
    ("a", "mbvU2QHIj3WRK1g8C72Pig=="),
]
NODE_DES_SAMPLE = ("{\"userId\":\"admin\"}", "73gKomuiPYZaAW6K92WnnxmSgz7mkYj9")


def test_aes_interop_with_node_cryptojs() -> None:
    """Decrypt Node-encrypted values; re-encrypt must reproduce them."""
    for plain, enc in NODE_AES_SAMPLES:
        assert aes_decrypt(enc) == plain
        assert aes_encrypt(plain) == enc


def test_des_interop_with_node_cryptojs() -> None:
    plain, enc = NODE_DES_SAMPLE
    assert des_decrypt(enc) == plain
    assert des_encrypt(plain) == enc


def test_identity_cookie_roundtrip() -> None:
    identity = Identity(
        login_id="a" * 32,
        user_id="admin",
        user_name="管理员",
        team_name="DEFAULT_TEAM",
        team_id="t" * 32,
        team_label="默认团队",
    )
    cookie = encode_identity_cookie(identity)
    decoded = decode_identity_cookie(cookie)
    assert decoded is not None
    assert decoded.user_id == "admin"
    assert decoded.user_name == "管理员"
    assert decoded.team_name == "DEFAULT_TEAM"


def test_decode_legacy_des_cookie() -> None:
    """Legacy `x` cookie (DES-encrypted JSON) must decode via fallback."""
    legacy = des_encrypt('{"userId":"admin","userName":"管理员"}')
    identity = decode_identity_cookie(legacy)
    assert identity is not None
    assert identity.user_id == "admin"
    assert identity.user_name == "管理员"


def test_normalize_identity_payload_snake_case() -> None:
    identity = normalize_identity_payload(
        {"login_id": "x", "user_id": "u", "user_name": "n", "team_name": "t", "team_id": "i", "team_label": "l"}
    )
    assert identity is not None
    assert identity.user_id == "u"
    assert identity.team_id == "i"


@pytest.fixture()
def rbac_db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    db = Session()
    # user + roles + team membership + menus
    db.add_all(
        [
            User(id="u" * 32, user_id="admin", user_name="Admin", state="1"),
            UserRole(role_id="r1", role_name="管理员", role_type="admin", state="1"),
            UserRoleRela(rela_id="x" * 32, role_id="r1", user_id="admin"),
            Team(team_id="t" * 32, team_name="DEFAULT_TEAM", label="默认团队", state="1"),
            TeamMember(
                member_id="m" * 32,
                team_name="DEFAULT_TEAM",
                user_id="admin",
                role_name="r1",
                state="1",
            ),
            Menu(menu_id="menu1", menu_name="用户管理", route="/system/users", state="1"),
            RoleMenuRela(rela_id="z" * 32, menu_id="menu1", role_id="r1"),
        ]
    )
    db.commit()
    yield db
    db.close()


def test_rbac_allows_controlled_route_with_role(rbac_db) -> None:
    allowed, reason = check_path_permission(rbac_db, "admin", "/system/users")
    assert allowed is True
    assert reason == ""


def test_rbac_uncontrolled_route_allowed(rbac_db) -> None:
    allowed, _ = check_path_permission(rbac_db, "admin", "/some/static/asset.js")
    assert allowed is True


def test_rbac_denies_when_no_role(rbac_db) -> None:
    allowed, reason = check_path_permission(rbac_db, "nobody", "/system/users")
    assert allowed is False
    assert reason in ("用户无角色", "无菜单权限")


def test_rbac_denies_unlinked_menu(rbac_db) -> None:
    # A controlled route with no role-menu link for this role.
    db = rbac_db
    db.add(Menu(menu_id="menu2", menu_name="秘密页面", route="/system/secret", state="1"))
    db.commit()
    allowed, reason = check_path_permission(db, "admin", "/system/secret")
    assert allowed is False
    assert reason == "无菜单权限"
