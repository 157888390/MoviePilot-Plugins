# -*- coding: utf-8 -*-
"""下载落盘：格式嗅探、文件名清洗、原子写入。"""

from __future__ import annotations

import html
import re
from pathlib import Path
from typing import Optional

from app.sdk.logging import logger

from .lxserver import QUALITY_EXT

# Windows / Linux 都不允许出现在文件名里的字符
INVALID_CHARS = re.compile(r'[\\/:*?"<>|\r\n\t]')


def sniff_suffix(head: bytes) -> Optional[str]:
    """按文件头嗅探真实容器格式。

    服务端存在请求 320k 却返回 ogg、请求 flac 却返回 mp3 的情况，所以扩展名
    必须以响应内容为准，不能只信音质参数。
    """
    if len(head) < 12:
        return None
    if head.startswith(b"ID3"):
        return ".mp3"
    if head[:2] in (b"\xff\xfb", b"\xff\xf3", b"\xff\xf2", b"\xff\xfa"):
        return ".mp3"
    if head.startswith(b"fLaC"):
        return ".flac"
    if head.startswith(b"OggS"):
        return ".ogg"
    if head.startswith(b"MAC "):
        return ".ape"
    if head.startswith(b"RIFF") and head[8:12] == b"WAVE":
        return ".wav"
    if head[4:8] == b"ftyp":
        return ".m4a"
    if head.startswith(b"\x1f\x8b"):
        return ".flac"
    return None


def sniff_image_suffix(head: bytes) -> Optional[str]:
    """按文件头判断图片格式。"""
    if head.startswith(b"\xff\xd8\xff"):
        return ".jpg"
    if head.startswith(b"\x89PNG"):
        return ".png"
    if head.startswith(b"RIFF") and head[8:12] == b"WEBP":
        return ".webp"
    return None


class LxDownloader:
    """把服务端返回的二进制流写到目标目录。"""

    def __init__(self, chunk_size: int = 64 * 1024) -> None:
        self._chunk_size = chunk_size

    def save_stream(
        self,
        response,
        dest_dir: Path,
        base_name: str,
        quality: str,
    ) -> tuple[Path, int]:
        """把已打开的流式响应写盘，返回 (最终路径, 字节数)。

        先读第一个分块用于嗅探格式再决定扩展名，避免写完再改后缀导致返回一个
        实际并不存在的路径。
        """
        base_name = self.sanitize(base_name)
        dest_dir = Path(dest_dir)
        dest_dir.mkdir(parents=True, exist_ok=True)

        iterator = response.iter_bytes(chunk_size=self._chunk_size)
        head = b""
        for chunk in iterator:
            head = chunk
            break

        suffix = sniff_suffix(head[:16]) or QUALITY_EXT.get(str(quality).lower(), ".mp3")
        final = self._unique_path(dest_dir / f"{base_name}{suffix}")
        tmp = final.with_name(final.name + ".part")

        written = 0
        try:
            with open(tmp, "wb") as file:
                if head:
                    file.write(head)
                    written += len(head)
                for chunk in iterator:
                    file.write(chunk)
                    written += len(chunk)
            tmp.replace(final)
        finally:
            if tmp.exists():
                tmp.unlink(missing_ok=True)

        return final, written

    def save_cover(self, response, song_path: Path) -> Optional[Path]:
        """把封面写到音频同名的图片文件。"""
        iterator = response.iter_bytes(chunk_size=self._chunk_size)
        head = b""
        for chunk in iterator:
            head = chunk
            break

        suffix = sniff_image_suffix(head[:8]) or ".jpg"
        final = song_path.with_suffix(suffix)
        tmp = final.with_name(final.name + ".part")
        try:
            with open(tmp, "wb") as file:
                if head:
                    file.write(head)
                for chunk in iterator:
                    file.write(chunk)
            tmp.replace(final)
            return final
        except Exception as err:  # noqa: BLE001
            logger.warn(f"封面写入失败：{err}")
            return None
        finally:
            if tmp.exists():
                tmp.unlink(missing_ok=True)

    @staticmethod
    def build_filename(song_info: dict, template: str = "{name} - {singer}") -> str:
        """按模板生成文件名主体（不含扩展名）。"""
        values = {
            "name": song_info.get("name") or "未知歌曲",
            "singer": song_info.get("singer") or "未知歌手",
            "album": song_info.get("albumName") or "",
            "source": song_info.get("source") or "",
            "songmid": song_info.get("songmid") or "",
        }
        try:
            rendered = template.format(**values)
        except (KeyError, IndexError, ValueError):
            rendered = f"{values['name']} - {values['singer']}"
        return LxDownloader.sanitize(rendered)

    @staticmethod
    def sanitize(name: str) -> str:
        """清洗成合法文件名，去掉非法字符与首尾空白点。"""
        cleaned = html.unescape(str(name)).replace("/", "&")
        cleaned = INVALID_CHARS.sub("", cleaned).strip().strip(".")
        return cleaned or "unknown"

    @staticmethod
    def _unique_path(path: Path) -> Path:
        """目标文件已存在时追加序号，避免覆盖历史下载。"""
        if not path.exists():
            return path
        stem, suffix, parent = path.stem, path.suffix, path.parent
        index = 1
        while True:
            candidate = parent / f"{stem} ({index}){suffix}"
            if not candidate.exists():
                return candidate
            index += 1
