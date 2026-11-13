"""生成独立 SQL、导入 PostgreSQL，或刷新后发布数据库版本。"""
import argparse
import csv
import hashlib
import os
import subprocess
import sys
from pathlib import Path

from .config import DEFAULT_CONFIG, ROOT, load_config
from .dataset import checksum, documents, json_text, read_json, records

COLUMNS = "version,level,code,name,parent_level,parent_code,province_code,lineage,legacy_path,payload"


def literal(value):
    return "'" + value.replace("'", "''") + "'"


def generate_sql(source, output):
    source, output = Path(source), Path(output)
    meta = read_json(source / "meta.json")
    digest = checksum(source)  # 写文件前校验完整性，避免留下可导入的残缺版本。
    actual_digest = hashlib.sha256()
    version = literal(meta["datasetVersion"])
    output.parent.mkdir(parents=True, exist_ok=True)
    pending = output.with_name(output.name + ".tmp")
    try:
        with pending.open("w", encoding="utf-8", newline="") as stream:
            stream.write("\\set ON_ERROR_STOP on\nSET client_encoding = 'UTF8';\nSET standard_conforming_strings = on;\nBEGIN;\n")
            stream.write("SELECT pg_advisory_xact_lock(734219861);\n")
            stream.write((ROOT / "database/schema.sql").read_text(encoding="utf-8"))
            stream.write(f"\nDO $$ BEGIN IF EXISTS (SELECT 1 FROM divisions.datasets WHERE version={version} AND checksum <> '{digest}') THEN RAISE EXCEPTION 'Immutable dataset conflict'; END IF; END $$;\n")
            stream.write(f"INSERT INTO divisions.datasets(version,checksum,meta) VALUES ({version},'{digest}',{literal(json_text(meta))}::jsonb) ON CONFLICT DO NOTHING;\n")
            stream.write("CREATE TEMP TABLE import_regions (LIKE divisions.regions) ON COMMIT DROP;\n")
            stream.write(f"COPY import_regions ({COLUMNS}) FROM STDIN WITH (FORMAT csv, NULL '\\N');\n")
            writer = csv.writer(stream, lineterminator="\n")
            count = 0
            for row in records(source):
                actual_digest.update((json_text(row) + "\n").encode())
                values = list(row)
                values[7] = "{" + ",".join(values[7]) + "}"
                values[8] = "true" if values[8] else "false"
                values[9] = json_text(values[9])
                writer.writerow(["\\N" if value is None else value for value in values])
                count += 1
            stream.write("\\.\n")
            # 相同版本可重复导入，但不会覆盖已有不可变记录。
            stream.write(f"INSERT INTO divisions.regions ({COLUMNS}) SELECT {COLUMNS} FROM import_regions ON CONFLICT DO NOTHING;\n")
            for path, body in documents(source):
                if path != "status.json":
                    actual_digest.update((path + "\n" + json_text(body) + "\n").encode())
                conflict = "DO UPDATE SET body=EXCLUDED.body" if path == "status.json" else "DO NOTHING"
                stream.write(f"INSERT INTO divisions.documents VALUES ({version},{literal(path)},{literal(json_text(body))}::jsonb) ON CONFLICT(version,path) {conflict};\n")
            if actual_digest.hexdigest() != digest:
                raise ValueError("生成 SQL 期间源数据发生变化，请等待刷新结束后重试")
            stream.write(f"DO $$ BEGIN IF (SELECT count(*) FROM divisions.regions WHERE version={version}) <> {count} THEN RAISE EXCEPTION 'Dataset count mismatch'; END IF; END $$;\n")
            stream.write("SET CONSTRAINTS ALL IMMEDIATE;\n")
            stream.write(f"INSERT INTO divisions.current_dataset(singleton,version) VALUES(true,{version}) ON CONFLICT(singleton) DO UPDATE SET version=EXCLUDED.version;\n")
            stream.write("COMMIT;\nANALYZE divisions.regions;\n")
        pending.replace(output)
    finally:
        pending.unlink(missing_ok=True)
    return count


def import_sql(config, path):
    import psycopg
    from .config import connection_options
    try:
        # 读取本项目生成的单一 COPY 文件；不把 200+ MiB SQL 一次性装进内存。
        with Path(path).open(encoding="utf-8", newline="") as stream:
            if stream.readline() != "\\set ON_ERROR_STOP on\n":
                raise ValueError("仅支持 export-sql 生成的 SQL 文件")
            prefix = []
            for line in stream:
                if line.startswith("COPY import_regions ("):
                    copy_command = line
                    break
                prefix.append(line)
            else:
                raise ValueError("SQL 缺少 COPY 数据段")
            options = connection_options(config)
            # 导入包含建索引和全量 COPY，不能沿用查询接口的 30 秒限制。
            options["options"] = "-c statement_timeout=0"
            with psycopg.connect(**options, autocommit=True) as connection:
                connection.execute("".join(prefix))
                with connection.cursor().copy(copy_command) as copy:
                    for line in stream:
                        if line == "\\.\n":
                            break
                        copy.write(line.encode("utf-8"))
                    else:
                        raise ValueError("SQL 数据段不完整")
                suffix = stream.read()
                if "COMMIT;" not in suffix:
                    raise ValueError("SQL 缺少事务提交")
                connection.execute(suffix)
    except Exception:
        raise RuntimeError("SQL 导入失败，请检查连接、权限及文件完整性；未提交的事务自动回滚") from None


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    commands = parser.add_subparsers(dest="command", required=True)
    export = commands.add_parser("export-sql", help="生成含建表及全量数据的 psql 导入文件")
    export.add_argument("--source", type=Path, default=ROOT / "web/public/data")
    export.add_argument("--output", type=Path, default=ROOT / "database/data.sql")
    restore = commands.add_parser("import-sql", help="使用 INI 配置流式导入本项目生成的 SQL")
    restore.add_argument("--file", type=Path, default=ROOT / "database/data.sql")
    refresh = commands.add_parser("refresh", help="刷新文件数据，校验后导入 PostgreSQL")
    refresh.add_argument("--skip-fetch", action="store_true")
    args = parser.parse_args()
    if args.command == "export-sql":
        count = generate_sql(args.source, args.output)
        print(f"SQL 已生成：{args.output}；{count} 条区划记录")
    elif args.command == "import-sql":
        import_sql(load_config(args.config), args.file)
        print("数据库导入完成，当前版本已切换")
    else:
        config = load_config(args.config)
        command = ["bash", str(ROOT / "scripts/refresh.sh")]
        if args.skip_fetch:
            command.append("--skip-fetch")
        subprocess.run(command, cwd=ROOT, env={**os.environ, "SKIP_BUILD": "1"}, check=True)
        output = ROOT / "database/data.sql"
        generate_sql(ROOT / "web/public/data", output)
        import_sql(config, output)
        print("刷新及数据库导入完成；后端无需重启，已打开页面继续使用原版本")


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, RuntimeError, subprocess.CalledProcessError):
        print("操作失败：请检查配置、源数据完整性、后端依赖及数据库权限；未输出连接凭据。", file=sys.stderr)
        sys.exit(1)
