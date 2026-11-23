"""
反吸血守卫（PeerGuard）· V3

通过 qb 的 WebUI API 轮询 peer 列表，按 PeerID / 客户端名 / 行为特征识别吸血端，
命中后调用 ``transfer/banPeers`` 让对方断开。判定逻辑在 :mod:`guard` 里，
qb 通讯在 :mod:`qbclient` 里，本文件只做装配、调度与对外接口。

与 PeerBanHelper 的差异（本插件是它的裁剪版）：
  - 保留：PeerID 黑名单、客户端名黑名单、虚假进度、超量下载、连锁封禁、封禁记录
  - 放弃：GeoIP / 国家 / ASN 封禁、规则集订阅（BTN）、PT 站点 API 联动、表达式引擎
  - 因此没有外部依赖、没有常驻 JVM，代价是规则要自己维护
"""

from __future__ import annotations

import threading
import time
import traceback
from typing import Any, Dict, List, Optional, Tuple

from app.sdk.logging import logger
from app.sdk.plugin import _PluginBase
from app.sdk.services import DownloaderHelper

from .guard import DEFAULT_CLIENT_PATTERNS, DEFAULT_PEER_ID_PREFIXES, GuardConfig, RuleEngine
from .qbclient import QbClient, QbError, extract_peer_id

# 封禁记录在内存与落盘里各留多少条
BAN_KEEP = 300
# 单轮最多检查多少个活跃种子，防止极端情况下把一轮拖成长任务
MAX_TORRENT_PER_ROUND = 200


class PeerGuard(_PluginBase):
    """插件主类。"""

    # 插件名称
    plugin_name = "反吸血守卫"
    # 插件描述
    plugin_desc = "轮询 qBittorrent 的 peer 列表，按 PeerID、客户端名与行为特征封禁吸血客户端。"
    # 插件图标
    plugin_icon = "https://raw.githubusercontent.com/157888390/MoviePilot-Plugins/main/icons/peerguard.png"
    # 插件版本
    plugin_version = "0.1.0"
    # 插件作者
    plugin_author = "157888390"
    # 作者主页
    author_url = "https://github.com/157888390"
    # 插件配置项 ID 前缀
    plugin_config_prefix = "peerguard_"
    # 插件排序
    plugin_order = 26
    # 认证等级
    auth_level = 1

    def __init__(self) -> None:
        """初始化运行态。配置在 :meth:`init_plugin` 里注入。"""
        super().__init__()
        self._enabled: bool = False
        self._config: Dict[str, Any] = {}
        self._client: Optional[QbClient] = None
        self._client_key: str = ""
        self._engine: Optional[RuleEngine] = None
        self._lock = threading.RLock()
        self._banlist: List[Dict[str, Any]] = []
        self._stats: Dict[str, Any] = {
            "rounds": 0,
            "checked": 0,
            "banned": 0,
            "last_round": 0.0,
            "last_error": "",
            "by_reason": {},
        }

    # ------------------------------------------------------------------
    # 生命周期
    # ------------------------------------------------------------------
    def init_plugin(self, config: Optional[Dict[str, Any]] = None) -> None:
        """读取配置、重建客户端与规则引擎。"""
        config = config or {}
        self._config = config
        self._enabled = bool(config.get("enabled"))
        # 记录从上一次运行里恢复，统计同理；没有就按空处理
        stored = self.get_data("banlist") or []
        if isinstance(stored, list):
            with self._lock:
                self._banlist = stored[-BAN_KEEP:]
        stored_stats = self.get_data("stats")
        if isinstance(stored_stats, dict):
            with self._lock:
                self._stats.update({k: stored_stats.get(k, v) for k, v in self._stats.items()})
        # 客户端按需构建：配置里的下载器名变了就丢弃旧的
        self._client = None
        self._client_key = ""
        self._engine = self.__build_engine(config)
        if self._enabled:
            logger.info(
                f"反吸血守卫已启用：每 {self.__interval()} 秒检查一次"
                f"{'（演练模式，只记录不封禁）' if config.get('dry_run') else ''}"
            )

    def get_state(self) -> bool:
        """插件是否启用。"""
        return self._enabled

    def stop_service(self) -> None:
        """停用插件时清空基线，避免下次启用沿用陈旧数据。"""
        if self._engine:
            self._engine.reset()
        with self._lock:
            self._client = None
            self._client_key = ""

    # ------------------------------------------------------------------
    # 定时服务
    # ------------------------------------------------------------------
    def get_service(self) -> List[Dict[str, Any]]:
        """注册轮询服务；未启用时不注册，宿主自然停掉调度。"""
        if not self._enabled:
            return []
        return [
            {
                "id": "peerguard_scan",
                "name": "反吸血扫描",
                "trigger": "interval",
                "func": self.scan_round,
                "kwargs": {"seconds": self.__interval()},
            }
        ]

    # ------------------------------------------------------------------
    # 配置界面
    # ------------------------------------------------------------------
    def get_form(self) -> Tuple[List[dict], Dict[str, Any]]:
        """返回配置页组件与默认值。"""
        # 下载器候选只取 qb 类型：别的下载器拿不到 peer_id，规则等于废掉一半
        downloaders: List[Dict[str, str]] = []
        try:
            for name, conf in DownloaderHelper().get_configs().items():
                if str(getattr(conf, "type", "") or "").lower() == "qbittorrent":
                    downloaders.append({"title": name, "value": name})
        except Exception as err:  # noqa: BLE001 配置页不能因为读不到配置就白屏
            logger.debug(f"反吸血守卫读取下载器列表失败：{err}")

        defaults: Dict[str, Any] = {
            "enabled": False,
            "downloader": downloaders[0]["value"] if downloaders else "",
            "interval": 30,
            "dry_run": True,
            "ban_fake_progress": True,
            "ban_excessive_download": True,
            "ban_leech_only": False,
            "excessive_factor": 1.5,
            "progress_gap_mb": 50,
            "max_ban_per_round": 20,
            "ban_peer_ids": "\n".join(DEFAULT_PEER_ID_PREFIXES),
            "ban_client_names": "\n".join(DEFAULT_CLIENT_PATTERNS),
            "whitelist": "",
            "notify": False,
        }

        def field(model: str, label: str, hint: str = "", md: int = 6) -> dict:
            """拼一个带说明的输入框。"""
            props = {"model": model, "label": label}
            if hint:
                props["hint"] = hint
                props["persistent-hint"] = True
            return {"component": "VCol", "props": {"cols": 12, "md": md}, "content": [
                {"component": "VTextField", "props": props}
            ]}

        def switch(model: str, label: str, hint: str = "", md: int = 6) -> dict:
            """拼一个带说明的开关。"""
            props = {"model": model, "label": label}
            if hint:
                props["hint"] = hint
                props["persistent-hint"] = True
            return {"component": "VCol", "props": {"cols": 12, "md": md}, "content": [
                {"component": "VSwitch", "props": props}
            ]}

        def textarea(model: str, label: str, hint: str = "") -> dict:
            """拼一个多行文本框。"""
            return {"component": "VCol", "props": {"cols": 12}, "content": [
                {"component": "VTextarea", "props": {
                    "model": model, "label": label, "rows": 4,
                    "hint": hint, "persistent-hint": True,
                }}
            ]}

        return [
            {
                "component": "VForm",
                "content": [
                    {
                        "component": "VRow",
                        "content": [
                            switch("enabled", "启用插件", "停用后不再注册轮询服务", 4),
                            {
                                "component": "VCol",
                                "props": {"cols": 12, "md": 4},
                                "content": [
                                    {"component": "VSelect", "props": {
                                        "model": "downloader",
                                        "label": "下载器",
                                        "items": downloaders,
                                        "hint": "只列出 qbittorrent 类型的下载器",
                                        "persistent-hint": True,
                                    }}
                                ],
                            },
                            field("interval", "轮询间隔（秒）", "越小越及时，但每次都要拉一遍活跃种子的 peer 列表", 4),
                            switch("dry_run", "演练模式", "只记录命中项，不真的封禁 —— 建议先开一周看看误判", 4),
                            switch("notify", "封禁时推送", "每轮有新增封禁时发一条汇总通知", 4),
                            field("max_ban_per_round", "单轮封禁上限", "防止规则写错一次封掉一大片", 4),
                        ],
                    },
                    {
                        "component": "VRow",
                        "content": [
                            switch("ban_fake_progress", "检测虚假进度", "进度回退，或下载量涨了进度不动", 4),
                            switch("ban_excessive_download", "检测超量下载", "单 peer 累计下载远超种子体积", 4),
                            switch("ban_leech_only", "检测纯吸血", "连续多轮有下载零上传；PT 下载期本就零上传，谨慎开启", 4),
                            field("excessive_factor", "超量倍数", "累计下载超过种子体积的多少倍判定超量", 4),
                            field("progress_gap_mb", "进度停滞阈值（MB）", "下载这么多字节而进度几乎没动，判为假进度", 4),
                        ],
                    },
                    {
                        "component": "VRow",
                        "content": [
                            textarea("ban_peer_ids", "PeerID 前缀黑名单（每行一个）",
                                     "比较时忽略大小写；qb 的 peer_id_client 字段就是这里要填的前缀"),
                            textarea("ban_client_names", "客户端名正则（每行一条）",
                                     "PeerID 拿不到时的兜底；非法正则会被跳过并忽略"),
                            textarea("whitelist", "IP 白名单（每行一个 CIDR）",
                                     "内网与回环地址恒定放行，这里只填额外的网段，如 1.2.3.0/24"),
                        ],
                    },
                ],
            }
        ], defaults

    # ------------------------------------------------------------------
    # 详情页
    # ------------------------------------------------------------------
    def get_page(self) -> Optional[List[dict]]:
        """返回详情页：连接状态、累计统计、最近封禁。"""
        with self._lock:
            stats = dict(self._stats)
            recent = list(self._banlist[-20:])
        state = "运行中" if self._enabled else "未启用"
        if stats.get("last_error"):
            state += f"（上次出错：{stats['last_error']}）"
        rows = [
            {
                "ip": item.get("peer", ""),
                "client": (item.get("client") or "-")[:32],
                "reason": item.get("detail") or item.get("reason", ""),
                "time": time.strftime("%m-%d %H:%M", time.localtime(item.get("ts", 0))),
            }
            for item in reversed(recent)
        ]
        return [
            {
                "component": "VAlert",
                "props": {
                    "type": "info" if not stats.get("last_error") else "warning",
                    "variant": "tonal",
                    "density": "compact",
                    "text": (
                        f"状态：{state}｜已完成 {stats.get('rounds', 0)} 轮，"
                        f"累计检查 {stats.get('checked', 0)} 个 peer，"
                        f"封禁 {stats.get('banned', 0)} 次"
                        f"{'｜演练模式' if self._config.get('dry_run') else ''}"
                    ),
                },
            },
            {
                "component": "VTable",
                "props": {
                    "density": "compact",
                    "items": rows,
                    "headers": [
                        {"title": "时间", "key": "time"},
                        {"title": "Peer", "key": "ip"},
                        {"title": "客户端", "key": "client"},
                        {"title": "命中原因", "key": "reason"},
                    ],
                },
            },
        ]

    # ------------------------------------------------------------------
    # 对外接口
    # ------------------------------------------------------------------
    def get_api(self) -> List[Dict[str, Any]]:
        """注册接口，全部走 bear 认证。"""
        return [
            {
                "path": "/overview",
                "endpoint": self.api_overview,
                "methods": ["GET"],
                "auth": "bear",
                "summary": "运行概览",
            },
            {
                "path": "/banlist",
                "endpoint": self.api_banlist,
                "methods": ["GET"],
                "auth": "bear",
                "summary": "封禁记录",
            },
            {
                "path": "/check",
                "endpoint": self.api_check,
                "methods": ["POST"],
                "auth": "bear",
                "summary": "立即检查一轮（不封禁）",
            },
            {
                "path": "/ban",
                "endpoint": self.api_ban,
                "methods": ["POST"],
                "auth": "bear",
                "summary": "手动封禁",
            },
            {
                "path": "/clear",
                "endpoint": self.api_clear,
                "methods": ["POST"],
                "auth": "bear",
                "summary": "清空封禁记录",
            },
        ]

    def api_overview(self) -> Dict[str, Any]:
        """返回运行概览，供脚本或前端轮询。"""
        with self._lock:
            stats = dict(self._stats)
            count = len(self._banlist)
        return {
            "enabled": self._enabled,
            "dry_run": bool(self._config.get("dry_run")),
            "downloader": self._config.get("downloader") or "",
            "interval": self.__interval(),
            "ban_records": count,
            "stats": stats,
        }

    def api_banlist(self, limit: int = 50) -> Dict[str, Any]:
        """返回最近封禁记录（倒序）。"""
        try:
            size = max(1, min(int(limit), BAN_KEEP))
        except (TypeError, ValueError):
            size = 50
        with self._lock:
            items = list(reversed(self._banlist[-size:]))
        return {"total": len(self._banlist), "items": items}

    def api_check(self) -> Dict[str, Any]:
        """立即跑一轮检查并返回命中明细，不写封禁（演练用）。"""
        try:
            hits = self.__collect_hits()
        except QbError as err:
            return {"success": False, "message": str(err)}
        return {
            "success": True,
            "hits": len(hits),
            "items": hits[:100],
            "dry_run": True,
        }

    def api_ban(self, payload: Dict[str, Any] = None) -> Dict[str, Any]:
        """手动封禁：body 里给 ``peers`` 列表（``ip:port``）。"""
        payload = payload or {}
        peers = payload.get("peers")
        if isinstance(peers, str):
            peers = [p.strip() for p in peers.replace("|", ",").split(",") if p.strip()]
        if not isinstance(peers, list) or not peers:
            return {"success": False, "message": "peers 不能为空"}
        client = self.__client()
        if not client:
            return {"success": False, "message": "下载器未配置或不可用"}
        try:
            client.ban_peers([str(p) for p in peers])
        except QbError as err:
            return {"success": False, "message": str(err)}
        self.__record_bans([
            {"peer": str(p), "client": "", "reason": "manual", "detail": "手动封禁",
             "ts": time.time(), "hash": ""}
            for p in peers
        ])
        return {"success": True, "banned": len(peers)}

    def api_clear(self) -> Dict[str, Any]:
        """清空封禁记录（不影响 qb 已生效的封禁）。"""
        with self._lock:
            self._banlist.clear()
        self.save_data("banlist", [])
        return {"success": True}

    # ------------------------------------------------------------------
    # 核心：一轮扫描
    # ------------------------------------------------------------------
    def scan_round(self) -> None:
        """定时服务入口：检查一轮并封禁。异常一律吞掉，不能让调度器把服务摘掉。"""
        if not self._enabled:
            return
        try:
            hits = self.__collect_hits()
            banned = 0
            if hits and not self._config.get("dry_run"):
                client = self.__client()
                limit = self.__int("max_ban_per_round", 20)
                targets = [h["peer"] for h in hits][:max(1, limit)]
                if client:
                    client.ban_peers(targets)
                    banned = len(targets)
                    self.__record_bans(hits[:banned])
            elif hits:
                # 演练模式：照样记下来，前端能看到"本来会封谁"
                self.__record_bans(hits)
            with self._lock:
                self._stats["rounds"] += 1
                self._stats["last_round"] = time.time()
                self._stats["last_error"] = ""
            if banned and self._config.get("notify"):
                self.__notify(hits, banned)
        except QbError as err:
            self.__mark_error(str(err))
            logger.warning(f"反吸血守卫：{err}")
        except Exception as err:  # noqa: BLE001 单轮失败不应该影响后续轮次
            self.__mark_error(str(err))
            logger.error(f"反吸血守卫执行出错：{err} - {traceback.format_exc()}")

    def __collect_hits(self) -> List[Dict[str, Any]]:
        """
        遍历活跃种子，收集命中项。不修改 qb 状态（封禁由调用方决定）。

        "活跃"按 qb 的口径：有 leech 或者正在跑流量。``stalledUP`` 且无 leech
        的种子跳过 —— 没人连它，扫 peer 只是白花时间。
        """
        client = self.__client()
        if not client:
            raise QbError("下载器未配置：请在插件设置里选择 qbittorrent 下载器")
        engine = self._engine or self.__build_engine(self._config)
        self._engine = engine

        torrents = client.torrents()
        active = [
            t for t in torrents
            if int(t.get("num_leechs") or 0) > 0
            or int(t.get("up_speed") or 0) > 0
            or int(t.get("dl_speed") or 0) > 0
        ][:MAX_TORRENT_PER_ROUND]

        hits: List[Dict[str, Any]] = []
        alive: set = set()
        checked = 0
        for torrent in active:
            torrent_hash = str(torrent.get("hash") or "")
            if not torrent_hash:
                continue
            size = int(torrent.get("size") or 0)
            try:
                peers = client.peers(torrent_hash)
            except QbError:
                # 单个种子拉不到（正在删、刚加进来）不算致命，跳过继续
                continue
            for peer_key, peer in peers.items():
                ip = str(peer.get("ip") or "")
                port = peer.get("port") or 0
                key = f"{torrent_hash}|{ip}:{port}"
                alive.add(key)
                checked += 1
                peer_id = extract_peer_id(peer)
                hit = engine.evaluate(key, peer, size, peer_id)
                if hit:
                    hits.append({
                        "peer": f"{ip}:{port}",
                        "client": str(peer.get("client") or ""),
                        "peer_id": peer_id,
                        "reason": hit.reason,
                        "detail": hit.detail,
                        "hash": torrent_hash,
                        "ts": time.time(),
                    })
        engine.prune(alive)
        with self._lock:
            self._stats["checked"] += checked
        return hits

    # ------------------------------------------------------------------
    # 内部工具
    # ------------------------------------------------------------------
    def __build_engine(self, config: Dict[str, Any]) -> RuleEngine:
        """按配置装配规则引擎。"""
        def lines(key: str) -> List[str]:
            """把多行文本框拆成去空行的列表。"""
            raw = config.get(key)
            if isinstance(raw, list):
                return [str(x) for x in raw if str(x).strip()]
            return [line.strip() for line in str(raw or "").splitlines() if line.strip()]

        return RuleEngine(GuardConfig(
            client_patterns=lines("ban_client_names") or list(DEFAULT_CLIENT_PATTERNS),
            peer_id_prefixes=lines("ban_peer_ids") or list(DEFAULT_PEER_ID_PREFIXES),
            ban_fake_progress=bool(config.get("ban_fake_progress", True)),
            downloaded_progress_gap_mb=self.__int("progress_gap_mb", 50, config),
            ban_excessive_download=bool(config.get("ban_excessive_download", True)),
            excessive_factor=self.__float("excessive_factor", 1.5, config),
            ban_leech_only=bool(config.get("ban_leech_only", False)),
            whitelist=lines("whitelist"),
            max_ban_per_round=self.__int("max_ban_per_round", 20, config),
        ))

    def __client(self) -> Optional[QbClient]:
        """按需构建 qb 客户端；下载器名或地址变了就重建。"""
        name = str(self._config.get("downloader") or "")
        if not name:
            return None
        conf = DownloaderHelper().get_config(name)
        if not conf:
            return None
        cfg = getattr(conf, "config", None) or {}
        host = str(cfg.get("host") or "")
        username = str(cfg.get("username") or "")
        password = str(cfg.get("password") or "")
        key = f"{name}|{host}|{username}"
        with self._lock:
            if self._client and self._client_key == key:
                return self._client
            self._client = QbClient(host=host, username=username, password=password)
            self._client_key = key
            return self._client

    def __record_bans(self, hits: List[Dict[str, Any]]) -> None:
        """写入封禁记录并落盘，同时累计按原因统计。"""
        with self._lock:
            self._banlist.extend(hits)
            if len(self._banlist) > BAN_KEEP:
                del self._banlist[:-BAN_KEEP]
            self._stats["banned"] += len(hits)
            by_reason = self._stats.get("by_reason") or {}
            for hit in hits:
                reason = str(hit.get("reason") or "")
                by_reason[reason] = int(by_reason.get(reason, 0)) + 1
            self._stats["by_reason"] = by_reason
            snapshot = list(self._banlist[-BAN_KEEP:])
            stats = dict(self._stats)
        self.save_data("banlist", snapshot)
        self.save_data("stats", stats)

    def __mark_error(self, message: str) -> None:
        """记录最近一次错误，详情页与概览接口都会展示。"""
        with self._lock:
            self._stats["last_error"] = message[:200]

    def __notify(self, hits: List[Dict[str, Any]], banned: int) -> None:
        """把本轮封禁汇总成一条通知。"""
        reasons: Dict[str, int] = {}
        for hit in hits[:banned]:
            reasons[hit.get("reason", "")] = reasons.get(hit.get("reason", ""), 0) + 1
        detail = "、".join(f"{k}×{v}" for k, v in reasons.items())
        try:
            self.post_message(
                title="【反吸血守卫】已封禁吸血 Peer",
                text=f"本轮封禁 {banned} 个：{detail}",
            )
        except Exception as err:  # noqa: BLE001 通知失败不影响封禁结果
            logger.debug(f"反吸血守卫通知发送失败：{err}")

    def __interval(self) -> int:
        """轮询间隔，限制在 10 秒以上，避免把 qb 打成高频轮询。"""
        return max(10, self.__int("interval", 30))

    def __int(self, key: str, default: int, config: Optional[Dict[str, Any]] = None) -> int:
        """从配置里取整数，非法值回落默认。"""
        raw = (config if config is not None else self._config).get(key, default)
        try:
            return int(float(raw))
        except (TypeError, ValueError):
            return default

    def __float(self, key: str, default: float, config: Optional[Dict[str, Any]] = None) -> float:
        """从配置里取浮点数，非法值回落默认。"""
        raw = (config if config is not None else self._config).get(key, default)
        try:
            return float(raw)
        except (TypeError, ValueError):
            return default
