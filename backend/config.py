"""集中读取配置，不输出数据库连接信息。"""
import configparser
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG = ROOT / "config/backend.ini"


def load_config(path=DEFAULT_CONFIG):
    config = configparser.ConfigParser(interpolation=None)
    if not config.read(path, encoding="utf-8"):
        raise ValueError("配置文件不存在，请复制 config/backend.example.ini 为 config/backend.ini")
    for section in ("server", "postgres"):
        if section not in config:
            raise ValueError(f"配置缺少 [{section}]")
    server, pg = config["server"], config["postgres"]
    for section, key, low, high in (
        (server, "port", 1, 65535), (server, "max_requests", 1, 256),
        (pg, "port", 1, 65535), (pg, "connect_timeout", 1, 60),
        (pg, "statement_timeout_ms", 1, 300000),
        (pg, "pool_min", 1, 64), (pg, "pool_max", 1, 64),
    ):
        value = section.getint(key)
        if not low <= value <= high:
            raise ValueError(f"配置 {key} 超出范围")
    if pg.getint("pool_min") > pg.getint("pool_max"):
        raise ValueError("pool_min 不能超过 pool_max")
    for key in ("host", "dbname", "user"):
        if not pg.get(key, "").strip():
            raise ValueError(f"PostgreSQL 配置缺少 {key}")
    return config


def connection_options(config):
    pg = config["postgres"]
    return {**{key: pg[key] for key in ("host", "port", "dbname", "user", "password", "sslmode", "connect_timeout")},
            "application_name": "china-divisions",
            "options": f"-c statement_timeout={pg.getint('statement_timeout_ms')}"}
