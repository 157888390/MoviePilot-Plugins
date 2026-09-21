# -*- coding: utf-8 -*-
"""多歌手标签拆分的单元测试。

只覆盖纯函数 `split_artists` 与 `apply_artists` 的保守分支：
- 分隔符选择（顿号/逗号/分号要拆，`/` 与 `&` 不能拆，否则 AC/DC 之类的乐队名会被破坏）；
- 少于 2 位歌手时不动文件；
- 遇到非音频文件时返回 False 而不抛出。

`downloader.py` 依赖 MoviePilot 运行时（`app.sdk.logging`）且会 `from .lxserver import
QUALITY_EXT`（连带 httpx2），因此导入前先用桩模块顶掉，避免为纯函数测试拉起整个后端。
不依赖 mutagen，也不做真实网络与磁盘音频写入。
"""

from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path

# tests/v3/lxmusicdownloader/ -> 仓库根
REPO_ROOT = Path(__file__).resolve().parents[3]
PLUGIN_DIR = REPO_ROOT / "plugins.v3" / "lxmusicdownloader"


def _install_stubs() -> None:
    """用桩模块顶掉 MoviePilot 运行时与 lxserver，只留 downloader 本身。"""
    if "app.sdk.logging" not in sys.modules:
        app = types.ModuleType("app")
        app.__path__ = []  # type: ignore[attr-defined]
        sdk = types.ModuleType("app.sdk")
        sdk.__path__ = []  # type: ignore[attr-defined]
        logging_stub = types.ModuleType("app.sdk.logging")

        class _Logger:
            """吞掉所有日志调用的空实现。"""

            def __getattr__(self, _name: str):
                return lambda *args, **kwargs: None

        logging_stub.logger = _Logger()
        sys.modules["app"] = app
        sys.modules["app.sdk"] = sdk
        sys.modules["app.sdk.logging"] = logging_stub

    if "lxmusicdownloader" not in sys.modules:
        package = types.ModuleType("lxmusicdownloader")
        package.__path__ = [str(PLUGIN_DIR)]  # type: ignore[attr-defined]
        sys.modules["lxmusicdownloader"] = package

    if "lxmusicdownloader.lxserver" not in sys.modules:
        lxserver = types.ModuleType("lxmusicdownloader.lxserver")
        lxserver.QUALITY_EXT = {"flac": ".flac", "320k": ".mp3", "128k": ".mp3"}
        sys.modules["lxmusicdownloader.lxserver"] = lxserver


def _load_downloader() -> types.ModuleType:
    """按文件路径加载 downloader 模块，绕开插件包的 __init__。"""
    _install_stubs()
    name = "lxmusicdownloader.downloader"
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, PLUGIN_DIR / "downloader.py")
    if spec is None or spec.loader is None:  # pragma: no cover - 路径固定存在
        raise AssertionError(f"无法加载插件模块：{PLUGIN_DIR / 'downloader.py'}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


downloader = _load_downloader()


def test_split_artists_splits_lx_separator():
    """洛雪各音源默认用顿号拼接，必须拆开。"""
    assert downloader.split_artists("许嵩、何曼婷") == ["许嵩", "何曼婷"]


def test_split_artists_keeps_single_artist():
    assert downloader.split_artists("周杰伦") == ["周杰伦"]


def test_split_artists_supports_comma_and_semicolon():
    assert downloader.split_artists("A, B") == ["A", "B"]
    assert downloader.split_artists("A，B") == ["A", "B"]
    assert downloader.split_artists("A; B") == ["A", "B"]
    assert downloader.split_artists("A；B") == ["A", "B"]


def test_split_artists_does_not_break_band_names():
    """/ 与 & 是乐队名的合法字符，不能当分隔符。"""
    assert downloader.split_artists("AC/DC") == ["AC/DC"]
    assert downloader.split_artists("Simon & Garfunkel") == ["Simon & Garfunkel"]


def test_split_artists_dedupes_and_tolerates_empty():
    assert downloader.split_artists("许嵩、许嵩") == ["许嵩"]
    assert downloader.split_artists("") == []
    assert downloader.split_artists(None) == []


def test_split_artists_accepts_sequence():
    assert downloader.split_artists(["许嵩", "何曼婷"]) == ["许嵩", "何曼婷"]


def test_apply_artists_skips_single_artist(tmp_path):
    """单歌手时服务端写入的就是正确值，不应再开一次文件。"""
    target = tmp_path / "single.flac"
    target.write_bytes(b"fake")
    assert downloader.LxDownloader.apply_artists(target, ["周杰伦"]) is False
    assert target.read_bytes() == b"fake"


def test_apply_artists_is_safe_on_non_audio(tmp_path):
    """识别不出容器格式时静默返回 False，不能把下载结果搞崩。"""
    target = tmp_path / "broken.bin"
    target.write_bytes(b"not an audio container at all")
    assert downloader.LxDownloader.apply_artists(target, ["许嵩", "何曼婷"]) is False
