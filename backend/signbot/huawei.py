"""华为码道（DevCloud PromptCenter）每日签到 —— 纯 HTTP 直连。

## 协议（口径来自码道前端 bundle 反编译：index-B1jcQCDE.js 偏移 4023343 起）

    GET  {HW_BASE}/v1/ops/delivery?channel=WEB&_=<ms>   查活动列表
    POST {HW_BASE}/v1/ops/claim                        领取
         body {"campaignId":cid,"idempotentKey":"...","channel":"WEB"[,"benefitId":bid]}
    POST {HW_BASE}/v1/ops/confirm                      确认 —— 少这步不算签完
         body {"campaignId":cid}

    HW_BASE = "https://devcloud.cn-north-4.huaweicloud.com/chat/PromptCenterService"

## 三条必须遵守的业务规则

1. **campaignId 必须实时发现**。每日签到活动是 `type in ("USER_LOGIN","DAILY_CLAIM")`，
   campaignId 随活动轮次变（2026-10 起是 34，此前是 1）。写死数字会在换轮次后
   报「运营事件未生效或已结束」。
2. **状态幂等**。`ELIGIBLE → CLAIMED/CONFIRMED/CONSUMED`，已签直接跳过、不重复 claim。
   **回读状态没翻就不算成功、不写今日标记** —— 之前在这里也报 success，
   造成「软件显示已签到、码道客户端里还能领」的假成功。
3. **签到与积分是同一份 cookie**。签到走 `devcloud.cn-north-4/chat/PromptCenterService`，
   积分走同域 `devcloud.cn-north-4/chat/snap-manager/v1/statistics/plugin`。
   `codearts.huaweicloud.com/portal` 只是「打开个人用量页」的跳转链接，不是接口。
4. **有 claimActions 时必须逐个 benefit 领**（带 `benefitId`）。码道前端
   `b(O,V)` 的 idempotentKey 形如 `rank-claim-<cid>-<benefitId>-<ts>`。
   只领主活动会出现「领取 200 但状态仍 ELIGIBLE」。

## 头组是硬要求

华为网关按 UA + 一堆自定义头判断「这是不是浏览器发的 XHR」。缺任何一个就把请求
当普通页面访问，直接返回 SPA 的 HTML 而不是 JSON —— 表现为「解析失败」而不是 401，
排查时极具误导性。逐字对齐码道前端的 `lo()`：

    const xs = {"Content-Type":"application/json",
                "x-requested-with":"XMLHttpRequest",
                "x-codearts-doer-scenario-type":"web"};
    function lo(){ return {...xs, "Agent-Type":"PromptCenter", "X-Language": t}; }

其中 `cftk` 头是 CSRF 令牌，取值来自 cookie `devclouddevuibtcftk`。

## 会话短效

`devclouddevuibjJ_SESSION_ID` 静置 30~60 分钟即失效。因此本模块带一个
「今日已签」本地标记：当天签到成功后，卡片直接读标记，不再 ping 华为 ——
否则会出现「明明签成功了，但会话已经死了，卡片反而报登录态过期」。

## 与 WorkBuddy 等家的差别

华为没有「积分面额」概念上的每日固定值（活动会给 benefitAmount），
`total_points` 一律取同域 `snap-manager/v1/statistics/plugin` 的
`package_credit_remain`（真实可用余额），取不到就返回 -1（前端显示「—」），绝不编 0。
"""
import json
import os
import random
import re
import time
import urllib.error
import urllib.request
from datetime import date, datetime

TIMEOUT = 25

HW_BASE = "https://devcloud.cn-north-4.huaweicloud.com/chat/PromptCenterService"
HW_HOME = "https://devcloud.cn-north-4.huaweicloud.com/chat/home"
HW_PORTAL_BASE = "https://codearts.huaweicloud.com"

DELIVERY_PATH = "/v1/ops/delivery?channel=WEB"
CLAIM_PATH = "/v1/ops/claim"
CONFIRM_PATH = "/v1/ops/confirm"

# 积分余额：**就在签到同域**，用同一份 cookie。
# 口径来自码道前端 bundle（index-B1jcQCDE.js）里的 packageInfoStore：
#
#   async function Soe(){
#     const e = await Gt.get({url:"/snap-manager/v1/statistics/plugin"}).toPromise();
#     let t; try { t = await Gt.get({url:"/snap-manager/v1/package/overview"})... } catch {}
#     return Vkt(e, t)
#   }
#   function Vkt(e,t){ const n=e.package, r=Hkt(e.metrics); ... }
#
# 关键：`metrics[]` 来自 **statistics/plugin**（第一个请求），`package/overview`
# 只提供 `model_package`（按需 token 包）。之前只打 overview 所以永远读不到
# `usageTotalPackageCredit` —— 那个响应里根本没有 metrics 字段。
#
# metrics[] 里 name === "usageTotalPackageCredit" 的那一项：
#   package_credit_remain           可用积分（卡片主数字）
#   package_credit_used / _amount   已用 / 总额
#   package_credit_expiring_amount  未来 7 天内到期
#
# 响应是**扁平**的（没有 data 信封）：{package:{...}, metrics:[...], show:{...}}。
# 注意：`codearts.huaweicloud.com/portal` 只是「打开个人用量页」的跳转链接，
# 不是接口 —— 之前绕道去采门户 cookie 是白跑一趟（且门户未登录时只回
# 200+0 字节，永远读不到数）。
HW_CHAT_BASE = "https://devcloud.cn-north-4.huaweicloud.com/chat"
SNAP_STATS = "/snap-manager/v1/statistics/plugin"
SNAP_OVERVIEW = "/snap-manager/v1/package/overview"
CREDIT_METRIC = "usageTotalPackageCredit"

# 旧的门户路径，仅作兜底（需要单独一份 codearts cookie）。
# HW_PORTAL_BASE 在文件开头已定义（:66），这里不再重复 —— 重复定义虽然值相同，
# 但会让人误以为存在两套门户地址。
PORTAL_OVERVIEW = "/portal/snap-manager/v1/package/overview"

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/153.0.0.0 Safari/537.36 Edg/153.0.0.0")

# 状态里算「今天已经签过了」的那几个值。
#
# 码道前端 index.js 里有两套写法，都要认：
#   YMt = ["CLAIMED","CONFIRMED","CONSUMED"]         （jMt / 活动卡片判据）
#   __(I) => I==="CLAIMED"||I==="CONFIRM"||I==="CONSUME"   （Workbench 判据，单数）
# 只认复数写法时，如果服务端返单数，我们永远判不出「已签」，
# 症状就是「明明签了，软件还显示签到入口」。
DONE_STATES = ("CLAIMED", "CONFIRMED", "CONSUMED", "CONFIRM", "CONSUME")
# 每日签到活动的 type。前端 w_() 同时认大写与 snake 写法，default 也归到 daily_claim。
#
# ⚠️ `USER_LOGIN` 必须与 `DAILY_CLAIM` **分开**（v2.42 修的事故）：
# delivery 列表里「新用户登录即送 4000 积分」(USER_LOGIN) 与「每日签到领 100 积分」
# (DAILY_CLAIM) 是两条同时存在的活动。老代码把两者塞进同一个池子、谁先 ELIGIBLE
# 就返回谁 → 领的是登录礼，每日签到一动没动，回读自然还是 ELIGIBLE，
# 于是报出「领取接口返回成功，但活动状态仍是 ELIGIBLE」这种假失败（m23825 截图）。
DAILY_TYPES = ("DAILY_CLAIM", "DAILY_CHECKIN", "DAILY_CHECK_IN", "DAILY_SIGN_IN")
# 登录礼类活动：只在没有任何每日签到活动时才退而求其次（例如新号首日）。
LOGIN_TYPES = ("USER_LOGIN", "USER_LOGIN_DAILY")
# 单次 run 里最多领几个 benefit（防止 claimActions 异常长时打爆上游）
MAX_BENEFITS = 6

# 事件上报端点。码道前端进站第一件事就是 `o7t()`：
#   `POST /ops/events/trigger {triggerEvent:"user.login", channel:"WEB", idempotentKey:"login-<ymd>-<rand>"}`
# 用 localStorage["lastLoginReportDate"] 保证一天只报一次。
# 服务端靠这个事件把 `USER_LOGIN` 类活动的 `status` 从「不存在」推到 `ELIGIBLE`
# —— 我们从来不发，所以这类活动在我们眼里可能一直不可领（或领了不翻状态）。
EVENTS_PATH = "/ops/events/trigger"
LOGIN_EVENT = "user.login"

SESSION_COOKIE_NAMES = ("devclouddevuibjJ_SESSION_ID", "J_SESSION_ID")
CFTK_COOKIE = "devclouddevuibtcftk"


# ─────────────────────────── 凭据 ───────────────────────────

def _cookie_of(cfg: dict) -> str:
    """现取现用，绝不缓存常量。

    参考实现踩过这个坑：把 cookie 读成模块级常量后，「刚登录成功写回文件」
    对运行中的进程不可见，必须重启才生效。签到每次只发三四个请求，读一次
    配置的成本可以忽略。
    """
    return (cfg.get("cookie") or "").strip()


def _portal_cookie_of(cfg: dict) -> str:
    return (cfg.get("portalCookie") or "").strip()


def has_session(cookie: str) -> bool:
    """cookie 串里是否带了华为会话标识。"""
    if not cookie:
        return False
    for name in SESSION_COOKIE_NAMES:
        if name + "=" in cookie:
            return True
    return False


def has_portal_session(cookie: str) -> bool:
    """门户 cookie 里是否有真正的会话（`SessionID`）。

    不能只看「cookie 非空」：未登录访问 codearts 也会下发
    `HWWAFSESID` / `HWWAFSESTIME`（WAF 层的），而门户对未认证请求
    返回 200 + 0 字节 —— 空壳 cookie 会被误判成「会话可用」，
    症状是 portal_ok=true 但积分恒为 -1。
    参考实现（workbuddy-signin-master/huawei_login.js）的判据同样是
    `ca.some(c => c.name === 'SessionID')`。
    """
    if not cookie:
        return False
    for part in cookie.split(";"):
        name = part.strip().split("=", 1)[0].strip()
        if name == "SessionID":
            return True
    return False


def _cftk(cookie: str) -> str:
    """从 cookie 串里取 CSRF 令牌（`cftk` 头的值）。"""
    for part in cookie.split(";"):
        part = part.strip()
        if part.startswith(CFTK_COOKIE + "="):
            return part.split("=", 1)[1].strip()
    return ""


# ─────────────────────────── HTTP ───────────────────────────

def _headers(cookie: str, with_body: bool) -> dict:
    """PromptCenterService（签到）的头组。

    逐字对齐码道前端的 `lo()`（index-B1jcQCDE.js 偏移 4023343）：

        const xs = {"Content-Type":"application/json",
                    "x-requested-with":"XMLHttpRequest",
                    "x-codearts-doer-scenario-type":"web"};
        function lo(){ return {...xs, "Agent-Type":"PromptCenter", "X-Language": t}; }

    两个细节以前漏了：
    - `x-codearts-doer-scenario-type: web` —— 前端所有 PromptCenterService 请求都带；
    - 头名是 `Agent-Type`（大写 A）。HTTP 头名本不区分大小写，但华为网关
      会按原样参与风控打分，照抄前端写法最稳。
    """
    h = {
        "User-Agent": UA,
        "Agent-Type": "PromptCenter",
        "x-language": "zh-cn",
        "language": "zh-cn",
        "X-Language": "zh-cn",
        "x-requested-with": "XMLHttpRequest",
        "x-codearts-doer-scenario-type": "web",
        "accept": "application/json, text/plain, */*",
        "referer": HW_HOME,
        "Cookie": cookie,
    }
    tk = _cftk(cookie)
    if tk:
        h["cftk"] = tk
    if with_body:
        h["Content-Type"] = "application/json"
    return h


def _snap_headers(cookie: str) -> dict:
    """`/snap-manager/*` 这套接口的头组。

    和 PromptCenterService 不一样：码道前端用的是同一个 axios 实例
    （`new uIt("/chat")`），默认头是
    `{language, x-language, x-requested-with, cftk}`，拦截器再加
    `X-Language` 与 `x-codearts-doer-scenario-type: web`。
    **没有 `agent-type: PromptCenter`** —— 那个是签到接口专用的。
    """
    h = {
        "User-Agent": UA,
        "language": "zh-cn",
        "x-language": "zh-cn",
        "X-Language": "zh-cn",
        "x-requested-with": "XMLHttpRequest",
        "x-codearts-doer-scenario-type": "web",
        "accept": "application/json, text/plain, */*",
        "referer": HW_HOME,
        "Cookie": cookie,
    }
    tk = _cftk(cookie)
    if tk:
        h["cftk"] = tk
    return h


def _request(url: str, cookie: str, method: str = "GET", body=None, proxy: str = "",
             headers: dict = None):
    """返回 (http_status|None, 文本)。None 表示网络层失败。

    GET 一定带 `_=<ms>` 缓存戳：不带时华为会返回 SPA HTML 而不是 JSON，
    这是这套接口最容易被误判成「登录态过期」的坑。
    """
    if method == "GET":
        sep = "&" if "?" in url else "?"
        url = "%s%s_=%d" % (url, sep, int(time.time() * 1000))
    data = None
    if body is not None:
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(url, data=data, method=method,
                                 headers=headers or _headers(cookie, body is not None))
    # 默认**直连**：`build_opener()` 会继承系统代理（本机 7890 的 Clash 关了之后
    # 就变成 WinError 10061 拒绝连接），华为这几个域在国内直连反而更快更稳。
    # 用户显式填了 proxy 才走代理。
    if proxy:
        opener = urllib.request.build_opener(
            urllib.request.ProxyHandler({"http": proxy, "https": proxy}))
    else:
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(req, timeout=TIMEOUT) as r:
            return r.status, r.read(200_000).decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read(8000).decode("utf-8", "replace")
    except Exception as e:
        return None, "__NETERR__" + str(e)


def _api(path: str, cookie: str, method: str = "GET", body=None, proxy: str = ""):
    """调一次华为接口并解析 JSON。

    返回 (doc, err)。err 非空时 doc 为 None，err 是给人看的中文原因 ——
    不裸抛 `Expecting value: line 1 column 1`，那种报错对用户毫无意义。
    """
    status, raw = _request(HW_BASE + path, cookie, method, body, proxy)
    if status is None:
        return None, raw.replace("__NETERR__", "网络请求失败: ")
    if status in (401, 403):
        return None, ("华为登录态已失效（HTTP %d）：会话 Cookie 静置 30~60 分钟就会过期，"
                      "请重新「登录华为码道」抓一次" % status)
    text = (raw or "").strip()
    if not text:
        return None, "华为接口返回空响应（HTTP %d）：多半是登录态不完整" % status
    if text[0] not in "{[":
        return None, ("华为接口返回的不是 JSON（HTTP %d）：头组不完整或登录态失效。响应前 80 字符：%s"
                      % (status, text[:80].replace("\n", " ")))
    try:
        return json.loads(text), ""
    except Exception as e:
        return None, "华为接口返回的 JSON 解析失败: %s（前 80 字符：%s）" % (
            e, text[:80].replace("\n", " "))


# ─────────────────────────── 活动 ───────────────────────────

def find_daily(items) -> dict:
    """从活动列表里挑出**每日签到**活动。

    判据顺序（对齐码道前端 `w_()` 的活动类型归一 + 用户实机看到的列表）：

    1. **每日签到类**（`DAILY_TYPES`）优先，其次才是「登录礼」类（`LOGIN_TYPES`）。
       这一步是 v2.42 修的关键：`USER_LOGIN`（「新用户登录即送 4000 积分」）
       和 `DAILY_CLAIM`（「每日签到领 100 积分」）在 delivery 里**同时存在**，
       老代码把两者混在一个 `typed` 列表里、谁先 ELIGIBLE 就返回谁 ——
       结果是领了「登录礼」、每日签到根本没动，回读自然还是 ELIGIBLE。
       用户 m23825 的截图（「领取接口返回成功，但活动状态仍是 ELIGIBLE」）
       就是这个错位造成的假失败。
    2. 同一优先级内优先 `status == ELIGIBLE`（已领完的那项不该优先）；
    3. `campaignId == 1`（2026-10 之前的老轮次写法）；
    4. 兜底：全表只有一个可领项时才认它。
    """
    if not isinstance(items, list):
        return {}
    dicts = [x for x in items if isinstance(x, dict)]

    def pick(pool):
        for x in pool:
            if (x.get("status") or "").upper() == "ELIGIBLE":
                return x
        return pool[0] if pool else {}

    daily = [x for x in dicts
             if (x.get("type") or "").upper() in DAILY_TYPES]
    hit = pick(daily)
    if hit:
        return hit

    login = [x for x in dicts
             if (x.get("type") or "").upper() in LOGIN_TYPES]
    hit = pick(login)
    if hit:
        return hit

    for x in dicts:
        if x.get("campaignId") == 1:
            return x
    eligible = [x for x in dicts if (x.get("status") or "").upper() == "ELIGIBLE"]
    if len(eligible) == 1:
        return eligible[0]
    return {}


def find_by_id(items, cid) -> dict:
    """按 campaignId 从列表里取回**同一个**活动。

    回读必须按 id 定位，不能重新跑一遍 `find_daily`：签完之后原活动变成
    `CLAIMED`，而列表里另一个同类活动（例如「登录礼」）仍是 `ELIGIBLE`，
    重新挑就会挑到别人身上 —— 症状正是「明明签成了，却报状态仍是 ELIGIBLE」。
    """
    if not isinstance(items, list) or cid is None:
        return {}
    for x in items:
        if not isinstance(x, dict):
            continue
        if str(x.get("campaignId")) == str(cid):
            return x
    return {}


def fetch_delivery(cfg: dict):
    """查活动列表。返回 (camp, items, err)。"""
    cookie = _cookie_of(cfg)
    if not has_session(cookie):
        return {}, [], "未配置华为会话 Cookie（含 devclouddevuibjJ_SESSION_ID）"
    doc, err = _api(DELIVERY_PATH, cookie, proxy=cfg.get("proxy") or "")
    if doc is None:
        return {}, [], err
    if doc.get("code") not in (0, None):
        return {}, [], "华为活动查询失败 code=%s %s" % (
            doc.get("code"), str(doc.get("message") or "")[:120])
    items = ((doc.get("data") or {}).get("items")) or []
    camp = find_daily(items)
    if not camp:
        return {}, items, "未找到每日签到活动（活动可能已下线或换轮次）"
    return camp, items, ""


# ─────────────────────────── 今日标记 ───────────────────────────

def _mark_path() -> str:
    from .config import DATA_DIR
    return os.path.join(str(DATA_DIR), "huawei_today_done.json")


def _today() -> str:
    """今日日期，口径与 `db.today_str(8)` 一致（Asia/Shanghai）。

    以前这里用 `date.today()`（系统本地时区），与库里的 Asia/Shanghai 在
    跨零点附近会错开一天 —— 设备时区不是 UTC+8 时，「今日已签」标记会提前
    或延后失效。统一走一个来源。
    """
    try:
        from .db import today_str
        return today_str(8)
    except Exception:
        return date.today().isoformat()


def mark_today_done(message: str, amount=None):
    try:
        with open(_mark_path(), "w", encoding="utf-8") as f:
            json.dump({"date": _today(),
                       "ts": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                       "message": message, "amount": amount},
                      f, ensure_ascii=False)
    except Exception:
        pass


def today_done():
    """今天是否已经签成功过；是则返回标记 dict，否则 None（跨 0 点自动失效）。"""
    try:
        with open(_mark_path(), "r", encoding="utf-8") as f:
            d = json.load(f)
        if isinstance(d, dict) and d.get("date") == _today():
            return d
    except Exception:
        pass
    return None


def clear_today_done():
    try:
        os.remove(_mark_path())
    except Exception:
        pass


# ─────────────────────────── 积分余额 ───────────────────────────

def _num(v):
    """华为把数字写成字符串也写成数字，统一转成 int。取不到返 None。"""
    if v is None:
        return None
    try:
        return int(float(v))
    except Exception:
        return None


def _credit_from_metrics(metrics):
    """从 `metrics[]` 里挑出积分那一项并解包。

    码道前端（packageInfoStore）用的就是 `name === "usageTotalPackageCredit"`，
    其余 metrics 是 token 包（usageTokenChatMessages 之类），不能混用。
    """
    if not isinstance(metrics, list):
        return None
    for m in metrics:
        if not isinstance(m, dict):
            continue
        if (m.get("name") or "").strip() != CREDIT_METRIC:
            continue
        remain = _num(m.get("package_credit_remain"))
        if remain is None:
            # 有这一项但没余额字段 → 视为「读不到」，不能当 0
            return None
        used = _num(m.get("package_credit_used")) or 0
        amount = _num(m.get("package_credit_amount")) or 0
        exp = _num(m.get("package_credit_expiring_amount")) or 0
        return {"balance": remain, "used": used, "total": amount,
                "expiring": exp, "unit": "积分"}
    return None


def fetch_credit(cfg: dict):
    """华为码道 → 可用积分。

    **主路径就在签到同域**：`GET /chat/snap-manager/v1/statistics/plugin`，
    用的是签到那份 cookie（`devclouddevuibjJ_SESSION_ID`），不需要任何
    额外的门户会话。响应是扁平的 `{package:{...}, metrics:[...], show:{...}}`，
    在 `metrics[]` 里找 `usageTotalPackageCredit`：

        package_credit_remain           可用积分
        package_credit_used/_amount     已用 / 总额
        package_credit_expiring_amount  未来 7 天内到期

    **不能只打 `/package/overview`**：那个端点只返回 `model_package`
    （按需 token 包），没有 metrics，读积分必然是空。

    拿不到就一路降级到 codearts 门户（需要另一份 cookie，见 PORTAL_OVERVIEW），
    再拿不到返回 (None, 原因)。**绝不返回 0** —— 0 是「真的用完了」。
    """
    cookie = _cookie_of(cfg)
    proxy = (cfg.get("proxy") or "").strip()
    if not cookie:
        return None, "未配置华为会话 Cookie（devclouddevuibjJ_SESSION_ID）"

    # ── 主路径：签到同域。先 statistics/plugin（前端就是从这里取 metrics），
    #    再试 package/overview —— 万一上游哪天把 metrics 挪过去，两个都打一遍最稳。
    status, text, doc = None, "", None
    for path in (SNAP_STATS, SNAP_OVERVIEW):
        status, raw = _request(HW_CHAT_BASE + path, cookie, "GET", None, proxy,
                               headers=_snap_headers(cookie))
        if status is not None and status in (401, 403):
            return None, ("华为登录态已失效（HTTP %d）：会话 Cookie 静置 30~60 分钟就会过期，"
                          "请重新「登录华为码道」抓一次" % status)
        text = (raw or "").strip()
        doc = None
        if status is not None and text and text[0] in "{[":
            try:
                doc = json.loads(text)
            except Exception:
                doc = None
        if isinstance(doc, dict):
            credit = _credit_from_metrics(doc.get("metrics"))
            if credit:
                return credit, ""
            metrics = doc.get("metrics")
            if isinstance(metrics, list) and metrics:
                names = [str((m or {}).get("name") or "") for m in metrics if isinstance(m, dict)]
                if CREDIT_METRIC not in names:
                    return None, ("这个华为账号是 token 包（没有积分包）：%s"
                                  % ", ".join(n for n in names if n)[:80])
            elif doc.get("code") not in (None, 0):
                return None, ("码道积分接口返回 code=%s %s"
                              % (doc.get("code"), str(doc.get("message") or "")[:80]))

    # ── 兜底：codearts 门户（另一份 cookie）──
    portal = _portal_cookie_of(cfg)
    if portal and has_portal_session(portal):
        c2, err2 = _fetch_credit_portal(portal, proxy)
        if c2:
            return c2, ""
        return None, err2
    if not text:
        # 华为对「未登录 / 会话不完整」的请求回 200 + 0 字节，不是 401 ——
        # 所以空响应基本等于「这份 cookie 里没有能用的会话」。
        return None, ("码道积分接口返回空响应（HTTP %s，0 字节）：这份 Cookie 里没有可用的登录会话，"
                      "请重新「登录华为码道」采集一次" % status)
    return None, ("码道积分接口没有返回可用余额（HTTP %s，响应 %d 字节）。"
                  "会话 Cookie 静置 30~60 分钟就会过期，重新「登录华为码道」即可恢复。"
                  % (status, len(text)))


def _fetch_credit_portal(cookie: str, proxy: str = ""):
    """旧的门户口径（codearts 域，需要单独的 SessionID cookie）。仅作兜底。"""
    url = HW_PORTAL_BASE + PORTAL_OVERVIEW
    sep = "&" if "?" in url else "?"
    url = "%s%s_=%d" % (url, sep, int(time.time() * 1000))
    req = urllib.request.Request(url, headers={
        "User-Agent": UA,
        "x-requested-with": "XMLHttpRequest",
        "accept": "application/json, text/plain, */*",
        "referer": HW_PORTAL_BASE + "/portal/settings/personal-usage?locale=zh-cn",
        "Cookie": cookie,
    })
    opener = urllib.request.build_opener()
    if proxy:
        opener = urllib.request.build_opener(
            urllib.request.ProxyHandler({"http": proxy, "https": proxy}))
    else:
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(req, timeout=TIMEOUT) as r:
            raw = r.read(200_000).decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return None, "码道门户 HTTP %d（会话可能已失效）" % e.code
    except Exception as e:
        return None, "码道门户请求失败: %s" % e
    text = (raw or "").strip()
    if not text or text[0] not in "{[":
        return None, "码道门户返回的不是 JSON（会话可能已失效）"
    try:
        doc = json.loads(text)
    except Exception as e:
        return None, "码道门户 JSON 解析失败: %s" % e
    pkg = doc.get("all_credit_package") or {}
    remain = _num(pkg.get("package_credit_remain"))
    if remain is None:
        return None, "码道门户响应里没有 package_credit_remain"
    # 门户口径只给 remain / expiring，没有 used 与 total —— 一律填 -1，
    # 让前端显示「—」而不是假的 0（0 会被读成「已用 0 积分」）。
    return {"balance": remain, "used": -1, "total": -1,
            "expiring": _num(pkg.get("expiring_credit_amount")) or 0,
            "unit": "积分"}, ""


def held_credits(cfg: dict) -> int:
    """可用积分；读不到返 -1（前端显示「—」）。绝不返 0 —— 0 是「真的用完了」。"""
    c, _err = fetch_credit(cfg)
    if not c:
        return -1
    return int(c["balance"])


# ─────────────────────────── 签到 ───────────────────────────

def _ok_result(status: str, message: str, points: str = "", total: int = -1,
               detail: dict = None) -> dict:
    return {"status": status, "http_status": 200, "message": message,
            "points": points, "total_points": total,
            "detail": json.dumps(detail or {}, ensure_ascii=False)}


def trigger_login(cfg: dict) -> tuple:
    """上报一次 `user.login` 事件。返回 (ok, err)。

    码道前端每次进站都发（`o7t()`，一天一次）。不发的话，`USER_LOGIN` 类活动
    在服务端可能一直停在「未达成」，delivery 里拿到的 status 就不会是 ELIGIBLE。
    事件本身失败不该阻断签到 —— 调用方只记日志、继续往下走。
    """
    cookie = _cookie_of(cfg)
    if not has_session(cookie):
        return False, "未配置华为会话 Cookie"
    now = datetime.now()
    key = "login-%s%s%s-%s" % (now.year, "%02d" % now.month, "%02d" % now.day,
                               "".join(random.choice("abcdefghijklmnopqrstuvwxyz0123456789")
                                       for _ in range(6)))
    body = {"triggerEvent": LOGIN_EVENT, "channel": "WEB", "idempotentKey": key}
    doc, err = _api(EVENTS_PATH, cookie, method="POST", body=body,
                    proxy=cfg.get("proxy") or "")
    if doc is None:
        return False, err
    if doc.get("code") not in (0, None):
        return False, "事件上报 code=%s %s" % (
            doc.get("code"), str(doc.get("message") or "")[:120])
    return True, ""


def run(cfg: dict) -> dict:
    """执行一次签到。

    流程：本地今日标记短路 → delivery 查活动 → 已签则跳过 →
    claim → confirm → 回读 delivery 确认状态翻转 → 记标记。
    """
    cookie = _cookie_of(cfg)
    if not has_session(cookie):
        return {"status": "fail", "http_status": None,
                "message": "缺少华为会话 Cookie：点「登录华为码道」在站内登录一次即可自动采集"
                           "（Cookie 含 devclouddevuibjJ_SESSION_ID）",
                "points": "", "detail": ""}

    # 今日已签成功过 → 直接返回，不再 ping 华为。
    # 会话静置 30~60 分钟就死，重复 ping 只会把「已签到」误报成「登录态过期」。
    done = today_done()
    if done:
        held = held_credits(cfg)
        return _ok_result("already",
                          "今日已签到（本地记录 · %s）" % (done.get("message") or "已签"),
                          total=held, detail={"local_mark": done, "held": held})

    camp, items, err = fetch_delivery(cfg)
    if err:
        return {"status": "fail", "http_status": None, "message": err,
                "points": "", "detail": ""}

    # 活动清单留档：状态没翻时把它带进 detail，用户发一条红字就能看出
    # 「到底有几个活动、哪个是每日签到、type 是什么」—— 不用再靠猜。
    def _brief(lst):
        out = []
        for x in lst or []:
            if not isinstance(x, dict):
                continue
            out.append({"cid": x.get("campaignId"), "type": x.get("type"),
                        "status": x.get("status"), "amount": x.get("benefitAmount"),
                        "actions": len(x.get("claimActions") or [])})
        return out[:12]

    # 活动里没有可领项时，先补一次 `user.login` 事件再重查 —— 这是码道前端
    # 进站必做的事，不发的话 USER_LOGIN 类活动可能一直不进入 ELIGIBLE。
    if (camp.get("status") or "").upper() not in DONE_STATES and \
            (camp.get("status") or "").upper() != "ELIGIBLE":
        ev_ok, ev_err = trigger_login(cfg)
        if ev_ok:
            camp2_try, _it, err_try = fetch_delivery(cfg)
            if not err_try and camp2_try:
                camp = camp2_try

    cid = camp.get("campaignId")
    if cid is None:
        return {"status": "fail", "http_status": None,
                "message": "每日签到活动缺少 campaignId，无法领取",
                "points": "", "detail": json.dumps({"activities": _brief(items)},
                                                  ensure_ascii=False)}

    amount = camp.get("benefitAmount")
    unit = camp.get("benefitUnit") or "CREDIT"
    unit_cn = "积分" if unit == "CREDIT" else unit
    state = (camp.get("status") or "").upper()

    if state in DONE_STATES:
        held = held_credits(cfg)
        msg = "今日已签到（%s %s）" % (amount if amount is not None else "?", unit_cn)
        mark_today_done("幂等跳过：今日已签", amount)
        return _ok_result("already", msg, total=held,
                          detail={"campaign_id": cid, "status": state, "held": held})

    # 空 status 视为「可领」（服务端偶尔不下发 status）——
    # 所以这里用 bool(state) 而不是裸 `state and ...`，让类型明确是 bool。
    if camp.get("claimable") is False and bool(state) and state != "ELIGIBLE":
        return {"status": "fail", "http_status": None,
                "message": "签到活动当前不可领取（status=%s）" % (state or "?"),
                "points": "", "detail": json.dumps(camp, ensure_ascii=False)[:400]}

    proxy = cfg.get("proxy") or ""
    ts = int(time.time() * 1000)

    # 领取目标：有 claimActions 的活动必须逐个 benefit 领（码道前端 b(O,V) 就是这么做的，
    # idempotentKey 形如 `rank-claim-<cid>-<benefitId>-<ts>`）；没有就整活动领一次。
    # 只领一次主活动时，若服务端把「每日签到」拆成了子 benefit，会出现
    # 「领取接口 200 但状态仍 ELIGIBLE」—— 这正是之前「显示签到成功、实际没签到」的成因。
    targets = []
    acts = camp.get("claimActions")
    if isinstance(acts, list):
        for a in acts:
            if not isinstance(a, dict):
                continue
            bid = a.get("benefitId")
            if bid is None:
                continue
            st = (a.get("status") or "").upper()
            if st and st != "ELIGIBLE":
                continue
            targets.append(bid)
            if len(targets) >= MAX_BENEFITS:
                break

    # 原始响应留档：状态没翻时把 claim/confirm 的原文带进 detail，
    # 用户只要把那条红字发来就能定位（否则只能靠猜）。
    raw_claim = []
    raw_confirm = None
    claimed = []
    if targets:
        for bid in targets:
            body = {"campaignId": cid,
                    "idempotentKey": "claim_%s_%s_%d" % (cid, bid, ts),
                    "channel": "WEB", "benefitId": bid}
            doc, err = _api(CLAIM_PATH, cookie, method="POST", body=body, proxy=proxy)
            if doc is None:
                return {"status": "fail", "http_status": None,
                        "message": "领取失败（benefitId=%s）：%s" % (bid, err),
                        "points": "", "detail": json.dumps(
                            {"campaign_id": cid, "benefit": bid, "claim_raw": raw_claim,
                             "error": err}, ensure_ascii=False)}
            raw_claim.append({"benefit": bid, "resp": doc})
            if doc.get("code") not in (0, None):
                return {"status": "fail", "http_status": None,
                        "message": "领取失败 code=%s %s（benefitId=%s）" % (
                            doc.get("code"), str(doc.get("message") or "")[:140], bid),
                        "points": "", "detail": json.dumps(doc, ensure_ascii=False)[:400]}
            claimed.append(bid)
    else:
        claim_body = {"campaignId": cid,
                      "idempotentKey": "claim_%s_%d" % (cid, ts),
                      "channel": "WEB"}
        doc, err = _api(CLAIM_PATH, cookie, method="POST", body=claim_body, proxy=proxy)
        if doc is None:
            return {"status": "fail", "http_status": None, "message": "领取失败：" + err,
                    "points": "", "detail": ""}
        raw_claim.append({"benefit": None, "resp": doc})
        if doc.get("code") not in (0, None):
            return {"status": "fail", "http_status": None,
                    "message": "领取失败 code=%s %s" % (
                        doc.get("code"), str(doc.get("message") or "")[:140]),
                    "points": "", "detail": json.dumps(doc, ensure_ascii=False)[:400]}

    doc2, err2 = _api(CONFIRM_PATH, cookie, method="POST",
                      body={"campaignId": cid}, proxy=proxy)
    raw_confirm = doc2 if doc2 is not None else {"error": err2}
    if doc2 is None:
        # claim 成功但 confirm 失败：上游已经把状态推进到 CLAIMED，钱通常已经到账，
        # 但按协议「少 confirm 不算签完」，所以这里报 fail 让用户看到，同时不写今日标记
        # —— 下一次会自动重试 confirm（claim 是幂等的，会返回已领）。
        return {"status": "fail", "http_status": None,
                "message": "已领取但确认失败（下次会自动补确认）：" + err2,
                "points": "", "detail": ""}
    if doc2.get("code") not in (0, None):
        return {"status": "fail", "http_status": None,
                "message": "签到确认失败 code=%s %s" % (
                    doc2.get("code"), str(doc2.get("message") or "")[:140]),
                "points": "", "detail": ""}

    # 只信回读：confirm 之后重新查一次，确认状态真的翻转了才记成功。
    # **状态没翻就不算成功** —— 之前在这里也返回 success 并写今日标记，
    # 结果「软件显示已签到、码道客户端里还能领」，这是最误导人的一种假成功。
    #
    # 回读必须按 campaignId 定位同一个活动（`find_by_id`），不能重新 `find_daily`：
    # 签完之后原活动变成 CLAIMED，而列表里另一个同类活动（登录礼 / 学生认证）
    # 仍是 ELIGIBLE，重挑就会挑到别人身上，照样报「状态仍是 ELIGIBLE」。
    camp2, items2, err3 = fetch_delivery(cfg)
    same = find_by_id(items2, cid)
    state2 = (same.get("status") or camp2.get("status") or "").upper()
    held = held_credits(cfg)
    gained = ("+%s" % amount) if amount else ""
    detail = {"campaign_id": cid, "camp_type": camp.get("type"), "amount": amount,
              "unit": unit, "state_after": state2, "held": held,
              "benefits": claimed, "claim_raw": raw_claim, "confirm_raw": raw_confirm,
              "activities_before": _brief(items), "activities_after": _brief(items2)}
    if state2 in DONE_STATES:
        msg = "签到成功 %s%s" % (gained, unit_cn)
        mark_today_done(msg, amount)
        return _ok_result("success", msg, points=gained, total=held, detail=detail)
    if err3:
        # 回读失败但 claim+confirm 都 200：无法证实，也不写今日标记，下次重试。
        return {"status": "fail", "http_status": None,
                "message": "领取与确认都返回成功，但回读失败无法证实：%s" % err3[:80],
                "points": "", "detail": json.dumps(detail, ensure_ascii=False)}
    return {"status": "fail", "http_status": None,
            "message": "领取接口返回成功，但活动状态仍是 %s（未到账）。"
                       "活动 type=%s、campaignId=%s。"
                       "若反复出现请把这条发给我" % (
                           state2 or "未知", camp.get("type") or "?", cid),
            "points": "", "detail": json.dumps(detail, ensure_ascii=False)}


# ─────────────────────────── 体检 ───────────────────────────

def quick(cfg: dict) -> dict:
    """只查本地凭据，不联网。给 /api/status 的 light 模式用。"""
    cookie = _cookie_of(cfg)
    ok = has_session(cookie)
    return {
        "token_ok": ok,
        "token_len": len(cookie),
        "has_cftk": bool(_cftk(cookie)),
        # 积分接口就在签到同域，所以「会话可用」= 「积分可读」。
        # 保留 portal_ok 这个字段名是为了不动前端与安卓端的判据。
        "portal_ok": ok,
        "source": "config",
        "reachable": None,
        "issues": [] if ok else ["缺少华为会话 Cookie：点「登录华为码道」在站内登录一次"],
        "light": True,
    }


def probe(cfg: dict) -> dict:
    """只读体检：会话是否可用、今日是否已签、余额能否读到。"""
    cookie = _cookie_of(cfg)
    out = {
        "token_ok": has_session(cookie),
        "token_len": len(cookie),
        "has_cftk": bool(_cftk(cookie)),
        # 积分接口与签到同域（/chat/snap-manager/v1/statistics/plugin），
        # 所以只要会话在，积分就能读 —— 不再需要额外的门户 cookie。
        "portal_ok": has_session(cookie),
        "source": "config",
        "reachable": False,
        "checked_in": False,
        "issues": [],
    }
    if not out["token_ok"]:
        out["issues"].append("缺少华为会话 Cookie：点「登录华为码道」在站内登录一次")
        return out
    if not out["has_cftk"]:
        out["issues"].append("Cookie 里没有 devclouddevuibtcftk（CSRF 令牌），"
                             "领取会被拒 —— 请重新登录一次完整采集")
    done = today_done()
    if done:
        out["reachable"] = True
        out["checked_in"] = True
        out["today"] = done
        out["credit"] = _safe_credit(cfg)
        return out
    camp, _items, err = fetch_delivery(cfg)
    if err:
        out["issues"].append(err)
        return out
    out["reachable"] = True
    state = (camp.get("status") or "").upper()
    out["checked_in"] = state in DONE_STATES
    out["campaign_id"] = camp.get("campaignId")
    out["campaign_status"] = state
    out["amount"] = camp.get("benefitAmount")
    out["credit"] = _safe_credit(cfg)
    return out


def _safe_credit(cfg: dict):
    c, err = fetch_credit(cfg)
    if c is None:
        return {"ok": False, "error": err}
    return {"ok": True, **c}
