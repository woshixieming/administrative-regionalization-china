"""村级人工修订台账：创建草稿、记录审核，构建时应用已审核且生效的修订。"""
import argparse
import json
import pwd
import os
import re
import uuid
from datetime import date, datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit, parse_qs
from publish import project_lock

ROOT = Path(__file__).resolve().parent.parent


def validate(record):
    if not re.fullmatch(r"[a-f0-9]{24}", record["id"]) or not re.fullmatch(r"\d{12}", record["code"]):
        raise ValueError("修订 ID 或村级代码无效")
    date.fromisoformat(record["effectiveDate"])
    url = urlsplit(record["source"])
    if url.scheme not in ("http", "https") or not url.hostname or url.username or url.password:
        raise ValueError("依据需为不含凭据的公开 HTTP/HTTPS 链接")
    if any(re.search("token|secret|password|signature|api.?key", key, re.I) for key in parse_qs(url.query)):
        raise ValueError("依据链接不能包含凭据或签名参数")
    if not record.get("reason", "").strip() or len(record["reason"]) > 1000:
        raise ValueError("修订原因不能为空，且不能超过 1000 字")
    if not record.get("name") and not record.get("streetCode"):
        raise ValueError("至少提供新名称或新父乡级代码")
    if record.get("streetCode") and not re.fullmatch(r"\d{9}000", record["streetCode"]):
        raise ValueError("父乡级必须为末三位为 000 的 12 位代码")
    if record.get("streetCode") and record["streetCode"][:2] != record["code"][:2]:
        raise ValueError("人工修订暂仅支持同省调整所属乡级；跨省调整需另行核实代码")
    if record.get("name") and len(record["name"]) > 200:
        raise ValueError("名称过长")
    if record["status"] not in ("draft", "approved", "rejected"):
        raise ValueError("审核状态无效")
    if record["status"] != "draft" and not record.get("history"):
        raise ValueError("已审核修订必须具备审核记录")


def save(path, record):
    path.parent.mkdir(parents=True, exist_ok=True)
    pending = path.with_suffix(".tmp")
    pending.write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n")
    pending.replace(path)


def apply_patches(villages, directory, today=None, street_codes=None):
    records = []
    for path in sorted(directory.glob("*.json")):
        record = json.loads(path.read_text())
        validate(record)
        if record["status"] == "approved":
            records.append(record)
    records.sort(key=lambda record: (record["effectiveDate"], record["id"]))
    today = today or date.today()
    by_code = {v["code"]: v for v in villages}
    public = []
    for record in records:
        active = date.fromisoformat(record["effectiveDate"]) <= today
        if active:
            node = by_code.get(record["code"])
            if node is None:
                raise ValueError(f"人工修订的村级代码不存在：{record['code']}")
            if record.get("name"):
                node["name"] = record["name"]
            if record.get("streetCode"):
                if street_codes is not None and record["streetCode"] not in street_codes:
                    raise ValueError("人工修订的目标乡级不在当前来源数据中")
                node["streetCode"] = record["streetCode"]
            node.update({"patchId": record["id"], "patchSource": record["source"], "effectiveDate": record["effectiveDate"]})
        public.append({**{key: record[key] for key in ("id", "code", "name", "streetCode", "reason", "source", "effectiveDate")}, "applied": active})
    return public


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    create = sub.add_parser("create")
    create.add_argument("--code", required=True)
    create.add_argument("--name", default="")
    create.add_argument("--street-code", default="")
    create.add_argument("--source", required=True)
    create.add_argument("--reason", required=True)
    create.add_argument("--effective-date", required=True)
    review = sub.add_parser("review")
    review.add_argument("id")
    review.add_argument("--decision", choices=("approved", "rejected"), required=True)
    review.add_argument("--reason", required=True)
    args = parser.parse_args(argv)
    if args.command == "review" and not args.reason.strip():
        parser.error("审核原因不能为空")
    with project_lock(ROOT):
        update_record(args, parser)


def update_record(args, parser):
    account = pwd.getpwuid(os.getuid()).pw_name
    now = datetime.now(timezone.utc).isoformat()
    if args.command == "create":
        record = {"id": uuid.uuid4().hex[:24], "code": args.code, "name": args.name.strip(), "streetCode": args.street_code,
                  "source": args.source, "reason": args.reason, "effectiveDate": args.effective_date,
                  "status": "draft", "createdAt": now, "createdBy": account, "history": []}
    else:
        if not re.fullmatch(r"[a-f0-9]{24}", args.id):
            parser.error("修订 ID 无效")
        record = json.loads((ROOT / "data/patches" / f"{args.id}.json").read_text())
        if record["status"] != "draft":
            parser.error("已审核记录不再覆盖，请创建新的修订记录")
        record["status"] = args.decision
        record["history"].append({"at": now, "by": account, "decision": args.decision, "reason": args.reason})
    validate(record)
    save(ROOT / "data/patches" / f"{record['id']}.json", record)
    print(f"修订 {record['id']}：{record['status']}")


if __name__ == "__main__":
    main()
