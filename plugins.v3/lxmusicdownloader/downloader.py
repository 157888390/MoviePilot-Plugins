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

# 多歌手分隔符。洛雪各音源普遍用「、」拼接，少数用逗号或分号。
# 故意不含 "/" 与 "&"：AC/DC、Simon & Garfunkel 这类乐队名会被误拆。
ARTIST_SEPARATORS = re.compile(r"[、,，;；]")


def split_artists(value: object) -> list[str]:
    """把歌手字段拆成独立艺术家，已是多值时原样去重返回。

    服务端（music-tag-native）只把 ``singer`` 当成一个字符串写进 artist 标签
    （``tagger.artist = metadata.singer``），而 MoviePilot 解析单值时也不做切分
    （``_music_string_list()`` 原样返回），于是「许嵩、何曼婷」会被当成一个整体
    艺术家，与 MusicBrainz 返回的 ['许嵩', '何曼婷'] 求不到交集，候选全部判为
    不匹配，最终拿不到 media_source/media_id，整理被拒。
    """
    if isinstance(value, (list, tuple, set)):
        items = [str(item) for item in value]
    else:
        items = ARTIST_SEPARATORS.split(str(value or ""))
    artists: list[str] = []
    for item in items:
        name = item.strip()
        if name and name not in artists:
            artists.append(name)
    return artists


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
        """记录流式写盘的分块大小（默认 64KB）。"""
        self._chunk_size = chunk_size

    def save_stream(
        self,
        response,
        dest_dir: Path,
        base_name: str,
        quality: str,
        artists: Optional[list[str]] = None,
    ) -> tuple[Path, int]:
        """把已打开的流式响应写盘，返回 (最终路径, 字节数)。

        先读第一个分块用于嗅探格式再决定扩展名，避免写完再改后缀导致返回一个
        实际并不存在的路径。``artists`` 给出 2 个及以上歌手时会在落盘后把
        artist 标签改写成多值。
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
            # 必须在 rename 之前补标签：改名后文件立刻对外可见，目录监控可能
            # 马上触发整理，放之后再写就会和监控抢时间。mutagen 按内容识别
            # 容器格式，所以 .part 这种无扩展名的临时文件也能正常写入。
            if artists:
                self.apply_artists(tmp, artists)
            tmp.replace(final)
        finally:
            if tmp.exists():
                tmp.unlink(missing_ok=True)

        return final, written

    @staticmethod
    def apply_artists(path: Path, artists: list[str]) -> bool:
        """把 artist 标签改写成多值，让 MoviePilot 能按多艺术家匹配。

        只对 2 个及以上歌手动手：单歌手时服务端写入的就是正确值，没必要多开
        一次文件。任何异常都只记 debug 并返回 False，绝不影响下载结果。
        """
        if len(artists) < 2:
            return False
        try:
            from mutagen import File as MutagenFile
        except ImportError:  # 运行环境没有 mutagen（理论上 MoviePilot 自带）
            logger.debug("运行环境缺少 mutagen，跳过多歌手标签拆分")
            return False
        try:
            audio = MutagenFile(str(path), easy=True)
            if audio is None:
                logger.debug(f"无法识别音频容器，跳过标签拆分：{path}")
                return False
            if audio.tags is None:
                audio.add_tags()
            audio["artist"] = list(artists)
            audio.save()
            logger.info(f"已把 artist 标签拆成多值：{Path(path).name} -> {' / '.join(artists)}")
            return True
        except Exception as err:  # noqa: BLE001
            logger.debug(f"写入多歌手标签失败：{path} - {err}")
            return False

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
