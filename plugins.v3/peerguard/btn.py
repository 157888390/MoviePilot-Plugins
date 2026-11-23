"""
BTN（BitTorrent Threat Network）读取侧客户端。

对应 [BTN-Spec](https://github.com/PBH-BTN/BTN-Spec) 2.0.1，只实现反吸血真正用得上的部分：

  - ``ping/config``             → 能力清单与各能力的端点/间隔
  - ``ruleIpDenylist``          → 云端 IP 黑名单（纯文本，带 rev 增量）
  - ``ruleIpAllowlist``         → 云端 IP 白名单（公益站点/盒子，命中即放行）
  - ``rulePeerIdentity``        → 云端 PeerID / 客户端名规则
  - ``syncBanHistory``          → 回传自己的封禁记录（可选，须用户显式同意）

**为什么不用第三方库**：BTN 只用到 HTTP + JSON + GZIP，``requests`` 已经够用；
PoW 用 ``hashlib`` 单线程算，难度 6 bit 通常几毫秒。引入 PBH 的 Java 生态
或另起容器，正是本插件想省掉的东西。

**缓存是规范要求，不是优化**：规范明确「客户端每次获取 BTN 响应后，都应该持久化缓存本地」，
并且 ``rev`` 查询参数未变更时服务端回 204 —— 不缓存就等于每轮重下 110KB 名单。
缓存落在 ``get_data_path()`` 下，与封禁记录互不干扰。
"""

from __future__ import annotations

import base64
import gzip
import hashlib
import json
import random
import time
import zlib
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import requests

from .guard import CloudRule, IpMatcher

# 默认配置下发地址。个人 AppID 下发的能力清单可能与本文件不同，一切以响应为准
BTN_CONFIG_URL = "https://sparkle.ghostchu.com/ping/config"
# 本实现对齐的协议版本（规范内部版本号，不是 2.0.1 这个展示版本）
BTN_PROTOCOL_VERSION = 20
# 规范要求的 User-Agent 标记：实现名 + BTN-Protocol/规范版 + BTN-Protocol-Version/内部版
BTN_USER_AGENT = (
    "PeerGuard/0.2.0 BTN-Protocol/2.0.1 "
    f"BTN-Protocol-Version/{BTN_PROTOCOL_VERSION}"
)
# 单次提交最多多少条封禁记录（规范：超过 1000 条必须拆分，否则服务端截断）
BTN_SUBMIT_BATCH = 1000

# 缓存文件名
_CACHE_DENY = "btn_denylist.txt"
_CACHE_ALLOW = "btn_allowlist.txt"
_CACHE_RULES = "btn_rules.json"
_CACHE_CONFIG = "btn_config.json"


class BtnError(Exception):
    """BTN 侧不可用：鉴权失败、协议不兼容、网络错误等。"""


def torrent_identifier(info_hash: str) -> str:
    """
    计算 BTN 的种子匿名识别符（规范「Torrent Identifier 算法」）。

    ``crc32(info_hash)`` 当盐拼在明文后面再 SHA256 —— 盐只由 info_hash 自己决定，
    所以对同一个种子是稳定的，但服务端反查不出下载内容。

    :param info_hash: 40 位十六进制 info_hash
    :return: 64 位十六进制小写摘要；输入为空返回空串
    """
    text = (info_hash or "").strip().lower()
    if not text:
        return ""
    # Guava 的 crc32 HashCode 内部是小端字节序，Hex 也是小端输出 ——
    # 规范示例里 salt=4063bf66 就是这个顺序（直接 format(crc32,"08x") 会得到
    # 66bf6340，那是字节序反了，算出来的摘要对不上）。
    salt = zlib.crc32(text.encode("utf-8")).to_bytes(4, "little").hex()
    return hashlib.sha256(f"{text}{salt}".encode("utf-8")).hexdigest()


class BtnSnapshot:
    """一次刷新后的全部云端规则，供 :class:`RuleEngine` 直接使用。"""

    __slots__ = (
        "denylist",
        "allowlist",
        "peer_id_rules",
        "client_rules",
        "versions",
        "fetched_at",
    )

    def __init__(
        self,
        denylist: Optional[IpMatcher] = None,
        allowlist: Optional[IpMatcher] = None,
        peer_id_rules: Optional[List[CloudRule]] = None,
        client_rules: Optional[List[CloudRule]] = None,
        versions: Optional[Dict[str, str]] = None,
        fetched_at: float = 0.0,
    ) -> None:
        self.denylist = denylist or IpMatcher()
        self.allowlist = allowlist or IpMatcher()
        self.peer_id_rules = peer_id_rules or []
        self.client_rules = client_rules or []
        self.versions = versions or {}
        self.fetched_at = fetched_at

    def summary(self) -> Dict[str, Any]:
        """给前端/接口看的摘要，不暴露规则内容本身。"""
        return {
            "denylist": self.denylist.count,
            "allowlist": self.allowlist.count,
            "peer_id_rules": len(self.peer_id_rules),
            "client_rules": len(self.client_rules),
            "versions": dict(self.versions),
            "fetched_at": self.fetched_at,
        }


class BtnClient:
    """
    BTN 实例的最小客户端，同时负责落盘缓存。

    一个实例可跨轮复用：``refresh()`` 内部按 ``rev`` 做增量，规则没变时
    服务端回 204，本地直接沿用缓存，开销只有一次空请求。
    """

    def __init__(
        self,
        app_id: str,
        app_secret: str,
        config_url: str = BTN_CONFIG_URL,
        cache_dir: Optional[Path] = None,
        timeout: float = 20.0,
    ) -> None:
        """
        :param app_id: BTN AppID（PBH 网页里生成）
        :param app_secret: BTN AppSecret
        :param config_url: 配置下发地址，可指向自建 BTN 实例
        :param cache_dir: 缓存目录；为 None 时只走内存
        :param timeout: 单次请求超时（秒）
        """
        self.app_id = (app_id or "").strip()
        self.app_secret = (app_secret or "").strip()
        self.config_url = (config_url or BTN_CONFIG_URL).strip()
        self.cache_dir = Path(cache_dir) if cache_dir else None
        self.timeout = timeout
        self._session = requests.Session()
        self._session.headers.update({
            "User-Agent": BTN_USER_AGENT,
            "Accept-Encoding": "gzip",
        })
        if self.app_id and self.app_secret:
            self._session.headers.update({
                "X-BTN-AppID": self.app_id,
                "X-BTN-AppSecret": self.app_secret,
            })
        self._config: Dict[str, Any] = {}
        if self.cache_dir:
            self.cache_dir.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------
    # 底层请求
    # ------------------------------------------------------------------
    def _request(
        self,
        method: str,
        url: str,
        *,
        params: Optional[Dict[str, Any]] = None,
        headers: Optional[Dict[str, str]] = None,
        data: Optional[bytes] = None,
    ) -> requests.Response:
        """发一次请求；非 2xx 统一转成 :class:`BtnError`，带上服务端给的原因。"""
        try:
            resp = self._session.request(
                method, url, params=params, headers=headers, data=data,
                timeout=self.timeout,
            )
        except requests.RequestException as err:
            raise BtnError(f"连接 BTN 失败：{err}") from err
        if resp.status_code in (200, 204, 205):
            return resp
        body = (resp.text or "")[:200]
        try:
            detail = json.loads(body).get("message") or body
        except (ValueError, AttributeError):
            detail = body
        raise BtnError(f"BTN 返回 HTTP {resp.status_code}：{detail}")

    def _json(self, method: str, url: str, params: Optional[Dict[str, Any]] = None) -> Any:
        """发请求并解析 JSON。"""
        resp = self._request(method, url, params=params)
        if resp.status_code == 204 or not resp.content:
            return None
        try:
            return resp.json()
        except ValueError as err:
            raise BtnError("BTN 返回的不是 JSON") from err

    # ------------------------------------------------------------------
    # 能力：配置下发
    # ------------------------------------------------------------------
    def fetch_config(self) -> Dict[str, Any]:
        """
        拉取服务端能力清单，并校验协议版本是否在本实现的区间内。

        规范要求：版本不在区间内必须停止，而不是继续尝试 —— 否则会用旧语义
        解读新字段，比直接失败更难排查。
        """
        data = self._json("GET", self.config_url)
        if not isinstance(data, dict):
            raise BtnError("BTN 配置响应为空")
        low = int(data.get("min_protocol_version") or 0)
        high = int(data.get("max_protocol_version") or 0)
        if not (low <= BTN_PROTOCOL_VERSION <= high):
            raise BtnError(
                f"BTN 协议版本不兼容：服务端要求 {low}~{high}，"
                f"本插件实现 {BTN_PROTOCOL_VERSION}，请升级插件"
            )
        self._config = data
        self._write_cache(_CACHE_CONFIG, json.dumps(data, ensure_ascii=False))
        return data

    def _ability(self, key: str) -> Dict[str, Any]:
        """取某个能力的配置；没下发就当未启用。"""
        ability = (self._config or {}).get("ability") or {}
        conf = ability.get(key)
        return conf if isinstance(conf, dict) else {}

    def _pow_headers(self, conf: Dict[str, Any]) -> Dict[str, str]:
        """
        端点要求 PoW 时预先算一个解。

        规范允许服务端在遭遇攻击时给提交端点加验证码；这里按需计算，
        平时 ``pow_captcha`` 为 false，不产生任何开销。
        """
        if not conf.get("pow_captcha"):
            return {}
        endpoint = (self._config.get("proof_of_work_captcha") or {}).get("endpoint")
        if not endpoint:
            return {}
        session = self._json("GET", endpoint, params={"type": conf.get("config_key") or ""})
        if not isinstance(session, dict):
            return {}
        try:
            challenge = base64.b64decode(session["challengeBase64"])
            bits = int(session.get("difficultyBits") or 0)
            algorithm = str(session.get("algorithm") or "SHA-256")
            nonce = self._solve_pow(challenge, bits, algorithm)
            return {
                "X-BTN-PowID": str(session.get("id") or ""),
                "X-BTN-PowSolution": base64.b64encode(nonce).decode("ascii"),
            }
        except (KeyError, TypeError, ValueError):
            return {}

    @staticmethod
    def _solve_pow(challenge: bytes, bits: int, algorithm: str) -> bytes:
        """暴力找前 ``bits`` 位为零的 ``challenge+nonce``，nonce 固定 8 字节大端。"""
        if bits <= 0:
            return (0).to_bytes(8, "big")
        full, rest = divmod(bits, 8)
        mask = (0xFF << (8 - rest)) & 0xFF if rest else 0
        nonce = random.getrandbits(63)
        while True:
            raw = nonce.to_bytes(8, "big")
            digest = hashlib.new(algorithm.lower().replace("-", ""), challenge + raw).digest()
            if digest[:full] == b"\x00" * full and (not rest or (digest[full] & mask) == 0):
                return raw
            nonce = (nonce + 1) & 0x7FFFFFFFFFFFFFFF

    # ------------------------------------------------------------------
    # 能力：IP 名单
    # ------------------------------------------------------------------
    def _fetch_ip_list(self, name: str, conf: Dict[str, Any]) -> Tuple[Optional[str], str]:
        """
        拉一份 IP 名单。

        :return: ``(文本, 版本号)``；服务端回 204 时文本为 None，表示沿用本地缓存
        """
        endpoint = str(conf.get("endpoint") or "")
        if not endpoint:
            return None, ""
        rev = self._read_rev(name)
        headers = self._pow_headers(conf)
        resp = self._request(
            "GET", endpoint, params={"rev": rev or "initial"}, headers=headers or None
        )
        if resp.status_code == 204:
            return None, rev
        text = resp.text or ""
        new_rev = (resp.headers.get("X-BTN-ContentVersion") or "").strip() or rev
        self._write_cache(
            _CACHE_DENY if name == "denylist" else _CACHE_ALLOW, text
        )
        self._write_cache(self._rev_file(name), new_rev)
        return text, new_rev

    # ------------------------------------------------------------------
    # 能力：云端 PeerID / 客户端名规则
    # ------------------------------------------------------------------
    def _fetch_rules(self, conf: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """拉云端规则；204 或没有端点时返回 None。"""
        endpoint = str(conf.get("endpoint") or "")
        if not endpoint:
            return None
        rev = self._read_rev("rules")
        headers = self._pow_headers(conf)
        resp = self._request(
            "GET", endpoint, params={"rev": rev or "initial"}, headers=headers or None
        )
        if resp.status_code == 204:
            return None
        try:
            data = resp.json()
        except ValueError:
            return None
        if not isinstance(data, dict):
            return None
        version = str(data.get("version") or resp.headers.get("X-BTN-ContentVersion") or "")
        self._write_cache(_CACHE_RULES, json.dumps(data, ensure_ascii=False))
        self._write_cache(_CACHE_RULES + ".rev", version)
        return data

    # ------------------------------------------------------------------
    # 主流程
    # ------------------------------------------------------------------
    def refresh(self, force: bool = False) -> BtnSnapshot:
        """
        刷新一次云端规则。

        :param force: 忽略本地 ``rev``，强制全量重下（手动刷新用）
        :return: 合并了缓存与本次增量的快照
        """
        if not self._config:
            self.fetch_config()

        snapshot = self.load_cache() or BtnSnapshot()
        if force:
            # 强制刷新时清掉本地 rev，让服务端回全量而不是 204
            for name in ("denylist", "allowlist", "rules"):
                self._write_cache(self._rev_file(name), "")
        versions = dict(snapshot.versions)

        deny_conf = self._ability("ip_denylist")
        if deny_conf:
            text, rev = self._fetch_ip_list("denylist", deny_conf)
            if text is not None:
                snapshot.denylist = IpMatcher(text.splitlines())
            elif not snapshot.denylist.count:
                # 204 但本地没有缓存（缓存被清过）：退回全量重下
                snapshot.denylist = IpMatcher(self._cached_lines(_CACHE_DENY))
            versions["denylist"] = rev

        allow_conf = self._ability("ip_allowlist")
        if allow_conf:
            text, rev = self._fetch_ip_list("allowlist", allow_conf)
            if text is not None:
                snapshot.allowlist = IpMatcher(text.splitlines())
            elif not snapshot.allowlist.count:
                snapshot.allowlist = IpMatcher(self._cached_lines(_CACHE_ALLOW))
            versions["allowlist"] = rev

        rules_conf = self._ability("rule_peer_identity")
        if rules_conf:
            data = self._fetch_rules(rules_conf)
            if data is not None:
                snapshot.peer_id_rules, snapshot.client_rules = _parse_rule_sections(data)
            elif not (snapshot.peer_id_rules or snapshot.client_rules):
                cached = self._read_json(_CACHE_RULES)
                if isinstance(cached, dict):
                    snapshot.peer_id_rules, snapshot.client_rules = _parse_rule_sections(cached)
            versions["rules"] = self._read_rev("rules")

        snapshot.versions = versions
        snapshot.fetched_at = time.time()
        return snapshot

    def load_cache(self) -> Optional[BtnSnapshot]:
        """
        只读本地缓存，不发网络请求。

        插件启动时先拿它装配规则引擎，第一轮扫描就有名单可用，
        不必等刷新任务跑完。
        """
        if not self.cache_dir:
            return None
        deny = self._cached_lines(_CACHE_DENY)
        allow = self._cached_lines(_CACHE_ALLOW)
        rules = self._read_json(_CACHE_RULES)
        if not deny and not allow and not isinstance(rules, dict):
            return None
        peer_id_rules: List[CloudRule] = []
        client_rules: List[CloudRule] = []
        if isinstance(rules, dict):
            peer_id_rules, client_rules = _parse_rule_sections(rules)
        versions = {
            "denylist": self._read_rev("denylist"),
            "allowlist": self._read_rev("allowlist"),
            "rules": self._read_rev("rules"),
        }
        return BtnSnapshot(
            denylist=IpMatcher(deny),
            allowlist=IpMatcher(allow),
            peer_id_rules=peer_id_rules,
            client_rules=client_rules,
            versions=versions,
        )

    def submit_bans(self, bans: List[Dict[str, Any]]) -> int:
        """
        回传封禁记录（``submit_bans`` 能力）。

        规范要求先取得用户同意才能提交 —— 插件里默认关闭，且只在用户显式打开
        ``btn_submit`` 时调用。请求体必须 GZIP，超 1000 条由调用方分批。

        :return: 服务端接受的条数（按 HTTP 200 记整批成功）
        """
        conf = self._ability("submit_bans")
        endpoint = str(conf.get("endpoint") or "")
        if not endpoint or not bans:
            return 0
        headers = {"Content-Encoding": "gzip", "Content-Type": "application/json"}
        headers.update(self._pow_headers(conf))
        accepted = 0
        for start in range(0, len(bans), BTN_SUBMIT_BATCH):
            batch = bans[start:start + BTN_SUBMIT_BATCH]
            body = gzip.compress(json.dumps({"bans": batch}, ensure_ascii=False).encode("utf-8"))
            self._request("POST", endpoint, headers=headers, data=body)
            accepted += len(batch)
        return accepted

    # ------------------------------------------------------------------
    # 缓存读写
    # ------------------------------------------------------------------
    def _cache_file(self, name: str) -> Optional[Path]:
        """缓存文件路径；没配目录时为 None。"""
        return self.cache_dir / name if self.cache_dir else None

    def _write_cache(self, name: str, text: str) -> None:
        """写缓存；失败只影响增量能力，不该让整轮刷新挂掉。"""
        path = self._cache_file(name)
        if not path:
            return
        try:
            path.write_text(text, encoding="utf-8")
        except OSError:
            pass

    def _read_cache(self, name: str) -> str:
        """读缓存文本，缺失返回空串。"""
        path = self._cache_file(name)
        if not path or not path.exists():
            return ""
        try:
            return path.read_text(encoding="utf-8")
        except OSError:
            return ""

    def _cached_lines(self, name: str) -> List[str]:
        """按行读缓存。"""
        return self._read_cache(name).splitlines()

    def _read_rev(self, name: str) -> str:
        """读某份缓存的版本号。"""
        return self._read_cache(self._rev_file(name)).strip()

    @staticmethod
    def _rev_file(name: str) -> str:
        """版本号旁挂文件名，与内容文件同目录。"""
        target = {"denylist": _CACHE_DENY, "allowlist": _CACHE_ALLOW, "rules": _CACHE_RULES}[name]
        return target + ".rev"

    def _read_json(self, name: str) -> Optional[Any]:
        """读 JSON 缓存，坏了就当没有。"""
        text = self._read_cache(name)
        if not text:
            return None
        try:
            return json.loads(text)
        except ValueError:
            return None


def _parse_rule_sections(data: Dict[str, Any]) -> Tuple[List[CloudRule], List[CloudRule]]:
    """
    把 ``rule_peer_identity`` 的响应拆成 PeerID 规则与客户端名规则。

    PBH v1 结构是 ``{"peer_id": {模块名: [规则...]}, "client_name": {...}}``，
    其余键（ip/port/script）本插件不处理：IP 走 denylist 能力，port/script
    需要 PBH 的表达式引擎，不是这里能廉价兼容的。
    """
    def collect(section: str) -> List[CloudRule]:
        out: List[CloudRule] = []
        rules = data.get(section)
        if not isinstance(rules, dict):
            return out
        for module, items in rules.items():
            if not isinstance(items, list):
                continue
            for raw in items:
                rule = CloudRule.parse(raw, str(module))
                if rule:
                    out.append(rule)
        return out

    return collect("peer_id"), collect("client_name")
