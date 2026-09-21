"""STRM 监控刮削插件（V2 专用）。

监控目录中新增或移入的 ``.strm`` 文件，向上定位剧集根目录后调用主程序
``MediaChain.scrape_metadata`` 就地补齐剧集级元数据（tvshow.nfo、海报等），
不转移文件、也不自行维护刮削记录。

与 V3 版的区别：V2 用 ``MediaChain.scrape_metadata`` + ``StorageChain.get_file_item(storage="local")``
取文件项；V3 版请改用 ``plugins.v3/strmscraper``（走 ``ScrapingChain``）。
"""

import re
import threading
import time
import traceback
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer
from watchdog.observers.polling import PollingObserver

from app import schemas
from app.chain.media import MediaChain
from app.chain.storage import StorageChain
from app.log import logger
from app.plugins import _PluginBase

# 全局锁，避免并发刮削同一目录
lock = threading.Lock()
# 同一目录 10 分钟内不重复刮削：一季多集落盘时避免整剧反复重刮
DEDUP_TTL = 600

# 季目录名匹配（Season 1 / S01 / Specials / Extras）
SEASON_RE = re.compile(r"^(season\s*\d+|s\d{1,3}|specials|extras)$", re.IGNORECASE)


class _StrmHandler(FileSystemEventHandler):
    """
    watchdog 事件处理器：把新增/移入的 .strm 事件转给插件处理
    """

    def __init__(self, monpath: str, plugin: Any, **kwargs):
        """绑定被监控目录与插件实例。"""
        super().__init__(**kwargs)
        self._watch_path = monpath
        self._plugin = plugin

    def on_created(self, event):
        """新建文件时触发，只处理文件、忽略目录。"""
        if not event.is_directory:
            self._plugin.event_handler(event_path=event.src_path, mon_path=self._watch_path)

    def on_moved(self, event):
        """移入文件时触发，覆盖「先写临时文件再改名」的落盘方式。"""
        if not getattr(event, "is_directory", False):
            self._plugin.event_handler(event_path=event.dest_path, mon_path=self._watch_path)


class StrmScraper(_PluginBase):
    """STRM 监控刮削插件主类（V2）。

    监控若干目录，捕获新增/移入的 ``.strm`` 文件后向上定位剧集根目录并就地刮削，
    另提供全量扫描接口与可选的定时全量刷新。
    """

    # 插件名称
    plugin_name = "STRM监控刮削"
    # 插件描述
    plugin_desc = "监控目录中新增的.strm文件，自动调用主程序刮削链补齐剧集级元数据（tvshow.nfo/海报等），记录完全由主程序管理。"
    # 插件标签（与 package.v2.json 的 labels 保持一致）
    plugin_label = "刮削,STRM,监控"
    # 插件图标（自定义图标必须写成完整 URL：裸文件名只会去官方库 icons/ 里找，找不到就回退成拼图占位图）
    plugin_icon = (
        "https://raw.githubusercontent.com/157888390/MoviePilot-Plugins"
        "/main/icons/strmscraper.png"
    )
    # 插件版本
    plugin_version = "1.1.4"
    # 插件作者
    plugin_author = "157888390"
    # 作者主页
    author_url = "https://github.com/157888390"
    # 插件配置项ID前缀
    plugin_config_prefix = "strmscraper_"
    # 加载顺序
    plugin_order = 5
    # 可使用的用户级别
    auth_level = 1

    # 私有属性
    _observer = []
    _enabled = False
    _mode = "compatibility"        # compatibility=轮询(兼容SMB/CD2/rclone挂载) / fast=inotify(本地盘)
    _monitor_dirs = ""
    _exclude_keywords = ""
    _overwrite = False
    _onlyonce = False
    _scraped: Dict[str, float] = {}   # 已刮削目录 + 时间戳，做轻量去重
    mediaChain = None
    storagechain = None
    _cron_enabled = False            # 启用定时全量刷新
    _cron_expression = ""            # 标准 5 段 cron：分 时 日 月 周（如 "0 4 * * *"）

    def init_plugin(self, config: dict = None):
        """读取配置并按配置重启目录监控，允许重复调用。"""
        self.mediaChain = MediaChain()
        self.storagechain = StorageChain()
        self._scraped = {}

        if config:
            self._enabled = config.get("enabled")
            self._mode = config.get("mode") or "compatibility"
            self._monitor_dirs = config.get("monitor_dirs") or ""
            self._exclude_keywords = config.get("exclude_keywords") or ""
            self._overwrite = config.get("overwrite") or False
            self._onlyonce = config.get("onlyonce") or False
            self._cron_enabled = config.get("cron_enabled") or False
            self._cron_expression = (config.get("cron_expression") or "").strip()

        # 先停止现有监控
        self.stop_service()

        if self._enabled:
            monitor_dirs = [d.strip() for d in self._monitor_dirs.split("\n") if d.strip()]
            if not monitor_dirs:
                logger.warn("STRM监控刮削已启用，但未配置监控目录")
            for mon_path in monitor_dirs:
                try:
                    if self._mode == "compatibility":
                        # 兼容模式：轮询，可兼容挂载的远程共享目录如SMB/CD2/rclone
                        observer = PollingObserver(timeout=10)
                    else:
                        # 性能模式：inotify（仅本地盘）
                        observer = Observer(timeout=10)
                    self._observer.append(observer)
                    observer.schedule(_StrmHandler(mon_path, self), path=mon_path, recursive=True)
                    observer.daemon = True
                    observer.start()
                    logger.info(f"STRM监控已启动：{mon_path}（{self._mode}模式）")
                except Exception as e:
                    err = str(e)
                    if "inotify" in err and "reached" in err:
                        logger.warn(
                            f"启动STRM监控失败：{err}，请在宿主机执行 "
                            "echo fs.inotify.max_user_watches=524288 | sudo tee -a /etc/sysctl.conf && sudo sysctl -p"
                        )
                    else:
                        logger.error(f"启动STRM监控失败 {mon_path}：{err}")

        if self._onlyonce:
            logger.info("STRM监控刮削：3秒后立即全量扫描一次")
            threading.Timer(3.0, self.full_scan).start()
            self._onlyonce = False
            self.__save_config()

    def __save_config(self):
        """把当前运行状态回写为插件配置。

        ``onlyonce`` 恒写 False：一次性任务执行完必须落盘关闭，否则重启后
        会被当成仍待执行而重复全量扫描。
        """
        self.update_config({
            "enabled": self._enabled,
            "mode": self._mode,
            "monitor_dirs": self._monitor_dirs,
            "exclude_keywords": self._exclude_keywords,
            "overwrite": self._overwrite,
            "onlyonce": False,
            "cron_enabled": self._cron_enabled,
            "cron_expression": self._cron_expression,
        })

    def get_state(self) -> bool:
        """返回插件当前是否启用。"""
        return self._enabled

    # ------------------------------------------------------------------
    # 事件处理：watchdog 直接同步调用，简单直接（借鉴目录实时监控插件）
    # ------------------------------------------------------------------
    def event_handler(self, event_path: str, mon_path: str):
        """
        处理文件变化：过滤 .strm 后，就地刮削（不转移，刮在 .strm 所在目录）
        """
        try:
            if not event_path.lower().endswith(".strm"):
                return
            file_path = Path(event_path)
            if not file_path.exists():
                return
            # 命中排除关键词不处理
            if self._exclude_keywords:
                for kw in self._exclude_keywords.split("\n"):
                    if kw and re.findall(kw, event_path):
                        logger.info(f"{event_path} 命中排除关键词 {kw}，跳过")
                        return
            # 回收站及隐藏文件不处理
            if any(p in event_path for p in ["/@Recycle/", "/#recycle/", "/@eaDir", "/."]):
                logger.debug(f"{event_path} 是回收站或隐藏文件，跳过")
                return
            self.__scrape(self.__series_root(file_path))
        except Exception as e:
            logger.error(f"STRM事件处理出错：{str(e)} - {traceback.format_exc()}")

    # ------------------------------------------------------------------
    # 刮削实现：全部交给主程序 MediaChain，本插件不落任何元数据
    # ------------------------------------------------------------------
    def __scrape(self, target_dir: Path):
        """对已定位好的剧集根目录执行一次刮削。

        ``target_dir`` 必须是**调用方**向上定位好的剧集根（电影为其所在目录），
        本方法不会也不应再做一次向上定位。
        """
        # target_dir 已是定位好的剧集根目录（电影为其所在目录）。
        # 注意：调用方（event_handler / full_scan）已负责向上定位剧集根，
        # 此处不要再对 target_dir 调用 __series_root，否则会把目录当成文件
        # 再次向上取父目录，导致所有剧集被折叠成监控根目录。
        # 只有对“目录”刮削，主程序才写出 tvshow.nfo + poster/backdrop/logo/
        # banner/thumb/season01-poster + Season1/season.nfo，与手动刮削目录一致；
        # 单文件刮削只会出单集 .nfo，缺上述剧集级文件。
        key = str(target_dir)
        now = time.time()
        with lock:
            if key in self._scraped and now - self._scraped[key] < DEDUP_TTL:
                logger.info(f"{key} 近期已刮削，跳过")
                return
            self._scraped[key] = now

        try:
            # 用主程序 storagechain 构造文件项（目录 → type=dir）
            file_item = self.storagechain.get_file_item(storage="local", path=target_dir)
            if not file_item:
                logger.warn(f"未找到文件项：{target_dir}")
                return
            # 刮削“目录”而非单文件：NFO/图片/记录完全由主程序管理，与手动刮削一致
            self.mediaChain.scrape_metadata(fileitem=file_item, overwrite=self._overwrite)
            logger.info(f"STRM刮削完成：{target_dir}")
        except Exception as e:
            logger.error(f"STRM刮削失败 {target_dir}：{str(e)} - {traceback.format_exc()}")

    @staticmethod
    def __series_root(file_path: Path) -> Path:
        """
        从 .strm 向上定位剧集根目录：跳过 Season 1 / S01 / Specials / Extras 等季目录。
        电影目录名非季目录 → 直接停在电影所在目录。
        """
        d = file_path.parent
        while d.name and SEASON_RE.match(d.name):
            d = d.parent
        return d

    def full_scan(self):
        """
        全量扫描监控目录内所有 .strm，按剧集根目录去重后刮削
        """
        for mon_path in [d.strip() for d in self._monitor_dirs.split("\n") if d.strip()]:
            root = Path(mon_path)
            if not root.exists():
                logger.warn(f"监控目录不存在：{mon_path}")
                continue
            logger.info(f"STRM全量扫描：{root}")
            seen: set = set()
            for strm in root.rglob("*.strm"):
                if self._exclude_keywords:
                    if any(kw and re.findall(kw, str(strm)) for kw in self._exclude_keywords.split("\n")):
                        continue
                target = self.__series_root(strm)
                k = str(target)
                if k in seen:
                    continue
                seen.add(k)
                try:
                    self.__scrape(target)
                except Exception as e:
                    logger.error(f"STRM刮削失败 {target}：{str(e)}")

    # ------------------------------------------------------------------
    # 定时全量刷新（cron）：通过重写 get_service() 向 MoviePilot 本体调度器
    # 注册任务，由本体（APScheduler）按 cron 表达式周期性执行，不自行造轮子。
    # 本体每次更新会先 remove 旧任务再 add，因此启用/禁用/改表达式都会自动生效。
    # ------------------------------------------------------------------
    def get_service(self) -> List[Dict[str, Any]]:
        """
        向本体调度器注册定时任务。仅在启用且 cron 表达式合法时返回服务，
        关闭或表达式非法时返回空列表（本体据此移除旧任务）。
        """
        if not self._cron_enabled or not self._cron_expression:
            return []
        try:
            from apscheduler.triggers.cron import CronTrigger
            trigger = CronTrigger.from_crontab(self._cron_expression)
        except Exception as e:
            logger.error(f"STRM定时刷新：cron 表达式无效 [{self._cron_expression}]：{e}")
            return []
        return [{
            "id": "strm_cron_scan",
            "name": "STRM定时全量刷新",
            "trigger": trigger,
            "func": self.scheduled_full_scan,
            "kwargs": {
                "max_instances": 1,
                "misfire_grace_time": 3600,
                "coalesce": True,
            },
        }]

    def scheduled_full_scan(self):
        """cron 到点触发：对全部监控目录执行一次全量刷新刮削"""
        if not self._cron_enabled:
            return
        try:
            logger.info("STRM定时刷新触发：开始全量扫描")
            self.full_scan()
            logger.info("STRM定时刷新完成")
        except Exception as e:
            logger.error(f"STRM定时刷新失败：{str(e)} - {traceback.format_exc()}")

    # ------------------------------------------------------------------
    # 远程触发 / API
    # ------------------------------------------------------------------
    def get_api(self) -> List[Dict[str, Any]]:
        """返回插件 API 列表。

        本插件没有 Vue 前端，接口供外部/脚本调用，因此不声明 ``auth``，
        沿用默认的 apikey 认证。
        """
        return [{
            "path": "/strm_scan",
            "endpoint": self.api_scan,
            "methods": ["GET"],
            "summary": "STRM全量刮削",
            "description": "触发一次全量扫描刮削",
        }]

    def api_scan(self) -> schemas.Response:
        """后台启动一次全量扫描刮削，立即返回以免阻塞请求。"""
        threading.Thread(target=self.full_scan, daemon=True).start()
        return schemas.Response(success=True)

    # ------------------------------------------------------------------
    # 界面
    # ------------------------------------------------------------------
    def get_form(self) -> Tuple[List[dict], Dict[str, Any]]:
        """返回 Vuetify JSON 配置表单与默认配置（本插件为 JSON 配置模式）。"""
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
                                                {"title": "兼容模式（轮询，网络盘用）", "value": "compatibility"},
                                                {"title": "性能模式（inotify）", "value": "fast"},
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
                                "props": {"cols": 12, "md": 4},
                                "content": [
                                    {
                                        "component": "VSwitch",
                                        "props": {"model": "cron_enabled", "label": "启用定时全量刷新"},
                                    }
                                ],
                            },
                            {
                                "component": "VCol",
                                "props": {"cols": 12, "md": 8},
                                "content": [
                                    {
                                        "component": "VTextField",
                                        "props": {
                                            "model": "cron_expression",
                                            "label": "Cron 表达式（分 时 日 月 周）",
                                            "placeholder": "如 0 4 * * * 表示每天 04:00",
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
                                        "component": "VAlert",
                                        "props": {
                                            "type": "info",
                                            "variant": "tonal",
                                            "text": "启用后通过主程序定时器（APScheduler）按上述 Cron 表达式自动全量刷新刮削；"
                                                    "关闭或改表达式会自动重新注册，无需重启。",
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
                                            "model": "monitor_dirs",
                                            "label": "监控目录",
                                            "rows": 4,
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
                                        "component": "VTextarea",
                                        "props": {
                                            "model": "exclude_keywords",
                                            "label": "排除关键词",
                                            "rows": 2,
                                            "placeholder": "每行一个关键词（正则）",
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
            "mode": "compatibility",
            "monitor_dirs": "",
            "exclude_keywords": "",
            "cron_enabled": False,
            "cron_expression": "0 4 * * *",
        }

    def get_page(self) -> Optional[List[dict]]:
        """返回详情页内容；本插件没有详情页，返回 None 表示不渲染。"""
        return None

    def stop_service(self):
        """
        停止监控
        """
        for observer in self._observer:
            try:
                observer.stop()
                observer.join(timeout=5)
            except Exception as e:
                logger.error(f"停止STRM监控失败：{str(e)}")
        self._observer = []
