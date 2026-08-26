"""代理 IP 类型探测与可用性检测。

通过代理访问 ip-api.com，一次请求同时得到：
  - 可用性（能否连通 + 延迟）
  - 出口 IP 及其类型（residential 住宅 / datacenter 机房 / mobile 移动 / unknown）

分类以 ISP/ASN 名为主，ip-api 的 hosting/mobile 标记为辅——因为 ip-api 的
hosting 标记并不可靠（会把 Leaseweb 等机房标成 hosting=False）。
"""
from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Callable

import requests

# 常见机房 / 托管商关键字（命中即判 datacenter）
_DC_KEYWORDS = (
    "leaseweb", "psychz", "hivelocity", "gtt", "ovh", "hetzner", "digitalocean",
    "digital ocean", "amazon", "aws", "google", "microsoft", "azure", "vultr",
    "choopa", "linode", "akamai", "cloudflare", "colocrossing", "m247", "cogent",
    "datacamp", "datacenter", "data center", "hosting", "colo", "contabo",
    "scaleway", "oracle", "limestone", "quadranet", "nforce", "latitude",
    "internetport", "tier.net", "heart internet", "web3 leaders", "zenlayer",
    "gcore", "g-core", "constant company", "fdcservers", "nessus", "etop",
    "netjoin", "xneelo", "sternforth", "virtual systems", "en technologies",
    "thunderbox", "hostroyale", "ipxo", "packethub", "servers", "vps", "cloud",
)
# 常见消费级家宽关键字（命中即判 residential）
_RES_KEYWORDS = (
    "comcast", "cox", "verizon", "at&t", "spectrum", "charter", "centurylink",
    "frontier", "orange", "vodafone", "telefonica", "telecom italia",
    "deutsche telekom", "free sas", "sfr", "wave broadband", "rcn", "optimum",
    "cablevision", "xfinity", "virgin media", "kpn", "ziggo", "telstra",
    "rogers", "bell canada", "videotron", "jio", "airtel", "china telecom",
    "china unicom", "china mobile", "broadband", "cable", "dsl", "fiber", "fios",
)
_MOBILE_KEYWORDS = ("wireless", "cellular", "t-mobile", " o2", "telenor", "lte")

_IPAPI_URL = (
    "http://ip-api.com/json/"
    "?fields=status,message,query,countryCode,isp,org,as,proxy,hosting,mobile"
)


def classify(isp: str, as_name: str, hosting: bool, proxy_flag: bool, mobile: bool) -> str:
    """根据 ISP/ASN 名 + ip-api 标记判定 IP 类型。"""
    text = f"{isp} {as_name}".lower()
    # 住宅优先级最高（消费级 ISP 名很明确）
    if any(k in text for k in _RES_KEYWORDS):
        return "residential"
    if mobile and not hosting:
        return "mobile"
    if any(k in text for k in _MOBILE_KEYWORDS) and not hosting:
        return "mobile"
    if hosting or any(k in text for k in _DC_KEYWORDS):
        return "datacenter"
    if proxy_flag:
        return "datacenter"
    return "unknown"


def probe_one(url: str, timeout: int = 15) -> dict:
    """通过 proxy 请求 ip-api，同时得到可用性与出口 IP 类型。"""
    proxies = {"http": url, "https": url}
    t0 = time.time()
    try:
        r = requests.get(_IPAPI_URL, proxies=proxies, timeout=timeout)
        latency = int((time.time() - t0) * 1000)
        data = r.json()
        if data.get("status") != "success":
            return {"status": "fail", "latency_ms": latency,
                    "error": data.get("message", "lookup failed")}
        ip_type = classify(
            str(data.get("isp", "")), str(data.get("as", "")),
            bool(data.get("hosting")), bool(data.get("proxy")), bool(data.get("mobile")),
        )
        return {
            "status": "ok", "latency_ms": latency,
            "egress_ip": data.get("query", ""), "country": data.get("countryCode", ""),
            "isp": data.get("isp", ""), "ip_type": ip_type,
        }
    except Exception as exc:  # noqa: BLE001
        return {"status": "fail", "latency_ms": int((time.time() - t0) * 1000),
                "error": str(exc)[:150]}


def probe_all(items: list[tuple[int, str]], workers: int = 16,
              on_result: Callable[[int, dict], None] | None = None) -> None:
    """并发探测 items=[(proxy_id, url)]；每条完成时回调 on_result(id, result) 落库。"""
    if not items:
        return
    with ThreadPoolExecutor(max_workers=max(1, min(workers, len(items)))) as ex:
        futures = {ex.submit(probe_one, url): pid for pid, url in items}
        for fut in as_completed(futures):
            pid = futures[fut]
            try:
                res = fut.result()
            except Exception as exc:  # noqa: BLE001
                res = {"status": "fail", "latency_ms": 0, "error": str(exc)[:150]}
            if on_result:
                on_result(pid, res)
