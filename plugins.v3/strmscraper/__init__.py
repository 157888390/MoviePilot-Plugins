import os
import re
import threading
import time
import traceback
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from fastapi import Body, HTTPException
from fastapi.responses import FileResponse, RedirectResponse
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
# 剧集特征匹配：S01E02 / s01.e02 / 第 12 集 / xxx.E02.xxx
EPISODE_RE = re.compile(
    r"(?i)(?:s(\d{1,2})[.\s_-]*e(\d{1,3}))|(?:第\s*(\d{1,4})\s*[集集话話])|(?:[.\s_-]e(\d{1,3})[.\s_-])"
)
# 列表扫描安全上限，避免超大媒体库卡死接口
MAX_SCAN_FILES = 20000
# 列表缓存有效期（秒）
LIST_CACHE_TTL = 60
# 目录名中的 TMDB ID 标记，形如 xxx [tmdbid=477447]
TMDBID_RE = re.compile(r"\[tmdbid=(\d+)\]", re.IGNORECASE)
# 媒体目录内的海报候选文件名（小写比较）
POSTER_NAMES = (
    "poster.jpg", "poster.jpeg", "poster.png", "poster.webp",
    "folder.jpg", "folder.jpeg", "folder.png",
    "cover.jpg", "cover.png", "thumb.jpg",
    "tvshow.jpg", "tvshow.png", "movie.jpg", "movie.png",
)
# 允许作为海报读取的图片后缀
POSTER_SUFFIX = {".jpg", ".jpeg", ".png", ".webp", ".gif"}


def scan_strm_files(root: Path, skip=None, limit: int = MAX_SCAN_FILES) -> List[Path]:
    """
    递归收集目录下所有 .strm 文件。

    用 os.walk 而非 Path.rglob：rglob 遇到无权限/已失效的子目录会直接抛出 OSError，
    导致整次扫描或整个清单接口中断；os.walk 通过 onerror 回调跳过这些目录。
    CD2 / alist / rclone 等挂载后端偶发 EACCES、ESTALE 时尤其重要。
    """
    found: List[Path] = []

    def _on_error(err: OSError):
        logger.warn(f"STRM扫描跳过不可读目录：{err}")

    for current, dirs, files in os.walk(str(root), onerror=_on_error, followlinks=False):
        dirs.sort()
        for name in sorted(files):
            if not name.endswith(".strm"):
                continue
            full = Path(current) / name
            if skip is not None and skip(str(full)):
                continue
            found.append(full)
            if len(found) >= limit:
                return found
    return found


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
    # 插件图标（自定义图标必须写成完整 URL：裸文件名只会去官方库 icons/ 里找，找不到就回退成拼图占位图）
    plugin_icon = (
        "https://raw.githubusercontent.com/157888390/MoviePilot-Plugins"
        "/main/icons/strmscraper.png"
    )
    # 插件版本（V3 专用：从 1.x 跃迁到下一个主版本并归零）
    plugin_version = "3.1.0"
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
    _skip_scraped = True           # 全量扫描时跳过已刮削（nfo 齐全）的目录
    _onlyonce = False
    _scraped: Dict[str, float] = {}   # 已刮削目录 + 时间戳，做轻量去重
    _cron_enabled = False            # 启用定时全量刷新
    _cron_expression = ""            # 标准 5 段 cron：分 时 日 月 周（如 "0 4 * * *"）
    _sidebar_enabled = True          # 是否在主界面左侧导航栏显示「STRM刮削」入口
    _task_lock = threading.Lock()    # 任务状态读写锁
    _tasks: Dict[str, Dict[str, Any]] = {}   # 刮削任务：task_id -> 任务详情
    _list_cache: Dict[str, Any] = {"time": 0.0, "items": []}   # 媒体清单缓存
    _poster_cache: Dict[str, str] = {}   # TMDB 海报地址缓存：key -> url

    def init_plugin(self, config: dict = None):
        self._scraped = {}
        self._list_cache = {"time": 0.0, "items": []}
        # 历史版本遗留的刮削历史数据已不再使用（海报墙改由 Vue 侧栏页实现），直接清理
        try:
            if self.get_data("scrape_history"):
                self.del_data("scrape_history")
        except Exception as e:
            logger.debug(f"清理历史刮削数据失败（非阻断）：{e}")

        if config:
            self._enabled = config.get("enabled")
            self._mode = config.get("mode") or "compatibility"
            self._monitor_dirs = config.get("monitor_dirs") or ""
            self._exclude_keywords = config.get("exclude_keywords") or ""
            self._overwrite = config.get("overwrite") or False
            # 缺省为 True：老配置没有这个键时要保持跳过行为
            self._skip_scraped = config.get("skip_scraped", True)
            self._onlyonce = config.get("onlyonce") or False
            self._cron_enabled = config.get("cron_enabled") or False
            self._cron_expression = (config.get("cron_expression") or "").strip()
            # 缺省为 True：老配置没有这个键时保持显示侧栏入口
            self._sidebar_enabled = config.get("sidebar_enabled", True)

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
            "skip_scraped": self._skip_scraped,
            "onlyonce": False,
            "cron_enabled": self._cron_enabled,
            "cron_expression": self._cron_expression,
            "sidebar_enabled": self._sidebar_enabled,
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
        key = f"dir:{target_dir}"
        if not self.__dedup(key):
            return

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
        except Exception as e:
            logger.error(f"STRM刮削失败 {target_dir}：{str(e)} - {traceback.format_exc()}")

    def __dedup(self, key: str) -> bool:
        """
        判断目标是否处于去重窗口内，允许处理时写入时间戳并返回 True
        """
        now = time.time()
        with lock:
            last = self._scraped.get(key)
            if last and now - last < DEDUP_TTL:
                logger.info(f"{key} 近期已刮削，跳过")
                return False
            self._scraped[key] = now
            return True

    @staticmethod
    def __fallback_file_item(target: Path, target_type: str) -> schemas.FileItem:
        """
        无法从存储链解析时手工构造本地文件项作为兜底

        :param target: 目录或文件
        :param target_type: dir=目录刮削（补齐剧集级文件），file=单文件刮削（单集/单版本）
        """
        # 目录必须以 / 结尾，主程序据此判定目录刮削分支
        item_path = target.as_posix() + "/" if target_type == "dir" else target.as_posix()
        return schemas.FileItem(
            storage="local",
            type=target_type,
            path=item_path,
            name=target.name,
            basename=target.stem,
            extension=None if target_type == "dir" else target.suffix[1:],
            modify_time=target.stat().st_mtime,
        )

    def __scrape_file(self, strm_file: Path, overwrite: Optional[bool] = None) -> Tuple[bool, str]:
        """
        文件级刮削：只处理单个 .strm（电视剧单集 / 电影多版本中的指定版本）

        .strm 位于主程序 settings.RMT_MEDIAEXT 白名单内，因此单文件刮削受支持。
        单文件只会产出该集的 .nfo 与单集图，剧集级文件仍需对整个根目录做目录级刮削。
        """
        if strm_file.suffix.lower() != ".strm":
            return False, "只支持刮削 .strm 文件"
        key = f"file:{strm_file}"
        if not self.__dedup(key):
            return False, "近期已刮削，已跳过"
        try:
            # 优先走存储链解析（CloudDrive2 / alist / rclone 等挂载场景），
            # 取不到时再用手写的本地文件项兜底。
            file_item = self.__resolve_file_item(strm_file) or self.__fallback_file_item(strm_file, "file")
            if file_item and file_item.type != "file":
                file_item.type = "file"
            ok, msg = ScrapingChain().scrape_metadata(
                fileitem=file_item,
                init_folder=False,
                recursive=False,
                overwrite=self._overwrite if overwrite is None else overwrite,
            )
            logger.info(f"STRM单文件刮削{'完成' if ok else '未完成'}：{strm_file}")
            return ok, msg
        except Exception as e:
            logger.error(f"STRM单文件刮削失败 {strm_file}：{str(e)} - {traceback.format_exc()}")
            return False, str(e)

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

    @staticmethod
    def __already_scraped(target: Path, strms: List[Path]) -> bool:
        """
        判断一个剧集根目录是否已经刮削完整。

        判据：
        1) 目录下每个 .strm 都有同名 .nfo；
        2) 若 .strm 位于子目录（即存在 Season 1 / S01 等季目录），还要求根目录有 tvshow.nfo。

        说明：宿主的「文件已存在，跳过」只对 backdrop 生效，其余 8~9 种图片每次都会重下，
        因此必须在插件侧提前整目录跳过，否则重扫成本接近首次刮削。
        """
        if not all(StrmScraper.__nfo_exists(s) for s in strms):
            return False
        # 存在季目录 → 视为剧集，额外要求剧集级 tvshow.nfo
        if any(s.parent != target for s in strms):
            return (target / "tvshow.nfo").exists()
        return True

    def full_scan(self, force: bool = False):
        """
        全量扫描监控目录内所有 .strm，按剧集根目录去重后刮削。

        :param force: True 时无视「跳过已刮削」开关，强制重刮（覆盖与否仍由 _overwrite 决定）
        """
        for mon_path in [d.strip() for d in self._monitor_dirs.split("\n") if d.strip()]:
            root = Path(mon_path)
            if not root.exists():
                logger.warn(f"监控目录不存在：{mon_path}")
                continue
            logger.info(f"STRM全量扫描：{root}{'（强制）' if force else ''}")
            # 先完整收集：剧集根目录 -> 该目录下全部 .strm（避免边遍历边写文件）
            groups: Dict[Path, List[Path]] = {}

            def _skip(full: str) -> bool:
                if not self._exclude_keywords:
                    return False
                return any(kw and re.findall(kw, full) for kw in self._exclude_keywords.split("\n"))

            for strm in scan_strm_files(root, skip=_skip):
                groups.setdefault(self.__series_root(strm), []).append(strm)

            skip = (not force) and self._skip_scraped
            scraped_cnt = skipped_cnt = 0
            for target, strms in groups.items():
                if skip and self.__already_scraped(target, strms):
                    skipped_cnt += 1
                    continue
                try:
                    self.__scrape(target)
                    scraped_cnt += 1
                except Exception as e:
                    logger.error(f"STRM刮削失败 {target}：{str(e)}")
            logger.info(
                f"STRM全量扫描结束：{root} 共 {len(groups)} 个目录，"
                f"刮削 {scraped_cnt} 个，跳过 {skipped_cnt} 个已刮削"
            )

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
    # 媒体库扫描：区分电影 / 电视剧，识别季、单集与电影多版本
    # ------------------------------------------------------------------
    def __is_excluded(self, raw_path: str) -> bool:
        """
        判断路径是否命中排除关键词或属于回收站/隐藏文件
        """
        # 统一为正斜杠，保证 Windows 路径下的回收站/隐藏目录判断同样生效
        posix_path = raw_path.replace("\\", "/")
        if self._exclude_keywords:
            for kw in self._exclude_keywords.split("\n"):
                if kw and re.findall(kw, raw_path):
                    return True
        return any(p in posix_path for p in ["/@Recycle/", "/#recycle/", "/@eaDir", "/."])

    def __is_allowed(self, raw_path: str) -> bool:
        """
        校验路径是否位于已配置的监控目录内，防止接口越权刮削任意文件
        """
        target = Path(raw_path).resolve()
        for mon_path in [d.strip() for d in self._monitor_dirs.split("\n") if d.strip()]:
            try:
                target.relative_to(Path(mon_path).resolve())
                return True
            except ValueError:
                continue
        return False

    @staticmethod
    def __nfo_exists(strm_path: Path) -> bool:
        """
        判断单个 .strm 是否已经生成对应的 .nfo 文件
        """
        return strm_path.with_suffix(".nfo").exists()

    @staticmethod
    def __season_no(file_name: str, season_dir: str) -> int:
        """
        推断单集所属季号：优先取季目录数字，其次取文件名中的 SxxExx
        """
        matched = re.search(r"(?i)(?:season\s*(\d+)|^s(\d{1,3})$)", season_dir)
        if matched:
            return int(matched.group(1) or matched.group(2) or 1)
        matched = EPISODE_RE.search(file_name)
        if matched and matched.group(1):
            return int(matched.group(1))
        return 1

    @staticmethod
    def __episode_no(file_name: str) -> Optional[int]:
        """
        从文件名中提取集号，无法识别时返回 None
        """
        matched = EPISODE_RE.search(file_name)
        if not matched:
            return None
        for group in (matched.group(2), matched.group(3), matched.group(4)):
            if group:
                return int(group)
        return None

    def __collect_items(self) -> List[Dict[str, Any]]:
        """
        扫描监控目录，聚合出媒体清单（区分电影/电视剧，识别季、单集与电影多版本）
        """
        groups: Dict[str, Dict[str, Any]] = {}
        scanned = 0
        for mon_path in [d.strip() for d in self._monitor_dirs.split("\n") if d.strip()]:
            root = Path(mon_path)
            if not root.exists():
                continue
            found = scan_strm_files(root, skip=self.__is_excluded)
            if len(found) >= MAX_SCAN_FILES:
                logger.warn(f"STRM列表扫描达到上限 {MAX_SCAN_FILES}，结果可能不完整")
            for strm in found:
                scanned += 1
                series_root = self.__series_root(strm)
                # 季目录：文件相对剧集根的那一级目录名
                try:
                    rel_parent = strm.parent.relative_to(series_root).as_posix()
                except ValueError:
                    rel_parent = ""
                season_dir = rel_parent.split("/")[0] if rel_parent else ""
                if not season_dir or not SEASON_RE.match(season_dir):
                    season_dir = ""
                entry = groups.setdefault(str(series_root), {
                    "path": str(series_root),
                    "title": series_root.name,
                    "files": [],
                })
                try:
                    stat = strm.stat()
                    entry["files"].append({
                        "path": str(strm),
                        "name": strm.name,
                        "size": stat.st_size,
                        "modify_time": stat.st_mtime,
                        "season": season_dir,
                        "scraped": self.__nfo_exists(strm),
                    })
                except OSError as e:
                    logger.debug(f"读取文件信息失败，跳过 {strm}：{e}")

        result: List[Dict[str, Any]] = []
        for root_path, entry in groups.items():
            files = entry["files"]
            # 剧集判定：存在季目录，或文件名带集号特征
            episode_files = [f for f in files if f["season"] or self.__episode_no(f["name"])]
            if episode_files:
                seasons: Dict[int, List[Dict[str, Any]]] = {}
                for file_item in files:
                    season_no = self.__season_no(file_item["name"], file_item["season"])
                    seasons.setdefault(season_no, []).append(file_item)
                season_list = []
                for season_no in sorted(seasons.keys()):
                    eps = sorted(
                        seasons[season_no],
                        key=lambda x: (self.__episode_no(x["name"]) or 0, x["name"]),
                    )
                    season_list.append({
                        "no": season_no,
                        "name": f"Season {season_no}" if season_no else "Specials",
                        "count": len(eps),
                        "files": eps,
                    })
                result.append({
                    "path": root_path,
                    "title": entry["title"],
                    "type": "tv",
                    "total_files": len(files),
                    "total_episodes": len(files),
                    "seasons": season_list,
                    "unscraped": sum(1 for f in files if not f["scraped"]),
                    "dir_scraped": (Path(root_path) / "tvshow.nfo").exists(),
                    "last_scrape": max((f["modify_time"] for f in files), default=0),
                })
            else:
                versions = [{
                    "path": file_item["path"],
                    "name": file_item["name"],
                    "size": file_item["size"],
                    "modify_time": file_item["modify_time"],
                    "scraped": file_item["scraped"],
                } for file_item in files]
                result.append({
                    "path": root_path,
                    "title": entry["title"],
                    "type": "movie",
                    "total_files": len(versions),
                    "total_episodes": 0,
                    "multi_version": len(versions) > 1,
                    "versions": versions,
                    "unscraped": sum(1 for v in versions if not v["scraped"]),
                    "dir_scraped": (Path(root_path) / "movie.nfo").exists(),
                    "last_scrape": max((v["modify_time"] for v in versions), default=0),
                })
        return sorted(result, key=lambda x: x["title"])

    def __list_items(self, force: bool = False) -> List[Dict[str, Any]]:
        """
        带缓存的媒体清单查询
        """
        now = time.time()
        if not force and now - self._list_cache.get("time", 0) < LIST_CACHE_TTL:
            return self._list_cache["items"]
        items = self.__collect_items()
        self._list_cache = {"time": now, "items": items}
        return items

    # ------------------------------------------------------------------
    # 异步刮削任务：支持目录级与文件级（单集/单版本）批量刮削
    # ------------------------------------------------------------------
    def __submit_task(self, paths: List[str], target: str, overwrite: Optional[bool]) -> str:
        """
        提交一个后台刮削任务并返回任务ID
        """
        task_id = uuid.uuid4().hex[:12]
        with self._task_lock:
            self._tasks[task_id] = {
                "id": task_id,
                "status": "running",
                "target": target,
                "total": len(paths),
                "done": 0,
                "success": 0,
                "failed": 0,
                "results": [],
                "started": time.time(),
            }
        threading.Thread(
            target=self.__run_task, args=(task_id, paths, target, overwrite), daemon=True
        ).start()
        return task_id

    def __run_task(self, task_id: str, paths: List[str], target: str, overwrite: Optional[bool]):
        """
        执行后台刮削任务并更新任务状态
        """
        for raw in paths:
            try:
                path = Path(raw)
                if target == "file":
                    # 用户显式触发的刮削一律绕过去重窗口，避免 10 分钟内点了没反应
                    with lock:
                        self._scraped.pop(f"file:{path}", None)
                    ok, msg = self.__scrape_file(path, overwrite)
                else:
                    with lock:
                        self._scraped.pop(f"dir:{path}", None)
                    ok, msg = self.__scrape_dir(path, overwrite)
            except Exception as e:  # noqa: BLE001
                ok, msg = False, str(e)
            with self._task_lock:
                task = self._tasks.get(task_id)
                if not task:
                    break
                task["done"] += 1
                task["success" if ok else "failed"] += 1
                task["results"].append({"path": raw, "success": ok, "message": msg})
        with self._task_lock:
            task = self._tasks.get(task_id)
            if task and task.get("status") != "canceled":
                task["status"] = "done"
                task["finished"] = time.time()
        self._list_cache = {"time": 0.0, "items": []}

    def __scrape_dir(self, target_dir: Path, overwrite: Optional[bool] = None) -> Tuple[bool, str]:
        """
        目录级刮削并返回结果；与 __scrape 的区别是回传成功与否，便于任务状态统计
        """
        try:
            file_item = self.__resolve_file_item(target_dir) or self.__fallback_file_item(target_dir, "dir")
            ok, msg = ScrapingChain().scrape_metadata(
                fileitem=file_item,
                init_folder=True,
                recursive=True,
                overwrite=self._overwrite if overwrite is None else overwrite,
            )
            return ok, msg
        except Exception as e:
            logger.error(f"STRM目录刮削失败 {target_dir}：{str(e)} - {traceback.format_exc()}")
            return False, str(e)

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
                "description": "触发一次全量扫描刮削，force=true 时忽略「跳过已刮削」开关",
            },
            {
                "path": "/strm_rescrape",
                "endpoint": self.api_rescrape,
                "methods": ["GET"],
                "summary": "重新刮削单个合集",
                "description": "对指定目录（覆盖模式）重新执行一次刮削",
            },
            {
                # 与 /strm_scan 同一实现，但走 bear 认证，供 Vue 侧栏页调用
                "path": "/scan",
                "endpoint": self.api_scan,
                "methods": ["GET"],
                "auth": "bear",
                "summary": "触发全量扫描",
                "description": "后台启动一次全量扫描，force=true 时忽略「跳过已刮削」开关",
            },
            {
                "path": "/overview",
                "endpoint": self.api_overview,
                "methods": ["GET"],
                "auth": "bear",
                "summary": "STRM概览统计",
                "description": "返回媒体总数、电影/电视剧数量与刮削状态统计",
            },
            {
                "path": "/items",
                "endpoint": self.api_items,
                "methods": ["GET"],
                "auth": "bear",
                "summary": "STRM媒体清单",
                "description": "返回电影（含多版本）与电视剧（含季/集）聚合清单",
            },
            {
                "path": "/files",
                "endpoint": self.api_files,
                "methods": ["GET"],
                "auth": "bear",
                "summary": "STRM单集/版本清单",
                "description": "按媒体目录返回单集或版本文件明细，season 为空时返回全部",
            },
            {
                "path": "/scrape",
                "endpoint": self.api_scrape,
                "methods": ["POST"],
                "auth": "bear",
                "summary": "STRM刮削任务",
                "description": "按目录或按文件批量刮削，target=dir 为目录级刮削，target=file 为单集/单版本刮削",
            },
            {
                "path": "/tasks",
                "endpoint": self.api_tasks,
                "methods": ["GET"],
                "auth": "bear",
                "summary": "STRM任务状态",
                "description": "返回刮削任务的执行进度与结果",
            },
            {
                # 图片由 <img> 直接请求，无法携带 Bearer 头，因此匿名放行；
                # 读取范围严格限制为「监控目录内的海报文件名」，见 api_cover 校验。
                "path": "/cover",
                "endpoint": self.api_cover,
                "methods": ["GET"],
                "allow_anonymous": True,
                "summary": "STRM媒体海报",
                "description": "优先返回媒体目录内的本地海报，缺失时按目录名中的 tmdbid 回退 TMDB",
            },
        ]

    # ------------------------------------------------------------------
    # 海报：优先取媒体目录内的本地海报，缺失时按目录名中的 tmdbid 回退 TMDB
    # ------------------------------------------------------------------
    def __local_poster(self, media_dir: Path) -> Optional[Path]:
        """
        在媒体目录下查找刮削产出的海报文件
        """
        try:
            if not media_dir.is_dir():
                return None
            for name in POSTER_NAMES:
                candidate = media_dir / name
                if candidate.is_file() and candidate.suffix.lower() in POSTER_SUFFIX:
                    return candidate
            # 兜底：目录下任何 poster* 图片（命名随刮削器变化）
            for child in sorted(media_dir.iterdir()):
                if child.is_file() and child.name.lower().startswith("poster") \
                        and child.suffix.lower() in POSTER_SUFFIX:
                    return child
        except OSError as e:
            logger.debug(f"读取本地海报失败 {media_dir}：{e}")
        return None

    def __tmdb_poster_url(self, media_dir: Path, media_type: Optional[str] = None) -> Optional[str]:
        """
        按目录名中的 [tmdbid=xxx] 查询 TMDB，返回海报绝对地址（带内存缓存）
        """
        matched = TMDBID_RE.search(media_dir.name)
        if not matched:
            return None
        tmdbid = int(matched.group(1))
        cache_key = f"{media_type or 'auto'}:{tmdbid}"
        cached = self._poster_cache.get(cache_key)
        if cached is not None:
            return cached or None
        url = ""
        try:
            from app.chain.tmdb import TmdbChain
            from app.core.config import settings
            from app.schemas.types import MediaType

            mtype = MediaType.TV if media_type == "tv" else MediaType.MOVIE
            info = TmdbChain().tmdb_info(tmdbid=tmdbid, mtype=mtype) or {}
            poster_path = info.get("poster_path")
            if not poster_path and mtype == MediaType.TV:
                # 电视剧详情缺海报时，尝试季海报
                try:
                    season_info = TmdbChain().tmdb_info(tmdbid=tmdbid, mtype=MediaType.TV, season=1) or {}
                    poster_path = season_info.get("poster_path") or poster_path
                except Exception:
                    pass
            if poster_path:
                domain = str(getattr(settings, "TMDB_IMAGE_DOMAIN", "") or "https://image.tmdb.org").rstrip("/")
                url = f"{domain}/t/p/w500{poster_path}"
        except Exception as e:
            logger.debug(f"TMDB 海报查询失败（tmdbid={tmdbid}）：{e}")
        self._poster_cache[cache_key] = url
        return url or None

    def api_cover(self, path: str = "", type: str = ""):
        """
        返回媒体海报：本地海报优先，其次 TMDB，都没有则 404（前端回退渐变占位图）
        """
        if not path:
            raise HTTPException(status_code=404, detail="poster not found")
        media_dir = Path(path)
        try:
            media_dir = media_dir.resolve()
        except OSError:
            raise HTTPException(status_code=404, detail="poster not found")
        # 安全校验：只允许读取已配置监控目录范围内的文件
        allowed_roots = []
        for d in self._monitor_dirs.split("\n"):
            d = d.strip()
            if d:
                try:
                    allowed_roots.append(str(Path(d).resolve()).rstrip(os.sep) + os.sep)
                except OSError:
                    continue
        target_str = str(media_dir)
        if not allowed_roots or not any(
            target_str == r.rstrip(os.sep) or target_str.startswith(r) for r in allowed_roots
        ):
            raise HTTPException(status_code=404, detail="poster not found")

        local = self.__local_poster(media_dir)
        if local:
            return FileResponse(
                str(local),
                media_type="image/jpeg" if local.suffix.lower() in (".jpg", ".jpeg") else "image/png",
                headers={"Cache-Control": "public, max-age=86400"},
            )
        remote = self.__tmdb_poster_url(media_dir, type)
        if remote:
            return RedirectResponse(url=remote, status_code=302)
        raise HTTPException(status_code=404, detail="poster not found")

    def api_scan(self, force: bool = False) -> schemas.Response:
        threading.Thread(target=self.full_scan, kwargs={"force": force}, daemon=True).start()
        return schemas.Response(success=True, message="全量扫描已在后台启动")

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
        # 清除该目录的去重缓存（dir / file 两种 key 都清，兼容历史版本写入）
        key = str(target)
        with lock:
            self._scraped.pop(key, None)
            self._scraped.pop(f"dir:{key}", None)
        try:
            self.__scrape(target)
            return schemas.Response(success=True, message=f"已触发重新刮削：{path}")
        except Exception as e:
            return schemas.Response(success=False, message=f"刮削失败：{e}")
        finally:
            self._overwrite = old_overwrite

    # ------------------------------------------------------------------
    # 前端联邦界面专用接口（Vue 侧栏页 AppPage 调用）
    # ------------------------------------------------------------------
    @staticmethod
    def __envelope(data: Any = None, success: bool = True, message: str = "") -> Dict[str, Any]:
        """
        构造与宿主普通 REST 一致的响应信封
        """
        return {"success": success, "message": message, "data": data}

    def api_overview(self) -> Dict[str, Any]:
        """
        返回媒体总数、类型分布与刮削状态统计
        """
        items = self.__list_items()
        overview = {
            "total": len(items),
            "movie": sum(1 for i in items if i["type"] == "movie"),
            "tv": sum(1 for i in items if i["type"] == "tv"),
            "multi_version_movie": sum(1 for i in items if i.get("multi_version")),
            "dir_scraped": sum(1 for i in items if i["dir_scraped"]),
            "unscraped_items": sum(1 for i in items if i["unscraped"] > 0),
            "total_files": sum(i["total_files"] for i in items),
            "monitor_dirs": [d.strip() for d in self._monitor_dirs.split("\n") if d.strip()],
            "monitoring": bool(self._observer),
        }
        return self.__envelope(overview)

    def api_items(self, refresh: bool = False) -> Dict[str, Any]:
        """
        返回媒体清单聚合结果
        """
        return self.__envelope(self.__list_items(force=refresh))

    def api_files(self, path: str = "", season: str = "") -> Dict[str, Any]:
        """
        返回指定媒体目录下的单集或版本文件明细
        """
        if not path:
            return self.__envelope(None, False, "缺少 path 参数")
        if not self.__is_allowed(path):
            return self.__envelope(None, False, "path 不在监控目录范围内")
        target = Path(path).resolve()
        for item in self.__list_items():
            if Path(item["path"]).resolve() != target:
                continue
            files: List[Dict[str, Any]] = []
            if item["type"] == "tv":
                for season_item in item["seasons"]:
                    if season and str(season_item["no"]) != season:
                        continue
                    for file_item in season_item["files"]:
                        row = dict(file_item)
                        row["season_no"] = season_item["no"]
                        row["season_name"] = season_item["name"]
                        row["episode"] = self.__episode_no(file_item["name"])
                        files.append(row)
            else:
                files = list(item["versions"])
            return self.__envelope({
                "title": item["title"],
                "type": item["type"],
                "path": item["path"],
                "files": files,
            })
        return self.__envelope(None, False, "未找到该媒体目录，请先刷新列表")

    def api_scrape(self, payload: Dict[str, Any] = Body(default={})) -> Dict[str, Any]:
        """
        提交刮削任务，支持目录级与文件级（单集/单版本）批量刮削
        """
        body = payload or {}
        paths = [str(p) for p in (body.get("paths") or []) if str(p).strip()]
        target = "file" if body.get("target") == "file" else "dir"
        raw_overwrite = body.get("overwrite")
        overwrite = None if raw_overwrite is None else bool(raw_overwrite)
        if not paths:
            return self.__envelope(None, False, "缺少 paths 参数")
        illegal = [p for p in paths if not self.__is_allowed(p)]
        if illegal:
            return self.__envelope({"illegal": illegal}, False, "存在不在监控目录范围内的路径")
        task_id = self.__submit_task(paths, target, overwrite)
        return self.__envelope({"task_id": task_id})

    def api_tasks(self, task_id: str = "") -> Dict[str, Any]:
        """
        返回刮削任务状态，task_id 为空时返回全部任务
        """
        with self._task_lock:
            if task_id:
                task = self._tasks.get(task_id)
                return self.__envelope(task) if task else self.__envelope(None, False, "任务不存在")
            tasks = list(self._tasks.values())
        tasks.sort(key=lambda x: x.get("started", 0), reverse=True)
        return self.__envelope(tasks[:50])

    # ------------------------------------------------------------------
    # 界面
    # ------------------------------------------------------------------
    @staticmethod
    def get_render_mode() -> Tuple[str, Optional[str]]:
        """
        获取插件渲染模式：已构建联邦产物时使用 Vue 远程组件，否则回退 Vuetify 表单
        """
        dist_path = Path(__file__).parent / "dist" / "assets"
        if (dist_path / "remoteEntry.js").exists():
            return "vue", "dist/assets"
        return "vuetify", None

    def get_sidebar_nav(self) -> List[Dict[str, Any]]:
        """
        声明插件在主界面左侧导航栏中的全页入口，仅在 Vue 模式且已启用时生效
        """
        if not self.get_state():
            return []
        if self.get_render_mode()[0] != "vue":
            return []
        if not self._sidebar_enabled:
            return []
        return [{
            "nav_key": "main",
            "title": "STRM刮削",
            "icon": "mdi-filmstrip",
            "section": "organize",
            "permission": "manage",
            "order": 60,
        }]

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
                            {
                                "component": "VCol",
                                "props": {"cols": 12, "md": 4},
                                "content": [
                                    {
                                        "component": "VSwitch",
                                        "props": {
                                            "model": "skip_scraped",
                                            "label": "全量扫描跳过已刮削",
                                        },
                                    }
                                ],
                            },
                            {
                                "component": "VCol",
                                "props": {"cols": 12, "md": 4},
                                "content": [
                                    {
                                        "component": "VSwitch",
                                        "props": {
                                            "model": "sidebar_enabled",
                                            "label": "显示侧栏入口",
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
            "skip_scraped": True,
            "mode": "compatibility",
            "monitor_dirs": "",
            "exclude_keywords": "",
            "cron_enabled": False,
            "cron_expression": "0 4 * * *",
            "sidebar_enabled": True,
        }

    def get_page(self) -> Optional[List[dict]]:
        """
        返回插件详情页面

        Vue 模式下（已构建联邦产物）详情页由远程组件渲染，返回空列表；
        未构建时给出最简运行状态提示。海报墙与刮削历史改由 Vue 侧栏页实现，
        后端不再维护 scrape_history，也不再自行拼装 TMDB 海报地址。
        """
        if self.get_render_mode()[0] == "vue":
            return []
        monitor_dirs = [d.strip() for d in self._monitor_dirs.split("\n") if d.strip()]
        return [
            {
                "component": "VAlert",
                "props": {
                    "type": "info",
                    "variant": "tonal",
                    "text": f"STRM 监控{'运行中' if self._observer else '未启动'}，"
                            f"监控目录 {len(monitor_dirs)} 个。"
                            f"媒体清单与单集/版本刮削请使用侧栏「STRM刮削」页面（Vue 模式），"
                            f"或调用 /strm_scan 触发全量扫描。",
                },
            }
        ]

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
