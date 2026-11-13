"""费用科目自动归类 + 规则可维护。"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass

DEFAULT_RULES: list[tuple[str, str]] = [
    ("物业", "物业费"),
    ("餐费", "业务招待费"),
    ("餐饮", "业务招待费"),
    ("住宿", "差旅费"),
    ("酒店", "差旅费"),
    ("机票", "差旅费"),
    ("火车", "差旅费"),
    ("客运", "差旅费"),
    ("出租车", "差旅费"),
    ("网约车", "差旅费"),
    ("滴滴", "差旅费"),
    ("旅客运输", "差旅费"),
    ("车辆", "车辆使用费"),
    ("加油", "车辆使用费"),
    ("汽油", "车辆使用费"),
    ("停车", "车辆使用费"),
    ("通行费", "车辆使用费"),
    ("过路", "车辆使用费"),
    ("运输", "运输费"),
    ("物流", "运输费"),
    ("快递", "运输费"),
    ("软件", "软件服务费"),
    ("信息技术", "信息技术服务费"),
    ("技术服", "信息技术服务费"),
    ("云服务", "信息技术服务费"),
    ("电脑", "办公费"),
    ("办公", "办公费"),
    ("耗材", "办公费"),
    ("打印", "办公费"),
    ("文具", "办公费"),
    ("通讯", "通讯费"),
    ("话费", "通讯费"),
    ("宽带", "通讯费"),
    ("会议", "会议费"),
    ("培训", "培训费"),
    ("维修", "维修费"),
    ("租赁", "租赁费"),
    ("水电", "水电费"),
    ("电费", "水电费"),
    ("水费", "水电费"),
    ("广告", "广告宣传费"),
    ("宣传", "广告宣传费"),
    ("咨询", "咨询费"),
    ("审计", "审计费"),
    ("设计", "设计费"),
    ("印刷", "印刷费"),
    ("劳务", "劳务费"),
    ("工程", "工程款"),
    ("材料", "材料费"),
]

SUBJECTS = sorted({s for _k, s in DEFAULT_RULES} | {"其他"})


@dataclass
class Rule:
    keyword: str
    subject: str

    def to_dict(self) -> dict:
        return {"keyword": self.keyword, "subject": self.subject}


class ExpenseClassifier:
    """关键词归类器；规则按长度降序匹配，长词优先，避免「餐」误吃「餐饮」。"""

    def __init__(self, rules: list[Rule] | None = None):
        self.rules: list[Rule] = rules or [Rule(k, s) for k, s in DEFAULT_RULES]
        self._reindex()

    def _reindex(self) -> None:
        self.rules.sort(key=lambda r: (-len(r.keyword), r.keyword))
        self._map = [(r.keyword, r.subject) for r in self.rules]

    def set_rules(self, rules: list[Rule]) -> None:
        self.rules = rules
        self._reindex()

    def classify(self, text: str) -> str:
        t = (text or "").replace(" ", "")
        if not t:
            return "其他"
        for kw, subject in self._map:
            if kw and kw in t:
                return subject
        return "其他"

    # ---- 持久化（JSON，放在 data/ 下，跟着程序走） ----
    @classmethod
    def load(cls, path: str) -> "ExpenseClassifier":
        if not os.path.exists(path):
            return cls()
        try:
            with open(path, "r", encoding="utf-8") as fh:
                raw = json.load(fh)
            rules = [Rule(d["keyword"], d["subject"]) for d in raw.get("rules", [])]
            return cls(rules) if rules else cls()
        except Exception:
            return cls()

    def save(self, path: str) -> None:
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        payload = {"subjects": SUBJECTS, "rules": [r.to_dict() for r in self.rules]}
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False, indent=2)