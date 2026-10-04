#!/usr/bin/env python
"""数据源元数据种子（分类 / 类型 / 表单字段）—— 数据源管理的配置基线。

用法:
  cd /home/jenkins/chengkai/kb_compilation && uv run python scripts/seed_ds_meta.py

这些是「数据源管理」向导的**种子数据**（迁移自 data-synth system/datasources）：
  - modo_ds_category  左侧分类导航（大数据/关系型/存储/NoSQL）
  - modo_ds_type      可选数据源类型（16 种）
  - modo_ds_form_field 各类型的动态表单字段元数据（74 条，驱动新建/编辑表单渲染）

幂等：按固定 id upsert，已存在则补齐漂移字段（不改管理员手工调整过的 label/sorted 以外的必要项）。
img（类型图标 base64）不入种子：体积大且前端有默认图标兜底，管理员可按需补。
"""
from __future__ import annotations

from api.db import get_sessionmaker
from api.models.framework import Base, DsCategory, DsFormField, DsType

# (id, category_name, category_label, sorted)
DS_CATEGORIES: list[tuple[str, str, str, int]] = [
    ("7", "bigdata", "大数据", 30),
    ("9", "rdb", "关系数据库", 40),
    ("21", "storage", "存储", 60),
    ("13", "nosql", "NoSQL数据库", 100),
]

# (id, ds_type, ds_type_label, ds_category, sorted, is_support)
DS_TYPES: list[tuple[str, str, str, str, int, str]] = [
    ("101", "hive", "Hive", "bigdata", 10, "1"),
    ("110", "trino", "Trino", "bigdata", 100, "1"),
    ("404", "elasticsearch", "Elasticsearch", "nosql", 120, "1"),
    ("201", "mysql", "MySQL", "rdb", 20, "1"),
    ("204", "dm", "达梦", "rdb", 50, "1"),
    ("205", "kingbasees8", "rdb人大金仓KingbaseES8", "rdb", 60, "1"),
    ("206", "goldendb", "GoldenDB", "rdb", 60, "1"),
    ("208", "postgresql", "PostgreSQL", "rdb", 90, "1"),
    ("801", "hdfs", "HDFS", "storage", 50, "0"),
    ("802", "minio", "MinIO", "storage", 60, "1"),
    ("803", "s3", "S3", "storage", 70, "0"),
    ("804", "moosefs", "MooseFS", "storage", 80, "0"),
    ("805", "ozone", "Ozone", "storage", 90, "0"),
    ("806", "ftp", "FTP/SFTP", "storage", 100, "1"),
    ("807", "oss", "OSS", "storage", 110, "1"),
    ("808", "obs", "OBS", "storage", 110, "1"),
]

# (id, ds_type, name, label, widget, sorted, default_value, invisible, is_conf, options, place_hold, regex, ds_version)
DS_FORM_FIELDS: list[tuple] = [
    ("dm02", "dm", "url", "JDBC URL", "Input", 10, None, 0, 1, None, None, None, None),
    ("dm03", "dm", "dsAcct", "用户名", "Input", 20, None, 0, 1, None, None, None, None),
    ("dm04", "dm", "dsAuth", "密码", "Password", 30, None, 0, 0, None, None, None, None),
    ("dm06", "dm", "dsSchema", "schema", "Input", 70, None, 0, 1, None, None, None, None),
    ("es-default-10", "elasticsearch", "schema", "Schema", "Select", 10, None, 0, 1, "[{\"key\":\"http\",\"label\":\"http\",\"value\":\"http\"},{\"key\":\"https\",\"label\":\"https\",\"value\":\"https\"}]", None, None, None),
    ("es-default-20", "elasticsearch", "host", "主机", "Input", 20, None, 0, 1, None, None, None, None),
    ("es-default-30", "elasticsearch", "port", "端口", "Input", 30, None, 0, 1, None, None, None, None),
    ("es-default-40", "elasticsearch", "dsAcct", "用户名", "Input", 40, None, 0, 1, None, None, None, None),
    ("es-default-50", "elasticsearch", "dsAuth", "密码", "Password", 50, None, 0, 0, None, None, None, None),
    ("es-default-60", "elasticsearch", "index", "索引", "Input", 60, None, 0, 1, None, None, None, None),
    ("ftp-003", "ftp", "host", "主机", "Input", 30, None, 0, 1, None, None, None, None),
    ("ftp-004", "ftp", "port", "端口", "Integer", 40, None, 0, 1, None, None, None, None),
    ("ftp-005", "ftp", "dsAcct", "用户名", "Input", 50, None, 0, 1, None, None, None, None),
    ("ftp-006", "ftp", "dsAuth", "密码", "Password", 60, None, 0, 0, None, None, None, None),
    ("ftp-007", "ftp", "ftpPath", "默认路径", "Input", 70, None, 0, 1, None, None, None, None),
    ("ftp-008", "ftp", "ftpReact", "ftpReact", "FtpReact", 80, None, 0, 1, None, None, None, None),
    ("ftp-009", "ftp", "protocol", "protocol", "Input", 90, None, 1, 1, None, None, None, None),
    ("goldendb-10", "goldendb", "url", "JDBC URL", "Input", 10, None, 0, 1, None, None, None, "8.x"),
    ("goldendb-20", "goldendb", "dsAcct", "用户名", "Input", 30, None, 0, 1, None, None, None, "8.x"),
    ("goldendb-30", "goldendb", "dsAuth", "密码", "Password", 40, None, 0, 0, None, None, None, "8.x"),
    ("goldendb-60", "goldendb", "dsSchema", "schema", "Input", 70, None, 0, 1, None, None, None, "8.x"),
    ("hdfs-3003", "hdfs", "filePath", "默认目录", "Input", 30, None, 0, 1, None, None, None, "3.x"),
    ("hdfs-3005", "hdfs", "dsAcct", "用户名", "Input", 50, None, 0, 1, None, None, None, "3.x"),
    ("hdfs-3006", "hdfs", "dsAuth", "密码", "Password", 60, None, 0, 0, None, None, None, "3.x"),
    ("hdfs-3007", "hdfs", "dsAuthType", "开启Kerberos认证", "Kerberos", 70, None, 0, 1, None, "pwd", None, "3.x"),
    ("hive-3x-0100", "hive", "url", "JDBC URL", "Input", 100, None, 0, 1, None, None, None, "3.x"),
    ("hive-3x-0600", "hive", "execEngine", "执行引擎", "Select", 200, None, 0, 1, "[{\"key\":\"mr\",\"label\":\"MR\",\"value\":\"mr\"},{\"key\":\"tez\",\"label\":\"Tez\",\"value\":\"tez\"},{\"key\":\"spark\",\"label\":\"Spark\",\"value\":\"spark\"}]", None, None, "3.x"),
    ("hive-3x-0700", "hive", "queue", "队列名", "InputTag", 300, None, 0, 1, None, None, None, "3.x"),
    ("hive-3x-0800", "hive", "metastore", "元数据库", "Input", 400, None, 0, 1, "metastore", "metastore", None, "3.x"),
    ("hive-3x-1200", "hive", "location", "存储路径", "Input", 500, None, 0, 1, None, None, None, "3.x"),
    ("hive-3x-1300", "hive", "confDir", "配置文件目录", "Input", 600, None, 0, 1, None, None, None, "3.x"),
    ("hive-3x-0300", "hive", "dsAcct", "用户名", "Input", 700, None, 0, 1, None, None, None, "3.x"),
    ("hive-3x-0400", "hive", "dsAuth", "密码", "Password", 800, None, 0, 0, None, None, None, "3.x"),
    ("hive-3x-0500", "hive", "dsAuthType", "开启Kerberos认证", "Kerberos", 801, None, 0, 1, None, "pwd", None, "3.x"),
    ("hive-3x-0200", "hive", "dsSchema", "schema", "Input", 802, None, 0, 1, None, None, None, "3.x"),
    ("hive-3x-0900", "hive", "metastoreSaslEnabled", "元数据库认证", "Switch", 900, None, 1, 1, None, None, None, "3.x"),
    ("hive-3x-1000", "hive", "metastoreKeytab", "元数据库keytab", "Input", 1000, None, 1, 1, None, None, None, "3.x"),
    ("hive-3x-1100", "hive", "metastorePrincipal", "元数据库principal", "Input", 1100, None, 1, 1, None, None, None, "3.x"),
    ("hive-3x-1400", "hive", "driverClassName", "驱动", "TextArea", 1400, None, 1, 1, None, "org.apache.hive.jdbc.HiveDriver", None, "3.x"),
    ("hive-3x-1500", "hive", "hadoopConfig", "高可用配置", "TextAreaWithCopy", 1500, None, 1, 1, None, "{\\n\"dfs.nameservices\": \"defaultDfs\",\\n    \"dfs.ha.namenodes.defaultDfs\": \"namenode1\",\\n        \"dfs.namenode.rpc-address.defaultDfs.namenode1\": \"\",\\n            \"dfs.client.failover.proxy.provider.defaultDfs\": \"org.apache.hadoop.hdfs.server.namenode.ha.ConfiguredFailoverProxyProvider\"\\n}", None, "3.x"),
    ("kingbasees8-10", "kingbasees8", "url", "JDBC URL", "Input", 10, None, 0, 1, None, None, None, None),
    ("kingbasees8-20", "kingbasees8", "dsAcct", "用户名", "Input", 30, None, 0, 1, None, None, None, None),
    ("kingbasees8-30", "kingbasees8", "dsAuth", "密码", "Password", 40, None, 0, 0, None, None, None, None),
    ("kingbasees8-40", "kingbasees8", "dsAuthType", "开启Kerberos认证", "Kerberos", 50, None, 1, 1, None, "pwd", None, None),
    ("kingbasees8-50", "kingbasees8", "driverClassName", "驱动", "TextArea", 60, None, 1, 1, None, "com.kingbase8.Driver", None, None),
    ("kingbasees8-60", "kingbasees8", "dsSchema", "schema", "Input", 70, None, 0, 1, None, None, None, None),
    ("minio01", "minio", "url", "URL", "Input", 3, None, 0, 1, None, None, None, None),
    ("minio04", "minio", "bucketName", "桶名", "Input", 20, None, 0, 1, None, None, None, None),
    ("minio02", "minio", "dsAcct", "用户名", "Input", 40, None, 0, 1, None, None, None, None),
    ("minio03", "minio", "dsAuth", "密码", "Password", 50, None, 0, 1, None, None, None, None),
    ("mysql-8-x-10", "mysql", "url", "JDBC URL", "Input", 10, None, 0, 1, None, None, None, "8.x"),
    ("mysql-8-x-20", "mysql", "dsAcct", "用户名", "Input", 30, None, 0, 1, None, None, None, "8.x"),
    ("mysql-8-x-30", "mysql", "dsAuth", "密码", "Password", 40, None, 0, 0, None, None, None, "8.x"),
    ("mysql-8-x-40", "mysql", "dsAuthType", "开启Kerberos认证", "Kerberos", 50, None, 1, 1, None, "pwd", None, "8.x"),
    ("mysql-8-x-50", "mysql", "driverClassName", "驱动", "TextArea", 60, None, 1, 1, None, "com.mysql.cj.jdbc.Driver", None, "8.x"),
    ("mysql-8-x-60", "mysql", "dsSchema", "schema", "Input", 70, None, 0, 1, None, None, None, "8.x"),
    ("obs03", "obs", "url", "URL", "Input", 10, None, 0, 1, None, None, None, None),
    ("obs04", "obs", "bucketName", "Bucket Name", "Input", 20, None, 0, 1, None, None, None, None),
    ("obs01", "obs", "dsAcct", "accessKeyId", "Input", 30, None, 0, 1, None, None, None, None),
    ("obs02", "obs", "dsAuth", "accessKeySecret", "Password", 40, None, 0, 1, None, None, None, None),
    ("oss-30", "oss", "url", "URL", "Input", 10, None, 0, 1, None, None, None, None),
    ("oss-40", "oss", "bucketName", "Bucket Name", "Input", 20, None, 0, 1, None, None, None, None),
    ("oss-10", "oss", "dsAcct", "accessKeyId", "Input", 30, None, 0, 1, None, None, None, None),
    ("oss-20", "oss", "dsAuth", "accessKeySecret", "Password", 40, None, 0, 1, None, None, None, None),
    ("postgresql-16-x-10", "postgresql", "url", "JDBC URL", "Input", 10, None, 0, 1, None, None, None, "16.x"),
    ("postgresql-16-x-30", "postgresql", "dsAcct", "用户名", "Input", 30, None, 0, 1, None, None, None, "16.x"),
    ("postgresql-16-x-40", "postgresql", "dsAuth", "密码", "Password", 40, None, 0, 0, None, None, None, "16.x"),
    ("postgresql-16-x-70", "postgresql", "dsSchema", "schema", "Input", 70, None, 0, 1, None, None, None, "16.x"),
    ("trino10", "trino", "url", "JDBC URL", "Input", 10, None, 0, 1, None, None, None, None),
    ("trino20", "trino", "dsAcct", "用户名", "Input", 30, None, 0, 1, None, None, None, None),
    ("trino30", "trino", "dsAuth", "密码", "Password", 40, None, 0, 0, None, None, None, None),
    ("trino40", "trino", "dsAuthType", "开启Kerberos认证", "Kerberos", 50, None, 1, 1, None, "pwd", None, None),
    ("trino50", "trino", "driverClassName", "驱动", "TextArea", 60, None, 1, 1, None, "io.trino.jdbc.TrinoDriver", None, None),
    ("trino60", "trino", "dsSchema", "schema", "Input", 70, None, 0, 1, None, None, None, None),
]

def seed(session) -> tuple[int, int, int]:
    """返回 (分类, 类型, 字段) 的新建/修正计数。"""
    cat_new = cat_fix = 0
    for cid, cname, clabel, csorted in DS_CATEGORIES:
        row = session.get(DsCategory, cid)
        if row is None:
            session.add(DsCategory(id=cid, category_name=cname, category_label=clabel, sorted=csorted))
            cat_new += 1
        else:
            changed = False
            for attr, value in (("category_name", cname), ("category_label", clabel), ("sorted", csorted)):
                if getattr(row, attr) != value:
                    setattr(row, attr, value)
                    changed = True
            cat_fix += 1 if changed else 0
    session.commit()

    typ_new = typ_fix = 0
    for tid, tname, tlabel, tcat, tsorted, tsupport in DS_TYPES:
        row = session.get(DsType, tid)
        if row is None:
            session.add(
                DsType(
                    id=tid,
                    ds_type=tname,
                    ds_type_label=tlabel,
                    ds_category=tcat,
                    sorted=tsorted,
                    is_support=tsupport,
                )
            )
            typ_new += 1
        else:
            changed = False
            for attr, value in (
                ("ds_type", tname),
                ("ds_type_label", tlabel),
                ("ds_category", tcat),
                ("sorted", tsorted),
                ("is_support", tsupport),
            ):
                if getattr(row, attr) != value:
                    setattr(row, attr, value)
                    changed = True
            typ_fix += 1 if changed else 0
    session.commit()

    fld_new = fld_fix = 0
    for (
        fid, ftype, fname, flabel, fwidget, fsorted,
        fdefault, finvisible, fisconf, foptions, fplace, fregex, fversion,
    ) in DS_FORM_FIELDS:
        row = session.get(DsFormField, fid)
        if row is None:
            session.add(
                DsFormField(
                    id=fid,
                    ds_type=ftype,
                    name=fname,
                    label=flabel,
                    widget=fwidget,
                    sorted=fsorted,
                    default_value=fdefault,
                    invisible=finvisible,
                    is_conf=fisconf,
                    options=foptions,
                    place_hold=fplace,
                    regex=fregex,
                    ds_version=fversion,
                )
            )
            fld_new += 1
        else:
            changed = False
            for attr, value in (
                ("ds_type", ftype),
                ("name", fname),
                ("label", flabel),
                ("widget", fwidget),
                ("sorted", fsorted),
                ("default_value", fdefault),
                ("invisible", finvisible),
                ("is_conf", fisconf),
                ("options", foptions),
                ("place_hold", fplace),
                ("regex", fregex),
                ("ds_version", fversion),
            ):
                if getattr(row, attr) != value:
                    setattr(row, attr, value)
                    changed = True
            fld_fix += 1 if changed else 0
    session.commit()
    return cat_new, typ_new, fld_new


def main() -> None:
    Base.metadata.create_all(get_sessionmaker().kw["bind"])
    db = get_sessionmaker()()
    try:
        cn, tn, fn = seed(db)
        print(
            f"数据源元数据种子 OK: 分类 {len(DS_CATEGORIES)} / 类型 {len(DS_TYPES)} / "
            f"表单字段 {len(DS_FORM_FIELDS)}  [新建 {cn}/{tn}/{fn}]"
        )
    finally:
        db.close()


if __name__ == "__main__":
    main()
