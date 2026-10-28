# -*- coding: utf-8 -*-
"""NFO 补全插件。

监控指定目录下的 ``.nfo`` 文件，解析其内容，检测缺失或不完整的字段，并调用宿主
TMDB 模块补齐（标题、年份、简介、类型、评分、演职员等），最后**原子**写回。

设计要点：
- **目录监控**：watchdog（默认轮询 ``PollingObserver``，兼容 Docker/远程挂载）+ 启动时
  全量扫描 + 定时扫描（``get_service``）+ 手动 ``/nfo_scan`` 命令。
- **处理模型**：watchdog 回调只做廉价过滤并入队，真正的解析/取数/写回由单消费者线程
  串行执行，避免并发刮削打爆 TMDB。
- **写回安全**：只补缺失字段、不覆盖已有值；写前备份（``.bak``）；临时文件 + 原子替换。
- **异常隔离**：每个文件独立 try/except，单文件失败不影响其它文件，也不拖垮主线程。
"""

from __future__ import annotations

import queue
import threading
import time
import traceback
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from apscheduler.triggers.cron import CronTrigger
from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer
from watchdog.observers.polling import PollingObserver

from app.schemas.types import EventType, MessageType
from app.sdk.events import Event, eventmanager
from app.sdk.logging import logger
from app.sdk.plugin import _PluginBase

from .enrich import TmdbEnricher
from .nfo import NfoDocument

DEFAULT_CRON = "0 3 * * *"


class _NfoEventHandler(FileSystemEventHandler):
    """把新增/移入的 .nfo 事件转给插件入队，回调里不做重活。"""

    def __init__(self, plugin: "NfoCompleter"):
        self._plugin = plugin

    def on_created(self, event):
        self._dispatch(event)

    def on_moved(self, event):
        self._dispatch(event)

    def _dispatch(self, event):
        if event.is_directory:
            return
        path = getattr(event, "dest_path", None) or event.src_path
        self._plugin.enqueue(path)


class NfoCompleter(_PluginBase):
    """NFO 补全插件主类。"""

    plugin_name = "NFO 补全"
    plugin_desc = "监控目录中的 .nfo 文件，检测缺失或不完整的字段并用 TMDB 数据补全。"
    plugin_label = "NFO,元数据,补全,刮削"
    # 自定义图标必须写成完整 URL：裸文件名只会去官方库 icons/ 里找，找不到就回退成拼图占位图
    plugin_icon = (
        "https://raw.githubusercontent.com/157888390/MoviePilot-Plugins"
        "/main/icons/nfocompleter.png"
    )
    plugin_version = "1.0.0"
    plugin_author = "157888390"
    author_url = "https://github.com/157888390"
    plugin_config_prefix = "nfocompleter_"
    plugin_order = 62
    auth_level = 1

    _enabled = False
    _monitor_dirs = ""
    _mode = "compatibility"
    _scan_cron = DEFAULT_CRON
    _backup = True
    _dry_run = False
    _notify = False

    # 运行时状态（实例级，V3 要求不跨实例共享可变状态）
    _observers: List[Any] = []
    _queue: Optional[queue.Queue] = None
    _worker: Optional[threading.Thread] = None
    _stop_event: Optional[threading.Event] = None
    _lock: Optional[threading.Lock] = None
    _enricher: Optional[TmdbEnricher] = None
    _stats: Dict[str, int] = {
        "scanned": 0,
        "complete": 0,
        "enriched": 0,
        "no_match": 0,
        "failed": 0,
        "detected": 0,
    }

    # ------------------------------------------------------------------ #
    #                            生命周期                                  #
    # ------------------------------------------------------------------ #
    def init_plugin(self, config: Optional[Dict[str, Any]] = None) -> None:
        """读取配置并重建运行态，允许重复调用。"""
        # 先停掉上一代监控与消费者，避免重载窗口内旧线程继续写
        self.stop_service()

        config = config or {}
        self._enabled = bool(config.get("enabled"))
        self._monitor_dirs = str(config.get("monitor_dirs") or "")
        self._mode = str(config.get("mode") or "compatibility")
        self._scan_cron = str(config.get("scan_cron") or DEFAULT_CRON)
        self._backup = bool(config.get("backup", True))
        self._dry_run = bool(config.get("dry_run"))
        self._notify = bool(config.get("notify"))

        if self._lock is None:
            self._lock = threading.Lock()
        self._enricher = TmdbEnricher()

        if not self._enabled:
            return

        if not self._enricher.available:
            logger.warn("NFO 补全已启用，但宿主 TMDB 模块未运行，仅能检测、无法补全")

        self._start_worker()
        self._start_monitor()

        # 启动时异步全量扫描一次（等配置落盘与监控启动完成）
        threading.Thread(target=self._delayed_full_scan, daemon=True, name="NfoCompleter-Boot").start()

    def get_state(self) -> bool:
        return bool(self._enabled)

    def stop_service(self) -> None:
        """停止目录监控与消费者线程，可重复调用。"""
        self._enabled = False
        self._stop_monitor()
        self._stop_worker()

    def _start_monitor(self) -> None:
        dirs = self._resolve_dirs()
        if not dirs:
            logger.warn("NFO 补全已启用，但未配置有效的监控目录")
        for mon_path in dirs:
            try:
                if self._mode == "performance":
                    observer = Observer(timeout=10)
                else:
                    # 兼容模式：轮询，兼容 SMB/CD2/rclone 等远程挂载目录
                    observer = PollingObserver(timeout=10)
                observer.schedule(_NfoEventHandler(self), path=str(mon_path), recursive=True)
                observer.daemon = True
                observer.start()
                self._observers.append(observer)
                logger.info(f"NFO 监控已启动：{mon_path}（{self._mode} 模式）")
            except Exception as err:  # noqa: BLE001
                logger.error(f"启动 NFO 监控失败 {mon_path}：{err}")

    def _stop_monitor(self) -> None:
        for observer in self._observers:
            try:
                observer.stop()
                observer.join(timeout=5)
            except Exception:  # noqa: BLE001
                pass
        self._observers = []

    def _start_worker(self) -> None:
        if self._worker and self._worker.is_alive():
            return
        self._queue = queue.Queue()
        self._stop_event = threading.Event()
        self._worker = threading.Thread(
            target=self._worker_loop, daemon=True, name="NfoCompleter"
        )
        self._worker.start()

    def _stop_worker(self) -> None:
        if self._stop_event:
            self._stop_event.set()
        if self._worker and self._worker.is_alive():
            self._worker.join(timeout=5)
        self._worker = None
        self._queue = None
        self._stop_event = None

    def _worker_loop(self) -> None:
        while self._stop_event and not self._stop_event.is_set():
            try:
                path = self._queue.get(timeout=1.0)
            except queue.Empty:
                continue
            try:
                self._process(Path(path))
            except Exception as err:  # noqa: BLE001  单文件异常不得拖垮消费者
                logger.error(f"处理 NFO 异常 {path}：{err} - {traceback.format_exc()}")

    # ------------------------------------------------------------------ #
    #                            扫描与处理                                #
    # ------------------------------------------------------------------ #
    def _delayed_full_scan(self) -> None:
        """启动 3 秒后做一次全量扫描。"""
        time.sleep(3.0)
        self.scan_all()

    def scan_all(self, paths: Optional[List[str]] = None) -> Dict[str, int]:
        """全量扫描监控目录下的 .nfo 并入队。"""
        if not self._enabled:
            return {"queued": 0}
        count = 0
        if paths:
            targets: List[Path] = []
            for p in paths:
                pp = Path(p)
                targets.append(pp)
        else:
            targets = self._resolve_dirs()
        for target in targets:
            if target.is_dir():
                for p in target.rglob("*.nfo"):
                    self.enqueue(p)
                    count += 1
            elif str(target).lower().endswith(".nfo"):
                self.enqueue(target)
                count += 1
        logger.info(f"NFO 补全：已入队 {count} 个 NFO 文件")
        return {"queued": count}

    def enqueue(self, path: Any) -> None:
        """把单个路径入队（过滤非 .nfo）。watchdog 回调也走这里。"""
        if not self._enabled or self._queue is None:
            return
        path = str(path)
        if not path.lower().endswith(".nfo"):
            return
        self._queue.put(path)

    def _process(self, path: Path) -> None:
        """处理单个 NFO：防抖 -> 解析 -> 校验 -> 补全 -> 写回。"""
        if not path.exists() or path.suffix.lower() != ".nfo":
            return

        # 防抖：外部可能仍在写文件，等 mtime 稳定后再读
        try:
            mtime = path.stat().st_mtime_ns
        except OSError:
            return
        time.sleep(0.2)
        try:
            if path.stat().st_mtime_ns != mtime:
                self.enqueue(path)  # 文件仍在变化，重新入队
                return
        except OSError:
            return

        self._stats["scanned"] = self._stats.get("scanned", 0) + 1

        try:
            doc = NfoDocument(path)
        except Exception as err:  # noqa: BLE001
            self._stats["failed"] = self._stats.get("failed", 0) + 1
            logger.warn(f"解析 NFO 失败 {path}：{err}")
            return

        missing = doc.missing_fields()
        if not missing:
            self._stats["complete"] = self._stats.get("complete", 0) + 1
            return

        if self._dry_run:
            self._stats["detected"] = self._stats.get("detected", 0) + 1
            logger.info(f"[dry-run] {path} 缺失字段：{', '.join(missing)}")
            return

        identity = doc.identity()
        if self._enricher is None:
            return
        updates, tmdb_id = self._enricher.enrich(doc.kind, identity, missing)
        if not updates and not tmdb_id:
            self._stats["no_match"] = self._stats.get("no_match", 0) + 1
            logger.info(f"{path} 未从 TMDB 匹配到数据，跳过（缺失：{', '.join(missing)}）")
            return

        written, backup = doc.apply(updates, tmdb_id=tmdb_id, backup=self._backup)
        if written:
            self._stats["enriched"] = self._stats.get("enriched", 0) + 1
            logger.info(
                f"已补全 {path}：写入 {written} 个字段（{', '.join(updates)}）"
                + (f"；备份：{backup}" if backup else "")
            )
        else:
            self._stats["complete"] = self._stats.get("complete", 0) + 1

    def _resolve_dirs(self) -> List[Path]:
        """解析配置里的监控目录（多行，每行一个）。"""
        dirs: List[Path] = []
        for line in self._monitor_dirs.splitlines():
            line = line.strip()
            if not line:
                continue
            p = Path(line).expanduser()
            if p.is_dir():
                dirs.append(p)
        return dirs

    # ------------------------------------------------------------------ #
    #                            远程命令                                  #
    # ------------------------------------------------------------------ #
    @staticmethod
    def get_command() -> List[Dict[str, Any]]:
        return [
            {
                "cmd": "/nfo_scan",
                "event": EventType.PluginAction,
                "desc": "全量扫描并补全 NFO",
                "category": "NFO 补全",
                "data": {"action": "nfo_scan"},
            }
        ]

    @eventmanager.register(EventType.PluginAction)
    def handle_action(self, event: Event) -> None:
        data = event.event_data or {}
        if data.get("action") != "nfo_scan":
            return
        if not self.get_state():
            self.post_message(mtype=MessageType.Plugin, title="NFO 补全", text="插件未启用。")
            return
        result = self.scan_all()
        self.post_message(
            mtype=MessageType.Plugin,
            title="NFO 补全",
            text=f"已开始扫描，入队 {result['queued']} 个 NFO 文件。",
        )

    # ------------------------------------------------------------------ #
    #                            定时任务                                  #
    # ------------------------------------------------------------------ #
    def get_service(self) -> List[Dict[str, Any]]:
        """注册定时全量扫描。"""
        if not self.get_state():
            return []
        try:
            trigger = CronTrigger.from_crontab(self._scan_cron or DEFAULT_CRON)
        except Exception as err:  # noqa: BLE001
            logger.warn(f"NFO 补全 cron 表达式非法（{self._scan_cron}），回退默认：{err}")
            trigger = CronTrigger.from_crontab(DEFAULT_CRON)
        return [
            {
                "id": "NfoCompleter.Scan",
                "name": "NFO 补全定时扫描",
                "trigger": trigger,
                "func": self.scan_all,
                "kwargs": {},
            }
        ]

    # ------------------------------------------------------------------ #
    #                            页面与接口                                #
    # ------------------------------------------------------------------ #
    def get_form(self) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
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
                                    {"component": "VSwitch", "props": {"model": "enabled", "label": "启用插件"}}
                                ],
                            },
                            {
                                "component": "VCol",
                                "props": {"cols": 12, "md": 4},
                                "content": [
                                    {"component": "VSwitch", "props": {"model": "backup", "label": "写回前备份"}}
                                ],
                            },
                            {
                                "component": "VCol",
                                "props": {"cols": 12, "md": 4},
                                "content": [
                                    {"component": "VSwitch", "props": {"model": "dry_run", "label": "仅检测不写回"}}
                                ],
                            },
                            {
                                "component": "VCol",
                                "props": {"cols": 12, "md": 4},
                                "content": [
                                    {"component": "VSwitch", "props": {"model": "notify", "label": "完成后通知"}}
                                ],
                            },
                        ],
                    },
                    {
                        "component": "VTextarea",
                        "props": {
                            "model": "monitor_dirs",
                            "label": "监控目录（每行一个）",
                            "placeholder": "/media/movies\n/media/tv",
                            "rows": 3,
                        },
                    },
                    {
                        "component": "VRow",
                        "content": [
                            {
                                "component": "VCol",
                                "props": {"cols": 12, "md": 6},
                                "content": [
                                    {
                                        "component": "VTextField",
                                        "props": {
                                            "model": "scan_cron",
                                            "label": "定时扫描 cron 表达式",
                                            "placeholder": DEFAULT_CRON,
                                        },
                                    }
                                ],
                            },
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
                                                {"title": "兼容（轮询，适合远程挂载）", "value": "compatibility"},
                                                {"title": "性能（inotify，仅本地盘）", "value": "performance"},
                                            ],
                                        },
                                    }
                                ],
                            },
                        ],
                    },
                ],
            }
        ], {
            "enabled": False,
            "monitor_dirs": "",
            "mode": "compatibility",
            "scan_cron": DEFAULT_CRON,
            "backup": True,
            "dry_run": False,
            "notify": False,
        }

    def get_page(self) -> Optional[List[Dict[str, Any]]]:
        stats = self._stats or {}
        return [
            {
                "component": "VAlert",
                "props": {
                    "type": "info",
                    "variant": "tonal",
                    "text": "远程命令：/nfo_scan 全量扫描并补全。补全只补缺失字段，不覆盖已有内容。",
                },
            },
            {
                "component": "VTable",
                "props": {
                    "headers": [
                        {"title": "项目", "key": "key", "sortable": False},
                        {"title": "值", "key": "value", "sortable": False},
                    ],
                    "items": [
                        {"key": "已扫描", "value": stats.get("scanned", 0)},
                        {"key": "已补全", "value": stats.get("enriched", 0)},
                        {"key": "已完整", "value": stats.get("complete", 0)},
                        {"key": "未匹配", "value": stats.get("no_match", 0)},
                        {"key": "失败", "value": stats.get("failed", 0)},
                    ],
                    "density": "compact",
                },
            },
        ]

    def get_api(self) -> List[Dict[str, Any]]:
        return [
            {
                "path": "/scan",
                "endpoint": self.api_scan,
                "methods": ["POST"],
                "auth": "bear",
                "summary": "触发全量扫描",
            },
            {
                "path": "/stats",
                "endpoint": self.api_stats,
                "methods": ["GET"],
                "auth": "bear",
                "summary": "扫描统计",
            },
        ]

    def api_scan(self) -> Dict[str, Any]:
        result = self.scan_all()
        return {"success": True, "data": result}

    def api_stats(self) -> Dict[str, Any]:
        return {"success": True, "data": self._stats or {}}
