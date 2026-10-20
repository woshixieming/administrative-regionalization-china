"""截止日期必须来自已确认版本或明确指定，不按当前年份推断。"""
import os
import re
from datetime import date


def official_cutoff(previous=None):
    override = os.environ.get("MCA_AS_OF")
    value = override or (previous or {}).get("asOf")
    if not value:
        raise ValueError("首次抓取需通过 MCA_AS_OF 指定官方发布的截止日期；后续默认保留已确认日期")
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        raise ValueError("官方截止日期必须为 YYYY-MM-DD")
    if date.fromisoformat(value) > date.today():
        raise ValueError("数据截止日期不能在未来")
    return value, "explicit" if override else "retained"
