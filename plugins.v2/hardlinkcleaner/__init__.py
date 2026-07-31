import os
import re
import shutil
import threading
import time
import traceback
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer
from watchdog.observers.polling import PollingObserver

from app import schemas
from app.log import logger
from app.plugins import _PluginBase


# --------------------------------------------------------------------------
# 核心设计：源目录 → 硬链接目录 的路径映射
#
# MoviePilot 的硬链接目录默认是源目录的“镜像”，相对路径结构完全一致。
# 因此“映射关系”不是需要持久化的数据，而是由目录结构直接推导：
#
#     link_path = hardlink_dir / source_path.relative_to(source_root)
#
# 优点：O(1) 查找、零状态、自我修复（文件移动/重命名后不会像 dict 那样过期）。
# 仅当硬链接目录与源目录结构非镜像时，才需要额外的持久化索引（本插件默认不采用）。
# --------------------------------------------------------------------------


class _DeleteHandler(FileSystemEventHandler):
    """
    watchdog 事件处理器：仅把【源目录】中的删除/移出事件转给插件。
    绝不监控硬链接目录（单向）。
    """

    def __init__(self, plugin: Any, **kwargs):
        super().__init__(**kwargs)
        self._plugin = plugin

    def on_deleted(self, event):
        # 文件或目录被删除
        self._plugin.event_handler(event_path=event.src_path, is_dir=event.is_directory)

    def on_moved(self, event):
        # 文件被移出源目录（如移到回收站 / 其他盘）== 源已不在，按删除处理。
        # 但若 dest 仍在某个源目录内（源内重命名），则跳过。
        dest = getattr(event, "dest_path", None)
        if dest and self._plugin.is_in_source(dest):
            return
        self._plugin.event_handler(event_path=event.src_path, is_dir=event.is_directory)


class HardLinkCleaner(_PluginBase):
    # 插件名称
    plugin_name = "硬链接联动清理"
    # 插件描述
    plugin_desc = "单向监控源目录，源文件/文件夹被删除时自动清理硬链接目录中的对应硬链接及其刮削产物（.nfo/海报等），并级联清理空目录。"
    # 插件图标
    plugin_icon = "hardlinkcleaner.png"
    # 插件版本
    plugin_version = "1.0.0"
    # 插件作者
    plugin_author = "157888390"
    # 作者主页
    author_url = "https://github.com/157888390"
    # 插件配置项ID前缀
    plugin_config_prefix = "hardlinkcleaner_"
    # 加载顺序
    plugin_order = 6
    # 可使用的用户级别
    auth_level = 1

    # 私有属性
    _observer = []
    _worker: Optional[threading.Thread] = None
    _event = threading.Event()
    _lock = threading.Lock()
    _pending: Dict[str, Dict[str, Any]] = {}      # link路径 -> {kind, due}
    _enabled = False
    _mode = "compatibility"          # compatibility=轮询(网络盘) / fast=inotify(本地盘)
    _source_dirs = ""                # 源目录，每行一个
    _hardlink_dir = ""               # 硬链接目录（不监控）
    _delete_delay = 2                # 删除去抖秒数，避免删除风暴中反复操作
    _clean_empty = True              # 级联清理空目录
    _onlyonce = False                # 启动后执行一次孤儿清理扫描
    _source_roots: List[Path] = []
    _hardlink_root: Optional[Path] = None

    # 单文件刮削产物的常见命名（与 MoviePilot/Emby/Kodi 对齐）
    _ART_SUFFIXES = [
        ".nfo",
        ".jpg", ".jpeg", ".png", ".webp",
        "-thumb.jpg", "-thumb.jpeg", "-thumb.png",
        "-fanart.jpg", "-fanart.png",
        "-landscape.jpg", "-landscape.png",
    ]

    # ==================================================================
    # 生命周期
    # ==================================================================
    def init_plugin(self, config: dict = None):
        self._source_roots = []
        self._pending = {}
        self._event = threading.Event()

        if config:
            self._enabled = config.get("enabled")
            self._mode = config.get("mode") or "compatibility"
            self._source_dirs = config.get("source_dirs") or ""
            self._hardlink_dir = config.get("hardlink_dir") or ""
            try:
                self._delete_delay = int(config.get("delete_delay") or 2)
            except (TypeError, ValueError):
                self._delete_delay = 2
            self._clean_empty = config.get("clean_empty", True)
            self._onlyonce = config.get("onlyonce") or False

        # 解析源根目录（保留配置值，即使当前不存在也记录，便于后续挂载）
        self._source_roots = [
            Path(d.strip()) for d in self._source_dirs.split("\n") if d.strip()
        ]
        self._hardlink_root = Path(self._hardlink_dir) if self._hardlink_dir else None

        # 先停止现有监控与 worker
        self.stop_service()

        if not self._enabled:
            return

        if not self._source_roots:
            logger.warn("硬链接联动清理已启用，但未配置源目录")
        if not self._hardlink_root:
            logger.warn("硬链接联动清理已启用，但未配置硬链接目录")

        # 启动后台删除 worker
        self._event.clear()
        self._worker = threading.Thread(target=self.__worker, daemon=True)
        self._worker.start()

        # 仅监控【源目录】
        for src in self._source_roots:
            try:
                if self._mode == "compatibility":
                    observer = PollingObserver(timeout=10)
                else:
                    observer = Observer(timeout=10)
                self._observer.append(observer)
                observer.schedule(_DeleteHandler(self), path=str(src), recursive=True)
                observer.daemon = True
                observer.start()
                logger.info(f"硬链接联动清理监控已启动：{src}（{self._mode}模式，仅监控源目录）")
            except Exception as e:
                err = str(e)
                if "inotify" in err and "reached" in err:
                    logger.warn(
                        f"启动监控失败：{err}，请在宿主机执行 "
                        "echo fs.inotify.max_user_watches=524288 | sudo tee -a /etc/sysctl.conf && sudo sysctl -p"
                    )
                else:
                    logger.error(f"启动监控失败 {src}：{err}")

        if self._onlyonce:
            logger.info("硬链接联动清理：3秒后执行一次孤儿扫描")
            threading.Timer(3.0, self.orphan_scan).start()
            self._onlyonce = False
            self.__save_config()

    def __save_config(self):
        self.update_config({
            "enabled": self._enabled,
            "mode": self._mode,
            "source_dirs": self._source_dirs,
            "hardlink_dir": self._hardlink_dir,
            "delete_delay": self._delete_delay,
            "clean_empty": self._clean_empty,
            "onlyonce": False,
        })

    def get_state(self) -> bool:
        return self._enabled

    def stop_service(self):
        # 停止监控
        for observer in self._observer:
            try:
                observer.stop()
                observer.join(timeout=5)
            except Exception as e:
                logger.error(f"停止监控失败：{str(e)}")
        self._observer = []
        # 停止 worker
        self._event.set()
        if self._worker and self._worker.is_alive():
            self._worker.join(timeout=5)
        self._worker = None

    # ==================================================================
    # 路径映射管理（核心）
    # ==================================================================
    def is_in_source(self, path: str) -> bool:
        """判断路径是否位于任一源根目录内"""
        p = Path(path)
        for root in self._source_roots:
            if p == root or self.__is_relative_to(p, root):
                return True
        return False

    def map_source_to_link(self, source_path: Path) -> Optional[Path]:
        """
        核心映射：源路径 -> 硬链接路径（结构镜像，O(1)、零状态）。

        仅当 source_path 位于某个源根目录、且硬链接根已配置时返回映射结果，
        否则返回 None（事件不属于本插件职责范围）。
        """
        if not self._hardlink_root:
            return None
        source_path = Path(source_path)
        for root in self._source_roots:
            if source_path == root or self.__is_relative_to(source_path, root):
                rel = source_path.relative_to(root)
                return self._hardlink_root / rel
        return None

    @staticmethod
    def __is_relative_to(path: Path, base: Path) -> bool:
        try:
            return path.is_relative_to(base)
        except Exception:
            return False

    # ==================================================================
    # 事件处理
    # ==================================================================
    def event_handler(self, event_path: str, is_dir: bool):
        """
        watchdog 回调：源目录中发生删除/移出时，定位硬链接目标并排入去抖队列。
        """
        try:
            ep = Path(event_path)
            # 安全护栏：绝不处理硬链接目录自身产生的事件（虽然本就不监控）
            if self._hardlink_root and self.__is_relative_to(ep, self._hardlink_root):
                return
            link = self.map_source_to_link(ep)
            if not link:
                return
            self.__schedule(link, "dir" if is_dir else "file")
        except Exception as e:
            logger.error(f"硬链接清理事件处理出错：{str(e)} - {traceback.format_exc()}")

    # ==================================================================
    # 删除调度（去抖 + 目录吸收子文件）
    # ==================================================================
    def __schedule(self, link_path: Path, kind: str):
        key = str(link_path)
        with self._lock:
            if kind == "dir":
                # 目录删除：吸收掉其下已排队的子文件，整目录一次性处理
                prefix = key + os.sep
                for k in list(self._pending.keys()):
                    if k == key or k.startswith(prefix):
                        del self._pending[k]
                self._pending[key] = {"kind": "dir", "due": time.time() + self._delete_delay}
            else:
                existing = self._pending.get(key)
                if existing and existing["kind"] == "dir":
                    return  # 该目录已排定整删，子文件跳过
                self._pending[key] = {"kind": "file", "due": time.time() + self._delete_delay}

    def __worker(self):
        """后台线程：到点后执行删除，避免删除风暴中反复 IO。"""
        while not self._event.is_set():
            now = time.time()
            due: List[Tuple[str, str]] = []
            with self._lock:
                for k, v in list(self._pending.items()):
                    if v["due"] <= now:
                        due.append((k, v["kind"]))
                        del self._pending[k]
            for k, kind in due:
                try:
                    if kind == "dir":
                        self.__delete_link_dir(Path(k))
                    else:
                        self.__delete_link_file(Path(k))
                except Exception as e:
                    logger.error(f"硬链接清理失败 {k}：{str(e)} - {traceback.format_exc()}")
            self._event.wait(0.5)

    # ==================================================================
    # 联动删除核心逻辑
    # ==================================================================
    def __under_hardlink(self, path: Path) -> bool:
        if not self._hardlink_root:
            return False
        return path == self._hardlink_root or self.__is_relative_to(path, self._hardlink_root)

    def __episode_artifacts(self, link_file: Path) -> List[Path]:
        """
        单文件硬链接 + 其刮削产物（同 stem 的 .nfo / 海报等）。
        不含剧集级/剧级文件（tvshow.nfo、poster.jpg 等，stem 不同），
        那些仅在整目录删除时被一并移除。
        """
        parent = link_file.parent
        stem = link_file.stem
        files = [link_file]
        for suf in self._ART_SUFFIXES:
            files.append(parent / (stem + suf))
        return files

    def __delete_link_file(self, link_file: Path):
        if not self.__under_hardlink(link_file):
            logger.warn(f"安全检查失败，拒绝删除非硬链接文件：{link_file}")
            return
        for f in self.__episode_artifacts(link_file):
            if f.exists():
                try:
                    f.unlink()
                    logger.info(f"删除硬链接刮削产物：{f}")
                except Exception as e:
                    logger.error(f"删除失败 {f}：{str(e)}")
        # 级联清理空目录
        self.__clean_empty_parents(link_file.parent)

    def __delete_link_dir(self, link_dir: Path):
        if not self.__under_hardlink(link_dir):
            logger.warn(f"安全检查失败，拒绝删除非硬链接目录：{link_dir}")
            return
        if not link_dir.exists():
            return
        logger.info(f"删除硬链接目录：{link_dir}")
        try:
            shutil.rmtree(link_dir)
        except Exception as e:
            logger.error(f"删除目录失败 {link_dir}：{str(e)}")
            return
        self.__clean_empty_parents(link_dir.parent)

    def __clean_empty_parents(self, start: Path):
        """从 start 向上级联删除空目录，直到硬链接根目录为止。"""
        if not self._clean_empty:
            return
        cur = start
        while cur and cur != self._hardlink_root and self.__under_hardlink(cur):
            try:
                entries = list(cur.iterdir())
            except FileNotFoundError:
                cur = cur.parent
                continue
            if entries:
                break  # 非空，停止向上
            logger.info(f"删除空目录：{cur}")
            try:
                cur.rmdir()
            except Exception as e:
                logger.error(f"删除空目录失败 {cur}：{str(e)}")
                break
            cur = cur.parent

    # ==================================================================
    # 孤儿清理扫描（启动触发 / API 触发）
    # 扫描硬链接目录中“源已不存在”的文件并清理，再级联空目录。
    # ==================================================================
    def __source_exists(self, link_path: Path) -> bool:
        if not self._hardlink_root:
            return False
        try:
            rel = link_path.relative_to(self._hardlink_root)
        except Exception:
            return False
        for root in self._source_roots:
            if (root / rel).exists():
                return True
        return False

    def orphan_scan(self):
        if not self._hardlink_root or not self._hardlink_root.exists():
            logger.warn("硬链接目录不存在，跳过孤儿清理")
            return
        logger.info(f"开始孤儿清理扫描：{self._hardlink_root}")
        orphan_files: List[Path] = []
        for root, _dirs, files in os.walk(self._hardlink_root):
            for fn in files:
                fp = Path(root) / fn
                if not self.__source_exists(fp):
                    orphan_files.append(fp)
        for fp in orphan_files:
            try:
                self.__delete_link_file(fp)
            except Exception as e:
                logger.error(f"孤儿清理失败 {fp}：{str(e)}")
        logger.info(f"孤儿清理扫描完成，处理 {len(orphan_files)} 个孤儿文件")

    # ==================================================================
    # 远程触发 / API
    # ==================================================================
    def get_api(self) -> List[Dict[str, Any]]:
        return [{
            "path": "/hardlink_cleanup",
            "endpoint": self.api_cleanup,
            "methods": ["GET"],
            "summary": "硬链接孤儿清理",
            "description": "扫描硬链接目录，删除源已不存在的硬链接及其刮削产物",
        }]

    def api_cleanup(self) -> schemas.Response:
        threading.Thread(target=self.orphan_scan, daemon=True).start()
        return schemas.Response(success=True)

    # ==================================================================
    # 界面
    # ==================================================================
    def get_form(self) -> Tuple[List[dict], Dict[str, Any]]:
        return [
            {
                "component": "VForm",
                "content": [
                    {
                        "component": "VRow",
                        "content": [
                            {
                                "component": "VCol",
                                "props": {"cols": 12, "md": 4},
                                "content": [
                                    {"component": "VSwitch", "props": {"model": "enabled", "label": "启用插件"}},
                                ],
                            },
                            {
                                "component": "VCol",
                                "props": {"cols": 12, "md": 4},
                                "content": [
                                    {"component": "VSwitch", "props": {"model": "clean_empty", "label": "级联清理空目录"}},
                                ],
                            },
                            {
                                "component": "VCol",
                                "props": {"cols": 12, "md": 4},
                                "content": [
                                    {"component": "VSwitch", "props": {"model": "onlyonce", "label": "启动后孤儿扫描一次"}},
                                ],
                            },
                        ],
                    },
                    {
                        "component": "VRow",
                        "content": [
                            {
                                "component": "VCol",
                                "props": {"cols": 12, "md": 6},
                                "content": [
                                    {
                                        "component": "VTextarea",
                                        "props": {
                                            "model": "source_dirs",
                                            "label": "源目录（仅监控这些，每行一个）",
                                            "rows": 3,
                                            "placeholder": "/media/downloads/tv\n/media/downloads/movie",
                                        },
                                    },
                                ],
                            },
                            {
                                "component": "VCol",
                                "props": {"cols": 12, "md": 6},
                                "content": [
                                    {
                                        "component": "VTextField",
                                        "props": {
                                            "model": "hardlink_dir",
                                            "label": "硬链接目录（不监控，仅清理）",
                                            "placeholder": "/media/library",
                                        },
                                    },
                                ],
                            },
                        ],
                    },
                    {
                        "component": "VRow",
                        "content": [
                            {
                                "component": "VCol",
                                "props": {"cols": 12, "md": 6},
                                "content": [
                                    {
                                        "component": "VSelect",
                                        "props": {
                                            "model": "mode",
                                            "label": "监控模式",
                                            "items": [
                                                {"title": "兼容模式（轮询，网络盘用）", "value": "compatibility"},
                                                {"title": "性能模式（inotify）", "value": "fast"},
                                            ],
                                        },
                                    },
                                ],
                            },
                            {
                                "component": "VCol",
                                "props": {"cols": 12, "md": 6},
                                "content": [
                                    {
                                        "component": "VTextField",
                                        "props": {
                                            "model": "delete_delay",
                                            "label": "删除延迟(秒)",
                                            "type": "number",
                                            "placeholder": "2",
                                        },
                                    },
                                ],
                            },
                        ],
                    },
                    {
                        "component": "VRow",
                        "content": [
                            {
                                "component": "VCol",
                                "props": {"cols": 12},
                                "content": [
                                    {
                                        "component": "VAlert",
                                        "props": {
                                            "type": "warning",
                                            "variant": "tonal",
                                            "text": "单向监控：仅监听源目录的删除/移出事件，绝不会改动源目录。"
                                                    "删除硬链接不影响源文件；网络挂载目录（CD2/rclone/SMB）请选兼容模式。",
                                        },
                                    },
                                ],
                            },
                        ],
                    },
                ],
            }
        ], {
            "enabled": False,
            "clean_empty": True,
            "onlyonce": False,
            "source_dirs": "",
            "hardlink_dir": "",
            "mode": "compatibility",
            "delete_delay": 2,
        }

    def get_page(self) -> Optional[List[dict]]:
        return None
