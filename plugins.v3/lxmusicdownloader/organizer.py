# -*- coding: utf-8 -*-
"""LX 下载文件的独立音乐库归档。"""

from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path
from typing import Any

from app.sdk.logging import logger

from .downloader import LxDownloader


def _safe_segment(value: object, fallback: str) -> str:
    """把标签值变成安全的单层目录名。"""
    text = str(value or "").strip()
    return LxDownloader.sanitize(text) if text else fallback


def _target_name(song: dict[str, Any], source_path: Path) -> str:
    """归档时使用曲号前缀，缺曲号时只使用歌名。"""
    title = _safe_segment(song.get("name"), source_path.stem or "未知歌曲")
    track = song.get("track_number") or song.get("track")
    try:
        number = int(str(track).split("/", 1)[0])
    except (TypeError, ValueError):
        number = 0
    prefix = f"{number:02d} - " if number > 0 else ""
    return f"{prefix}{title}{source_path.suffix.lower()}"


def _copy_exclusive(source: Path, destination: Path) -> bool:
    """先完整复制，再以硬链接原子提交；目标存在时绝不覆盖。"""
    fd, temporary = tempfile.mkstemp(prefix=".lx-", suffix=".lxpart", dir=destination.parent)
    staging = Path(temporary)
    try:
        os.close(fd)
        shutil.copy2(source, staging)
        try:
            os.link(staging, destination)
        except FileExistsError:
            return False
        return True
    finally:
        staging.unlink(missing_ok=True)


def organize_download(
    song: dict[str, Any],
    audio_path: Path,
    library_root: str,
) -> dict[str, Any]:
    """归档新下载音频及同名封面、歌词；保留源文件，不覆盖目标。"""
    target_audio: Path | None = None
    try:
        if not str(library_root or "").strip():
            raise ValueError("未配置音乐库目录")
        root = Path(library_root).expanduser()
        if not root.is_absolute():
            raise ValueError("音乐库目录必须为绝对路径")
        root = root.resolve()
        audio_path = Path(audio_path)
        if not audio_path.is_file():
            raise ValueError(f"源音频不存在：{audio_path}")
        singer = str(song.get("singer") or "").strip()
        artist = _safe_segment(singer, "未知歌手")
        album = _safe_segment(song.get("albumName") or song.get("album"), "未知专辑")
        target_dir = root / artist / album
        target_audio = target_dir / _target_name(song, audio_path)
        if not target_audio.resolve().is_relative_to(root):
            raise ValueError("归档路径超出音乐库目录")
        result = {"source": str(audio_path), "target": str(target_audio)}
        if target_audio.exists():
            return {**result, "status": "skipped", "message": "目标已有同名音频"}
        target_dir.mkdir(parents=True, exist_ok=True)
        if not _copy_exclusive(audio_path, target_audio):
            return {**result, "status": "skipped", "message": "目标已有同名音频"}
    except Exception as err:  # noqa: BLE001
        logger.warning(f"音乐归档失败：{audio_path} -> {target_audio} - {err}")
        return {"status": "failed", "source": str(audio_path), "target": str(target_audio or ""), "message": str(err)}

    copied: list[str] = []
    warnings: list[str] = []
    cover = next((audio_path.with_suffix(s) for s in (".jpg", ".jpeg", ".png", ".webp")
                  if audio_path.with_suffix(s).is_file()), None)
    for attachment in (cover, audio_path.with_suffix(".lrc")):
        if attachment is None or not attachment.is_file():
            continue
        destination = target_audio.with_suffix(attachment.suffix)
        try:
            if _copy_exclusive(attachment, destination):
                copied.append(str(destination))
        except Exception as err:  # noqa: BLE001
            warnings.append(f"{attachment.name}：{err}")
            logger.warning(f"音乐附件归档失败：{attachment} - {err}")
    message = f"已归档到 {target_audio}"
    if warnings:
        message += "；附件失败：" + "；".join(warnings)
    return {**result, "status": "organized", "attachments": copied, "warnings": warnings, "message": message}
