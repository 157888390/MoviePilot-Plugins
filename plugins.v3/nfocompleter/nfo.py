# -*- coding: utf-8 -*-
"""NFO 文件的解析、字段完整性校验与原子写回。

只依赖标准库，负责把一份 Kodi 规范的 NFO（movie / tvshow / episode）读成
「字段 -> 当前值」视图，判断哪些字段缺失或不完整，并把补全结果**原子**写回。
不负责数据来源（补全值由 ``enrich`` 模块提供）。

安全与约束：
- 只**追加缺失字段**，绝不覆盖已有有效值（覆盖语义由调用方用 ``overwrite`` 控制，
  本模块默认保守）。
- 写回前先备份原文件，再用「同目录临时文件 + ``os.replace``」原子替换。
- 解析失败抛出 ``xml.etree.ElementTree.ParseError``，由调用方按单文件异常隔离。
"""

from __future__ import annotations

import os
import shutil
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# 视为「缺失 / 不完整」的占位值（已归一为小写）
_PLACEHOLDER_VALUES = {
    "",
    "未知",
    "unknown",
    "n/a",
    "na",
    "none",
    "null",
    "-",
    "0",
    "0.0",
    "0000",
    "0000-00-00",
    "undefined",
    "待补充",
    "待完善",
}

# NFO 根节点 -> 内部类型名
_ROOT_KINDS = {
    "movie": "movie",
    "tvshow": "tvshow",
    "episodedetails": "episode",
}

# 字段规范：字段名 -> 取值类型
#   single: 单个文本元素（<title>xxx</title>）
#   multi : 多个同名文本元素（<genre>a</genre><genre>b</genre>）
#   person: 人物元素（<actor><name>..</name><role>..</role></actor>）
_FIELD_SCHEMAS: Dict[str, Dict[str, str]] = {
    "movie": {
        "title": "single",
        "originaltitle": "single",
        "year": "single",
        "premiered": "single",
        "runtime": "single",
        "plot": "single",
        "outline": "single",
        "tagline": "single",
        "rating": "single",
        "votes": "single",
        "mpaa": "single",
        "genre": "multi",
        "director": "multi",
        "credits": "multi",
        "studio": "multi",
        "country": "multi",
        "actor": "person",
    },
    "tvshow": {
        "title": "single",
        "originaltitle": "single",
        "showtitle": "single",
        "year": "single",
        "premiered": "single",
        "runtime": "single",
        "plot": "single",
        "outline": "single",
        "rating": "single",
        "votes": "single",
        "mpaa": "single",
        "genre": "multi",
        "studio": "multi",
        "actor": "person",
    },
    "episode": {
        "title": "single",
        "originaltitle": "single",
        "showtitle": "single",
        "season": "single",
        "episode": "single",
        "aired": "single",
        "runtime": "single",
        "plot": "single",
        "rating": "single",
        "votes": "single",
        "director": "multi",
        "credits": "multi",
        "actor": "person",
    },
}

# 核心（必需）字段：任一缺失即判「不完整」，用于统计与日志分级
_REQUIRED_FIELDS: Dict[str, set] = {
    "movie": {"title", "year", "plot", "genre"},
    "tvshow": {"title", "year", "plot", "genre"},
    "episode": {"title", "season", "episode"},
}


class NfoDocument:
    """一份 NFO 文件的读取、校验与原子写回。"""

    def __init__(self, path: Path):
        self.path = Path(path)
        self.tree = ET.parse(self.path)
        self.root = self.tree.getroot()
        kind = _ROOT_KINDS.get(self._local_name(self.root.tag))
        if kind is None:
            raise ValueError(f"不支持的 NFO 根节点：{self.root.tag}")
        self.kind = kind
        self.schema = _FIELD_SCHEMAS[self.kind]
        self.required = _REQUIRED_FIELDS[self.kind]

    # ------------------------------------------------------------------ #
    #                            读取与校验                                #
    # ------------------------------------------------------------------ #
    @staticmethod
    def _local_name(tag: str) -> str:
        """去掉命名空间前缀，如 ``{ns}movie`` -> ``movie``。"""
        return tag.rsplit("}", 1)[-1]

    @staticmethod
    def _is_placeholder(value: str) -> bool:
        """判断文本是否为占位值（空串 / 未知 / 0 等）。"""
        if value is None:
            return True
        return value.strip().lower() in _PLACEHOLDER_VALUES

    def _read_field(self, field: str, ftype: str) -> Optional[Any]:
        """读取字段当前的有效值；无效（缺失/占位）返回 None。"""
        if ftype == "single":
            elem = self.root.find(field)
            if elem is None:
                return None
            text = (elem.text or "").strip()
            return None if self._is_placeholder(text) else text
        if ftype == "multi":
            values = []
            for elem in self.root.findall(field):
                text = (elem.text or "").strip()
                if not self._is_placeholder(text) and text not in values:
                    values.append(text)
            return values or None
        if ftype == "person":
            persons = []
            for elem in self.root.findall(field):
                name = (elem.findtext("name") or "").strip()
                if self._is_placeholder(name):
                    continue
                role = (elem.findtext("role") or "").strip()
                persons.append({"name": name, "role": role})
            return persons or None
        return None

    def missing_fields(self) -> List[str]:
        """返回缺失或不完整的字段名列表（保持 schema 顺序）。"""
        return [f for f, t in self.schema.items() if self._read_field(f, t) is None]

    def missing_required(self) -> List[str]:
        """返回缺失的核心字段名列表。"""
        return [f for f in self.required if self._read_field(f, self.schema[f]) is None]

    def is_complete(self) -> bool:
        """核心字段齐全即视为完整。"""
        return not self.missing_required()

    def identity(self) -> Dict[str, Any]:
        """提取用于数据源定位的身份信息。"""
        ident: Dict[str, Any] = {"kind": self.kind}
        title = self._read_field("title", "single")
        if self.kind == "movie":
            ident["title"] = title
            ident["year"] = self._extract_year()
        elif self.kind == "tvshow":
            ident["title"] = title
            ident["year"] = self._extract_year()
        elif self.kind == "episode":
            ident["title"] = title
            ident["showtitle"] = self._read_field("showtitle", "single")
            ident["season"] = self._read_field("season", "single")
            ident["episode"] = self._read_field("episode", "single")
        ident["tmdb_id"] = self.read_tmdb_id()
        return ident

    def _extract_year(self) -> Optional[str]:
        """优先取 year 字段，其次从 premiered 取前 4 位。"""
        year = self._read_field("year", "single")
        if year:
            return year
        premiered = self._read_field("premiered", "single")
        if premiered and len(premiered) >= 4:
            return premiered[:4]
        return None

    def read_tmdb_id(self) -> Optional[int]:
        """读取 tmdb 类型的 uniqueid；无有效值返回 None。"""
        for elem in self.root.findall("uniqueid"):
            if (elem.get("type") or "").lower() == "tmdb":
                text = (elem.text or "").strip()
                if text.isdigit():
                    return int(text)
        return None

    # ------------------------------------------------------------------ #
    #                            写回                                     #
    # ------------------------------------------------------------------ #
    def apply(
        self,
        updates: Dict[str, Any],
        tmdb_id: Optional[int] = None,
        backup: bool = True,
    ) -> Tuple[int, str]:
        """把 ``updates`` 中「当前仍缺失」的字段写回并原子替换。

        :param updates: 字段名 -> 补全值；值类型按 schema（single=str, multi=list[str],
            person=list[{"name","role"}]）
        :param tmdb_id: 需要补写的 TMDB uniqueid（当前缺失时才会写入）
        :param backup: 写回前是否备份原文件，默认 True
        :return: (实际写入字段数, 备份文件路径)；无变化返回 (0, "")
        """
        changed: List[str] = []
        for field, value in updates.items():
            ftype = self.schema.get(field)
            if ftype is None:
                continue
            # 默认不覆盖：已有有效值就跳过
            if self._read_field(field, ftype) is not None:
                continue
            if value is None or (isinstance(value, (list, tuple)) and not value):
                continue
            self._write_field(field, ftype, value)
            changed.append(field)

        if tmdb_id and self.read_tmdb_id() is None:
            self._write_uniqueid(tmdb_id)
            changed.append("uniqueid")

        if not changed:
            return 0, ""

        backup_path = self._backup() if backup else ""
        self._atomic_write()
        return len(changed), backup_path

    def _write_field(self, field: str, ftype: str, value: Any) -> None:
        """向根节点追加缺失字段。"""
        if ftype == "single":
            elem = ET.SubElement(self.root, field)
            elem.text = str(value)
        elif ftype == "multi":
            for item in value:
                elem = ET.SubElement(self.root, field)
                elem.text = str(item)
        elif ftype == "person":
            for person in value:
                name = str(person.get("name") or "").strip()
                if self._is_placeholder(name):
                    continue
                elem = ET.SubElement(self.root, field)
                name_elem = ET.SubElement(elem, "name")
                name_elem.text = name
                role = str(person.get("role") or "").strip()
                if role:
                    role_elem = ET.SubElement(elem, "role")
                    role_elem.text = role

    def _write_uniqueid(self, tmdb_id: int) -> None:
        """写入 tmdb 类型的 uniqueid。"""
        elem = ET.SubElement(self.root, "uniqueid")
        elem.set("type", "tmdb")
        elem.set("default", "true")
        elem.text = str(tmdb_id)

    def _backup(self) -> str:
        """复制原文件为同目录 ``.bak`` 后缀。"""
        backup = self.path.with_name(self.path.name + ".bak")
        shutil.copy2(self.path, backup)
        return str(backup)

    def _atomic_write(self) -> None:
        """缩进美化后写临时文件，再用 ``os.replace`` 原子替换。"""
        try:
            ET.indent(self.tree, space="  ")
        except Exception:  # noqa: BLE001  旧 Python 无 indent 时退化为原样
            pass
        xml_bytes = ET.tostring(
            self.root, encoding="utf-8", xml_declaration=True, short_empty_elements=True
        )
        tmp = self.path.with_name(self.path.name + ".tmp")
        tmp.write_bytes(xml_bytes)
        os.replace(tmp, self.path)
