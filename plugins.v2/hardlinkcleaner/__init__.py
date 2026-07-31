import os
import re
import shutil
import threading
import time
import traceback
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer
from watchdog.observers.polling import PollingObserver

from app import schemas
from app.log import logger
from app.plugins import _PluginBase


# --------------------------------------------------------------------------
# 核心设计（v1.1.0 重写）：用 inode 标识硬链接，而非路径镜像
#
# 旧版（v1.0.0）假设「硬链接目录是源目录的结构镜像，可用相对路径互推」。
# 这个前提在 MoviePilot 下是错的：硬链接库是主程序按刮削规则【重新组织】的
# （/电影/动画电影/片名 (年份) [tmdbid]/...），与源目录结构完全不同。
# 用相对路径反推源文件 → 永远找不到 → 整个库被判成孤儿 → 被清空（实测误删 1 万+ 文件）。
#
# 硬链接的本质是「同一 inode 的多个目录项」。因此正确的映射依据是 inode：
#   - 源文件 S 与硬链接库文件 L 共享同一个 st_ino。
#   - S 被删除后，L 仍在，但其 inode 已不在「活源」集合里 → 这是真正的孤儿。
#
# 安全护栏（防误删，缺一不可）：
#   1) 源目录存在性：孤儿扫描前必须确认所有源根目录存在且可读，否则中止。
#   2) 源非空：扫描到的活源 inode 数为 0（源被卸载/清空）→ 中止，不信任。
#   3) 爆破半径上限：单次拟删除文件数超过「最大删除数」→ 中止（本次事故 10839 会被拦下）。
#   4) 仅清理「已知源 inode 且已失活」的库文件：从不删除从未作为源出现过的库文件
#      （即普通拷贝/非硬链接文件不受波及）。
#   5) 所有删除前校验目标确实位于硬链接目录内。
# --------------------------------------------------------------------------


class _SourceHandler(FileSystemEventHandler):
    """
    watchdog 事件处理器：仅监控【源目录】（单向）。
    - 创建/修改：记录源文件路径→inode，用于删除时定位硬链接孪生文件。
    - 删除/移出：触发联动清理。
    绝不监控硬链接目录。
    """

    def __init__(self, plugin: Any, **kwargs):
        super().__init__(**kwargs)
        self._plugin = plugin

    def on_created(self, event):
        if not event.is_directory:
            self._plugin._track_source(event.src_path)

    def on_modified(self, event):
        if not event.is_directory:
            self._plugin._track_source(event.src_path)

    def on_deleted(self, event):
        # 先把已记录的 inode 保留（前面创建时已记录）；再触发清理
        self._plugin.event_handler(event_path=event.src_path, is_dir=event.is_directory)

    def on_moved(self, event):
        dest = getattr(event, "dest_path", None)
        if dest and self._plugin.is_in_source(dest):
            # 源内重命名：更新记录，不当作删除
            if not event.is_directory:
                self._plugin._track_source(dest)
            return
        # 移出源目录（如进回收站/其他盘）→ 按删除处理
        self._plugin.event_handler(event_path=event.src_path, is_dir=event.is_directory)


class HardLinkCleaner(_PluginBase):
    # 插件名称
    plugin_name = "硬链接联动清理"
    # 插件描述
    plugin_desc = "单向监控源目录，源文件/文件夹被删除或移出时，按 inode 定位并清理硬链接目录中对应的硬链接及其刮削产物，并级联清理空目录。"
    # 插件图标
    plugin_icon = "hardlinkcleaner.png"
    # 插件版本
    plugin_version = "1.1.0"
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
    _pending: Dict[str, Dict[str, Any]] = {}      # 源路径 -> {inode, due}，去抖队列
    _indexer: Optional[threading.Thread] = None
    _enabled = False
    _mode = "compatibility"          # compatibility=轮询(网络盘) / fast=inotify(本地盘)
    _source_dirs = ""                # 源目录，每行一个
    _hardlink_dir = ""               # 硬链接目录（不监控）
    _delete_delay = 2                # 删除去抖秒数
    _clean_empty = True              # 级联清理空目录
    _onlyonce = False                # 启动后执行一次孤儿清理扫描
    _dryrun = True                   # 试运行：只记录不删除（默认开，防误删）
    _delete_limit = 500             # 孤儿扫描单次最大删除文件数（爆破半径护栏）
    _source_roots: List[Path] = []
    _hardlink_root: Optional[Path] = None

    # inode 索引（核心）
    _src_by_path: Dict[str, int] = {}       # 源路径(str) -> inode
    _src_by_inode: Dict[int, Set[str]] = {}  # inode -> {源路径}
    _lib_by_inode: Dict[int, List[str]] = {}  # inode -> [硬链接库路径]
    _known_src: Set[int] = set()            # 曾经作为源出现过的 inode（播种 + 增量更新）
    _index_ready = False

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
        self._src_by_path = {}
        self._src_by_inode = {}
        self._lib_by_inode = {}
        self._known_src = set()
        self._pending = {}
        self._index_ready = False
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
            try:
                self._delete_limit = int(config.get("delete_limit") or 500)
            except (TypeError, ValueError):
                self._delete_limit = 500
            self._clean_empty = config.get("clean_empty", True)
            self._onlyonce = config.get("onlyonce") or False
            self._dryrun = config.get("dryrun", True)

        # 解析源根目录 / 硬链接根
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

        # 启动后台索引构建（源 + 库 inode 索引），完成后 _index_ready=True
        self._indexer = threading.Thread(target=self.__build_indices, daemon=True)
        self._indexer.start()

        # 仅监控【源目录】
        for src in self._source_roots:
            try:
                if self._mode == "compatibility":
                    observer = PollingObserver(timeout=10)
                else:
                    observer = Observer(timeout=10)
                self._observer.append(observer)
                observer.schedule(_SourceHandler(self), path=str(src), recursive=True)
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
            logger.info("硬链接联动清理：5秒后执行一次孤儿扫描")
            threading.Timer(5.0, self.orphan_scan).start()
            self._onlyonce = False
            self.__save_config()

    def __save_config(self):
        self.update_config({
            "enabled": self._enabled,
            "mode": self._mode,
            "source_dirs": self._source_dirs,
            "hardlink_dir": self._hardlink_dir,
            "delete_delay": self._delete_delay,
            "delete_limit": self._delete_limit,
            "clean_empty": self._clean_empty,
            "onlyonce": False,
            "dryrun": self._dryrun,
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
    # inode 索引
    # ==================================================================
    def _track_source(self, path: str):
        """记录一个源文件的 inode（创建/修改时调用）。"""
        try:
            st = os.stat(path)
        except Exception:
            return
        if not os.path.isfile(path):
            return
        ino = st.st_ino
        with self._lock:
            self._src_by_path[path] = ino
            self._src_by_inode.setdefault(ino, set()).add(path)
            self._known_src.add(ino)

    def __build_indices(self):
        """后台构建 源 inode 索引 与 硬链接库 inode 索引。"""
        try:
            with self._lock:
                src_by_path, src_by_inode, known = {}, {}, set()
                lib_by_inode: Dict[int, List[str]] = {}
            # 扫描源
            for root in self._source_roots:
                if not root.exists():
                    continue
                for dirpath, _dirs, files in os.walk(root):
                    for fn in files:
                        fp = os.path.join(dirpath, fn)
                        try:
                            ino = os.stat(fp).st_ino
                        except Exception:
                            continue
                        with self._lock:
                            src_by_path[fp] = ino
                            src_by_inode.setdefault(ino, set()).add(fp)
                            known.add(ino)
            # 扫描硬链接库
            if self._hardlink_root and self._hardlink_root.exists():
                for dirpath, _dirs, files in os.walk(self._hardlink_root):
                    for fn in files:
                        fp = os.path.join(dirpath, fn)
                        try:
                            ino = os.stat(fp).st_ino
                        except Exception:
                            continue
                        with self._lock:
                            lib_by_inode.setdefault(ino, []).append(fp)
            with self._lock:
                self._src_by_path = src_by_path
                self._src_by_inode = src_by_inode
                self._known_src = known
                self._lib_by_inode = lib_by_inode
                self._index_ready = True
            logger.info(
                f"硬链接联动清理索引构建完成：源文件 {len(src_by_path)} 个，"
                f"库文件 {sum(len(v) for v in lib_by_inode.values())} 个，"
                f"已知源 inode {len(known)} 个"
            )
        except Exception as e:
            logger.error(f"索引构建失败：{str(e)} - {traceback.format_exc()}")

    # ==================================================================
    # 路径工具
    # ==================================================================
    @staticmethod
    def __is_relative_to(path: Path, base: Path) -> bool:
        try:
            return path.is_relative_to(base)
        except Exception:
            return False

    def is_in_source(self, path: str) -> bool:
        p = Path(path)
        for root in self._source_roots:
            if p == root or self.__is_relative_to(p, root):
                return True
        return False

    def __under_hardlink(self, path: Path) -> bool:
        if not self._hardlink_root:
            return False
        return path == self._hardlink_root or self.__is_relative_to(path, self._hardlink_root)

    # ==================================================================
    # 事件处理（去抖）
    # ==================================================================
    def event_handler(self, event_path: str, is_dir: bool):
        """
        watchdog 回调：源目录中发生删除/移出时，按 inode 定位硬链接孪生文件并排入去抖队列。
        """
        try:
            ep = Path(event_path)
            # 安全护栏：绝不处理硬链接目录自身产生的事件（虽本就不监控）
            if self._hardlink_root and self.__is_relative_to(ep, self._hardlink_root):
                return
            with self._lock:
                ino = self._src_by_path.get(str(ep))
            if ino is None:
                # 未在索引中（如插件启动前已存在的源被删）：用文件名兜底定位
                self.__schedule_by_basename(ep, is_dir)
                return
            self.__schedule(str(ep), ino)
        except Exception as e:
            logger.error(f"硬链接清理事件处理出错：{str(e)} - {traceback.format_exc()}")

    def __schedule(self, source_path: str, inode: int):
        with self._lock:
            self._pending[source_path] = {"inode": inode, "due": time.time() + self._delete_delay}

    def __schedule_by_basename(self, ep: Path, is_dir: bool):
        # 兜底：按文件名在硬链接库中查找（仅当索引未覆盖该源时）
        if is_dir:
            return  # 目录级兜底风险高，跳过（可由孤儿扫描处理）
        self.__schedule(str(ep), -1)  # inode=-1 标记为「按文件名兜底」

    def __worker(self):
        """后台线程：到点后执行删除，避免删除风暴中反复 IO。"""
        while not self._event.is_set():
            now = time.time()
            due: List[Tuple[str, int]] = []
            with self._lock:
                for k, v in list(self._pending.items()):
                    if v["due"] <= now:
                        due.append((k, v["inode"]))
                        del self._pending[k]
            for k, ino in due:
                try:
                    self.__process_deletion(k, ino)
                except Exception as e:
                    logger.error(f"硬链接清理失败 {k}：{str(e)} - {traceback.format_exc()}")
            self._event.wait(0.5)

    # ==================================================================
    # 删除核心
    # ==================================================================
    def __process_deletion(self, source_path: str, inode: int):
        """
        依据 inode（或文件名兜底）定位硬链接孪生文件并删除。
        inode == -1 表示走文件名兜底逻辑。
        """
        ep = Path(source_path)
        twins: List[str] = []
        if inode and inode != -1:
            with self._lock:
                twins = list(self._lib_by_inode.get(inode, []))
        if not twins:
            # 兜底：在硬链接库中按 basename 查找唯一匹配
            twins = self.__find_by_basename(ep)
        if not twins:
            logger.info(f"未找到源对应的硬链接，跳过：{ep}")
            return
        for t in twins:
            self.__delete_link_file(Path(t))
        # 清理索引
        with self._lock:
            if inode and inode != -1:
                self._src_by_path.pop(source_path, None)
                s = self._src_by_inode.get(inode)
                if s:
                    s.discard(source_path)
                # 孪生已从库删除，更新 lib 索引
                self._lib_by_inode[inode] = [
                    p for p in self._lib_by_inode.get(inode, []) if os.path.exists(p)
                ]

    def __find_by_basename(self, ep: Path) -> List[str]:
        """在硬链接库中按文件名查找唯一匹配（兜底用）。多匹配则跳过以防误删。"""
        if not self._hardlink_root or not self._hardlink_root.exists():
            return []
        name = ep.name
        hits: List[str] = []
        for dirpath, _dirs, files in os.walk(self._hardlink_root):
            for fn in files:
                if fn == name:
                    fp = os.path.join(dirpath, fn)
                    # 仅当它当前不是活源（避免删到仍被源占用的文件）
                    try:
                        ino = os.stat(fp).st_ino
                    except Exception:
                        continue
                    with self._lock:
                        if ino in self._src_by_inode:
                            continue
                    hits.append(fp)
        if len(hits) == 1:
            logger.info(f"按文件名兜底定位硬链接：{ep.name} -> {hits[0]}")
            return hits
        if len(hits) > 1:
            logger.warn(f"按文件名定位到多个候选，为避免误删已跳过：{ep.name} -> {hits}")
        return []

    def __episode_artifacts(self, link_file: Path) -> List[Path]:
        """单文件硬链接 + 其同 stem 刮削产物。不含剧集/剧级文件（stem 不同）。"""
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
        if self._dryrun:
            logger.info(f"[试运行] 将删除（未实际执行）：{link_file} 及其同 stem 刮削产物")
            return
        for f in self.__episode_artifacts(link_file):
            if f.exists():
                try:
                    f.unlink()
                    logger.info(f"删除硬链接刮削产物：{f}")
                except Exception as e:
                    logger.error(f"删除失败 {f}：{str(e)}")
        self.__clean_empty_parents(link_file.parent)

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
                break
            logger.info(f"删除空目录：{cur}")
            try:
                cur.rmdir()
            except Exception as e:
                logger.error(f"删除空目录失败 {cur}：{str(e)}")
                break
            cur = cur.parent

    # ==================================================================
    # 孤儿清理扫描（inode 基 + 多重护栏）
    # 仅清理「曾经是源、现在源已失活」的硬链接库文件，绝不波及普通库文件。
    # ==================================================================
    def orphan_scan(self):
        if not self._hardlink_root or not self._hardlink_root.exists():
            logger.warn("硬链接目录不存在，跳过孤儿清理")
            return

        live_roots = [r for r in self._source_roots if r.exists()]
        if len(live_roots) != len(self._source_roots):
            logger.error("孤儿清理中止：存在源目录不可访问（可能被卸载），为防误删已停止")
            return

        # 当前活源 inode 集合
        cur_src: Set[int] = set()
        for root in live_roots:
            for dirpath, _dirs, files in os.walk(root):
                for fn in files:
                    fp = os.path.join(dirpath, fn)
                    try:
                        cur_src.add(os.stat(fp).st_ino)
                    except Exception:
                        continue
        if not cur_src:
            logger.error("孤儿清理中止：源目录为空/未读取到任何文件（可能已卸载），为防误删已停止")
            return

        # 当前硬链接库 inode 索引
        lib_by_inode: Dict[int, List[str]] = {}
        for dirpath, _dirs, files in os.walk(self._hardlink_root):
            for fn in files:
                fp = os.path.join(dirpath, fn)
                try:
                    lib_by_inode.setdefault(os.stat(fp).st_ino, []).append(fp)
                except Exception:
                    continue

        # 孤儿 = 库中存在、且曾作为源、但当前源已失活的 inode
        with self._lock:
            known = set(self._known_src)
        orphans: List[Tuple[int, List[str]]] = []
        for ino, paths in lib_by_inode.items():
            if ino in known and ino not in cur_src:
                orphans.append((ino, paths))

        total = sum(len(p) for _, p in orphans)
        if total > self._delete_limit:
            logger.error(
                f"孤儿清理拟删除 {total} 个文件，超过上限 {self._delete_limit}，"
                f"已中止以防误删！请检查源/硬链接配置是否正确。"
            )
            return

        if total == 0:
            logger.info("孤儿清理扫描完成：无可清理的孤儿文件")
            return

        logger.info(f"开始孤儿清理：{total} 个孤儿文件（上限 {self._delete_limit}）")
        for ino, paths in orphans:
            for p in paths:
                try:
                    self.__delete_link_file(Path(p))
                except Exception as e:
                    logger.error(f"孤儿清理失败 {p}：{str(e)}")
        logger.info(f"孤儿清理扫描完成，处理 {total} 个孤儿文件")

    # ==================================================================
    # 远程触发 / API
    # ==================================================================
    def get_api(self) -> List[Dict[str, Any]]:
        return [{
            "path": "/hardlink_cleanup",
            "endpoint": self.api_cleanup,
            "methods": ["GET"],
            "summary": "硬链接孤儿清理",
            "description": "扫描硬链接目录，删除源已不存在的硬链接及其刮削产物（多重安全护栏）",
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
                                "props": {"cols": 12, "md": 3},
                                "content": [
                                    {"component": "VSwitch", "props": {"model": "enabled", "label": "启用插件"}},
                                ],
                            },
                            {
                                "component": "VCol",
                                "props": {"cols": 12, "md": 3},
                                "content": [
                                    {"component": "VSwitch", "props": {"model": "clean_empty", "label": "级联清理空目录"}},
                                ],
                            },
                            {
                                "component": "VCol",
                                "props": {"cols": 12, "md": 3},
                                "content": [
                                    {"component": "VSwitch", "props": {"model": "onlyonce", "label": "启动后孤儿扫描一次"}},
                                ],
                            },
                            {
                                "component": "VCol",
                                "props": {"cols": 12, "md": 3},
                                "content": [
                                    {"component": "VSwitch", "props": {"model": "dryrun", "label": "试运行(只记录不删除)"}},
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
                                    },
                                ],
                            },
                            {
                                "component": "VCol",
                                "props": {"cols": 12, "md": 4},
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
                            {
                                "component": "VCol",
                                "props": {"cols": 12, "md": 4},
                                "content": [
                                    {
                                        "component": "VTextField",
                                        "props": {
                                            "model": "delete_limit",
                                            "label": "孤儿扫描最大删除数",
                                            "type": "number",
                                            "placeholder": "500",
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
                                            "text": "单向监控：仅监听源目录的删除/移出，绝不改动源目录。删除基于 inode 定位硬链接，"
                                                    "普通拷贝/非硬链接库文件不会被波及。孤儿扫描设有源存在性、源非空、单次删除上限三重护栏；"
                                                    "网络挂载目录（CD2/rclone/SMB）请选兼容模式。误删无回收站，请先用「试运行」确认。",
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
            "dryrun": True,
            "source_dirs": "",
            "hardlink_dir": "",
            "mode": "compatibility",
            "delete_delay": 2,
            "delete_limit": 500,
        }

    def get_page(self) -> Optional[List[dict]]:
        return None
