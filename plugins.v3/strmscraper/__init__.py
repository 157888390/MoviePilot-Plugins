import re
import threading
import time
import traceback
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer
from watchdog.observers.polling import PollingObserver

from app import schemas
from app.chain.scraping import ScrapingChain
from app.chain.storage import StorageChain
from app.plugins import _PluginBase
from app.sdk.logging import logger

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
        super().__init__(**kwargs)
        self._watch_path = monpath
        self._plugin = plugin

    def on_created(self, event):
        if not event.is_directory:
            self._plugin.event_handler(event_path=event.src_path, mon_path=self._watch_path)

    def on_moved(self, event):
        # 覆盖“先写临时文件再改名”的落盘方式
        if not getattr(event, "is_directory", False):
            self._plugin.event_handler(event_path=event.dest_path, mon_path=self._watch_path)


class StrmScraper(_PluginBase):
    # 插件名称
    plugin_name = "STRM监控刮削"
    # 插件描述
    plugin_desc = "监控目录中新增的.strm文件，自动调用主程序刮削链（ScrapingChain）补齐元数据（tvshow.nfo/海报等），记录完全由主程序管理。V3 专用插件。"
    # 插件图标
    plugin_icon = "strmscraper.png"
    # 插件版本（V3 专用：从 1.x 跃迁到下一个主版本并归零）
    plugin_version = "2.0.3"
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
    _overwrite = False             # 是否覆盖已有刮削产物
    _onlyonce = False
    _scraped: Dict[str, float] = {}   # 已刮削目录 + 时间戳，做轻量去重
    _cron_enabled = False            # 启用定时全量刷新
    _cron_expression = ""            # 标准 5 段 cron：分 时 日 月 周（如 "0 4 * * *"）

    def init_plugin(self, config: dict = None):
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
    # 刮削实现：全部交给主程序 ScrapingChain，本插件不落任何元数据
    # ------------------------------------------------------------------
    def __resolve_file_item(self, path: Path):
        """
        按路径解析出带 storage 字段的文件项。
        优先尝试 local（路径以本地挂载形式存在时，如 CloudDrive2 的 FUSE 挂载）；
        若 local 取不到，再枚举其它已配置存储（CloudDrive / alist / rclone 等）兜底，
        避免把 storage 写死为 "local" 而在纯云盘挂载场景下失效。
        """
        def _try(stype: str):
            try:
                return StorageChain().get_file_item(storage=stype, path=Path(path))
            except Exception:
                return None

        # 1) 优先 local：保持当前可用行为（/media/strm 等以本地挂载存在时）
        item = _try("local")
        if item:
            return item
        # 2) local 取不到，枚举其它已配置存储兜底
        try:
            from app.sdk.services import StorageHelper
            for s in StorageHelper().get_storagies():
                stype = getattr(s, "type", None)
                if not stype or stype == "local":
                    continue
                item = _try(stype)
                if item:
                    return item
        except Exception as e:
            logger.warning(f"枚举存储失败，已回退 local：{e}")
        return None

    def __scrape(self, target_dir: Path):
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
            # 用主程序 StorageChain 按路径解析出带 storage 字段的文件项；
            # ScrapingChain.scrape_metadata 内部依赖 fileitem.storage 定位，
            # 因此必须用 get_file_item 构造，不能手写缺 storage 的 FileItem。
            # storage 不再写死为 "local"：先试 local，取不到再枚举其它已配置
            # 存储（CloudDrive / alist / rclone 等）兜底。
            file_item = self.__resolve_file_item(target_dir)
            if not file_item:
                logger.warn(
                    f"未找到文件项：{target_dir}（请确认该路径已在 MoviePilot 存储中配置，"
                    f"或已作为本地存储挂载）"
                )
                return
            # 刮削“目录”而非单文件：NFO/图片/记录完全由主程序管理，与手动刮削一致；
            # init_folder=True 确保 tvshow.nfo / season.nfo / 海报 等剧集级文件被生成；
            # recursive=True 递归整棵目录树（含每个 .strm 的单集 nfo）。
            ok, msg = ScrapingChain().scrape_metadata(
                fileitem=file_item,
                init_folder=True,
                overwrite=self._overwrite,
                recursive=True,
            )
            if ok:
                logger.info(f"STRM刮削完成：{target_dir}")
            else:
                logger.warn(f"STRM刮削未完全成功 {target_dir}：{msg}")
            # 记录刮削历史（供详情页展示）
            self.__record_scrape_history(target_dir, ok, msg)
        except Exception as e:
            logger.error(f"STRM刮削失败 {target_dir}：{str(e)} - {traceback.format_exc()}")

    def __record_scrape_history(self, target_dir: Path, success: bool, msg: str = ""):
        """记录一次刮削结果到插件数据，供 get_page() 详情页展示"""
        try:
            history = self.get_data("scrape_history") or {}
            key = str(target_dir)

            # 统计该目录下的 strm 文件数
            strm_count = len(list(target_dir.rglob("*.strm")))

            # 尝试从 tvshow.nfo 提取标题和海报
            title = target_dir.name
            poster_path = ""
            tvshow_nfo = target_dir / "tvshow.nfo"
            if tvshow_nfo.exists():
                try:
                    import xml.etree.ElementTree as ET
                    tree = ET.parse(tvshow_nfo)
                    root = tree.getroot()
                    t = root.findtext("title")
                    if t:
                        title = t
                except Exception:
                    pass
            # 海报文件
            for poster_file in ["poster.jpg", "poster.png"]:
                p = target_dir / poster_file
                if p.exists():
                    poster_path = str(p)
                    break

            history[key] = {
                "title": title,
                "path": key,
                "poster_path": poster_path,
                "strm_count": strm_count,
                "last_scrape": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "last_scrape_short": datetime.now().strftime("%m-%d %H:%M"),
                "success": success,
                "message": msg,
            }
            self.save_data("scrape_history", history)
        except Exception as e:
            logger.debug(f"记录刮削历史失败（非阻断）：{e}")

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
        return [
            {
                "path": "/strm_scan",
                "endpoint": self.api_scan,
                "methods": ["GET"],
                "summary": "STRM全量刮削",
                "description": "触发一次全量扫描刮削",
            },
            {
                "path": "/strm_rescrape",
                "endpoint": self.api_rescrape,
                "methods": ["GET"],
                "summary": "重新刮削单个合集",
                "description": "对指定目录（覆盖模式）重新执行一次刮削",
            },
        ]

    def api_scan(self) -> schemas.Response:
        threading.Thread(target=self.full_scan, daemon=True).start()
        return schemas.Response(success=True)

    def api_rescrape(self, path: str, apikey: str = "") -> schemas.Response:
        """对单个合集目录执行覆盖重刮"""
        from app.core.config import settings
        if apikey != settings.API_TOKEN:
            return schemas.Response(success=False, message="API密钥错误")
        target = Path(path)
        if not target.exists():
            return schemas.Response(success=False, message=f"目录不存在：{path}")
        # 临时开启覆盖，绕过去重 TTL
        old_overwrite = self._overwrite
        self._overwrite = True
        # 清除该目录的去重缓存
        key = str(target)
        with lock:
            self._scraped.pop(key, None)
        try:
            self.__scrape(target)
            return schemas.Response(success=True, message=f"已触发重新刮削：{path}")
        except Exception as e:
            return schemas.Response(success=False, message=f"刮削失败：{e}")
        finally:
            self._overwrite = old_overwrite

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
                                            "text": "刮削通过主程序 ScrapingChain 完成，NFO/图片/记录与手动刮削完全一致；"
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
        """
        详情页：展示已刮削的合集（按剧集根目录分组），每个卡片带重新刮削按钮。
        仿照 EpisodeNoExist 插件的卡片网格布局。
        """
        from app.core.config import settings

        history = self.get_data("scrape_history") or {}
        details = history if isinstance(history, dict) else {}

        # 同时扫描监控目录，发现新目录也展示（即使还没刮削过）
        all_series: Dict[str, Dict] = {}
        for mon_path in [d.strip() for d in self._monitor_dirs.split("\n") if d.strip()]:
            root = Path(mon_path)
            if not root.exists():
                continue
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
                if k not in all_series:
                    # 统计该目录下的 strm 文件数（即使还没刮削过也要显示）
                    strm_count = len(list(target.rglob("*.strm")))
                    all_series[k] = {"path": k, "title": target.name, "strm_count": strm_count}

        # 合并历史数据
        for key, info in details.items():
            if key in all_series:
                all_series[key].update(info)
            else:
                all_series[key] = info

        series_list = list(all_series.values())
        # 按最近刮削时间排序
        series_list.sort(
            key=lambda x: x.get("last_scrape", ""), reverse=True
        )

        # 统计
        total_series = len(series_list)
        total_strms = sum(s.get("strm_count", 0) for s in series_list)
        success_count = sum(1 for s in series_list if s.get("success"))
        failed_count = total_series - success_count

        # 统计卡片行
        stat_cards = self.__build_stat_cards(total_series, total_strms, success_count, failed_count)

        # 合集卡片网格
        cards_content = []
        if not series_list:
            cards_content.append({
                "component": "div",
                "text": "暂无数据，请先配置监控目录并启用插件",
                "props": {"class": "text-center text-caption py-8"},
            })
        else:
            for item in series_list:
                cards_content.append(self.__build_series_card(item))

        return [
            {
                "component": "div",
                "content": [
                    stat_cards,
                    {
                        "component": "VCardTitle",
                        "props": {
                            "class": "pt-6 pb-2 px-0 text-base whitespace-nowrap text-center",
                        },
                        "content": [{
                            "component": "span",
                            "text": "··· 已刮削合集 ···",
                        }],
                    },
                    {
                        "component": "div",
                        "props": {
                            "class": "flex flex-row flex-wrap gap-4 items-start justify-center",
                        },
                        "content": cards_content,
                    },
                ],
            }
        ]

    # ------------------------------------------------------------------
    # 详情页组件构建方法
    # ------------------------------------------------------------------

    @staticmethod
    def __build_stat_cards(
        total_series: int, total_strms: int, success: int, failed: int
    ) -> dict:
        """构建顶部统计卡片行"""
        stats = [
            ("总合集", f"{total_series} 部", "M19 3H5c-1.1 0-2 .9-2 2v14c0 1.1.9 2 2 2h14c1.1 0 2-.9 2-2V5c0-1.1-.9-2-2-2zm0 16H5V5h14v14z M7 10h2v7H7zm4-3h2v10h-2zm4 3h2v7h-2z"),
            ("总集数", f"{total_strms} 集", "M18 4l2 4h-3l-2-4h-2l2 4h-3l-2-4H8l2 4H7L5 4H4c-1.1 0-1.99.9-1.99 2L2 18c0 1.1.9 2 2 2h16c1.1 0 2-.9 2-2V6c0-1.1-.9-2-2-2h-2zm0 14H4v-6h14v6zM7 15h2v-2H7v2zm4 0h2v-2h-2v2zm4 0h2v-2h-2v2z"),
            ("成功", f"{success} 部", "M12 2C6.48 2 2 6.48 2 12s4.48 10 10 10 10-4.48 10-10S17.52 2 12 2zm-2 15l-5-5 1.41-1.41L10 14.17l7.59-7.59L19 8l-9 9z"),
            ("失败/未刮", f"{failed} 部", "M12 2C6.48 2 2 6.48 2 12s4.48 10 10 10 10-4.48 10-10S17.52 2 12 2zm1 15h-2v-2h2v2zm0-4h-2V7h2v6z"),
        ]
        cards = []
        for label, value, svg_d in stats:
            cards.append({
                "component": "VCard",
                "props": {"variant": "tonal", "style": "width: 10rem;"},
                "content": [{
                    "component": "VCardText",
                    "props": {"class": "d-flex align-center"},
                    "content": [
                        {
                            "component": "svg",
                            "props": {
                                "class": "icon mr-2",
                                "viewBox": "0 0 24 24", "width": "32", "height": "32",
                            },
                            "content": [{
                                "component": "path",
                                "props": {"fill": "#8a8a8a", "d": svg_d},
                            }],
                        },
                        {
                            "component": "div",
                            "content": [
                                {"component": "span", "props": {"class": "text-caption"}, "text": label},
                                {"component": "span", "props": {"class": "text-h6"}, "text": value},
                            ],
                        },
                    ],
                }],
            })
        return {
            "component": "VRow",
            "props": {"class": "flex flex-row justify-center flex-wrap gap-6 pt-4"},
            "content": cards,
        }

    def __build_series_card(self, info: dict) -> dict:
        """构建单个合集卡片：海报 + 信息 + 重新刮削按钮"""
        from app.core.config import settings

        title = info.get("title", "未知")
        path = info.get("path", "")
        poster = info.get("poster_path", "")
        strm_count = info.get("strm_count", 0)
        last_scrape = info.get("last_scrape_short", "未刮削")
        success = info.get("success")
        message = info.get("message", "")

        # 状态文字
        if success is None:
            status_text = "待刮削"
            status_color = "text-grey"
        elif success:
            status_text = "刮削成功"
            status_color = "text-success"
        else:
            status_text = f"失败: {message[:20]}" if message else "刮削失败"
            status_color = "text-error"

        # 海报图片（通过 MoviePilot 图片代理展示本地文件）
        if poster:
            from urllib.parse import quote
            poster_url = f"/{settings.API_TOKEN}/image/local?path={quote(poster)}"
            poster_component = {
                "component": "VImg",
                "props": {
                    "src": poster_url,
                    "height": 240, "width": 160,
                    "aspect-ratio": "2/3",
                    "class": "object-cover shadow ring-gray-500 max-w-32",
                    "cover": True, "transition": True,
                    "lazy-src": "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAPAAAACgCAQAAACY0inuAAABB0lEQVR42u3RMREAAAjEMF45M65xwcClEppMlx4XwIAFWIAFWIAFWIABC7AAC7AAC7AAAxZgARZgARZgARZgwAIswAIswAIswIAFWIAFWIAFWIABC7AAC7AAC7AAAxZgARZgARZgAQYswAIswAIswAIMWIAFWIAFWIAFWIABC7AAC7AAC7AAAxZgARZgARZgAQYswAIswAIswAIswIAFWIAFWIAFWIABC7AAC7AAC7AAAxZgARZgARZgAQYswAIswAIswAIswIAFWIAFWIAFWIABC7AAC7AAC7AACzBgARZgARZgARZgwAIswAIswAIswIABAxZgARZgARZgAQYswAIswAIswAIMWIAFWIAFWIAFWIABC7AAC7AAC7AAAxZgARZgARZgAQYswAIswAIswAIswIAFWIAFWIAFWIABC7AAC7AAC7AAAzYBsAALsAALsAALMGABFmABFmABFmDAAizAAizAAizAAgxYgAVYgAVYgAUYsAALsAALsAALMGABFmABFmABFmDAAizAAizAAizAAgxYgAVYgAVYgAVYgAUYsAALsAALsAALMGABFmABFmABFmDAAizAAizAAizA",
                },
            }
        else:
            # 无海报时显示一个带标题的色块
            poster_component = {
                "component": "div",
                "props": {
                    "class": "flex items-center justify-center bg-grey-darken-3 shadow ring-gray-500 max-w-32",
                    "style": "width:160px;height:240px;overflow:hidden;",
                },
                "content": [{
                    "component": "span",
                    "props": {"class": "text-caption text-center px-2", "style": "text-overflow:ellipsis;"},
                    "text": title,
                }],
            }

        # 重新刮削按钮
        rescrape_btn = {
            "component": "VBtn",
            "props": {
                "class": "text-primary flex-grow",
                "variant": "tonal",
                "style": "height: 100%",
                "size": "small",
            },
            "events": {
                "click": {
                    "api": "plugin/StrmScraper/strm_rescrape",
                    "method": "get",
                    "params": {
                        "path": path,
                        "apikey": settings.API_TOKEN,
                    },
                }
            },
            "text": "重新刮削",
        }

        return {
            "component": "VCard",
            "props": {"variant": "tonal"},
            "content": [
                {
                    "component": "div",
                    "props": {"class": "flex flex-row"},
                    "content": [
                        poster_component,
                        {
                            "component": "div",
                            "props": {"class": ""},
                            "content": [
                                {
                                    "component": "VCardTitle",
                                    "props": {
                                        "class": "pt-6 pl-4 pr-4 text-lg",
                                        "style": "width: 12rem;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;",
                                    },
                                    "text": title,
                                },
                                {
                                    "component": "VCardText",
                                    "props": {"class": "pa-0 pl-4 pr-4 pb-1 whitespace-nowrap"},
                                    "text": f"状态: {status_text}",
                                },
                                {
                                    "component": "VCardText",
                                    "props": {"class": "pa-0 pl-4 pr-4 py-1 whitespace-nowrap"},
                                    "text": f"集数: {strm_count}",
                                },
                                {
                                    "component": "VCardText",
                                    "props": {"class": "pa-0 pl-4 pr-4 py-1 whitespace-nowrap"},
                                    "text": f"刮削: {last_scrape}",
                                },
                            ],
                        },
                    ],
                },
                {
                    "component": "div",
                    "props": {
                        "class": "bg-opacity-80 flex flex-row-reverse justify-between "
                                 "items-center flex-nowrap space-x-reverse space-x-4",
                        "variant": "tonal",
                        "rounded": "0",
                    },
                    "content": [rescrape_btn],
                },
            ],
        }

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
