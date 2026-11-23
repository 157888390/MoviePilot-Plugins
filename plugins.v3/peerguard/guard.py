"""
反吸血判定规则。

拆成独立模块是为了能脱离 MoviePilot 单独跑测试：这里不 import 任何宿主符号，
输入是 qb 的 peer 字典和种子体积，输出是"命中哪条规则"。插件本体只负责
取数据、调 :class:`RuleEngine`、把命中项交给 qb 封禁。

判定信号只有三个来源（qb WebUI 能提供的全部）：
  - ``peer_id_client`` → 识别吸血客户端（qb 4.5+ 才提供）
  - ``client`` 名称 → 兜底识别
  - ``progress`` / ``downloaded`` / ``uploaded`` 的跨轮变化 → 识别虚假进度与超量下载

另外接受来自 BTN 的云端数据（:class:`CloudRule` / :class:`IpMatcher`）：规则解析在
:mod:`btn` 里做，这里只负责"怎么用"，所以本模块依然不 import 宿主。
"""

from __future__ import annotations

import bisect
import ipaddress
import json
import re
import time
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple, Union

# 默认 PeerID 前缀黑名单：都是国内常见的"只下不传"客户端。
# 对照 PBH 的默认规则集，去掉了语义不明确的几项，只留确定是吸血客户端的。
DEFAULT_PEER_ID_PREFIXES = [
    "-xl",  # 迅雷
    "-sd",  # 迅雷（伪）
    "-xm",  # 迅雷手机版
    "-ts",  # 迅雷极速版
    "-tt",  # 迅雷（另一支）
    "-dl",  # 迅雷（旧版）
    "-qd",  # QQ旋风
    "-xf",  # 先锋影音
    "-fg",  # 快车
    "-nx",  # 网际快车变种
    "-bn",  # 百度网盘离线
    "-rn0.0.0",  # 伪装成 rTorrent 的吸血端
]

# 默认客户端名称正则：peer_id 拿不到时的兜底。迅雷各版本的自报名称五花八门，
# 这里用宽松匹配，宁可漏也不误伤（误伤会影响正常 peer 的做种）。
DEFAULT_CLIENT_PATTERNS = [
    r"^XL[0-9]{4}",  # XL0012 / XL0018
    r"迅雷",
    r"Xunlei",
    r"^QQ旋风",
    r"^Xfplay",
    r"^FlashGet",
    r"百度网盘",
    r"^Thunder",
]


@dataclass
class GuardConfig:
    """一轮判定的全部参数，由插件从配置项装配。"""

    client_patterns: List[str] = field(default_factory=lambda: list(DEFAULT_CLIENT_PATTERNS))
    peer_id_prefixes: List[str] = field(default_factory=lambda: list(DEFAULT_PEER_ID_PREFIXES))
    ban_fake_progress: bool = True
    # 进度回退的容忍度：低于这个幅度算抖动，不算回退
    progress_regress_tolerance: float = 0.02
    # 下载量涨了多少（MB）而进度几乎没动，就认定上报的进度是假的
    downloaded_progress_gap_mb: int = 50
    ban_excessive_download: bool = True
    # 单 peer 累计下载超过种子体积的多少倍算超量
    excessive_factor: float = 1.5
    ban_leech_only: bool = False
    # 连续多少轮「有下载、零上传」才判定为纯吸血（默认关闭，PT 下载期本就零上传）
    leech_rounds: int = 5
    whitelist: List[str] = field(default_factory=list)
    max_ban_per_round: int = 20
    # ---- BTN 云端数据（由 plugins 在刷新后注入，缺省即不启用）----
    ban_btn_denylist: bool = True
    ban_btn_rules: bool = True
    denylist: Optional["IpMatcher"] = None
    allowlist: Optional["IpMatcher"] = None
    cloud_peer_id_rules: List["CloudRule"] = field(default_factory=list)
    cloud_client_rules: List["CloudRule"] = field(default_factory=list)


class IpMatcher:
    """
    IP / CIDR 集合，用二分查区间。

    BTN 的拒绝名单一次上千条网段，逐条 ``addr in network`` 会在每个 peer 上
    做上千次前缀比较；这里先 collapse 再折算成 ``[start, end]`` 区间数组，
    单次判定退化为一次 bisect。

    支持规范里的注释写法：整行 ``#`` / ``//`` 开头，或行尾 `` # 备注``。
    """

    __slots__ = ("count", "_v4_starts", "_v4_ends", "_v6_starts", "_v6_ends")

    def __init__(self, lines: Optional[Iterable[str]] = None) -> None:
        v4: List[ipaddress._BaseNetwork] = []
        v6: List[ipaddress._BaseNetwork] = []
        for raw in lines or []:
            network = self._parse_line(raw)
            if network is None:
                continue
            (v4 if network.version == 4 else v6).append(network)
        self._v4_starts, self._v4_ends = self._pack(v4)
        self._v6_starts, self._v6_ends = self._pack(v6)
        self.count = len(v4) + len(v6)

    @staticmethod
    def _parse_line(raw: str) -> Optional[ipaddress._BaseNetwork]:
        """解析一行，坏行进返回 None（名单里混进备注/空行是常态）。"""
        text = (raw or "").strip()
        if not text or text.startswith("#") or text.startswith("//"):
            return None
        for sep in (" #", " //", "\t#"):
            if sep in text:
                text = text.split(sep, 1)[0].strip()
                break
        if not text:
            return None
        try:
            return ipaddress.ip_network(text, strict=False)
        except ValueError:
            return None

    @staticmethod
    def _pack(
        networks: List[ipaddress._BaseNetwork],
    ) -> Tuple[List[int], List[int]]:
        """合并相邻网段后压成两个平行的有序整数数组。"""
        if not networks:
            return [], []
        spans = sorted(
            (int(net.network_address), int(net.broadcast_address))
            for net in ipaddress.collapse_addresses(networks)
        )
        return [s[0] for s in spans], [s[1] for s in spans]

    def contains(self, addr: Union[str, ipaddress._BaseAddress]) -> bool:
        """判断地址是否落在集合内；解析不出来的地址一律视为不命中。"""
        if isinstance(addr, str):
            try:
                addr = ipaddress.ip_address(addr)
            except ValueError:
                return False
        starts, ends = (
            (self._v4_starts, self._v4_ends)
            if addr.version == 4
            else (self._v6_starts, self._v6_ends)
        )
        if not starts:
            return False
        key = int(addr)
        index = bisect.bisect_right(starts, key) - 1
        return index >= 0 and key <= ends[index]

    def __len__(self) -> int:
        return self.count


@dataclass(frozen=True)
class CloudRule:
    """
    BTN 云端规则的一条（``rule_peer_identity`` 下发）。

    在线格式是 ``{"method": "STARTS_WITH", "content": "-xl"}`` 的 JSON 字符串，
    也兼容直接给裸字符串（按 CONTAINS 处理）。文本比较一律忽略大小写 ——
    qb 报上来的客户端名大小写不固定，PBH 的规则也是按忽略大小写写的。
    """

    module: str
    method: str
    content: str
    pattern: Optional[re.Pattern] = None

    _METHODS = ("STARTS_WITH", "ENDS_WITH", "CONTAINS", "EQUALS", "REGEX", "LENGTH")

    @classmethod
    def parse(cls, raw: Any, module: str = "") -> Optional["CloudRule"]:
        """把一条线上规则解析成对象；无法理解的可选规则直接丢弃。"""
        obj = raw
        if isinstance(raw, str):
            text = raw.strip()
            if not text:
                return None
            try:
                obj = json.loads(text)
            except ValueError:
                obj = {"method": "CONTAINS", "content": text}
        if isinstance(obj, (int, float)):
            obj = {"method": "CONTAINS", "content": str(obj)}
        if not isinstance(obj, dict):
            return None
        method = str(obj.get("method") or "CONTAINS").strip().upper().replace("-", "_")
        content = str(obj.get("content") or "").strip()
        if not content:
            return None
        if method not in cls._METHODS:
            method = "CONTAINS"
        pattern: Optional[re.Pattern] = None
        if method == "REGEX":
            try:
                pattern = re.compile(content, re.IGNORECASE)
            except re.error:
                return None
        return cls(module=str(module or ""), method=method, content=content, pattern=pattern)

    def match(self, value: str) -> bool:
        """按规则方法匹配一个值（peer_id 或客户端名）。"""
        if not value:
            return False
        if self.method == "REGEX":
            return bool(self.pattern and self.pattern.search(value))
        if self.method == "LENGTH":
            try:
                return len(value) == int(self.content)
            except ValueError:
                return False
        left = value.casefold()
        right = self.content.casefold()
        if self.method == "EQUALS":
            return left == right
        if self.method == "STARTS_WITH":
            return left.startswith(right)
        if self.method == "ENDS_WITH":
            return left.endswith(right)
        return right in left  # CONTAINS


@dataclass
class PeerBaseline:
    """单个 peer 上一轮的采样，用于算增量。"""

    progress: float = 0.0
    downloaded: int = 0
    uploaded: int = 0
    seen_rounds: int = 0
    zero_upload_rounds: int = 0
    last_ts: float = 0.0


@dataclass
class Hit:
    """一条命中结果。"""

    reason: str  # 规则代码，写进封禁记录
    detail: str  # 人话说明，前端与通知里展示


class RuleEngine:
    """
    有状态的规则引擎：必须按轮复用同一个实例，行为类规则依赖跨轮对比。

    ``evaluate`` 每调用一次就更新该 peer 的基线，所以同一轮里同一个 peer
    不要重复调用（插件按种子逐个遍历 peer，天然满足）。
    """

    def __init__(self, config: GuardConfig) -> None:
        self.config = config
        self._baseline: Dict[str, PeerBaseline] = {}
        self._client_res: List[re.Pattern] = []
        self._networks = self._compile_networks(config.whitelist)
        self._compile_patterns()

    # ------------------------------------------------------------------
    # 准备
    # ------------------------------------------------------------------
    def _compile_patterns(self) -> None:
        """把客户端名称规则编译成正则；非法正则跳过，不让一条坏规则毁掉整轮。"""
        compiled: List[re.Pattern] = []
        for raw in self.config.client_patterns:
            pattern = (raw or "").strip()
            if not pattern:
                continue
            try:
                compiled.append(re.compile(pattern, re.IGNORECASE))
            except re.error:
                continue
        self._client_res = compiled

    @staticmethod
    def _compile_networks(items: Iterable[str]) -> List[ipaddress._BaseNetwork]:
        """解析白名单 CIDR（也接受单个 IP）。"""
        networks: List[ipaddress._BaseNetwork] = []
        for raw in items:
            text = (raw or "").strip()
            if not text:
                continue
            try:
                networks.append(ipaddress.ip_network(text, strict=False))
            except ValueError:
                continue
        return networks

    def reset(self) -> None:
        """清空基线（插件停用或切换下载器时调用）。"""
        self._baseline.clear()

    def apply_cloud(
        self,
        *,
        denylist: Optional[IpMatcher] = None,
        allowlist: Optional[IpMatcher] = None,
        peer_id_rules: Optional[List[CloudRule]] = None,
        client_rules: Optional[List[CloudRule]] = None,
    ) -> None:
        """
        换一批 BTN 云端规则。

        不重建引擎 —— 重建会丢掉 :class:`PeerBaseline`，让"连续 N 轮"这类
        行为规则从头数起。规则更新与行为判定互不相干，各换各的。
        """
        if denylist is not None:
            self.config.denylist = denylist
        if allowlist is not None:
            self.config.allowlist = allowlist
        if peer_id_rules is not None:
            self.config.cloud_peer_id_rules = peer_id_rules
        if client_rules is not None:
            self.config.cloud_client_rules = client_rules

    def prune(self, alive: Set[str], max_keep: int = 20000) -> None:
        """
        丢弃本轮没出现的 peer 基线，避免长跑后字典无限膨胀。

        :param alive: 本轮出现过的 peer key 集合
        :param max_keep: 兜底上限，万一 alive 本身异常大也不至于吃满内存
        """
        if len(self._baseline) > max_keep:
            self._baseline.clear()
            return
        for key in [k for k in self._baseline if k not in alive]:
            self._baseline.pop(key, None)

    # ------------------------------------------------------------------
    # 判定
    # ------------------------------------------------------------------
    def evaluate(
        self,
        peer_key: str,
        peer: Dict[str, Any],
        torrent_size: int,
        peer_id: str = "",
    ) -> Optional[Hit]:
        """
        判定单个 peer。

        :param peer_key: ``hash|ip:port``，作为基线字典的键
        :param peer: qb 的 peer 对象
        :param torrent_size: 种子总体积（字节），算超量下载与进度换算要用
        :param peer_id: 已解析好的 peer_id（见 :func:`qbclient.extract_peer_id`）
        :return: 命中返回 :class:`Hit`，未命中返回 None
        """
        ip = str(peer.get("ip") or "")
        skip, hit = self._screen_ip(ip)
        if skip or hit:
            self._baseline.pop(peer_key, None)
            return hit

        # 1) PeerID 前缀：最快也最准，伪装客户端改 client 名但很少改 peer_id
        hit = self._match_peer_id(peer_id) or self._match_cloud(
            self.config.cloud_peer_id_rules, peer_id, "peer_id", "PeerID"
        )
        if hit:
            return hit

        # 2) 客户端名称
        client_name = str(peer.get("client") or "")
        hit = self._match_client(client_name) or self._match_cloud(
            self.config.cloud_client_rules, client_name, "client", "客户端名"
        )
        if hit:
            return hit

        # 3) 行为类：需要上一轮的基线
        baseline = self._baseline.get(peer_key)
        progress = float(peer.get("progress") or 0.0)
        downloaded = int(peer.get("downloaded") or 0)
        uploaded = int(peer.get("uploaded") or 0)
        now = time.time()

        if baseline and baseline.seen_rounds >= 1:
            hit = self._check_fake_progress(baseline, progress, downloaded, torrent_size)
            if hit:
                return hit
            hit = self._check_leech_only(baseline, downloaded, uploaded)
            if hit:
                return hit

        # 4) 超量下载：单看累计值即可，不需要基线
        hit = self._check_excessive(downloaded, torrent_size)
        if hit:
            return hit

        # 更新基线
        if baseline is None:
            baseline = PeerBaseline()
            self._baseline[peer_key] = baseline
        baseline.progress = progress
        baseline.downloaded = downloaded
        baseline.uploaded = uploaded
        baseline.seen_rounds += 1
        baseline.zero_upload_rounds = (
            baseline.zero_upload_rounds + 1 if uploaded == 0 else 0
        )
        baseline.last_ts = now
        return None

    # ------------------------------------------------------------------
    # 单条规则
    # ------------------------------------------------------------------
    def _screen_ip(self, ip: str) -> Tuple[bool, Optional[Hit]]:
        """
        先过一遍 IP 层：放行还是命中云端拒绝名单。

        顺序即优先级：内网 → 本地白名单 → BTN 允许名单 → BTN 拒绝名单。
        BTN 的允许名单里是别人登记过的盒子/公益站点，排在内网之后、拒绝名单之前，
        与 PBH 的语义一致（允许名单的用途正是"纠正被误判的 IP"）。

        :return: ``(是否放行, 命中)``，两者互斥
        """
        if not ip:
            return True, None
        try:
            addr = ipaddress.ip_address(ip)
        except ValueError:
            return True, None  # IP 解析不出来，宁可不判
        if addr.is_private or addr.is_loopback or addr.is_link_local:
            return True, None
        if any(addr in net for net in self._networks):
            return True, None
        allow = self.config.allowlist
        if allow is not None and allow.contains(addr):
            return True, None
        deny = self.config.denylist
        if self.config.ban_btn_denylist and deny is not None and deny.contains(addr):
            return False, Hit("btn_ip", f"命中 BTN 云端 IP 黑名单（{ip}）")
        return False, None

    def _match_peer_id(self, peer_id: str) -> Optional[Hit]:
        """按前缀匹配 peer_id。"""
        if not peer_id:
            return None
        lowered = peer_id.lower()
        for prefix in self.config.peer_id_prefixes:
            text = (prefix or "").strip().lower()
            if text and lowered.startswith(text):
                return Hit("peer_id", f"PeerID 命中黑名单前缀 {text}（{peer_id}）")
        return None

    def _match_client(self, client: str) -> Optional[Hit]:
        """按客户端名称正则匹配。"""
        if not client:
            return None
        for pattern in self._client_res:
            if pattern.search(client):
                return Hit("client", f"客户端名命中黑名单 {pattern.pattern}（{client}）")
        return None

    def _match_cloud(
        self,
        rules: List[CloudRule],
        value: str,
        kind: str,
        label: str,
    ) -> Optional[Hit]:
        """按 BTN 云端规则匹配；``kind`` 只用于区分命中原因码。"""
        if not self.config.ban_btn_rules or not value or not rules:
            return None
        for rule in rules:
            if rule.match(value):
                return Hit(
                    f"btn_{kind}",
                    f"命中 BTN 云端规则「{rule.module}」（{label} {value}）",
                )
        return None

    def _check_fake_progress(
        self,
        baseline: PeerBaseline,
        progress: float,
        downloaded: int,
        torrent_size: int,
    ) -> Optional[Hit]:
        """
        虚假进度：两种表现。

        1. 进度回退 —— 上报的完成度比上一轮还低，正常客户端不会倒退；
        2. 下载量与进度不符 —— 实际下了几十 MB，进度却纹丝不动。
           正常客户端每下一块都会推进进度，只有伪造上报才会这样。
        """
        if not self.config.ban_fake_progress:
            return None
        if progress < baseline.progress - self.config.progress_regress_tolerance:
            return Hit(
                "progress_regress",
                f"进度回退 {baseline.progress:.3f} → {progress:.3f}",
            )
        delta = downloaded - baseline.downloaded
        gap_bytes = self.config.downloaded_progress_gap_mb * 1024 * 1024
        if (
            delta >= gap_bytes
            and progress < 0.999
            and progress - baseline.progress < 0.001
        ):
            return Hit(
                "progress_fake",
                f"已下载 {delta / 1048576:.0f}MB 但进度仅 {baseline.progress:.3f}→{progress:.3f}",
            )
        return None

    def _check_excessive(self, downloaded: int, torrent_size: int) -> Optional[Hit]:
        """
        超量下载：对同一个种子下的量远超它的体积，典型是反复下载洗流量。

        种子体积未知（元数据还没拿到）时跳过，避免除零和误判。
        """
        if not self.config.ban_excessive_download or torrent_size <= 0:
            return None
        limit = torrent_size * max(self.config.excessive_factor, 1.0)
        if downloaded > limit:
            return Hit(
                "excessive",
                f"累计下载 {downloaded / 1073741824:.2f}GB，超过种子体积 "
                f"{self.config.excessive_factor:g} 倍（{torrent_size / 1073741824:.2f}GB）",
            )
        return None

    def _check_leech_only(
        self, baseline: PeerBaseline, downloaded: int, uploaded: int
    ) -> Optional[Hit]:
        """
        纯吸血：连续若干轮有下载、零上传。

        默认关闭 —— PT 在下载阶段本来就没有上传，开着会误伤正常下载中的 peer。
        只有 BT 场景或明确想严格执行时才建议打开。
        """
        if not self.config.ban_leech_only:
            return None
        grew = downloaded > baseline.downloaded
        if grew and uploaded == 0 and baseline.zero_upload_rounds >= self.config.leech_rounds:
            return Hit(
                "leech_only",
                f"连续 {baseline.zero_upload_rounds + 1} 轮有下载零上传",
            )
        return None
