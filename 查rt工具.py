# -*- coding: utf-8 -*-
"""
查rt工具 v6

1. 牌谱屋接口按昵称搜玩家 ID（纯 HTTP）
2. 一个 Chrome 打开牌谱屋玩家页，读最近 N 局的牌谱 ID
3. 已查过的牌谱直接用本地缓存 cache/reports.jsonl
4. 没查过的：同一个 mjai 表单页里循环提交（等 Turnstile token -> 页内 POST -> 重置 Turnstile）
5. 直接下载 report JSON 读 rating 和 AI 一致率，不打开 KillerDucky
"""

import argparse
import csv
import json
import math
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter, deque
from pathlib import Path

import undetected_chromedriver as uc
from selenium.common.exceptions import TimeoutException, WebDriverException


VERSION = "v6.2"
BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
PROFILE_DIR = DATA_DIR / "chrome_profile"
CACHE_FILE = DATA_DIR / "reports.jsonl"  # 按 牌谱ID+模型 缓存的分析结果
HISTORY_FILE = DATA_DIR / "history.jsonl"  # 每次查询一行，rt结果.xlsx 由它生成
EXCEL_FILE = BASE_DIR / "rt结果.xlsx"

AMAE_SITE = "https://amae-koromo.sapk.ch"
AMAE_API = "https://5-data.amae-koromo.com/api/v2/pl4"
MJAI = "https://mjai.ekyu.moe"
MJAI_FORM = f"{MJAI}/zh-cn.html"
ROOM_NAMES = {"9": "金之间", "12": "玉之间", "16": "王座之间"}
UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"
)
REPORT_RE = re.compile(r"/report/[0-9a-f]+\.json")
DEFAULT_PARALLEL = 1


# ---------------------------------------------------------------- HTTP

def http_get_json(url, timeout=30):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def search_player(nickname):
    url = f"{AMAE_API}/search_player/{urllib.parse.quote(nickname)}?limit=10"
    players = http_get_json(url)
    exact = [p for p in players if p.get("nickname") == nickname]
    if len(exact) == 1:
        return str(exact[0]["id"])
    if not players:
        return None
    print("[player] 找到多个玩家，请用 --player-id 指定：")
    for p in players:
        print(f"  id={p.get('id')}  {p.get('nickname')}")
    return None


def encode_account_id(player_id):
    """牌谱链接末尾 _a 后面的视角编码。"""
    return ((7 * int(player_id) + 1117113) ^ 86216345) + 1358437


def player_id_from_paipu(paipu):
    """encode_account_id 的反运算：从 ..._a217758789 里取回玩家 ID。"""
    match = re.search(r"_a(\d+)$", paipu or "")
    if not match:
        return None
    value = ((int(match.group(1)) - 1358437) ^ 86216345) - 1117113
    return str(value // 7) if value > 0 and value % 7 == 0 else None


# ---------------------------------------------------------------- cache

class ReportCache:
    def __init__(self, path):
        self.path = path
        self.items = {}
        if path.exists():
            for line in path.read_text(encoding="utf-8").splitlines():
                try:
                    item = json.loads(line)
                    self.items[item["key"]] = item
                except (ValueError, KeyError):
                    continue

    @staticmethod
    def key(paipu, model):
        return f"{paipu}|{model}"

    def get(self, paipu, model):
        return self.items.get(self.key(paipu, model))

    def put(self, paipu, model, result):
        item = dict(result, key=self.key(paipu, model), paipu=paipu, model=model,
                    saved_at=time.strftime("%Y-%m-%d %H:%M:%S"))
        self.items[item["key"]] = item
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(item, ensure_ascii=False) + "\n")


# ---------------------------------------------------------------- browser

def get_chrome_major_version():
    if not sys.platform.startswith("win"):
        return None
    try:
        import winreg

        for hive, key_path in [
            (winreg.HKEY_CURRENT_USER, r"Software\Google\Chrome\BLBeacon"),
            (winreg.HKEY_LOCAL_MACHINE, r"Software\Google\Chrome\BLBeacon"),
            (winreg.HKEY_LOCAL_MACHINE, r"Software\WOW6432Node\Google\Chrome\BLBeacon"),
        ]:
            try:
                with winreg.OpenKey(hive, key_path) as key:
                    version, _ = winreg.QueryValueEx(key, "version")
                match = re.match(r"(\d+)\.", str(version))
                if match:
                    return int(match.group(1))
            except OSError:
                continue
    except ImportError:
        pass
    for base in ("PROGRAMFILES", "PROGRAMFILES(X86)", "LOCALAPPDATA"):
        exe = Path(os.environ.get(base, "")) / "Google/Chrome/Application/chrome.exe"
        if not exe.exists():
            continue
        try:
            out = subprocess.check_output([str(exe), "--version"], text=True, timeout=5)
            match = re.search(r"(\d+)\.", out)
            if match:
                return int(match.group(1))
        except Exception:
            continue
    return None


def build_driver():
    PROFILE_DIR.mkdir(parents=True, exist_ok=True)
    options = uc.ChromeOptions()
    options.add_argument(f"--user-data-dir={PROFILE_DIR}")
    options.add_argument("--profile-directory=Default")
    options.add_argument("--lang=zh-CN")
    options.add_argument("--no-first-run")
    options.add_argument("--window-size=1100,800")
    major = get_chrome_major_version()
    last_exc = None
    for attempt in range(1, 4):
        try:
            if major:
                driver = uc.Chrome(options=options, use_subprocess=True, version_main=major)
            else:
                driver = uc.Chrome(options=options, use_subprocess=True)
            driver.set_page_load_timeout(60)
            driver.set_script_timeout(240)
            return driver
        except Exception as exc:
            last_exc = exc
            print(f"[warn] Chrome 启动失败 {attempt}/3: {exc.__class__.__name__}")
            time.sleep(3 * attempt)
    raise last_exc


def open_page(driver, url):
    try:
        driver.get(url)
    except TimeoutException:
        driver.execute_script("window.stop();")


def beep():
    try:
        import winsound

        winsound.MessageBeep()
    except Exception:
        print("\a", end="", flush=True)


def wait_until(driver, js, what, prompt_after=20):
    """轮询 js 直到返回真值。超过 prompt_after 秒就提示手动处理，然后一直等。"""
    started = time.time()
    prompted = False
    while True:
        try:
            value = driver.execute_script(js)
        except WebDriverException:
            value = None
        if value:
            return value
        if not prompted and time.time() - started > prompt_after:
            beep()
            print(f"[wait] {what} 还没好。如果浏览器里有人机验证，请手动点一下；程序会一直等。")
            prompted = True
        time.sleep(0.5)


# ---------------------------------------------------------------- 牌谱屋

JS_AMAE_ROWS = """
return Array.from(document.querySelectorAll('div[aria-rowindex]')).map(r => {
  const a = r.querySelector('a[href*="mjai.ekyu.moe"]');
  return [parseInt(r.getAttribute('aria-rowindex')), a ? a.href : null];
});
"""


def paipu_from_mjai_href(href):
    """https://mjai.ekyu.moe/zh-cn.html?url=https%3A...%3Fpaipu%3D<paipu> -> <paipu>"""
    target = urllib.parse.parse_qs(urllib.parse.urlparse(href).query).get("url", [""])[0]
    return urllib.parse.parse_qs(urllib.parse.urlparse(target).query).get("paipu", [None])[0]


def fetch_recent_paipu(driver, player_id, room, games):
    url = f"{AMAE_SITE}/player/{player_id}/{room}"
    print(f"[牌谱屋] 打开 {url}")
    open_page(driver, url)
    wait_until(driver, "return document.querySelector('div[aria-rowindex] a[href*=\"mjai.ekyu.moe\"]') !== null",
               "牌谱屋对局列表")
    found = {}
    stale = 0
    while len(found) < games and stale < 12:
        before = len(found)
        for idx, href in driver.execute_script(JS_AMAE_ROWS) or []:
            if href and idx not in found:
                paipu = paipu_from_mjai_href(href)
                if paipu:
                    found[idx] = paipu
        stale = stale + 1 if len(found) == before else 0
        if len(found) >= games:
            break
        driver.execute_script("window.scrollBy(0, 800);")
        time.sleep(0.4)
    paipus = [found[i] for i in sorted(found)][:games]
    print(f"[牌谱屋] 拿到 {len(paipus)}/{games} 局")
    return paipus


# ---------------------------------------------------------------- mjai

JS_TOKEN = """
const f = document.forms.reviewForm;
const inp = f && f.querySelector('input[name="cf-turnstile-response"]');
return inp ? inp.value : '';
"""

JS_START_SUBMIT = """
const [jobId, logUrl, model, timeoutSec] = arguments;
const f = document.forms.reviewForm;
const radio = f.querySelector('input[name="input-method"][value="log-url"]');
if (radio) radio.checked = true;
f.elements['log-url'].value = logUrl;
f.elements['mortal-model-tag'].value = model;
f.elements['player-id'].value = '';
f.elements['ui'].value = 'killerducky';
const body = new URLSearchParams(new FormData(f));
window.__rtJobs = window.__rtJobs || {};
window.__rtJobs[jobId] = null;
const ctl = new AbortController();
setTimeout(() => ctl.abort(), timeoutSec * 1000);
fetch(f.action, {method: 'POST', body: body, signal: ctl.signal, credentials: 'same-origin'})
  .then(async r => {
    let text = '';
    try { text = (await r.text()).slice(0, 3000); } catch (e) {}
    window.__rtJobs[jobId] = {status: r.status, url: r.url, text: text, retry: r.headers.get('retry-after')};
  })
  .catch(e => { window.__rtJobs[jobId] = {status: 0, url: '', text: String(e)}; });
return true;
"""

JS_TAKE_FINISHED = """
const jobs = window.__rtJobs || {};
const out = {};
for (const k of Object.keys(jobs)) {
  if (jobs[k] !== null) { out[k] = jobs[k]; delete jobs[k]; }
}
return out;
"""

JS_RESET = """
try {
  const w = document.querySelector('form[name="reviewForm"] .cf-turnstile');
  window.turnstile.reset(w);
  return true;
} catch (e) { return String(e); }
"""

JS_FETCH_JSON = """
const [path, done] = arguments;
fetch(path, {credentials: 'same-origin'})
  .then(r => r.text()).then(t => done(t)).catch(e => done('ERROR ' + e));
"""

TASK_RE = re.compile(r"/progress\?task=([0-9a-zA-Z]+)")


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


_no_redirect_opener = urllib.request.build_opener(_NoRedirect)


def check_task(task_id):
    """查一次排队页（不跟随跳转）。返回 ("done", 报告路径) / ("running", None) / ("limited", None)。"""
    req = urllib.request.Request(f"{MJAI}/progress?task={task_id}", headers={"User-Agent": UA})
    try:
        with _no_redirect_opener.open(req, timeout=30) as resp:
            status, location = resp.status, resp.headers.get("Location", "")
    except urllib.error.HTTPError as exc:
        status, location = exc.code, exc.headers.get("Location", "") if exc.headers else ""
    if 300 <= status < 400:
        match = REPORT_RE.search(urllib.parse.unquote(location))
        if match:
            return "done", match.group(0)
        raise RuntimeError(f"排队页跳到了意外地址：{location}")
    if status == 200:
        return "running", None
    if status == 429:
        return "limited", None
    raise RuntimeError(f"排队页返回 HTTP {status}")


def fetch_report_http(path):
    req = urllib.request.Request(MJAI + path, headers={"User-Agent": UA, "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=60) as resp:
        return json.loads(resp.read().decode("utf-8"))


class MjaiRunner:
    """一个 mjai 表单页里并行处理多局。

    同时最多 parallel 局在 mjai 上（提交中 + 服务器分析中）。每局只建一个分析任务：
    POST 结果不确定（网络错误、超时）时宁可报错也不重提；只有确定没建成任务（限流、token 无效）才重试。
    遇到限流就把并行数减 1，按 Retry-After 暂停；没有 Retry-After 就 3s、8s、20s、60s 逐步加长，
    下一次提交用新的 Turnstile token（不刷新整页）。
    """

    RATE_LIMIT_WAITS = (3, 8, 20, 60)

    POLL_SECONDS = 1.5
    TASK_TIMEOUT = 300

    def __init__(self, driver, model, parallel):
        self.driver = driver
        self.model = model
        self.limit = max(1, parallel)
        self.form_ready = False
        self.last_token = None
        self.token_wait_since = None
        self.prompted = False
        self.pause_until = 0.0
        self.limited_in_row = 0
        self.rate_limited = 0
        self.job_counter = 0

    # ---- 表单页 / Turnstile

    def open_form(self):
        open_page(self.driver, MJAI_FORM)
        wait_until(self.driver, "return !!(document.forms.reviewForm)", "mjai 页面")
        self.form_ready = True
        self.last_token = None

    def new_token(self):
        """有新 token 就返回，否则返回 None（不阻塞）。"""
        token = self.driver.execute_script(JS_TOKEN)
        if token and token != self.last_token:
            self.token_wait_since = None
            self.prompted = False
            return token
        now = time.time()
        if self.token_wait_since is None:
            self.token_wait_since = now
        elif not self.prompted and now - self.token_wait_since > 20:
            beep()
            print("[wait] Turnstile 还没通过。如果浏览器里出现验证框，请手动点一下；程序会一直等。")
            self.prompted = True
        return None

    def reset_token(self):
        result = self.driver.execute_script(JS_RESET)
        if result is not True:
            print(f"  [turnstile] 重置失败（{result}），等手上的提交完成后重新打开表单页")
            self.form_ready = False

    def rate_limit_hit(self, why, retry_after=None):
        self.rate_limited += 1
        old = self.limit
        self.limit = max(1, self.limit - 1)
        try:
            wait = min(max(float(retry_after), 1), 120)
            source = "Retry-After"
        except (TypeError, ValueError):
            wait = self.RATE_LIMIT_WAITS[min(self.limited_in_row, len(self.RATE_LIMIT_WAITS) - 1)]
            source = f"第 {self.limited_in_row + 1} 次连续限流"
        self.limited_in_row += 1
        self.pause_until = time.time() + wait
        print(f"  [限流] {why}：并行 {old} -> {self.limit}，暂停 {wait:.0f}s（{source}）")

    def fetch_report(self, path):
        try:
            return fetch_report_http(path)
        except (urllib.error.URLError, ValueError, OSError) as exc:
            print(f"  [report] 直接下载失败（{exc.__class__.__name__}），改用浏览器下载")
        return json.loads(self.driver.execute_async_script(JS_FETCH_JSON, path))

    # ---- 主循环

    def run(self, jobs, on_done):
        """jobs: [(idx, paipu)]；每局结束调用 on_done(idx, paipu, result 或 None, 错误信息)。"""
        queue = deque(jobs)
        attempts = Counter()
        posting = {}  # job_id -> (idx, paipu, t_post)
        tasks = {}  # task_id -> [idx, paipu, t_post, t_task, next_poll]

        def finish(idx, paipu, path, t_post):
            try:
                result = parse_report(self.fetch_report(path))
                result["report_url"] = f"{MJAI}/killerducky/?data={path}"
                on_done(idx, paipu, result, None, time.time() - t_post)
            except Exception as exc:
                on_done(idx, paipu, None, f"读报告失败: {exc}", time.time() - t_post)

        while queue or posting or tasks:
            now = time.time()

            # 1) 有空位就提交下一局
            if queue and len(posting) + len(tasks) < self.limit and now >= self.pause_until:
                if not self.form_ready:
                    if not posting:
                        self.open_form()
                else:
                    token = self.new_token()
                    if token:
                        idx, paipu = queue.popleft()
                        attempts[paipu] += 1
                        self.job_counter += 1
                        job_id = str(self.job_counter)
                        self.last_token = token
                        self.driver.execute_script(
                            JS_START_SUBMIT, job_id, f"https://game.maj-soul.com/1/?paipu={paipu}", self.model, 180
                        )
                        posting[job_id] = (idx, paipu, time.time())
                        print(f"[#{idx}] 提交（同时进行 {len(posting) + len(tasks)}/{self.limit}）")
                        self.reset_token()

            # 2) 收提交结果
            if posting:
                for job_id, res in (self.driver.execute_script(JS_TAKE_FINISHED) or {}).items():
                    idx, paipu, t_post = posting.pop(job_id)
                    status, url, text = res.get("status"), res.get("url") or "", res.get("text") or ""
                    lowered = text.lower()
                    task = TASK_RE.search(url)
                    report = REPORT_RE.search(urllib.parse.unquote(url))
                    if report or task:
                        self.limited_in_row = 0
                    if report:
                        finish(idx, paipu, report.group(0), t_post)
                    elif task:
                        tasks[task.group(1)] = [idx, paipu, t_post, time.time(), time.time() + self.POLL_SECONDS]
                    elif status == 429 or "1015" in lowered or "rate limit" in lowered:
                        self.rate_limit_hit(f"#{idx} 提交 HTTP {status}", res.get("retry"))
                        if attempts[paipu] < 4:
                            queue.appendleft((idx, paipu))
                        else:
                            on_done(idx, paipu, None, "多次被限流", time.time() - t_post)
                    elif status == 403 and "captcha" in lowered:
                        print(f"  [turnstile] #{idx} token 无效，重新排队")
                        if attempts[paipu] < 4:
                            queue.appendleft((idx, paipu))
                        else:
                            on_done(idx, paipu, None, "Turnstile 多次无效", time.time() - t_post)
                    else:
                        snippet = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", text))[:200]
                        on_done(idx, paipu, None, f"提交结果不明（不重提）：HTTP {status} {url} {snippet}",
                                time.time() - t_post)

            # 3) 查服务器分析进度（纯 HTTP，不开页面）
            for task_id, item in list(tasks.items()):
                idx, paipu, t_post, t_task, next_poll = item
                if time.time() < next_poll:
                    continue
                try:
                    state, path = check_task(task_id)
                except Exception as exc:
                    tasks.pop(task_id)
                    on_done(idx, paipu, None, str(exc), time.time() - t_post)
                    continue
                if state == "done":
                    tasks.pop(task_id)
                    print(f"  [#{idx}] 提交 {t_task - t_post:.1f}s，分析 {time.time() - t_task:.1f}s")
                    finish(idx, paipu, path, t_post)
                elif time.time() - t_task > self.TASK_TIMEOUT:
                    tasks.pop(task_id)
                    on_done(idx, paipu, None, f"分析超过 {self.TASK_TIMEOUT}s 没完成（task={task_id}）",
                            time.time() - t_post)
                else:
                    item[4] = time.time() + (10 if state == "limited" else self.POLL_SECONDS)

            time.sleep(0.2)


def parse_report(report):
    review = report.get("review") or {}
    total = review.get("total_reviewed") or 0
    matches = review.get("total_matches") or 0
    rating = review.get("rating")
    if rating is None or not total:
        raise ValueError("report JSON 里没有 rating")
    return {
        "rating": round(rating * 100, 1),
        "ai_consistency": round(matches / total * 100, 1),
        "matches": f"{matches}/{total}",
    }


# ---------------------------------------------------------------- 历史记录 / Excel

def append_history(record):
    HISTORY_FILE.parent.mkdir(parents=True, exist_ok=True)
    with HISTORY_FILE.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


def load_history():
    if not HISTORY_FILE.exists():
        return []
    runs = []
    for line in HISTORY_FILE.read_text(encoding="utf-8").splitlines():
        try:
            runs.append(json.loads(line))
        except ValueError:
            continue
    return runs


def known_player(nickname=None, player_id=None):
    """从查询记录里找 昵称 <-> 玩家ID，牌谱屋搜玩家被限流时也能用。"""
    for run in reversed(load_history()):
        if not run.get("player_id") or str(run.get("player")) == str(run.get("player_id")):
            continue
        if nickname and run.get("player") == nickname:
            return run["player"], str(run["player_id"])
        if player_id and str(run.get("player_id")) == str(player_id):
            return run["player"], str(run["player_id"])
    return None, None


def game_date(paipu):
    """牌谱 ID 开头是 yymmdd。"""
    match = re.match(r"(\d{2})(\d{2})(\d{2})-", paipu or "")
    return f"20{match.group(1)}-{match.group(2)}-{match.group(3)}" if match else ""


def mean(values):
    values = [v for v in values if isinstance(v, (int, float))]
    return round(sum(values) / len(values), 2) if values else None


def export_excel():
    """用 data/history.jsonl 重新生成 rt结果.xlsx。返回是否成功。"""
    try:
        from openpyxl import Workbook
        from openpyxl.styles import Font
    except ImportError:
        print("[excel] 没装 openpyxl，跳过 Excel（pip install openpyxl）")
        return False

    runs = sorted(load_history(), key=lambda r: r.get("time", ""))
    names = {str(r["player_id"]): r["player"] for r in runs
             if r.get("player_id") and str(r.get("player")) != str(r["player_id"])}
    for run in runs:  # 用 --player-id 查的记录补上昵称
        run["player"] = names.get(str(run.get("player_id")), run.get("player"))
    games = {}
    for run in runs:
        for row in run.get("rows", []):
            if not isinstance(row.get("rating"), (int, float)):
                continue
            key = (run.get("player_id") or run.get("player"), run.get("room"), row.get("paipu"))
            games[key] = (run, row)  # 后面的查询覆盖前面的

    wb = Workbook()
    ws = wb.active
    ws.title = "对局"
    ws.append(["玩家", "房间", "对局日期", "rating", "一致率%", "一致步数", "牌谱ID", "报告链接", "查询时间", "来源"])
    ordered = sorted(games.values(), key=lambda item: (item[0].get("player", ""), item[0].get("room", ""),
                                                       game_date(item[1].get("paipu"))), reverse=True)
    ordered.sort(key=lambda item: (item[0].get("player", ""), item[0].get("room", "")))
    for run, row in ordered:
        ws.append([run.get("player"), run.get("room"), game_date(row.get("paipu")), row.get("rating"),
                   row.get("ai_consistency"), row.get("matches"), row.get("paipu"), row.get("report_url") or "",
                   run.get("time"), run.get("source")])
        url = row.get("report_url")
        if url:
            cell = ws.cell(row=ws.max_row, column=8)
            cell.hyperlink = url
            cell.font = Font(color="0563C1", underline="single")

    ws2 = wb.create_sheet("查询记录")
    ws2.append(["查询时间", "玩家", "房间", "局数", "成功", "rating均值", "一致率均值%", "来源"])
    for run in reversed(runs):
        ok = [r for r in run.get("rows", []) if isinstance(r.get("rating"), (int, float))]
        ws2.append([run.get("time"), run.get("player"), run.get("room"), run.get("games"), len(ok),
                    mean([r["rating"] for r in ok]), mean([r.get("ai_consistency") for r in ok]), run.get("source")])

    widths = {"对局": [16, 9, 12, 8, 9, 10, 50, 60, 18, 12], "查询记录": [18, 16, 9, 6, 6, 11, 12, 12]}
    for sheet in (ws, ws2):
        for cell in sheet[1]:
            cell.font = Font(bold=True)
        sheet.freeze_panes = "A2"
        sheet.auto_filter.ref = sheet.dimensions
        for i, width in enumerate(widths[sheet.title]):
            sheet.column_dimensions[chr(ord("A") + i)].width = width

    add_chart_sheets(wb, games, runs)

    try:
        wb.save(EXCEL_FILE)
    except PermissionError:
        print(f"[excel] {EXCEL_FILE.name} 正在被 Excel 打开，没法更新。关掉后运行：python 查rt工具.py --export")
        return False
    print(f"[excel] 已更新 {EXCEL_FILE}（{len(games)} 局，{len(runs)} 次查询）")
    return True


TREND_WINDOW = 10  # 均线用最近几局
RANK_MIN_GAMES = 10  # 排行图里至少要有几局


def axis_bounds(values, step):
    """图表纵轴范围：避免 Excel 自动从 0 开始；下限取第 2 百分位，个别极低的局不把整张图压扁。"""
    ordered = sorted(values)
    low = ordered[int(len(ordered) * 0.02)]
    return math.floor(low / step) * step, math.ceil(ordered[-1] / step) * step


def add_chart_sheets(wb, games, runs):
    """「趋势」页（下拉选玩家+房间，图跟着变）和「排行」页。只用 INDEX/MATCH，旧版 Excel 也能算。"""
    from openpyxl.chart import BarChart, Reference, ScatterChart, Series
    from openpyxl.chart.shapes import GraphicalProperties
    from openpyxl.formatting.rule import FormulaRule
    from openpyxl.styles import Font
    from openpyxl.worksheet.datavalidation import DataValidation

    if not games:
        return
    # 每个 玩家+房间 的对局按时间从早到晚排；同一天的按查询里的顺序（牌谱屋列表是新的在前）
    groups = {}
    for run, row in games.values():
        pos = next(i for i, r in enumerate(run["rows"]) if r is row)
        name = f"{run.get('player')} | {run.get('room')}"
        groups.setdefault(name, []).append((game_date(row.get("paipu")), run.get("time", ""), -pos, row))
    for items in groups.values():
        items.sort(key=lambda item: item[:3])

    # 隐藏的「图表数据」页：每组连续几行，趋势页用 起始行+局数 去取
    data = wb.create_sheet("图表数据")
    data.append(["玩家 | 房间", "第几局", "对局日期", "rating", "一致率%"])
    stats = []
    for name, items in groups.items():
        start = data.max_row + 1
        for i, (date, _, _, row) in enumerate(items, 1):
            data.append([name, i, date, row["rating"], row.get("ai_consistency")])
        ratings = [item[3]["rating"] for item in items]
        cons = [item[3].get("ai_consistency") for item in items if isinstance(item[3].get("ai_consistency"), (int, float))]
        player, room = name.rsplit(" | ", 1)
        stats.append({"name": name, "player": player, "room": room, "n": len(items), "start": start,
                      "rating": mean(ratings), "cons": mean(cons), "recent": mean(ratings[-TREND_WINDOW:]),
                      "max": max(ratings), "min": min(ratings), "last": items[-1][0]})
    data.sheet_state = "hidden"

    # 「排行」：按房间分开，房间内按 rating 均值从高到低
    room_order = {r: i for i, r in enumerate(ROOM_NAMES.values())}
    stats.sort(key=lambda st: (room_order.get(st["room"], 9), st["room"], -(st["rating"] or 0)))
    rank = wb.create_sheet("排行")
    rank.append(["玩家 | 房间", "玩家", "房间", "局数", "rating均值", f"最近{TREND_WINDOW}局rating", "最高", "最低",
                 "一致率均值%", "最近对局", "起始行"])
    for st in stats:
        rank.append([st["name"], st["player"], st["room"], st["n"], st["rating"], st["recent"], st["max"], st["min"],
                     st["cons"], st["last"], st["start"]])
    rank.column_dimensions["A"].hidden = True
    rank.column_dimensions["K"].hidden = True
    for col, width in zip("BCDEFGHIJ", [16, 9, 6, 10, 13, 8, 8, 11, 12]):
        rank.column_dimensions[col].width = width
    for cell in rank[1]:
        cell.font = Font(bold=True)
    rank.freeze_panes = "B2"
    rank.auto_filter.ref = f"A1:K{rank.max_row}"

    # 每个房间一张条形图，只放局数够的玩家；图的数据放在隐藏的「图表数据」页 G:H，排行页筛选排序不影响图
    anchor_row = 1
    data_row = 1
    chart_col = 7
    for room in sorted({st["room"] for st in stats}, key=lambda r: room_order.get(r, 9)):
        picked = [st for st in stats if st["room"] == room and st["n"] >= RANK_MIN_GAMES][:30]
        if len(picked) < 2:
            continue
        top = data_row
        data.cell(row=top, column=chart_col, value=f"{room}（≥{RANK_MIN_GAMES}局）")
        data.cell(row=top, column=chart_col + 1, value="rating均值")
        for i, st in enumerate(reversed(picked), 1):  # 条形图从下往上画，倒过来让最高的在最上面
            data.cell(row=top + i, column=chart_col, value=f"{st['player']}（{st['n']}局）")
            data.cell(row=top + i, column=chart_col + 1, value=st["rating"])
        data_row = top + len(picked) + 2
        chart = BarChart()
        chart.type = "bar"
        chart.title = f"{room} rating 均值（至少 {RANK_MIN_GAMES} 局）"
        chart.title.overlay = False
        chart.legend = None
        chart.add_data(Reference(data, min_col=chart_col + 1, min_row=top, max_row=top + len(picked)), titles_from_data=True)
        chart.set_categories(Reference(data, min_col=chart_col, min_row=top + 1, max_row=top + len(picked)))
        low, high = axis_bounds([st["rating"] for st in picked], 5)
        chart.y_axis.scaling.min, chart.y_axis.scaling.max = low, high
        chart.x_axis.delete = False
        chart.y_axis.delete = False
        chart.height = max(6, 0.55 * len(picked) + 2)
        chart.width = 16
        rank.add_chart(chart, f"L{anchor_row}")
        anchor_row += int(chart.height * 2) + 2

    # 「趋势」：B1 下拉选，B2/B3 是起始行和局数，下面每行一局，超出局数的是 #N/A（图里不画，表里隐藏）
    trend = wb.create_sheet("趋势", 0)
    capacity = max(max(st["n"] for st in stats), 50)
    latest = f"{runs[-1].get('player')} | {runs[-1].get('room')}" if runs else stats[0]["name"]
    if latest not in groups:
        latest = max(stats, key=lambda st: st["n"])["name"]
    rows_ref = f"'排行'!$A$2:$A${len(stats) + 1}"
    trend["A1"], trend["B1"] = "选择玩家 ▶", latest
    trend["A2"], trend["B2"] = "起始行", f"=IFERROR(INDEX('排行'!$K$2:$K${len(stats) + 1},MATCH($B$1,{rows_ref},0)),0)"
    trend["A3"], trend["B3"] = "局数", f"=IFERROR(INDEX('排行'!$D$2:$D${len(stats) + 1},MATCH($B$1,{rows_ref},0)),0)"
    trend["D1"], trend["E1"] = "rating均值", f"=IFERROR(INDEX('排行'!$E$2:$E${len(stats) + 1},MATCH($B$1,{rows_ref},0)),\"\")"
    trend["D2"], trend["E2"] = f"最近{TREND_WINDOW}局", f"=IFERROR(INDEX('排行'!$F$2:$F${len(stats) + 1},MATCH($B$1,{rows_ref},0)),\"\")"
    trend["D3"], trend["E3"] = "一致率均值%", f"=IFERROR(INDEX('排行'!$I$2:$I${len(stats) + 1},MATCH($B$1,{rows_ref},0)),\"\")"
    trend["B1"].font = Font(bold=True, size=12, color="C00000")
    for ref in ("A1", "A2", "A3", "D1", "D2", "D3"):
        trend[ref].font = Font(bold=True)
    dv = DataValidation(type="list", formula1=f"={rows_ref}", allow_blank=False)
    dv.error, dv.errorTitle = "请从列表里选", "玩家不在列表里"
    trend.add_data_validation(dv)
    dv.add("B1")

    header = 5
    titles = ["第几局", "对局日期", "rating", "一致率%", f"rating {TREND_WINDOW}局均线", f"一致率 {TREND_WINDOW}局均线"]
    for col, title in enumerate(titles, 1):
        trend.cell(row=header, column=col, value=title).font = Font(bold=True)
    last = header + capacity
    for i in range(1, capacity + 1):
        r = header + i
        have = f"$B$3>={i}"
        src = lambda col, k=i: f"INDEX('图表数据'!${col}:${col},$B$2+{k - 1})"
        lo = max(0, i - TREND_WINDOW)
        avg = lambda col, k=i, lo=lo: f"AVERAGE(INDEX('图表数据'!${col}:${col},$B$2+{lo}):{src(col, k)})"
        trend.cell(row=r, column=1, value=f"=IF({have},{i},NA())")
        trend.cell(row=r, column=2, value=f"=IF({have},{src('C')},NA())")
        trend.cell(row=r, column=3, value=f"=IF({have},{src('D')},NA())")
        trend.cell(row=r, column=4, value=f"=IF({have},{src('E')},NA())")
        trend.cell(row=r, column=5, value=f"=IF({have},ROUND({avg('D')},2),NA())")
        trend.cell(row=r, column=6, value=f"=IF({have},ROUND({avg('E')},2),NA())")
    # #N/A 用白字藏起来（图需要 #N/A 才会跳过这些点）
    trend.conditional_formatting.add(f"A{header + 1}:F{last}",
                                     FormulaRule(formula=[f"ISNA(A{header + 1})"], font=Font(color="FFFFFF")))
    for col, width in zip("ABCDEF", [12, 12, 9, 9, 15, 15]):
        trend.column_dimensions[col].width = width
    trend.column_dimensions["B"].width = 22
    trend.freeze_panes = f"A{header + 1}"
    for sheet in (trend, rank):  # 打印时横向、宽度缩到一页
        sheet.page_setup.orientation = "landscape"
        sheet.sheet_properties.pageSetUpPr.fitToPage = True
        sheet.page_setup.fitToWidth, sheet.page_setup.fitToHeight = 1, 0

    def line_chart(title, y_col, avg_col, values, step):
        chart = ScatterChart()
        chart.title = title
        chart.title.overlay = False
        chart.style = 13
        xs = Reference(trend, min_col=1, min_row=header + 1, max_row=last)
        for col, as_points in ((y_col, True), (avg_col, False)):
            series = Series(Reference(trend, min_col=col, min_row=header, max_row=last), xs, title_from_data=True)
            if as_points:
                series.marker.symbol = "circle"
                series.marker.size = 5
                series.marker.graphicalProperties = GraphicalProperties(solidFill="4472C4")
                series.marker.graphicalProperties.line.solidFill = "4472C4"
                series.graphicalProperties.line.noFill = True
            else:
                series.marker.symbol = "none"
                series.graphicalProperties.line.solidFill = "ED7D31"
                series.graphicalProperties.line.width = 28000
            series.smooth = False
            chart.series.append(series)
        low, high = axis_bounds(values, step)
        chart.y_axis.scaling.min, chart.y_axis.scaling.max = low, high
        chart.x_axis.scaling.min = 1
        chart.x_axis.title = "第几局（从早到晚）"
        chart.x_axis.delete = False
        chart.y_axis.delete = False
        chart.x_axis.title.overlay = False
        chart.legend.position = "t"
        chart.legend.overlay = False
        chart.height, chart.width = 9, 22
        return chart

    all_ratings = [row["rating"] for _, row in games.values()]
    all_cons = [row["ai_consistency"] for _, row in games.values() if isinstance(row.get("ai_consistency"), (int, float))]
    trend.add_chart(line_chart("rating 走势（在 B1 换玩家）", 3, 5, all_ratings, 10), "H1")
    if all_cons:
        trend.add_chart(line_chart("AI 一致率走势 %", 4, 6, all_cons, 10), "H20")
    wb.move_sheet(rank, offset=1 - wb.index(rank))
    wb.active = 0


OLD_CSV_RE = re.compile(r"^(?P<player>.+)_(?P<room>gold|jade|throne)_recent_(?P<n>\d+)_(?P<ver>v[\w.]+)\.csv$")
OLD_ROOMS = {"gold": "金之间", "jade": "玉之间", "throne": "王座之间"}


def parse_number(text):
    match = re.search(r"-?\d+(?:\.\d+)?", str(text or ""))
    return float(match.group(0)) if match else None


def import_old_csv(folder):
    """把旧版本生成的 CSV 导入 history.jsonl（只导入一次，按文件名去重）。"""
    folder = Path(folder)
    done = {r.get("import_file") for r in load_history()}
    imported = 0
    for path in sorted(folder.glob("*.csv")):
        match = OLD_CSV_RE.match(path.name)
        if not match or path.name in done:
            continue
        rows = []
        with path.open(encoding="utf-8-sig", newline="") as f:
            for rec in csv.DictReader(f):
                paipu = rec.get("paipu") or paipu_from_mjai_href(rec.get("mjai_url") or "")
                rating = parse_number(rec.get("rating"))
                if not paipu or rating is None:
                    continue
                consistency = str(rec.get("ai_consistency") or "")
                frac = re.search(r"(\d+)\s*/\s*(\d+)", consistency)
                matches = rec.get("matches") or (f"{frac.group(1)}/{frac.group(2)}" if frac else "")
                pct = re.search(r"([\d.]+)\s*%", consistency)
                pct = float(pct.group(1)) if pct else parse_number(consistency)
                if frac and int(frac.group(2)):
                    pct = round(int(frac.group(1)) / int(frac.group(2)) * 100, 1)
                json_url = rec.get("report_url") or rec.get("json_url") or ""
                report = REPORT_RE.search(json_url)
                rows.append({"paipu": paipu, "rating": rating, "ai_consistency": pct, "matches": matches,
                             "report_url": f"{MJAI}/killerducky/?data={report.group(0)}" if report else ""})
        if not rows:
            continue
        append_history({
            "time": time.strftime("%Y-%m-%d %H:%M", time.localtime(path.stat().st_mtime)),
            "player": match.group("player"),
            "player_id": player_id_from_paipu(rows[0]["paipu"]),
            "room": OLD_ROOMS[match.group("room")],
            "games": int(match.group("n")),
            "source": f"{match.group('ver')} CSV",
            "import_file": path.name,
            "rows": rows,
        })
        imported += 1
    print(f"[import] 导入 {imported} 个 CSV")


# ---------------------------------------------------------------- main

def parse_args():
    ap = argparse.ArgumentParser(description=f"查rt工具 {VERSION}")
    ap.add_argument("--nickname", help="玩家昵称")
    ap.add_argument("--player-id", help="玩家 ID（填了就不搜昵称）")
    ap.add_argument("--room", choices=sorted(ROOM_NAMES), help="9=金之间，12=玉之间，16=王座之间")
    ap.add_argument("--games", type=int, help="查最近 N 局")
    ap.add_argument("--model", default="4.1b", help="Mortal 模型，默认 4.1b（和网页默认一致）")
    ap.add_argument("--refresh", action="store_true", help="忽略缓存，全部重新提交")
    ap.add_argument("--parallel", type=int, default=DEFAULT_PARALLEL,
                    help=f"同时在 mjai 上处理几局，默认 {DEFAULT_PARALLEL}；遇到限流会自动减少")
    ap.add_argument("--export", action="store_true", help="只重新生成 rt结果.xlsx，不查询")
    ap.add_argument("--import-csv", metavar="文件夹", help="把旧版本的 CSV 导入总表，然后退出")
    return ap.parse_args()


def main():
    started = time.time()
    args = parse_args()
    if args.import_csv:
        import_old_csv(args.import_csv)
        export_excel()
        return
    if args.export:
        export_excel()
        return

    nickname = args.nickname or (None if args.player_id else input("昵称: ").strip())
    room = args.room or input("房间 (9=金之间, 12=玉之间, 16=王座之间): ").strip()
    games = args.games or int(input("最近几局: ").strip())

    player_id = args.player_id
    if not player_id:
        try:
            player_id = search_player(nickname)
        except urllib.error.HTTPError as exc:
            print(f"[error] 牌谱屋搜玩家失败：HTTP {exc.code}（429 是限流，可以稍后再试）")
        if not player_id:
            player_id = known_player(nickname=nickname)[1]
            if player_id:
                print(f"[player] 用查询记录里的玩家 ID {player_id}")
    if not player_id:
        print("[error] 没找到玩家，可以用 --player-id 指定")
        return
    nickname = nickname or known_player(player_id=player_id)[0] or str(player_id)
    print(f"[player] {nickname} id={player_id}")

    cache = ReportCache(CACHE_FILE)
    t0 = time.time()
    driver = build_driver()
    print(f"[time] Chrome 启动 {time.time() - t0:.1f}s")
    rows = {}
    paipus = []
    runner = None
    try:
        t0 = time.time()
        paipus = fetch_recent_paipu(driver, player_id, room, games)
        print(f"[time] 牌谱屋列表 {time.time() - t0:.1f}s")
        if not paipus:
            print("[error] 牌谱屋没有读到对局")
            return

        jobs = []
        for idx, paipu in enumerate(paipus, 1):
            hit = None if args.refresh else cache.get(paipu, args.model)
            if hit:
                rows[idx] = dict(hit, index=idx, status="cache")
                print(f"[#{idx}] 缓存 rating={hit['rating']} 一致率={hit['ai_consistency']}%")
            else:
                jobs.append((idx, paipu))

        def on_done(idx, paipu, result, error, elapsed):
            if result:
                cache.put(paipu, args.model, result)
                rows[idx] = dict(result, index=idx, paipu=paipu, status="OK")
                print(f"[#{idx}] rating={result['rating']} 一致率={result['ai_consistency']}% "
                      f"({result['matches']})，用时 {elapsed:.1f}s")
            else:
                rows[idx] = {"index": idx, "paipu": paipu, "status": f"ERROR: {error}"}
                print(f"[#{idx}] 失败: {error}")

        if jobs:
            t0 = time.time()
            runner = MjaiRunner(driver, args.model, args.parallel)
            runner.run(jobs, on_done)
            print(f"[time] mjai {len(jobs)} 局 {time.time() - t0:.1f}s（平均 {(time.time() - t0) / len(jobs):.1f}s/局）")
    finally:
        try:
            driver.quit()
        except Exception:
            pass
        if rows:
            # 中途中断也把已完成的局记下来
            keep = ("paipu", "rating", "ai_consistency", "matches", "report_url", "status")
            append_history({
                "time": time.strftime("%Y-%m-%d %H:%M"),
                "player": nickname,
                "player_id": str(player_id),
                "room": ROOM_NAMES.get(room, room),
                "games": games,
                "model": args.model,
                "source": VERSION,
                "rows": [{k: rows[i].get(k) for k in keep} for i in sorted(rows)],
            })
            export_excel()

    ok = [rows[i] for i in sorted(rows) if rows[i].get("status") in ("OK", "cache")]
    if ok:
        print(f"[done] rating 均值 {mean([r['rating'] for r in ok])}，"
              f"一致率均值 {mean([r['ai_consistency'] for r in ok])}%，"
              f"成功 {len(ok)}/{len(rows)}，缓存命中 {sum(r['status'] == 'cache' for r in ok)}")
    if runner:
        print(f"[done] 并行 {args.parallel}，限流 {runner.rate_limited} 次，最后并行 {runner.limit}")
    print(f"[done] 用时 {time.time() - started:.0f}s")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n[stop] 已中断，查过的局都在缓存里，重跑会跳过")
        sys.exit(130)
