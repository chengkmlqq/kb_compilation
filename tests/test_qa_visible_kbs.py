"""全库问答可见范围测试（2026-10-19 统一口径：own ∪ team ∪ system 并集）。

锁定的行为（api/routers/qa.py visible_kbs_for_qa）：

1. admin：未选知识库时全库问答检索全部 state=1 的知识库（不看团队/归属）。
2. 非 admin：按 visible_clauses 并集过滤——own（owner_user_id=自己）∪
   team（owner_team_name=自己团队）∪ system（scope=system），state=1 才可见；
   与知识库列表 kb_visible_clauses 口径一致，保证「列表看得到 → 全库也能检到」。
3. 他人 personal 库、其他团队库、state=0 的库不进入全库问答范围。
"""
from __future__ import annotations

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from api.db import Base
from api.models.knowledge import KbDatasource
from api.routers.qa import visible_kbs_for_qa


def _make_db():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, expire_on_commit=False)
    return Session()


def _kb(
    kb_id: str,
    *,
    scope: str,
    owner_user_id: str = "",
    owner_team_name: str = "",
    state: str = "1",
) -> KbDatasource:
    return KbDatasource(
        id=kb_id,
        name=f"库-{kb_id}",
        scope=scope,
        owner_user_id=owner_user_id or None,
        owner_team_name=owner_team_name or None,
        state=state,
    )


def test_admin_sees_all_active_kbs() -> None:
    db = _make_db()
    db.add_all([
        _kb("own", scope="personal", owner_user_id="u1"),
        _kb("myteam", scope="team", owner_team_name="T1"),
        _kb("other-team", scope="team", owner_team_name="T2"),
        _kb("sys", scope="system"),
        _kb("offline", scope="system", state="0"),
    ])
    db.commit()

    kbs = visible_kbs_for_qa(db, "admin", "ROOT", True)
    ids = sorted(k.id for k in kbs)
    # admin 全量（含其他团队/个人），但 state=0 仍排除
    assert ids == ["myteam", "other-team", "own", "sys"]
    assert "offline" not in ids


def test_normal_user_sees_own_team_system_union() -> None:
    db = _make_db()
    db.add_all([
        _kb("own", scope="personal", owner_user_id="u1"),
        _kb("team", scope="team", owner_team_name="T1"),
        _kb("sys", scope="system"),
        # 不可见：他人 personal / 其他团队 / 下线库
        _kb("other-own", scope="personal", owner_user_id="u2"),
        _kb("other-team", scope="team", owner_team_name="T2"),
        _kb("offline", scope="system", state="0"),
    ])
    db.commit()

    kbs = visible_kbs_for_qa(db, "u1", "T1", False)
    ids = sorted(k.id for k in kbs)
    assert ids == ["own", "sys", "team"]
    assert "other-own" not in ids
    assert "other-team" not in ids
    assert "offline" not in ids


def test_normal_user_without_team_sees_own_and_system() -> None:
    """用户无团队：own ∪ system（不误纳入任何 team 库）。"""
    db = _make_db()
    db.add_all([
        _kb("own", scope="personal", owner_user_id="u1"),
        _kb("sys", scope="system"),
        _kb("team", scope="team", owner_team_name="T1"),
    ])
    db.commit()

    kbs = visible_kbs_for_qa(db, "u1", "", False)
    ids = sorted(k.id for k in kbs)
    assert ids == ["own", "sys"]
