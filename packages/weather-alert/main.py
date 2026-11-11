"""weather-alert: turn official weather warnings into a "to say" queue.

Adapted from https://github.com/not-zai/weather-watch (MIT). The original's
division of labour is preserved: the plugin only *stores* what it fetched;
whether and how to say it stays with the agent (here: the operator asks, or the
hourly tick reads the pending queue aloud to the session that configured it).

Endpoints (both public, no credentials, GET only):

* central meteorological observatory warnings:
  ``https://www.nmc.cn/rest/findAlarm?province=<省>``  (as used by the original)
* current conditions: ``https://api.open-meteo.com/v1/forecast?...``

Capability mapping:

* ``http``    — the two endpoints above only; the sandbox pins destination hosts
  via the host's http allow-list configuration and forbids redirects;
* ``storage`` — dedup list of already-spoken warning titles (capped);
* ``cron``    — hourly fetch (the original daemon's tick);
* ``send``    — deliver *new* warnings to the configuring session only;
* ``command`` — /weather (now), /weather-pending, /weather-ack, /weather-clear.

配置三件套存在 storage 里，由 /weather-set 写入：省份（findAlarm 用）、经纬度
（open-meteo 用）、目标会话（自动取 caller.session_key）。无配置时命令返回设置指引，
cron 直接跳过——不猜默认地点。
"""

from __future__ import annotations

import json
import time

from soapbot.plugins.base import Plugin
from soapbot.plugins.context import CallerContext, PluginContext

CFG_KEY = "config"
SPOKEN_KEY = "spoken_titles"
MAX_SPOKEN = 200
UA = "soapbot-weather-alert/0.1 (+https://github.com/Soa4P/soapbot-plugins)"

NMC_URL = "https://www.nmc.cn/rest/findAlarm"
OPEN_METEO_URL = "https://api.open-meteo.com/v1/forecast"


def _load_json(body: bytes) -> dict | None:
    try:
        payload = json.loads(body.decode("utf-8", errors="replace"))
        return payload if isinstance(payload, dict) else None
    except ValueError:
        return None


class WeatherAlertPlugin(Plugin):
    async def setup(self, ctx: PluginContext) -> None:
        await super().setup(ctx)

        @ctx.command("weather-set", description="配置预警关注的省份与经纬度", usage="/weather-set <省份名> <纬度> <经度>")
        async def weather_set(caller: CallerContext, args: list[str]) -> str:
            if len(args) < 3:
                return "用法：/weather-set 北京市 39.90 116.40"
            province = args[0]
            try:
                lat, lon = float(args[1]), float(args[2])
            except ValueError:
                return "经纬度要是数字，例如：/weather-set 北京市 39.90 116.40"
            ctx.storage.set(CFG_KEY, {
                "province": province,
                "lat": lat,
                "lon": lon,
                "session_key": caller.session_key,
                "set_at": time.time(),
            })
            return f"已配置：{province}（{lat}, {lon}）。预警会播报到这个会话。"

        @ctx.command("weather", description="看当前天气与最新一次抓到的预警", usage="/weather")
        async def weather(caller: CallerContext, args: list[str]) -> str:
            cfg = ctx.storage.get(CFG_KEY) or {}
            if not cfg.get("lat"):
                return "先用 /weather-set <省份> <纬度> <经度> 配置一次。"
            # SafeHttpClient.fetch 不接受单独的 params：查询串手工拼，值都来自
            # 配置里的两个 float，无注入面。
            url = f"{OPEN_METEO_URL}?latitude={cfg['lat']}&longitude={cfg['lon']}&current_weather=true"
            status, body = await ctx.http.fetch(url, headers={"User-Agent": UA})
            payload = _load_json(body) if status == 200 else None
            if payload is None:
                return f"open-meteo 没答上来（HTTP {status}）。"
            current = payload.get("current_weather") or {}
            temp = current.get("temperature", "?")
            wind = current.get("windspeed", "?")
            lines = [f"当前气温 {temp}°C，风速 {wind} km/h。"]
            spoken = ctx.storage.get(SPOKEN_KEY) or []
            fresh = await _fetch_new_warnings(ctx, cfg, spoken)
            if fresh:
                lines.append("新预警：")
                lines.extend(f"- {t}" for t in fresh)
                _mark_spoken(ctx, fresh)
            else:
                lines.append("没有新的预警。")
            return "\n".join(lines)

        @ctx.command("weather-pending", description="看还没播报过的预警", usage="/weather-pending")
        async def weather_pending(caller: CallerContext, args: list[str]) -> str:
            fresh = await _fetch_new_warnings(ctx, ctx.storage.get(CFG_KEY) or {},
                                             ctx.storage.get(SPOKEN_KEY) or [])
            if not fresh:
                return "没有未播报的预警。"
            return "未播报的预警：\n" + "\n".join(f"- {t}" for t in fresh)

        @ctx.command("weather-ack", description="把当前预警标记为已说", usage="/weather-ack")
        async def weather_ack(caller: CallerContext, args: list[str]) -> str:
            cfg = ctx.storage.get(CFG_KEY) or {}
            spoken = ctx.storage.get(SPOKEN_KEY) or []
            fresh = await _fetch_new_warnings(ctx, cfg, spoken)
            _mark_spoken(ctx, fresh)
            return f"已标记 {len(fresh)} 条为已说。"

        @ctx.command("weather-clear", description="清空去重记录", usage="/weather-clear")
        async def weather_clear(caller: CallerContext, args: list[str]) -> str:
            ctx.storage.set(SPOKEN_KEY, [])
            return "已清空去重记录。"

        @ctx.cron("0 * * * *", name="weather-alert-tick")
        async def tick() -> None:
            """Hourly: fetch, filter to unseen, deliver to the configuring session."""
            cfg = ctx.storage.get(CFG_KEY) or {}
            if not cfg.get("lat") or not cfg.get("session_key"):
                return
            spoken = ctx.storage.get(SPOKEN_KEY) or []
            fresh = await _fetch_new_warnings(ctx, cfg, spoken)
            if not fresh:
                return
            text = "气象台有新预警，你附近可能用得上：\n" + "\n".join(f"- {t}" for t in fresh)
            try:
                await ctx.send(text, session_key=str(cfg["session_key"]))
            except (PermissionError, RuntimeError, ValueError):
                return  # 发送失败不动 spoken：下次 tick 重试同样的预警
            _mark_spoken(ctx, fresh)


async def _fetch_new_warnings(ctx: PluginContext, cfg: dict, spoken: list) -> list[str]:
    """Fetch NMC warnings for the configured province, return unseen titles."""
    province = str(cfg.get("province") or "").strip()
    if not province:
        return []
    from urllib.parse import quote

    url = f"{NMC_URL}?province={quote(province)}"
    try:
        status, body = await ctx.http.fetch(url, headers={"User-Agent": UA})
    except (PermissionError, RuntimeError, ValueError):
        return []
    if status != 200:
        return []
    payload = _load_json(body)
    if payload is None:
        return []
    seen = {str(t) for t in spoken if isinstance(t, str)}
    titles: list[str] = []
    # findAlarm 的返回形状：{"data": {"alert": [{"title"/"alertId"...}]}}；
    # 官方接口字段偶有调整，这里只取 title 类字段，取不到就当本次没有。
    data = payload.get("data") or {}
    alerts = data.get("alert") or []
    if not isinstance(alerts, list):
        return []
    for alert in alerts:
        if not isinstance(alert, dict):
            continue
        title = str(alert.get("title") or alert.get("alertDesc") or "").strip()
        if title and title not in seen:
            titles.append(title)
    return titles[:20]


def _mark_spoken(ctx: PluginContext, titles: list[str]) -> None:
    if not titles:
        return
    spoken = [t for t in (ctx.storage.get(SPOKEN_KEY) or []) if isinstance(t, str)]
    spoken = (spoken + titles)[-MAX_SPOKEN:]
    ctx.storage.set(SPOKEN_KEY, spoken)
