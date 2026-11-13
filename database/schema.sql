-- PostgreSQL 14+；由有建表及安装 pg_trgm 权限的数据库所有者执行。
CREATE SCHEMA IF NOT EXISTS divisions;
CREATE EXTENSION IF NOT EXISTS pg_trgm;

CREATE TABLE IF NOT EXISTS divisions.datasets (
    version text PRIMARY KEY CHECK (version ~ '^[a-f0-9]{24}$'),
    checksum text NOT NULL,
    meta jsonb NOT NULL,
    imported_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS divisions.regions (
    version text NOT NULL REFERENCES divisions.datasets(version),
    level smallint NOT NULL CHECK (level BETWEEN 1 AND 5),
    code text NOT NULL,
    name text NOT NULL,
    parent_level smallint,
    parent_code text,
    province_code text NOT NULL,
    lineage text[] NOT NULL,
    legacy_path boolean NOT NULL,
    payload jsonb NOT NULL,
    PRIMARY KEY (version, level, code),
    CHECK ((level = 1 AND parent_level IS NULL AND parent_code IS NULL)
        OR (level > 1 AND parent_level = level - 1 AND parent_code IS NOT NULL)),
    CHECK (cardinality(lineage) = level AND lineage[level] = code),
    FOREIGN KEY (version, parent_level, parent_code)
        REFERENCES divisions.regions(version, level, code) DEFERRABLE INITIALLY DEFERRED
);

CREATE INDEX IF NOT EXISTS regions_children_idx ON divisions.regions(version, level, parent_code, code);
CREATE INDEX IF NOT EXISTS regions_province_idx ON divisions.regions(version, province_code, level, code);
CREATE INDEX IF NOT EXISTS regions_code_prefix_idx ON divisions.regions(version, code text_pattern_ops);
CREATE INDEX IF NOT EXISTS regions_name_idx ON divisions.regions USING gin (lower(name) gin_trgm_ops);
CREATE INDEX IF NOT EXISTS regions_lineage_idx ON divisions.regions USING gin (lineage);

CREATE TABLE IF NOT EXISTS divisions.documents (
    version text NOT NULL REFERENCES divisions.datasets(version),
    path text NOT NULL,
    body jsonb NOT NULL,
    PRIMARY KEY (version, path)
);

CREATE TABLE IF NOT EXISTS divisions.current_dataset (
    singleton boolean PRIMARY KEY DEFAULT true CHECK (singleton),
    version text NOT NULL REFERENCES divisions.datasets(version)
);
