"""STRM 监控刮削插件（V3 专用）。

监控目录中新增或移入的 ``.strm`` 文件，调用主程序 ``ScrapingChain`` **就地**补齐
剧集级元数据（tvshow.nfo、poster/backdrop/logo/banner/thumb、Season1/season.nfo），
不转移文件；刮削结果由主程序管理，本插件另按需把**刮削记录**落到自己的数据目录，
便于界面上回溯。

目录结构识别 ``<监控目录>/<分类名>/<剧名>/Season N/`` 与
``<监控目录>/<剧名>/Season N/`` 两种形态：前者的一级子目录即主程序
``category.yaml`` 生成的分类目录（国漫/日番/国产剧/欧美剧/日韩剧/纪录片/儿童/综艺/未分类），
界面据此分组，全量刷新也可只对某个分类执行。

媒体类型由「分类目录名 + 是否存在季/集特征」共同判定，输出 ``movie`` /
``tv`` / ``music`` 三种：音乐类分类（见 ``MUSIC_CATEGORIES``）下的条目既没有季目录
也没有集号，若只按「无集号即电影」判定会被界面错误标成电影，因此单列 ``music``。

与 V2 版的区别：V3 走 ``ScrapingChain.scrape_metadata``，V2 走
``MediaChain.scrape_metadata`` + ``StorageChain.get_file_item(storage=\"local\")``。
"""

import json
import os
import re
import threading
import time
import traceback
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

from fastapi import Body, HTTPException
from fastapi.responses import FileResponse, RedirectResponse
from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer
from watchdog.observers.polling import PollingObserver

from app import schemas
from app.chain.scraping import ScrapingChain
from app.chain.storage import StorageChain
from app.plugins import _PluginBase
from app.runtime.thread import ThreadHelper
from app.sdk.logging import logger

# 只保护 _scraped 去重表。刮削本身的互斥不靠这把锁，而是由「队列唯一的消费线程」保证：
# 全文只有 __worker_loop 一个线程会调用 __scrape_target，因此不存在并发刮削同一目录的可能。
lock = threading.Lock()
# 同一目录 10 分钟内不重复刮削：一季多集落盘时避免整剧反复重刮（**只对事件源生效**）
DEDUP_TTL = 600
# 去重表条目数超过该阈值时先淘汰过期条目，避免长期运行下无限增长
DEDUP_PRUNE_THRESHOLD = 128

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
# 刮削记录文件名（落在插件数据目录下）
RECORD_FILE = "scrape_records.json"
# 刮削记录保留条数上限，避免长期运行后数据目录无限膨胀
RECORD_LIMIT = 500
# 条目级刮削状态文件名（落在插件数据目录下）：path -> {status, time, error_code, message}
STATE_FILE = "scrape_state.json"
# 状态表条数上限，超出按 time 淘汰最旧，长期运行不无限增长
STATE_LIMIT = 2000
# 状态表里未写状态时的兜底值（新入库、尚未刮削的媒体）
STATE_PENDING = "pending"
# 失败原因错误码 → 中文文案（与前端 F9 的常量表保持一致）
ERROR_CODE_LABELS = {
    "not_matched": "未匹配到 TMDB",
    "timeout": "网络超时",
    "permission": "权限不足",
    "path_gone": "路径已失效",
    "unknown": "未知错误",
}
# 归类为「音乐」的分类目录名（小写比较）。
# 音乐条目既无季目录也无集号，只按结构判断会被误判成电影，因此以分类名辅助识别。
MUSIC_CATEGORIES = frozenset({
    "音乐", "music", "歌曲", "原声", "原声带", "ost", "古典", "古典音乐",
})
# TMDB 海报地址缓存条数上限（超出后按插入顺序淘汰最早的一条）
POSTER_CACHE_LIMIT = 512
# /queue 返回的「最近完成」条数上限
QUEUE_RECENT_LIMIT = 20
# /queue 返回的「排队中」条数上限（队列本身不截断，仅展示时截断）
QUEUE_SNAPSHOT_LIMIT = 50


def scan_strm_files(root: Path, skip=None, limit: int = MAX_SCAN_FILES) -> List[Path]:
    """
    递归收集目录下所有 .strm 文件。

    用 os.walk 而非 Path.rglob：rglob 遇到无权限/已失效的子目录会直接抛出 OSError，
    导致整次扫描或整个清单接口中断；os.walk 通过 onerror 回调跳过这些目录。
    CD2 / alist / rclone 等挂载后端偶发 EACCES、ESTALE 时尤其重要。
    """
    found: List[Path] = []

    def _on_error(err: OSError):
        """os.walk 的错误回调：跳过无权限或已失效的目录，不让整次扫描中断。"""
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


def list_category_dirs(root: Path) -> List[str]:
    """
    返回监控目录下可作为「分类分组」的一级子目录名（已排序）。

    判据（两步，缺一不可）：
    1) 该一级子目录的直接子项里有**不是季目录**的目录 —— 即它的孩子是「剧名」；
    2) 该一级子目录里没有直接躺着 .strm 文件 —— 分类目录只装剧名，自己不装剧集。

    这样 ``<库根>/国产剧/<剧名>/Season 1/`` 会命中（孩子是剧名），而
    ``<库根>/<剧名>/Season 1/`` 不会（孩子是季目录），电影目录也不会。

    自动识别而非读取 ``category.yaml``：分类名就是目录名，实时反映磁盘现状，
    也兼容用户自建的分类目录与中文以外的命名。
    """
    names: List[str] = []
    try:
        entries = sorted(root.iterdir(), key=lambda p: p.name)
    except OSError as err:
        logger.warn(f"读取监控目录失败：{root}：{err}")
        return names
    for entry in entries:
        try:
            if not entry.is_dir() or entry.name.startswith("."):
                continue
            if any(child.is_file() and child.suffix.lower() == ".strm" for child in entry.iterdir()):
                # 自己直接装 .strm → 是剧集目录/电影目录，不是分类层
                continue
            # 直接子项里存在「非季目录」的目录 → 这些是剧名，因此本层是分类层
            has_show_dir = any(
                child.is_dir() and not child.name.startswith(".") and not SEASON_RE.match(child.name)
                for child in entry.iterdir()
            )
        except OSError:
            continue
        if has_show_dir:
            names.append(entry.name)
    return names


def match_category(file_path: Path, root: Path, categories: Iterable[str]) -> str:
    """
    判断 ``file_path`` 相对监控目录 ``root`` 落在哪个分类目录内，不在分类内时返回空串。

    :param file_path: 任意深度的 .strm 或其父目录
    :param root: 该 .strm 所属的监控目录
    :param categories: ``list_category_dirs`` 的识别结果
    :return: 分类目录名；路径不落在任何分类内时返回空串
    """
    try:
        rel = file_path.relative_to(root)
    except ValueError:
        return ""
    head = rel.parts[0] if rel.parts else ""
    return head if head in set(categories) else ""



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
    """STRM 监控刮削插件主类。

    监控若干目录，捕获新增/移入的 ``.strm`` 文件后向上定位剧集根目录并就地刮削；
    同时提供全量扫描（可只对某个分类目录执行）与插件详情页海报墙（Vue 联邦 ``Page``）。

    界面不再注册主界面侧栏入口（``get_sidebar_nav`` 恒返回空），全部能力收敛到
    插件中心详情弹窗，由远程 ``Page`` 组件经 ``layout`` 事件声明所需宽度。
    """

    # 插件名称
    plugin_name = "STRM监控刮削"
    # 插件描述
    plugin_desc = "监控目录中新增的.strm文件，自动调用主程序刮削链（ScrapingChain）补齐元数据（tvshow.nfo/海报等），界面按分类目录分组，支持按分类刷新，刮削记录落插件数据目录。V3 专用插件。"
    # 插件标签（与 package.v3.json 的 labels 保持一致）
    plugin_label = "刮削,STRM,监控"
    # 插件图标（自定义图标必须写成完整 URL：裸文件名只会去官方库 icons/ 里找，找不到就回退成拼图占位图）
    plugin_icon = (
        "https://raw.githubusercontent.com/157888390/MoviePilot-Plugins"
        "/main/icons/strmscraper.png"
    )
    # 插件版本（V3 专用：本轮移除侧栏入口、新增音乐类型识别、规范化并发与缓存）
    plugin_version = "3.3.0"
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
    _cron_expression = ""          # 定时扫描 cron 表达式（空=关闭），交给宿主调度器执行
    _scraped: Dict[str, float] = {}   # 已刮削目录 + 时间戳，做轻量去重（超期条目会被淘汰）
    _record_enabled = True           # 是否把刮削记录落盘到插件数据目录
    _record_lock = threading.Lock()  # 刮削记录读写锁：写入是「读-改-写」，不加锁并发时会丢记录
    _list_cache: Dict[str, Any] = {"time": 0.0, "items": []}   # 媒体清单缓存
    _list_dirty = True               # 清单脏标记：刮削完成后置位，下次读取时才重扫目录
    # 监控根 -> 分类目录名列表，随清单重扫一并刷新（__category_of 写记录时避免每次遍历磁盘）
    _category_map: Dict[str, List[str]] = {}
    _poster_cache: Dict[str, str] = {}   # TMDB 海报地址缓存：key -> url（上限 POSTER_CACHE_LIMIT）
    _poster_lock = threading.Lock()      # 海报缓存读写锁：前端海报墙并发请求会并发读-改-写此字典

    # 条目级刮削状态表：path -> {status, time, error_code, message}，落盘到 scrape_state.json。
    # status 四态：scraped / failed / skipped / pending，供界面区分徽标与统计。
    _state_lock = threading.Lock()       # 状态表读写锁：读-改-写必须持锁
    _state_cache: Dict[str, Dict[str, Any]] = {}   # 状态内存缓存，随写随更新
    _state_loaded = False                # 状态表是否已从磁盘惰性加载进内存

    # 刮削队列：所有触发源（文件事件 / 界面按钮 / 启动时一次）统一入队，由唯一 worker
    # 串行消费。互斥由此从结构上保证，不再需要 _running 与 _tasks 两套各管一半的调度状态。
    _queue: List[Dict[str, Any]] = []        # 待刮削项；用户源插在事件源之前
    _queue_lock = threading.Lock()           # 队列与执行状态的读写锁
    _current: Optional[Dict[str, Any]] = None   # 正在执行的那一项
    _recent: List[Dict[str, Any]] = []       # 最近完成的项（上限 QUEUE_RECENT_LIMIT）
    _stats: Dict[str, int] = {"done": 0, "failed": 0, "canceled": 0}   # 累计统计
    _worker_running = False                  # 消费线程是否存活
    _worker_token: Optional[object] = None   # 当前存活 worker 的代际令牌：旧 worker 正常耗尽后
                                             # 不得踩掉新 worker 刚置的 _worker_running
    _abort_epoch = 0                         # 递增的中止标记：插件重载时让上一代 worker 自行退出
    # 取消令牌：请求取消时写入的 key（scan 项用 scope:paths 签名、普通项用 key）。
    # worker 每取一项前检查，命中即跳过并写 skipped，正在执行项不强行中断。
    _cancel_keys: set = set()
    # 扫描令牌：scan key 集合。scan 项一旦被 worker 取走展开，就不在队列里了，光靠 key
    # 判重挡不住「几秒内连点两次全量扫描」；这里记住「该扫描的展开项还没跑完」——
    # 展开出的目录项都带 origin=scan key，全部离开队列后令牌自动失效，因此不引入
    # 任何时间窗口，也不会把「跑完后再点一次」误判成重复。
    _scan_tokens: set = set()

    def init_plugin(self, config: dict = None):
        """读取配置、清理历史遗留数据并按需重启目录监控，允许重复调用。"""
        self._scraped = {}
        self._list_cache = {"time": 0.0, "items": []}
        self._list_dirty = True
        self._state_cache = {}
        self._state_loaded = False
        with self._queue_lock:
            self._stats = {"done": 0, "failed": 0, "canceled": 0}
            self._recent = []
            self._scan_tokens = set()
            self._cancel_keys = set()
            self._worker_token = None
        # 历史版本遗留的刮削历史数据已不再使用（海报墙改由 Vue 详情页实现），直接清理
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
            self._onlyonce = config.get("onlyonce") or False
            self._cron_expression = config.get("cron_expression") or ""
            # 缺省为 True：老配置没有这个键时保持记录开启
            self._record_enabled = config.get("record_enabled", True)
            # 历史配置里的 sidebar_enabled 已废弃：侧栏入口整体移除，读取时直接忽略

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
            # 走主程序共享线程池，而不是裸 threading.Timer：定时器线程不受
            # CONF.threadpool 约束，应用关闭时也不会被统一收敛。
            ThreadHelper().submit(self.__delayed_scan)
            self._onlyonce = False
            self.__save_config()

    def __delayed_scan(self):
        """延迟 3 秒后把一次全量扫描并入队列，等配置落盘与目录监控启动完成。

        延迟由线程池 worker 的 sleep 承担：一次性任务不值得引入独立定时线程。
        注意这里是**入队**而非直接执行，因此不会与用户手动触发的扫描互相抢占 ——
        两者在同一个队列里排队，天然串行。
        """
        time.sleep(3.0)
        result = self.__enqueue_scan(scope="all", paths=[], overwrite=None)
        logger.info(f"STRM监控刮削：启动时的全量扫描已入队（新增 {result['queued']} 项）")

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
            "record_enabled": self._record_enabled,
            "cron_expression": self._cron_expression,
        })

    def get_state(self) -> bool:
        """返回插件当前是否启用。"""
        return self._enabled

    # ------------------------------------------------------------------
    # 事件处理：watchdog 回调里只做轻量判断，真正的刮削丢给共享线程池
    # ------------------------------------------------------------------
    def event_handler(self, event_path: str, mon_path: str):
        """
        处理文件变化：过滤 .strm 后定位剧集根，并把它并入刮削队列。

        这里只做廉价判断（不碰磁盘以外的重活），刮削交给队列唯一的消费线程：
        一季多集连续落盘时事件会密集触发，若每个事件都直接占一个线程池 worker，
        并发数会随落盘速度失控，并可能与用户手动触发的刮削撞上同一目录。

        事件源独占 10 分钟去重窗口（``DEDUP_TTL``）：一季多集落盘时避免整剧被反复重刮。
        用户显式触发的刮削不走这个窗口 —— 那是「我就是要现在刮」的语义。
        """
        try:
            if not event_path.lower().endswith(".strm"):
                return
            if self.__is_excluded(event_path):
                logger.debug(f"{event_path} 命中排除规则，跳过")
                # 排除命中记 skipped：让界面能看到「该目录被跳过」而不是当作从未处理
                self.__write_state(event_path, "skipped", "unknown", "命中排除规则")
                return
            file_path = Path(event_path)
            if not file_path.exists():
                return
            target = self.__series_root(file_path)
            if not self.__dedup(f"dir:{target}"):
                # 去重窗口内重复落盘：记 skipped，避免界面把它显示成「未刮」误导用户重刮
                self.__write_state(str(target), "skipped", "unknown", "去重窗口内跳过")
                return
            self.__enqueue(
                [{"key": f"dir:{target}", "kind": "dir", "target": str(target)}],
                overwrite=None,
                source="event",
            )
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
            """按存储类型取文件项，取不到返回 None 而不是抛出。"""
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

    def __scrape_target(self, target: Path, kind: str, overwrite: bool) -> Tuple[bool, str]:
        """
        对单个目标执行一次刮削并返回 (是否成功, 消息)。

        这是插件里**唯一**的刮削执行点：目录级与文件级、事件源与用户源全部汇集到这里，
        所以「同一目录不会被并发刮削」由「只有一个线程会调它」这一事实保证，
        而不是靠某个散落的布尔量或那把只保护去重表的 ``lock``。

        ``kind=dir`` 走目录级刮削（``init_folder=True`` + ``recursive=True``）：主程序才会
        写出 tvshow.nfo / season.nfo / poster / backdrop / logo / banner / thumb 等剧集级
        文件，结果与在 MoviePilot 里手动刮削一个目录一致；``kind=file`` 走单文件级，
        只产出该集或该版本的 nfo。

        :param target: 已由调用方向上定位好的剧集根目录（电影为其所在目录），或单个 .strm
        :param kind: ``dir`` 或 ``file``
        :param overwrite: 覆盖策略，已在入队时解析完毕（不再回落配置，避免队列中途换语义）
        """
        key = f"{kind}:{target}"
        if kind == "file" and target.suffix.lower() != ".strm":
            return False, "只支持刮削 .strm 文件"
        try:
            # 用主程序 StorageChain 按路径解析出带 storage 字段的文件项：
            # ScrapingChain.scrape_metadata 内部依赖 fileitem.storage 定位，不能手写
            # 缺 storage 的 FileItem。__resolve_file_item 先试 local，取不到再枚举
            # 其它已配置存储（CloudDrive / alist / rclone 等）兜底。
            file_item = self.__resolve_file_item(target)
            if not file_item:
                # 手工构造本地文件项兜底：路径本机可达但没注册进存储时仍能刮。
                # __fallback_file_item 会 stat()，路径不存在时抛 OSError，由下方分支接住。
                file_item = self.__fallback_file_item(target, kind)
            if kind == "file" and file_item.type != "file":
                file_item.type = "file"
            ok, msg = ScrapingChain().scrape_metadata(
                fileitem=file_item,
                init_folder=kind == "dir",
                recursive=kind == "dir",
                overwrite=overwrite,
            )
            if ok:
                logger.info(f"STRM刮削完成：{key}")
                self.__write_state(str(target), "scraped")
            else:
                logger.warn(f"STRM刮削未完全成功 {key}：{msg}")
                self.__write_state(
                    str(target), "failed", self.__classify_error(Exception(msg or "unknown")), msg
                )
            self.__write_record(
                str(target), kind, ok, msg,
                "" if ok else self.__classify_error(Exception(msg or "unknown")),
            )
            return ok, msg
        except OSError as e:
            logger.warn(f"STRM刮削跳过 {key}：路径不可访问（{e}）")
            code = self.__classify_error(e)
            self.__write_state(str(target), "failed", code, str(e))
            self.__write_record(str(target), kind, False, f"路径不可访问：{e}", code)
            return False, f"路径不可访问：{e}"
        except Exception as e:
            logger.error(f"STRM刮削失败 {key}：{str(e)} - {traceback.format_exc()}")
            code = self.__classify_error(e)
            self.__write_state(str(target), "failed", code, str(e))
            self.__write_record(str(target), kind, False, str(e), code)
            return False, str(e)

    def __dedup(self, key: str) -> bool:
        """
        判断目标是否处于去重窗口内；允许处理时写入时间戳并返回 True。

        写入前按阈值淘汰过期条目：去重窗口只有 ``DEDUP_TTL`` 秒，超期条目不再有判断
        价值，事件持续触发时若不淘汰会让该字典随运行时长无限增长。
        """
        now = time.time()
        with lock:
            last = self._scraped.get(key)
            if last and now - last < DEDUP_TTL:
                logger.info(f"{key} 近期已刮削，跳过")
                return False
            if len(self._scraped) >= DEDUP_PRUNE_THRESHOLD:
                for stale in [k for k, ts in self._scraped.items() if now - ts >= DEDUP_TTL]:
                    self._scraped.pop(stale, None)
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

    def __collect_scan_targets(self, scope: str, paths: Optional[List[str]] = None) -> List[Path]:
        """
        枚举待刮削的剧集根目录（已去重、已排序稳定）。

        只负责「找出该刮哪些目录」，**不执行刮削** —— 结果会被展开成队列项，由唯一的
        worker 逐个消费。这样界面上看到的是「正在刮 X，还剩 N 个」的具体进度，
        而不是一个「扫描中」的布尔量。

        代价说明：主程序的「文件已存在，跳过」只对 backdrop 生效，其余 8~9 种图片每次都会
        重新下载，因此全量扫描成本接近首次刮削；只想补新增内容时应依赖事件监控。

        :param scope: ``all``=全部监控目录；``category``=只枚举 ``paths`` 指定的分类目录；
            ``incremental``=只枚举状态表不存在或刮削时间早于目录 mtime 的目录（补新增）；
            ``unscraped``=只枚举状态不为 ``scraped`` 的目录（补漏）。
        :param paths: ``scope=category`` 时的分类目录绝对路径列表
        """
        roots: List[Path] = []
        seen_roots: set = set()
        if scope == "category" and paths:
            for raw in paths:
                try:
                    candidate = Path(raw).resolve()
                except OSError:
                    continue
                # 只接受位于某个监控目录之内、且真实存在的目录，防止越界扫描。
                # 这里统一用 resolve 后的路径判断与去重，与 __is_allowed 的口径一致；
                # 原实现漏了去重，多层嵌套的监控目录下同一分类会被枚举两次。
                if not self.__is_allowed(str(candidate)) or not candidate.is_dir():
                    continue
                if candidate in seen_roots:
                    continue
                seen_roots.add(candidate)
                roots.append(candidate)
        else:
            for mon_path in [d.strip() for d in self._monitor_dirs.split("\n") if d.strip()]:
                root = Path(mon_path)
                try:
                    resolved = root.resolve()
                except OSError:
                    resolved = root
                if resolved in seen_roots:
                    continue
                seen_roots.add(resolved)
                roots.append(root)

        targets: List[Path] = []
        seen_targets: set = set()
        mtimes: Dict[Path, float] = {}
        for root in roots:
            if not root.exists():
                logger.warn(f"监控目录不存在：{root}")
                continue
            logger.info(f"STRM扫描枚举：{root}")
            # 先完整收集剧集根目录再返回，避免边遍历边触发刮削
            found = scan_strm_files(root, skip=self.__is_excluded)
            if len(found) >= MAX_SCAN_FILES:
                logger.warn(f"STRM扫描达到上限 {MAX_SCAN_FILES}，结果可能不完整")
            for strm in found:
                target = self.__series_root(strm)
                try:
                    mtime = strm.stat().st_mtime
                except OSError:
                    mtime = 0
                if target in seen_targets:
                    mtimes[target] = max(mtimes.get(target, 0), mtime)
                    continue
                seen_targets.add(target)
                mtimes[target] = mtime
                targets.append(target)

        # 增量 / 补漏过滤：只有显式指定时才按状态表筛，all/category 仍返回全部目标
        if scope in ("incremental", "unscraped"):
            filtered: List[Path] = []
            for target in targets:
                state_entry = self.__read_state(str(target))
                if scope == "incremental":
                    # 状态表没有该目录（全新入库），或上次刮削时间早于目录 mtime（落盘过新文件）
                    if not state_entry or float(state_entry.get("time") or 0) < mtimes.get(target, 0):
                        filtered.append(target)
                else:  # unscraped
                    if not state_entry or state_entry.get("status") != "scraped":
                        filtered.append(target)
            return filtered
        return targets

    # ------------------------------------------------------------------
    # 刮削记录：落在插件自己的数据目录（get_data_path），不受主程序记录影响
    # ------------------------------------------------------------------
    def __record_path(self) -> Path:
        """返回刮削记录文件的绝对路径（父目录不存在时创建）。"""
        data_dir = Path(self.get_data_path())
        data_dir.mkdir(parents=True, exist_ok=True)
        return data_dir / RECORD_FILE

    # ------------------------------------------------------------------
    # 条目级刮削状态：path -> {status, time, error_code, message}，落在 scrape_state.json
    # ------------------------------------------------------------------
    def __state_path(self) -> Path:
        """返回刮削状态文件的绝对路径（父目录不存在时创建）。"""
        data_dir = Path(self.get_data_path())
        data_dir.mkdir(parents=True, exist_ok=True)
        return data_dir / STATE_FILE

    def __load_state(self) -> Dict[str, Dict[str, Any]]:
        """读取已落盘的状态表；文件缺失或损坏时返回空字典。"""
        try:
            raw = self.__state_path().read_text(encoding="utf-8")
            data = json.loads(raw)
            return data if isinstance(data, dict) else {}
        except FileNotFoundError:
            return {}
        except Exception as e:
            logger.debug(f"读取刮削状态失败：{e}")
            return {}

    def __ensure_state_loaded(self):
        """惰性把状态表从磁盘加载进内存（进程重启后第一次读时触发）。"""
        if self._state_loaded:
            return
        with self._state_lock:
            if self._state_loaded:
                return
            self._state_cache = self.__load_state()
            self._state_loaded = True

    @staticmethod
    def __state_key(path: str) -> str:
        """状态表 key 规范化：统一成正斜杠。

        Windows 下 ``str(Path(...))`` 产出反斜杠路径，而接口层可能传正斜杠，若不规范化，
        同一目录会以两种 key 写入状态表、导致查不到。这里统一 ``as_posix()`` 口径。
        """
        try:
            return Path(path).as_posix()
        except Exception:
            return str(path)

    def __read_state(self, path: str) -> Optional[Dict[str, Any]]:
        """读取单条状态（内存缓存优先），取不到返回 None。"""
        self.__ensure_state_loaded()
        key = self.__state_key(path)
        with self._state_lock:
            return self._state_cache.get(key)

    def __write_state(
        self, path: str, status: str, error_code: str = "", message: str = ""
    ) -> None:
        """
        写入（或覆盖）单条状态并落盘。

        读-改-写必须持 ``_state_lock``：worker 每完成一项都会写状态，若不加锁，
        并发写会在「淘汰最旧 + 落盘」时互相覆盖，甚至触发字典迭代竞态。
        状态表按 ``time`` 淘汰最旧，条数上限 ``STATE_LIMIT``，长期运行不无限增长。
        """
        self.__ensure_state_loaded()
        key = self.__state_key(path)
        try:
            with self._state_lock:
                self._state_cache[key] = {
                    "status": status,
                    "time": time.time(),
                    "error_code": error_code,
                    "message": message or "",
                }
                if len(self._state_cache) > STATE_LIMIT:
                    ordered = sorted(
                        self._state_cache.items(), key=lambda kv: kv[1].get("time") or 0
                    )
                    for stale_path, _ in ordered[: len(self._state_cache) - STATE_LIMIT]:
                        self._state_cache.pop(stale_path, None)
                self.__state_path().write_text(
                    json.dumps(self._state_cache, ensure_ascii=False, indent=1),
                    encoding="utf-8",
                )
        except Exception as e:
            logger.debug(f"写入刮削状态失败（非阻断）：{e}")

    @staticmethod
    def __classify_error(exc: Exception) -> str:
        """
        把刮削异常归入结构化错误码，供记录与状态表携带、界面做中文映射。

        分类优先级：OSError 的 errno 最可靠，其次按异常/消息关键词兜底；
        都匹配不上归 ``unknown``。错误码集合与前端 F9 的映射表一一对应。
        """
        import errno

        if isinstance(exc, OSError):
            code = getattr(exc, "errno", None)
            if code in (errno.EACCES, errno.EPERM):
                return "permission"
            if code in (errno.ENOENT, errno.ESTALE, errno.ENOTDIR):
                return "path_gone"
        text = str(exc).lower()
        if any(word in text for word in ("timeout", "timed out", "超时", "连接超时")):
            return "timeout"
        if any(word in text for word in ("permission", "denied", "权限", "拒绝访问")):
            return "permission"
        if any(
            word in text
            for word in ("no such file", "not found", "not exist", "不存在", "已失效", "已删除", "路径")
        ):
            return "path_gone"
        if any(
            word in text
            for word in ("未匹配", "匹配失败", "无法识别", "not matched", "no match", "识别失败")
        ):
            return "not_matched"
        return "unknown"

    def __load_records(self) -> List[Dict[str, Any]]:
        """读取已落盘的刮削记录；文件缺失或损坏时返回空列表。"""
        try:
            raw = self.__record_path().read_text(encoding="utf-8")
            data = json.loads(raw)
            return data if isinstance(data, list) else []
        except FileNotFoundError:
            return []
        except Exception as e:
            logger.debug(f"读取刮削记录失败：{e}")
            return []

    def __write_record(
        self, target: str, target_type: str, success: bool, message: str = "", error_code: str = ""
    ):
        """
        追加一条刮削记录并落盘。

        写入是「读整个 JSON → 追加 → 整体回写」，必须持 ``_record_lock``：
        早期版本没加锁，多个刮削并发时后写的会覆盖先写的，记录凭空消失。

        记录只保留最近 ``RECORD_LIMIT`` 条，写入失败仅记 debug 日志，绝不阻断刮削主流程。
        ``error_code`` 是结构化失败原因（not_matched / timeout / permission / path_gone /
        unknown），空串表示成功或无分类信息。
        """
        if not self._record_enabled:
            return
        try:
            with self._record_lock:
                records = self.__load_records()
                records.append({
                    "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    "type": target_type,
                    "target": target,
                    "title": Path(target).name,
                    "category": self.__category_of(Path(target)),
                    "success": bool(success),
                    "message": message or "",
                    "error_code": error_code or "",
                })
                if len(records) > RECORD_LIMIT:
                    records = records[-RECORD_LIMIT:]
                self.__record_path().write_text(
                    json.dumps(records, ensure_ascii=False, indent=1), encoding="utf-8"
                )
        except Exception as e:
            logger.debug(f"写入刮削记录失败（非阻断）：{e}")

    def __category_of(self, target: Path) -> str:
        """返回目标路径所属的分类目录名，不在任何分类内时返回空串。

        分类目录名优先取 ``_category_map`` 缓存（随清单重扫刷新），避免每次写记录都
        重新遍历磁盘；缓存未命中时回退实时 ``list_category_dirs`` 并回填。
        """
        for mon_path in [d.strip() for d in self._monitor_dirs.split("\n") if d.strip()]:
            root = Path(mon_path)
            if not target.is_absolute():
                continue
            categories = self._category_map.get(str(root))
            if categories is None:
                categories = list_category_dirs(root)
                self._category_map[str(root)] = categories
            name = match_category(target, root, categories)
            if name:
                return name
        return ""


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

    def __item_state(
        self, root_path: str, dir_scraped: bool, max_mtime: float
    ) -> Tuple[str, str, str, float]:
        """
        聚合单条媒体的刮削状态，返回 ``(status, error_code, error_message, last_scrape)``。

        状态优先取 scrape_state.json（真实刮削结果），无记录时用 ``.nfo`` 是否存在的
        ``dir_scraped`` 兜底；``last_scrape`` 取状态表的真实刮削时间，取不到回落文件 mtime。
        """
        state_entry = self.__read_state(root_path)
        if state_entry and state_entry.get("status") in ("scraped", "failed", "skipped"):
            return (
                str(state_entry["status"]),
                str(state_entry.get("error_code") or ""),
                str(state_entry.get("message") or ""),
                float(state_entry.get("time") or max_mtime),
            )
        status = "scraped" if dir_scraped else STATE_PENDING
        return status, "", "", max_mtime

    def __file_state(self, path: str, scraped: bool) -> Tuple[str, str, str]:
        """单集/单版本级刮削状态，返回 ``(status, error_code, error_message)``。

        状态表（scrape_state.json）对单集刮削同样落盘，故优先取它；无记录时用 ``.nfo``
        是否存在的 ``scraped`` 兜底。供集列表四态图标（F7）使用，与目录级 ``__item_state``
        同口径、只是不含时间字段。
        """
        state_entry = self.__read_state(path)
        if state_entry and state_entry.get("status") in ("scraped", "failed", "skipped"):
            return (
                str(state_entry["status"]),
                str(state_entry.get("error_code") or ""),
                str(state_entry.get("message") or ""),
            )
        status = "scraped" if scraped else STATE_PENDING
        return status, "", ""

    def __collect_items(self) -> List[Dict[str, Any]]:
        """
        扫描监控目录，聚合出媒体清单（区分电影/电视剧，识别季、单集与电影多版本）

        每条结果附带条目级刮削状态（``status`` / ``error_code`` / ``error_message``，
        取自 scrape_state.json，``.nfo`` 是否存在只作无状态记录时的兜底）与两个时间：
        ``last_file_change``（文件 mtime）与 ``last_scrape``（真实刮削时间，取不到回落 mtime）。
        """
        groups: Dict[str, Dict[str, Any]] = {}
        self._category_map = {}
        for mon_path in [d.strip() for d in self._monitor_dirs.split("\n") if d.strip()]:
            root = Path(mon_path)
            if not root.exists():
                continue
            categories = list_category_dirs(root)
            self._category_map[str(root)] = categories
            found = scan_strm_files(root, skip=self.__is_excluded)
            if len(found) >= MAX_SCAN_FILES:
                logger.warn(f"STRM列表扫描达到上限 {MAX_SCAN_FILES}，结果可能不完整")
            for strm in found:
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
                    "category": match_category(series_root, root, categories),
                    "category_dir": str(root / match_category(series_root, root, categories))
                    if match_category(series_root, root, categories) else "",
                    "root": str(root),
                    "files": [],
                })
                try:
                    stat = strm.stat()
                    scraped = self.__nfo_exists(strm)
                    status, error_code, error_message = self.__file_state(str(strm), scraped)
                    entry["files"].append({
                        "path": str(strm),
                        "name": strm.name,
                        "size": stat.st_size,
                        "modify_time": stat.st_mtime,
                        "season": season_dir,
                        "scraped": scraped,
                        "status": status,
                        "error_code": error_code,
                        "error_message": error_message,
                    })
                except OSError as e:
                    logger.debug(f"读取文件信息失败，跳过 {strm}：{e}")

        result: List[Dict[str, Any]] = []
        for root_path, entry in groups.items():
            files = entry["files"]
            # 文件最后变更时间（mtime），供「文件更新」展示；真实刮削时间从状态表取
            max_mtime = max((f["modify_time"] for f in files), default=0)
            # 音乐分类下的条目既没有季目录也没有集号，只按结构判定会落进 movie 分支，
            # 界面因而渲染成「电影」徽标，与分类行「音乐」自相矛盾，故先按分类名拦下。
            is_music = entry["category"].strip().lower() in MUSIC_CATEGORIES
            # 剧集判定：存在季目录，或文件名带集号特征
            episode_files = [] if is_music else [
                f for f in files if f["season"] or self.__episode_no(f["name"])
            ]
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
                dir_scraped = (Path(root_path) / "tvshow.nfo").exists()
                status, error_code, error_message, last_scrape = self.__item_state(
                    root_path, dir_scraped, max_mtime
                )
                result.append({
                    "path": root_path,
                    "title": entry["title"],
                    "category": entry["category"],
                    "category_dir": entry["category_dir"],
                    "root": entry["root"],
                    "type": "tv",
                    "total_files": len(files),
                    "total_episodes": len(files),
                    "seasons": season_list,
                    "unscraped": sum(1 for f in files if not f["scraped"]),
                    "dir_scraped": dir_scraped,
                    "status": status,
                    "error_code": error_code,
                    "error_message": error_message,
                    "last_file_change": max_mtime,
                    "last_scrape": last_scrape,
                })
            else:
                versions = []
                for file_item in files:
                    status, error_code, error_message = self.__file_state(
                        file_item["path"], file_item["scraped"]
                    )
                    versions.append({
                        "path": file_item["path"],
                        "name": file_item["name"],
                        "size": file_item["size"],
                        "modify_time": file_item["modify_time"],
                        "scraped": file_item["scraped"],
                        "status": status,
                        "error_code": error_code,
                        "error_message": error_message,
                    })
                # 音乐目录没有 movie.nfo 这类约定产物，用「全部文件已刮削」代表整目录完成
                dir_scraped = (
                    all(v["scraped"] for v in versions) if is_music
                    else (Path(root_path) / "movie.nfo").exists()
                )
                status, error_code, error_message, last_scrape = self.__item_state(
                    root_path, dir_scraped, max_mtime
                )
                result.append({
                    "path": root_path,
                    "title": entry["title"],
                    "category": entry["category"],
                    "category_dir": entry["category_dir"],
                    "root": entry["root"],
                    "type": "music" if is_music else "movie",
                    "total_files": len(versions),
                    "total_episodes": 0,
                    "multi_version": len(versions) > 1,
                    "versions": versions,
                    "unscraped": sum(1 for v in versions if not v["scraped"]),
                    "dir_scraped": dir_scraped,
                    "status": status,
                    "error_code": error_code,
                    "error_message": error_message,
                    "last_file_change": max_mtime,
                    "last_scrape": last_scrape,
                })
        return sorted(result, key=lambda x: x["title"])

    def __collect_categories(self) -> List[Dict[str, Any]]:
        """
        汇总监控目录下的分类分组，供界面按分类浏览与按分类刷新。

        每个分类返回名称、绝对路径、媒体数与待刮削数；分类目录下若还有再一层的分类
        目录（如 ``<库根>/电视剧/日番/<剧名>/``），同样汇总为 ``children`` 二级分类，
        供界面按类型做二级筛选。不在任何分类内的媒体统一归到「未分类」分组
        （``path`` 为空），保证界面不会漏掉媒体。
        """
        groups: Dict[str, Dict[str, Any]] = {}
        order: List[str] = []
        for mon_path in [d.strip() for d in self._monitor_dirs.split("\n") if d.strip()]:
            root = Path(mon_path)
            if not root.exists():
                continue
            for name in list_category_dirs(root):
                key = str(root / name)
                if key in groups:
                    continue
                children: List[Dict[str, Any]] = []
                for child_name in list_category_dirs(Path(key)):
                    children.append({
                        "name": child_name,
                        "path": str(Path(key) / child_name),
                        "total": 0,
                        "unscraped": 0,
                        "movie": 0,
                        "tv": 0,
                        "music": 0,
                    })
                # 散装在分类目录下（没进任何二级分类）的媒体也留一个分组，路径回落为
                # 分类目录本身；「未分类」是兜底分组，没有媒体时会被剔除。
                children.append({
                    "name": "未分类",
                    "path": key,
                    "total": 0,
                    "unscraped": 0,
                    "movie": 0,
                    "tv": 0,
                    "music": 0,
                    "loose": True,
                })
                groups[key] = {
                    "name": name,
                    "path": key,
                    "root": str(root),
                    "total": 0,
                    "unscraped": 0,
                    "movie": 0,
                    "tv": 0,
                    "music": 0,
                    "children": children,
                }
                order.append(key)
            # 未分类分组固定放在末尾，只在该监控目录存在散装媒体时才出现
            loose = str(root)
            if loose not in groups:
                groups[loose] = {
                    "name": "未分类",
                    "path": "",
                    "root": str(root),
                    "total": 0,
                    "unscraped": 0,
                    "movie": 0,
                    "tv": 0,
                    "music": 0,
                    "children": [],
                }
                order.append(loose)

        for item in self.__list_items():
            bucket = groups.get(item.get("category_dir") or "") or groups.get(item.get("root", ""))
            if not bucket:
                continue
            # 一级分类与二级分类分别累加，二级分类为空（未分类兜底）时也要计进去，
            # 否则该分类的媒体数会大于其所有二级分类之和
            for target in (bucket, self.__match_child(bucket, item)):
                if target is None:
                    continue
                target["total"] += 1
                target["unscraped"] += 1 if item.get("unscraped") else 0
                target[item["type"]] = target.get(item["type"], 0) + 1

        # 没有散装媒体时把「未分类」占位分组去掉，避免界面上出现空分类；
        # 二级分类里的空目录保留（实时反映磁盘结构），只剔除空的「未分类」兜底分组
        result = [groups[key] for key in order if groups[key]["total"] or groups[key]["path"]]
        for group in result:
            group["children"] = [
                child for child in group["children"] if child["total"] or not child.get("loose")
            ]
        return sorted(result, key=lambda x: (x["name"] == "未分类", x["name"]))

    @classmethod
    def __match_child(
        cls, bucket: Dict[str, Any], item: Dict[str, Any]
    ) -> Optional[Dict[str, Any]]:
        """
        判断媒体落在分类分组的哪个二级分类里。

        路径相对分类目录只有一层（媒体直接躺在分类目录下）时不构成二级分类，
        归到「未分类」兜底分组；分组本身没有二级分类时返回 ``None``。
        """
        children: List[Dict[str, Any]] = bucket.get("children") or []
        if not children:
            return None
        loose = next((child for child in children if child.get("loose")), None)
        try:
            rel = Path(str(item.get("path", ""))).relative_to(str(bucket.get("path", "")))
        except (ValueError, OSError):
            return loose
        head = rel.parts[0] if len(rel.parts) > 1 else ""
        for child in children:
            if not child.get("loose") and child["name"] == head:
                return child
        return loose


    def __list_items(self, force: bool = False) -> List[Dict[str, Any]]:
        """
        带缓存的媒体清单查询。

        失效有三种来源：显式 ``force``（界面上的刷新）、缓存过期（``LIST_CACHE_TTL``）、
        以及 ``_list_dirty`` —— 刮削每完成一项就置位。早期版本只在整批任务结束时清缓存，
        事件触发的刮削完成后界面要等一分钟才看得到变化，就是缺了这个信号。
        """
        now = time.time()
        expired = now - self._list_cache.get("time", 0) >= LIST_CACHE_TTL
        if not force and not self._list_dirty and not expired:
            return self._list_cache["items"]
        items = self.__collect_items()
        self._list_cache = {"time": now, "items": items}
        self._list_dirty = False
        return items

    # ------------------------------------------------------------------
    # 刮削队列：所有触发源统一入队，由唯一 worker 串行消费
    # ------------------------------------------------------------------
    def __enqueue(
        self, items: List[Dict[str, Any]], overwrite: Optional[bool], source: str
    ) -> Dict[str, int]:
        """
        把目标并入队列，返回 ``{"queued": 新增条数, "deduped": 被判重丢弃的条数}``。

        :param items: 形如 ``{"key": …, "kind": "dir|file|scan", "target": …}``；
            ``kind=scan`` 的项额外带 ``scope`` / ``paths``，由 worker 展开成具体目录
        :param overwrite: ``None`` 表示跟随插件配置的「覆盖已有元数据」开关
        :param source: ``user``（界面或启动触发，优先执行）或 ``event``（文件监控触发）

        去重规则（同一时刻同一目标只留一份待办）：
        - ``key`` 相同：新条目 ``overwrite`` 更高则就地升级，否则丢弃；
        - 目标已被队内某个目录项覆盖：丢弃（目录级 ``recursive=True`` 本就递归整棵子树）。

        用户源插在事件源之前：用户正等着看结果，而事件还可能持续涌入。
        """
        added = deduped = 0
        resolved = self._overwrite if overwrite is None else bool(overwrite)
        with self._queue_lock:
            # 正在执行的项也算「在队内」，否则刚排上的目录会被重复入队
            active = self._queue + ([self._current] if self._current else [])
            for raw in items:
                kind = str(raw.get("kind") or "dir")
                target = str(raw.get("target") or "").strip()
                key = str(raw.get("key") or f"{kind}:{target}")
                if target and self.__covered_by_dir(active, kind, target):
                    deduped += 1
                    continue
                same = next((it for it in self._queue if it["key"] == key), None)
                if same is not None:
                    if resolved and not same["overwrite"]:
                        same["overwrite"] = True
                        added += 1
                    else:
                        deduped += 1
                    continue
                if self._current and self._current.get("key") == key:
                    # 同一目标正在执行：不必再排一份，等它自己跑完即可
                    deduped += 1
                    continue
                if kind == "dir" and target:
                    # 目录级会递归整棵子树，队内被它覆盖的文件项已无意义，先清掉
                    prefix = target.rstrip("/") + "/"
                    for stale in [
                        it for it in self._queue
                        if it["kind"] == "file" and str(it["target"]).startswith(prefix)
                    ]:
                        self._queue.remove(stale)
                entry: Dict[str, Any] = {
                    "key": key,
                    "kind": kind,
                    "target": target,
                    "overwrite": resolved,
                    "source": source,
                    "queued_at": time.time(),
                }
                for field in ("scope", "paths", "origin"):
                    if raw.get(field) is not None:
                        entry[field] = raw[field]
                if source == "user":
                    index = next(
                        (i for i, it in enumerate(self._queue) if it.get("source") != "user"),
                        len(self._queue),
                    )
                    self._queue.insert(index, entry)
                else:
                    self._queue.append(entry)
                added += 1
                active = self._queue + ([self._current] if self._current else [])
        if added:
            self.__ensure_worker()
        return {"queued": added, "deduped": deduped}

    @staticmethod
    def __covered_by_dir(active: List[Dict[str, Any]], kind: str, target: str) -> bool:
        """
        目标是否已被队列中某个目录项覆盖。

        目录级刮削 ``recursive=True`` 会递归整棵子树，所以一个目录项同时能吞掉它下面的
        目录项与文件项。同路径不算「被覆盖」—— 那是重复入队，交给 ``key`` 判重处理。
        """
        def norm(value: str) -> str:
            # 统一成正斜杠再比前缀：Windows 下目标是反斜杠路径，只比 "/" 会整片漏判
            return str(value).replace("\\", "/").rstrip("/")

        target = norm(target)
        for item in active:
            if not item or item.get("kind") != "dir":
                continue
            base = norm(item.get("target") or "")
            if not base or base == target:
                continue
            if target.startswith(base + "/"):
                return True
        return False

    def __scan_live(self, key: str) -> bool:
        """
        该扫描是否仍有「在队内待办」的部分（调用前须持有 ``_queue_lock``）。

        判定只看这一条扫描自己的产物：scan 项本身还在排队或正在展开（``key`` 相同），
        或者它展开出的目录项还有一个留在队列 / 正在执行（``origin`` 相同）。因此同目录下
        由文件事件排进来的无关项不会把它「续命」，跑完后再点一次也一定能提交。
        """
        for item in list(self._queue) + ([self._current] if self._current else []):
            if not item:
                continue
            if item.get("key") == key or item.get("origin") == key:
                return True
        return False

    def __enqueue_scan(
        self, scope: str, paths: List[str], overwrite: Optional[bool], source: str = "user"
    ) -> Dict[str, int]:
        """
        把一次扫描并入队列。

        扫描范围本身作为待展开项交给 worker 去枚举 —— 遍历上万文件是重 I/O，不能放在
        请求线程里做，否则接口会长时间不返回。

        同一范围的扫描在「上一次还没跑完」时只接受一次：``scan`` 项被 worker 取走展开后
        就离开队列了，仅靠 key 判重挡不住连点，所以这里额外靠 ``_scan_tokens`` 判活。
        """
        signature = "|".join(sorted(paths))
        key = f"scan:{scope}:{signature}"
        label = {
            "all": "全部监控目录",
            "category": f"{len(paths)} 个分类",
            "incremental": "增量扫描（只补新增）",
            "unscraped": "补漏扫描（只刮未刮）",
        }.get(scope, "扫描")
        with self._queue_lock:
            # 先淘汰已经不成立的令牌（其展开项全部跑完），否则界面会一直报「已在队列中」
            self._scan_tokens = {token for token in self._scan_tokens if self.__scan_live(token)}
            if key in self._scan_tokens:
                return {"queued": 0, "deduped": 1}
        result = self.__enqueue(
            [{
                "key": key,
                "kind": "scan",
                "target": label,
                "scope": scope,
                "paths": list(paths),
            }],
            overwrite=overwrite,
            source=source,
        )
        if result["queued"]:
            with self._queue_lock:
                self._scan_tokens.add(key)
        return result

    def __ensure_worker(self):
        """没有存活 worker 时拉起一个；判断在锁内，避免并发建出两个消费线程。"""
        with self._queue_lock:
            if self._worker_running or not self._queue:
                return
            token = object()
            self._worker_token = token
            self._worker_running = True
        ThreadHelper().submit(self.__worker_loop, token)

    def __cancel_match(self, item: Dict[str, Any], target: str, scope: str) -> bool:
        """
        判断队列项是否命中取消条件（调用前须持有 ``_queue_lock``）。

        ``scope`` 命中扫描项；``target`` 命中目标本身或位于其子树下的项（目录级取消
        应连带其子项一起取消）。两者都不传时匹配全部（等价 mode=all）。
        """
        if not target and not scope:
            return True
        if scope and str(item.get("scope") or "") == scope:
            return True
        tgt = str(item.get("target") or "")
        if not target:
            return False
        prefix = target.replace("\\", "/").rstrip("/") + "/"
        norm = tgt.replace("\\", "/").rstrip("/")
        return norm == target.replace("\\", "/").rstrip("/") or norm.startswith(prefix)

    def __should_skip(self, item: Dict[str, Any]) -> bool:
        """取消标记命中：返回 True 表示跳过该项（不执行、写 skipped）。"""
        if "*" in self._cancel_keys:
            return True
        key = str(item.get("key") or "")
        if key and key in self._cancel_keys:
            return True
        origin = str(item.get("origin") or "")
        if origin and origin in self._cancel_keys:
            return True
        return False

    def __worker_loop(self, token: object):
        """
        队列的唯一消费者：串行取出并执行，直到队列为空。

        「同一目录不会被并发刮削」正是由这里保证 —— 插件里只有这一个线程会调用
        ``__scrape_target``。单项异常一律吞掉并继续下一项，绝不让一个坏目标终止整条队列。

        ``token`` 是本代 worker 的身份令牌：旧 worker 正常耗尽、释放锁再到外层 finally
        重新拿锁之间，新 worker 可能已被拉起；只有 ``self._worker_token is token`` 时才
        允许旧 worker 清掉存活标记，否则会把新 worker 刚置的 ``True`` 踩回 ``False``，
        下次入队又拉起一个 worker，破坏「单消费者」不变式。
        """
        epoch = self._abort_epoch
        try:
            while True:
                with self._queue_lock:
                    if epoch != self._abort_epoch:
                        # 插件已重载，这一代 worker 立即退场，避免与新实例抢同一目录
                        logger.info("STRM刮削队列：插件已重载，消费者退出")
                        self._worker_running = False
                        return
                    if not self._queue:
                        # 队列自然耗尽：必须在同一锁块内清掉存活标记，否则 __ensure_worker
                        # 会看到残留 True 而拒绝拉起新 worker，新入队的项就此僵死无人消费。
                        self._worker_running = False
                        return
                    item = self._queue.pop(0)
                    if self.__should_skip(item):
                        # 命中取消标记：不执行，写 skipped，继续取下一项
                        kind = str(item.get("kind") or "dir")
                        if kind != "scan":
                            self.__write_state(
                                str(item.get("target") or ""), "skipped", "unknown", "已取消"
                            )
                        self._stats["canceled"] += 1
                        self._list_dirty = True
                        continue
                    self._current = item
                try:
                    self.__run_item(item)
                except Exception as e:  # noqa: BLE001
                    # 统计统一在 __run_item 内部累计，这里只记日志：若再计一次 failed，
                    # 会与 __run_item 已累计的失败形成双计。
                    logger.error(
                        f"STRM刮削队列项异常 {item.get('target')}：{str(e)} - {traceback.format_exc()}"
                    )
                finally:
                    # 每完成一项就置脏：界面下次读清单时自然看到最新刮削状态
                    self._list_dirty = True
                    with self._queue_lock:
                        # 仅当「当前项仍是我这一项」才清空：插件重载后同一实例可能已由
                        # 新 worker 接手 _current，无条件置 None 会踩掉新 worker 的状态。
                        if self._current is item:
                            self._current = None
        except Exception as e:  # noqa: BLE001
            logger.error(f"STRM刮削队列消费者异常：{str(e)} - {traceback.format_exc()}")
        finally:
            with self._queue_lock:
                if self._worker_token is token:
                    self._worker_running = False
                    self._worker_token = None

    def __run_item(self, item: Dict[str, Any]):
        """执行队列中的一项：scan 项展开成具体目录，其余直接刮削并累计统计。"""
        kind = str(item.get("kind") or "dir")
        if kind == "scan":
            targets = self.__collect_scan_targets(
                str(item.get("scope") or "all"), list(item.get("paths") or [])
            )
            if not targets:
                logger.info("STRM扫描：范围内没有待刮削的目录")
                return
            logger.info(f"STRM扫描展开：{len(targets)} 个目录入队")
            # origin 记录「这批目录是哪次扫描展开出来的」，供 __scan_live 判断该扫描是否
            # 还有未跑完的部分；不带这个标记的话，同目录下的文件事件项会让扫描令牌无法失效
            origin = str(item.get("key") or "")
            self.__enqueue(
                [{"key": f"dir:{t}", "kind": "dir", "target": str(t), "origin": origin}
                 for t in targets],
                overwrite=item.get("overwrite"),
                source=str(item.get("source") or "user"),
            )
            return
        ok, msg = self.__scrape_target(
            Path(str(item.get("target"))), kind, bool(item.get("overwrite"))
        )
        with self._queue_lock:
            self._stats["done" if ok else "failed"] += 1
            self._recent.append({
                "kind": kind,
                "target": item.get("target"),
                "source": item.get("source"),
                "success": bool(ok),
                "message": msg or "",
                "time": time.time(),
            })
            del self._recent[:-QUEUE_RECENT_LIMIT]

    # ------------------------------------------------------------------
    # 远程触发 / API
    # ------------------------------------------------------------------
    def get_api(self) -> List[Dict[str, Any]]:
        """返回插件 API 列表。

        供 Vue 详情页调用的接口统一声明 ``auth: "bear"``；``/strm_scan`` 与
        ``/strm_rescrape`` 保持默认 apikey 认证，供外部脚本或旧调用方使用。
        """
        return [
            {
                "path": "/strm_scan",
                "endpoint": self.api_scan,
                "methods": ["GET"],
                "summary": "STRM全量刮削",
                "description": "把一次全量扫描并入队列；scope=category 时只扫 paths 指定的分类目录",
            },
            {
                "path": "/strm_rescrape",
                "endpoint": self.api_rescrape,
                "methods": ["GET"],
                "summary": "重新刮削单个合集",
                "description": "把指定目录以覆盖模式并入刮削队列",
            },
            {
                # 与 /strm_scan 同一实现，但走 bear 认证，供 Vue 详情页调用
                "path": "/scan",
                "endpoint": self.api_scan,
                "methods": ["GET"],
                "auth": "bear",
                "summary": "触发全量扫描",
                "description": "把一次扫描并入队列；overwrite 不传时跟随配置开关，scope=category 时只扫 paths 指定的分类目录",
            },
            {
                "path": "/categories",
                "endpoint": self.api_categories,
                "methods": ["GET"],
                "auth": "bear",
                "summary": "STRM分类分组",
                "description": "返回监控目录下按一级子目录识别的分类分组（国漫/日番/国产剧等）及统计",
            },
            {
                "path": "/records",
                "endpoint": self.api_records,
                "methods": ["GET"],
                "auth": "bear",
                "summary": "STRM刮削记录",
                "description": "返回最近若干条刮削记录（时间、目标、分类、结果）",
            },
            {
                "path": "/records/clear",
                "endpoint": self.api_records_clear,
                "methods": ["GET"],
                "auth": "bear",
                "summary": "清空刮削记录",
                "description": "删除插件数据目录下的刮削记录文件",
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
                "summary": "STRM刮削入队",
                "description": "把目标并入刮削队列，target=dir 为目录级刮削，target=file 为单集/单版本刮削",
            },
            {
                "path": "/queue",
                "endpoint": self.api_queue,
                "methods": ["GET"],
                "auth": "bear",
                "summary": "STRM刮削队列状态",
                "description": "返回正在执行的目标、排队数量、累计统计与最近完成项",
            },
            {
                "path": "/queue/cancel",
                "endpoint": self.api_queue_cancel,
                "methods": ["POST"],
                "auth": "bear",
                "summary": "取消队列任务",
                "description": "body：{mode: all|one, target: 目录路径, scope: 扫描范围}；mode=all 清空队列，mode=one 取消匹配项；正在执行项不强行中断",
            },
            {
                "path": "/retry_failed",
                "endpoint": self.api_retry_failed,
                "methods": ["POST"],
                "auth": "bear",
                "summary": "重试失败项",
                "description": "body：{category?: 分类路径}；把状态为 failed 的目录批量入队，返回 {queued, deduped}",
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
        with self._poster_lock:
            cached = self._poster_cache.get(cache_key)
        if cached is not None:
            return cached or None
        url = ""
        try:
            from app.chain.tmdb import TmdbChain
            from app.schemas.types import MediaType
            from app.sdk.config import settings

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
        # 缓存写入前按插入顺序淘汰最早一条：TMDB 地址数量有限，但仍需兜底上限。
        # 读-改-写须持锁，否则前端海报墙并发请求会并发修改字典，触发
        # RuntimeError（dictionary changed size during iteration）导致崩溃。
        with self._poster_lock:
            if cache_key not in self._poster_cache and len(self._poster_cache) >= POSTER_CACHE_LIMIT:
                self._poster_cache.pop(next(iter(self._poster_cache)), None)
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
        # 海报降级选型：走「前端 onerror 降级」而非后端返回 1x1 占位图。理由——前端已有
        # posterFailed 标记 + 按标题哈希稳定的渐变占位图，比一张 1x1 空白更能传达「这是
        # 某部媒体」；且 <img> 直接请求此接口无法带 Bearer，返回 404 是最小面且语义清晰。
        raise HTTPException(status_code=404, detail="poster not found")

    def api_scan(
        self, overwrite: Optional[bool] = None, scope: str = "all", paths: str = ""
    ) -> schemas.Response:
        """
        把一次扫描并入刮削队列，立即返回以免阻塞请求。

        这里只校验范围、不枚举目录：真正遍历磁盘发生在队列 worker 里，否则上万文件的
        媒体库会让这个接口长时间不响应。

        :param overwrite: 是否覆盖已有 NFO 与图片；不传时跟随插件配置的「覆盖已有元数据」开关
        :param scope: 四种语义：
            ``all``=全部监控目录；``category``=只扫 ``paths`` 指定的分类目录；
            ``incremental``=只扫状态表不存在或刮削时间早于目录 mtime 的目录（补新增）；
            ``unscraped``=只扫状态不为 ``scraped`` 的目录（补漏）。
        :param paths: ``scope=category`` 时用逗号分隔的分类目录绝对路径
        """
        targets = [p.strip() for p in (paths or "").split(",") if p.strip()]
        if scope == "category":
            if not targets:
                return schemas.Response(success=False, message="未指定要刷新的分类目录")
            illegal = [p for p in targets if not self.__is_allowed(p)]
            if illegal:
                return schemas.Response(success=False, message="存在不在监控目录范围内的路径")
        if scope not in ("all", "category", "incremental", "unscraped"):
            return schemas.Response(success=False, message=f"不支持的 scope：{scope}")
        result = self.__enqueue_scan(scope=scope, paths=targets, overwrite=overwrite)
        if not result["queued"]:
            return schemas.Response(
                success=True, message="该扫描已在队列中，或已被范围更大的扫描覆盖", data=result
            )
        if scope == "category":
            return schemas.Response(
                success=True,
                message=f"已按分类加入扫描队列（{len(targets)} 个分类）",
                data=result,
            )
        scope_label = {
            "incremental": "增量扫描",
            "unscraped": "补漏扫描",
        }.get(scope, "全量扫描")
        return schemas.Response(success=True, message=f"{scope_label}已加入队列", data=result)

    def api_categories(self) -> Dict[str, Any]:
        """返回监控目录下的分类分组列表（含媒体数与待刮削数）。"""
        return self.__envelope(self.__collect_categories())

    def api_records(
        self,
        limit: int = 100,
        success: Optional[str] = None,
        type: str = "",
        category: str = "",
        since: str = "",
        until: str = "",
    ) -> Dict[str, Any]:
        """
        返回刮削记录（默认最多 100 条，倒序），支持按结果/类型/分类/时间范围过滤。

        记录是历史事实，不因目标目录被删除而清理；这里为每条补 ``exists`` 标记，
        让界面能区分「已刮削」与「目标目录已不存在」，而不是删完目录后记录看起来还在。

        :param success: ``true``/``false`` 只取成功/失败记录，空串不过滤
        :param type: ``dir``/``file`` 只取对应刮削类型，空串不过滤
        :param category: 只取该分类下的记录，空串不过滤
        :param since/until: ``YYYY-MM-DD`` 时间范围（闭区间），空串不过滤
        """
        records = self.__load_records()
        if success in ("true", "false"):
            want = success == "true"
            records = [r for r in records if bool(r.get("success")) == want]
        if type:
            records = [r for r in records if str(r.get("type") or "") == type]
        if category:
            records = [r for r in records if str(r.get("category") or "") == category]
        if since or until:
            def in_range(rec_time: str) -> bool:
                day = str(rec_time or "")[:10]
                if since and day < since:
                    return False
                if until and day > until:
                    return False
                return True
            records = [r for r in records if in_range(str(r.get("time") or ""))]
        try:
            size = max(1, min(int(limit), RECORD_LIMIT))
        except (TypeError, ValueError):
            size = 100
        page = list(reversed(records[-size:]))
        for record in page:
            target = str(record.get("target") or "")
            try:
                record["exists"] = bool(target) and Path(target).exists()
            except OSError:
                record["exists"] = False
        return self.__envelope(page)

    def api_records_clear(self) -> Dict[str, Any]:
        """清空已落盘的刮削记录。"""
        try:
            path = self.__record_path()
            if path.exists():
                path.unlink()
            return self.__envelope(None, True, "刮削记录已清空")
        except Exception as e:
            return self.__envelope(None, False, f"清空刮削记录失败：{e}")

    def api_rescrape(self, path: str, apikey: str = "") -> schemas.Response:
        """把单个合集目录以覆盖模式并入刮削队列（立即返回）。"""
        from app.sdk.config import settings
        if apikey != settings.API_TOKEN:
            return schemas.Response(success=False, message="API密钥错误")
        # 统一 resolve 后再入队：保证 key 与 __is_allowed 的口径一致，避免符号链接/
        # 相对路径导致同一目录被当成两个不同目标重复入队或漏判越界。
        try:
            target = Path(path).resolve()
        except OSError:
            return schemas.Response(success=False, message=f"目录无法解析：{path}")
        if not target.exists():
            return schemas.Response(success=False, message=f"目录不存在：{path}")
        # 与其它写接口口径一致：只允许刮削监控目录范围内的路径
        if not self.__is_allowed(str(target)):
            return schemas.Response(success=False, message="目录不在监控目录范围内")
        # 覆盖模式随条目入队，不再临时改写实例属性：那条路径会污染其它并发刮削的语义
        result = self.__enqueue(
            [{"key": f"dir:{target}", "kind": "dir", "target": str(target)}],
            overwrite=True,
            source="user",
        )
        return schemas.Response(
            success=True, message=f"已加入重新刮削队列：{path}", data=result
        )

    # ------------------------------------------------------------------
    # 前端联邦界面专用接口（Vue 详情页 Page 与配置页 Config 调用）
    # ------------------------------------------------------------------
    @staticmethod
    def __envelope(data: Any = None, success: bool = True, message: str = "") -> Dict[str, Any]:
        """
        构造与宿主普通 REST 一致的响应信封
        """
        return {"success": success, "message": message, "data": data}

    def api_overview(self, refresh: bool = False) -> Dict[str, Any]:
        """
        返回媒体总数、类型分布与刮削状态统计

        :param refresh: 为 True 时跳过硬缓存重扫监控目录，用于「刚删掉目录但界面还在」
            这类显式刷新场景
        """
        items = self.__list_items(force=refresh)
        with self._queue_lock:
            busy = self._worker_running or bool(self._queue)
            queued = len(self._queue)
        # 条目级状态分布：scraped / failed / skipped / pending 四计数，界面徽标直接引用
        status_dist = {"scraped": 0, "failed": 0, "skipped": 0, "pending": 0}
        for item in items:
            key = item.get("status") or STATE_PENDING
            status_dist[key] = status_dist.get(key, 0) + 1
        overview = {
            "total": len(items),
            "movie": sum(1 for i in items if i["type"] == "movie"),
            "tv": sum(1 for i in items if i["type"] == "tv"),
            "music": sum(1 for i in items if i["type"] == "music"),
            "multi_version_movie": sum(1 for i in items if i.get("multi_version")),
            "dir_scraped": sum(1 for i in items if i["dir_scraped"]),
            "unscraped_items": sum(1 for i in items if i["unscraped"] > 0),
            "total_files": sum(i["total_files"] for i in items),
            "status": status_dist,
            "monitor_dirs": [d.strip() for d in self._monitor_dirs.split("\n") if d.strip()],
            "monitoring": bool(self._observer),
            # busy/queued 是 /queue 的轻量摘要，供不想单独拉队列的调用方使用
            "busy": busy,
            "queued": queued,
            "overwrite": self._overwrite,
            "categories": self.__collect_categories(),
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
        把目标并入刮削队列；target=dir 走目录级，target=file 走单集/单版本。

        目录级 ``recursive=True`` 会递归整棵子树，因此入队的目录项会自动吞掉队内位于
        其下的文件项，重复入队只会计入 ``deduped`` 而不产生额外刮削。
        """
        body = payload or {}
        paths = [str(p) for p in (body.get("paths") or []) if str(p).strip()]
        kind = "file" if body.get("target") == "file" else "dir"
        raw_overwrite = body.get("overwrite")
        overwrite = None if raw_overwrite is None else bool(raw_overwrite)
        if not paths:
            return self.__envelope(None, False, "缺少 paths 参数")
        illegal = [p for p in paths if not self.__is_allowed(p)]
        if illegal:
            return self.__envelope({"illegal": illegal}, False, "存在不在监控目录范围内的路径")
        result = self.__enqueue(
            [{"key": f"{kind}:{p}", "kind": kind, "target": p} for p in paths],
            overwrite=overwrite,
            source="user",
        )
        return self.__envelope(result)

    def api_queue(self) -> Dict[str, Any]:
        """
        返回刮削队列快照：正在执行、排队中、累计统计与最近完成项。

        界面上「正在干什么」只需要看这一个接口，不再区分「扫描」与「任务」两种形态。
        """
        with self._queue_lock:
            running = dict(self._current) if self._current else None
            queued = [dict(it) for it in self._queue]
            stats = dict(self._stats)
            recent = [dict(it) for it in reversed(self._recent[-QUEUE_RECENT_LIMIT:])]
            busy = self._worker_running or bool(queued)
        return self.__envelope({
            "running": running,
            "queued": queued[:QUEUE_SNAPSHOT_LIMIT],
            "queued_total": len(queued),
            "stats": stats,
            "recent": recent,
            "busy": busy,
        })

    def api_queue_cancel(self, payload: Dict[str, Any] = Body(default={})) -> Dict[str, Any]:
        """
        取消队列中的待刮削项。

        body：``{"mode": "all"|"one", "target": "目录路径", "scope": "all|category|…"}``。
        ``mode=all`` 清空整个队列；``mode=one`` 只取消匹配 ``target``（含其子树）或
        ``scope`` 的项。正在执行的项不强行中断，只停止取后续项。

        被取消的项加入 ``_cancel_keys``：若某个扫描项已被 worker 取走、正在展开，
        它后续展开出的目录项会在取项时命中取消标记而跳过，不会继续刮削。
        """
        body = payload or {}
        mode = str(body.get("mode") or "one")
        target = str(body.get("target") or "")
        scope = str(body.get("scope") or "")
        with self._queue_lock:
            if mode == "all":
                canceled = len(self._queue)
                self._queue = []
                self._cancel_keys.add("*")
            else:
                keep: List[Dict[str, Any]] = []
                canceled = 0
                for item in self._queue:
                    if self.__cancel_match(item, target, scope):
                        key = str(item.get("key") or "")
                        if key:
                            self._cancel_keys.add(key)
                        canceled += 1
                    else:
                        keep.append(item)
                self._queue = keep
            self._stats["canceled"] += canceled
        return self.__envelope({"canceled": canceled})

    def api_retry_failed(self, payload: Dict[str, Any] = Body(default={})) -> Dict[str, Any]:
        """
        批量重试失败项：读状态表把 ``status=failed`` 的目录批量入队，``source=user``。

        body：``{"category": "分类路径", "scope": "all|category"}``。``category`` 提供时
        只重试该分类目录下的失败项；不提供时重试全部失败项。返回 ``{"queued", "deduped"}``。
        """
        body = payload or {}
        category = str(body.get("category") or "")
        self.__ensure_state_loaded()
        with self._state_lock:
            failed = [
                (path, entry)
                for path, entry in self._state_cache.items()
                if str(entry.get("status") or "") == "failed"
            ]
        items: List[Dict[str, Any]] = []
        for path, _entry in failed:
            if not path:
                continue
            if category and not (
                path == category.rstrip("/")
                or path.replace("\\", "/").startswith(category.replace("\\", "/").rstrip("/") + "/")
            ):
                continue
            if not self.__is_allowed(path):
                continue
            items.append({"key": f"dir:{path}", "kind": "dir", "target": path})
        if not items:
            return self.__envelope({"queued": 0, "deduped": 0}, True, "没有可重试的失败项")
        result = self.__enqueue(items, overwrite=None, source="user")
        return self.__envelope(result)

    # ------------------------------------------------------------------
    # 界面
    # ------------------------------------------------------------------
    def get_service(self) -> Optional[List[Dict[str, Any]]]:
        """
        注册定时扫描服务：配置了 ``cron_expression`` 时，到点把一次全量扫描并入队列。

        空表达式返回 None 关闭；用宿主调度器（``get_service`` 契约）而非自建线程，
        定时器随插件生命周期被宿主统一管理，应用关闭时也会被一并收敛。
        """
        cron = (self._cron_expression or "").strip()
        if not cron:
            return None
        return [{
            "id": "strmscraper_scheduled_scan",
            "name": "STRM定时扫描",
            "trigger": cron,
            "func": self.__scheduled_scan,
            "kwargs": {},
        }]

    def __scheduled_scan(self):
        """定时扫描回调：把一次全量补漏扫描并入队列，覆盖策略跟随插件配置。"""
        if not self._enabled:
            return
        result = self.__enqueue_scan(scope="all", paths=[], overwrite=None, source="user")
        logger.info(f"STRM定时扫描：全量扫描已入队（新增 {result['queued']} 项）")

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
        不注册主界面侧栏入口。

        海报墙、分类筛选、单集/版本刮削与刮削记录已全部收敛到插件中心的详情页
        （远程 ``Page`` 组件，经 ``layout`` 事件声明所需宽度），侧栏全页不再有独立价值。
        保留方法并恒返回空列表，是为了显式声明「本插件不注册侧栏入口」。
        """
        return []

    def get_form(self) -> Tuple[List[dict], Dict[str, Any]]:
        """返回插件配置表单与默认配置。

        Vue 模式下表单由远程 ``Config`` 组件渲染，这里的 schema 只作为非 Vue
        渲染时的兜底，默认值则始终用于初始化。
        """
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
                                            "model": "record_enabled",
                                            "label": "记录刮削历史",
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
                                "props": {"cols": 12},
                                "content": [
                                    {
                                        "component": "VAlert",
                                        "props": {
                                            "type": "info",
                                            "variant": "tonal",
                                            "text": "全量扫描会遍历监控目录下所有 .strm；刮削是否覆盖已有 NFO 与图片"
                                                    "由「覆盖已有元数据」决定。电视剧分类分组按监控目录下的一级"
                                                    "子目录自动识别（如国漫/日番/国产剧），可按分类单独刷新。",
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
                                        "component": "VTextfield",
                                        "props": {
                                            "model": "cron_expression",
                                            "label": "定时扫描（cron 表达式）",
                                            "placeholder": "留空关闭，例如 0 3 * * * 表示每天 03:00 全量补漏扫描一次",
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
            "record_enabled": True,
            "cron_expression": "",
        }

    def get_page(self) -> Optional[List[dict]]:
        """
        返回插件详情页面

        Vue 模式下（已构建联邦产物）详情页由远程 ``Page`` 组件渲染，返回空列表；
        未构建时给出最简运行状态提示。海报墙、分类筛选、单集/版本刮削与刮削记录
        全部由远程组件承载，后端不再自行拼装界面元素。
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
                            f"媒体清单、分类筛选与单集/版本刮削位于插件详情页（需已构建 Vue 联邦产物），"
                            f"或调用 /strm_scan 触发全量扫描。",
                },
            }
        ]

    def stop_service(self):
        """
        停止目录监控，并让刮削队列的消费者在下一个检查点退出。

        ``_abort_epoch`` 递增后，上一代 worker 会在取下一项前发现自己已过期并主动退出 ——
        插件重载时不会出现两个消费者同时刮削同一目录。
        """
        self._abort_epoch += 1
        with self._queue_lock:
            self._queue = []
            self._worker_running = False
            self._worker_token = None
            self._current = None
            self._scan_tokens = set()
            self._cancel_keys = set()
        for observer in self._observer:
            try:
                observer.stop()
                observer.join(timeout=5)
            except Exception as e:
                logger.error(f"停止STRM监控失败：{str(e)}")
        self._observer = []
