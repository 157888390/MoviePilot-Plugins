import threading
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import pytz
from apscheduler.schedulers.background import BackgroundScheduler
from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer
from watchdog.observers.polling import PollingObserver

from app import schemas
from app.chain.media import MediaChain
from app.core.config import settings
from app.core.metainfo import MetaInfoPath
from app.log import logger
from app.plugins import _PluginBase
from app.schemas import MediaType

# 事件去抖间隔（秒）：strm 文件通常很小，写入很快，但网络盘可能有延迟
DEBOUNCE_SECONDS = 5


class StrmFileHandler(FileSystemEventHandler):
    """
    watchdog 事件处理器：只关心新出现的 .strm 文件
    """

    def __init__(self, plugin, watch_path: str):
        super().__init__()
        self._plugin = plugin
        self._watch_path = watch_path

    def on_created(self, event):
        if not event.is_directory:
            self._plugin.enqueue_file(event.src_path)

    def on_moved(self, event):
        # 覆盖“先写临时文件再改名”的落盘方式
        if not event.is_directory:
            self._plugin.enqueue_file(event.dest_path)


class StrmScraper(_PluginBase):
    # 插件名称
    plugin_name = "STRM监控刮削"
    # 插件描述
    plugin_desc = "监控目录中新增的.strm文件，自动调用主程序刮削功能补齐元数据，记录完全由主程序管理。"
    # 插件图标
    plugin_icon = "strmscraper.png"
    # 插件版本
    plugin_version = "1.0.0"
    # 插件作者
    plugin_author = "157888390"
    # 作者主页
    author_url = "https://github.com/157888390"
    # 插件配置项ID前缀
    plugin_config_prefix = "strmscraper_"
    # 加载顺序
    plugin_order = 20
    # 可使用的用户级别
    auth_level = 1

    # 私有属性
    _enabled = False
    _onlyonce = False
    _mode = "fast"
    _monitor_paths = ""
    _exclude_paths = ""
    _overwrite = False
    _observers: List[Any] = []
    _scheduler = None
    _event = threading.Event()
    # 待处理队列：{path: 入队时间}，用于去抖
    _pending: Dict[str, float] = {}
    _pending_lock = threading.Lock()
    _worker: Optional[threading.Thread] = None

    def init_plugin(self, config: dict = None):
        config = config or {}
        self._enabled = bool(config.get("enabled"))
        self._onlyonce = bool(config.get("onlyonce"))
        self._mode = config.get("mode") or "fast"
        self._monitor_paths = config.get("monitor_paths") or ""
        self._exclude_paths = config.get("exclude_paths") or ""
        self._overwrite = bool(config.get("overwrite"))

        # 先停止现有监控与任务
        self.stop_service()

        if self._enabled:
            self.__start_monitor()
            self.__start_worker()

        if self._onlyonce:
            logger.info("STRM监控刮削：立即运行一次全量扫描")
            self._scheduler = BackgroundScheduler(timezone=settings.TZ)
            self._scheduler.add_job(
                func=self.full_scan,
                trigger="date",
                run_date=datetime.now(tz=pytz.timezone(settings.TZ)) + timedelta(seconds=3),
                name="STRM全量扫描",
            )
            self._scheduler.start()
            # 关闭一次性开关并保存配置
            self._onlyonce = False
            self.update_config({
                "enabled": self._enabled,
                "onlyonce": False,
                "mode": self._mode,
                "monitor_paths": self._monitor_paths,
                "exclude_paths": self._exclude_paths,
                "overwrite": self._overwrite,
            })

    def get_state(self) -> bool:
        return self._enabled

    @staticmethod
    def get_command() -> List[Dict[str, Any]]:
        return []

    def get_api(self) -> List[Dict[str, Any]]:
        return []

    # ------------------------------------------------------------------
    # 监控实现
    # ------------------------------------------------------------------
    def __parse_paths(self) -> List[Tuple[Path, Optional[MediaType]]]:
        """
        解析监控目录配置，支持 路径#电视剧 / 路径#电影 强制类型
        """
        results = []
        for line in self._monitor_paths.split("\n"):
            line = line.strip()
            if not line:
                continue
            mtype = None
            if line.count("#") == 1:
                path_str, type_str = line.split("#")
                mtype = next(
                    (m for m in MediaType.__members__.values() if m.value == type_str),
                    None,
                )
            else:
                path_str = line
            path = Path(path_str)
            if not path.exists():
                logger.warning(f"STRM监控目录不存在：{path_str}")
                continue
            results.append((path, mtype))
        return results

    def __start_monitor(self):
        """
        为每个配置目录启动 watchdog 监控
        """
        for path, _ in self.__parse_paths():
            try:
                if self._mode == "compatibility":
                    observer = PollingObserver(timeout=10)
                else:
                    observer = Observer(timeout=10)
                observer.schedule(
                    StrmFileHandler(self, str(path)), str(path), recursive=True
                )
                observer.daemon = True
                observer.start()
                self._observers.append(observer)
                logger.info(f"STRM监控已启动：{path}（{self._mode}模式）")
            except Exception as e:
                err = str(e)
                if "inotify" in err and "reached" in err:
                    logger.warn(
                        "监控目录数量超过系统 inotify 限制，请调整宿主机 "
                        "fs.inotify.max_user_watches / max_user_instances 后重启"
                    )
                else:
                    logger.error(f"启动STRM监控失败：{err}")

    def __start_worker(self):
        """
        启动去抖消费线程
        """
        self._event.clear()
        self._worker = threading.Thread(target=self.__consume_loop, daemon=True)
        self._worker.start()

    def enqueue_file(self, file_path: str):
        """
        watchdog 回调入口：过滤后加入待处理队列
        """
        if not file_path or not file_path.lower().endswith(".strm"):
            return
        if self.__is_excluded(Path(file_path)):
            logger.debug(f"{file_path} 在排除目录中，跳过")
            return
        with self._pending_lock:
            self._pending[file_path] = time.time()
        logger.info(f"发现新STRM文件，加入刮削队列：{file_path}")

    def __consume_loop(self):
        """
        轮询待处理队列，超过去抖时间的文件依次刮削
        """
        while not self._event.is_set():
            ready: List[str] = []
            now = time.time()
            with self._pending_lock:
                for path, ts in list(self._pending.items()):
                    if now - ts >= DEBOUNCE_SECONDS:
                        ready.append(path)
                        self._pending.pop(path, None)
            for path in ready:
                if self._event.is_set():
                    return
                try:
                    self.__scrape_strm(Path(path))
                except Exception as e:
                    logger.error(f"刮削 {path} 出错：{str(e)}")
            self._event.wait(2)

    def __is_excluded(self, file_path: Path) -> bool:
        for line in self._exclude_paths.split("\n"):
            line = line.strip()
            if not line:
                continue
            try:
                if file_path.is_relative_to(Path(line)):
                    return True
            except Exception:
                continue
        return False

    def __forced_type(self, file_path: Path) -> Optional[MediaType]:
        """
        文件所属监控根目录若配置了强制类型则返回
        """
        for path, mtype in self.__parse_paths():
            try:
                if mtype and file_path.is_relative_to(path):
                    return mtype
            except Exception:
                continue
        return None

    # ------------------------------------------------------------------
    # 刮削实现：全部交给主程序 MediaChain，本插件不落任何元数据
    # ------------------------------------------------------------------
    def __scrape_strm(self, file_path: Path):
        if not file_path.exists():
            logger.warning(f"STRM文件已不存在，跳过：{file_path}")
            return
        # 识别媒体信息（走主程序识别链，含缓存与站点辅助识别）
        meta = MetaInfoPath(file_path)
        forced_type = self.__forced_type(file_path)
        if forced_type:
            meta.type = forced_type
        mediainfo = self.chain.recognize_media(meta=meta)
        if not mediainfo:
            logger.warn(f"未识别到媒体信息，无法刮削：{file_path}")
            return
        # 补齐图片信息
        self.chain.obtain_images(mediainfo)
        # 调用主程序刮削链。与手动刮削/整理刮削完全同一入口，
        # NFO、图片、刮削记录均由主程序统一生成和管理。
        MediaChain().scrape_metadata(
            fileitem=schemas.FileItem(
                storage="local",
                type="file",
                path=str(file_path).replace("\\", "/"),
                name=file_path.name,
                basename=file_path.stem,
                extension=file_path.suffix[1:],
                modify_time=file_path.stat().st_mtime,
            ),
            meta=meta,
            mediainfo=mediainfo,
            overwrite=self._overwrite,
        )
        logger.info(f"STRM刮削完成（由主程序处理）：{file_path}")

    def full_scan(self):
        """
        全量扫描监控目录中的所有 strm 文件并刮削
        """
        for path, _ in self.__parse_paths():
            logger.info(f"开始全量扫描：{path}")
            for strm_file in path.rglob("*.strm"):
                if self._event.is_set():
                    logger.info("STRM全量扫描已停止")
                    return
                if self.__is_excluded(strm_file):
                    continue
                try:
                    self.__scrape_strm(strm_file)
                except Exception as e:
                    logger.error(f"刮削 {strm_file} 出错：{str(e)}")
        logger.info("STRM全量扫描完成")

    # ------------------------------------------------------------------
    # 界面
    # ------------------------------------------------------------------
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
                                    {
                                        "component": "VSwitch",
                                        "props": {"model": "enabled", "label": "启用插件"},
                                    }
                                ],
                            },
                            {
                                "component": "VCol",
                                "props": {"cols": 12, "md": 4},
                                "content": [
                                    {
                                        "component": "VSwitch",
                                        "props": {"model": "onlyonce", "label": "立即全量扫描一次"},
                                    }
                                ],
                            },
                            {
                                "component": "VCol",
                                "props": {"cols": 12, "md": 4},
                                "content": [
                                    {
                                        "component": "VSwitch",
                                        "props": {"model": "overwrite", "label": "覆盖已有元数据"},
                                    }
                                ],
                            },
                        ],
                    },
                    {
                        "component": "VRow",
                        "content": [
                            {
                                "component": "VCol",
                                "props": {"cols": 12, "md": 4},
                                "content": [
                                    {
                                        "component": "VSelect",
                                        "props": {
                                            "model": "mode",
                                            "label": "监控模式",
                                            "items": [
                                                {"title": "性能模式（inotify）", "value": "fast"},
                                                {"title": "兼容模式（轮询，网络盘用）", "value": "compatibility"},
                                            ],
                                        },
                                    }
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
                                        "component": "VTextarea",
                                        "props": {
                                            "model": "monitor_paths",
                                            "label": "监控目录",
                                            "rows": 4,
                                            "placeholder": "每行一个目录，可在目录后拼接#电视剧或#电影强制指定类型",
                                        },
                                    }
                                ],
                            }
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
                                        "component": "VTextarea",
                                        "props": {
                                            "model": "exclude_paths",
                                            "label": "排除目录",
                                            "rows": 2,
                                            "placeholder": "每行一个目录",
                                        },
                                    }
                                ],
                            }
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
                                            "type": "info",
                                            "variant": "tonal",
                                            "text": "刮削通过主程序刮削链完成，NFO/图片/记录与手动刮削完全一致；"
                                                    "网络挂载目录（CD2/rclone/SMB等）请选择兼容模式。",
                                        },
                                    }
                                ],
                            }
                        ],
                    },
                ],
            }
        ], {
            "enabled": False,
            "onlyonce": False,
            "overwrite": False,
            "mode": "fast",
            "monitor_paths": "",
            "exclude_paths": "",
        }

    def get_page(self) -> Optional[List[dict]]:
        return None

    def stop_service(self):
        """
        停止监控与后台任务
        """
        self._event.set()
        for observer in self._observers:
            try:
                observer.stop()
                observer.join(timeout=5)
            except Exception as e:
                logger.error(f"停止STRM监控失败：{str(e)}")
        self._observers = []
        if self._worker and self._worker.is_alive():
            self._worker.join(timeout=5)
        self._worker = None
        try:
            if self._scheduler:
                self._scheduler.remove_all_jobs()
                if self._scheduler.running:
                    self._scheduler.shutdown()
                self._scheduler = None
        except Exception as e:
            logger.error(f"停止STRM调度器失败：{str(e)}")
        with self._pending_lock:
            self._pending.clear()
        self._event.clear()
