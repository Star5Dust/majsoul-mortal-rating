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
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

import undetected_chromedriver as uc
from selenium.common.exceptions import TimeoutException, WebDriverException


VERSION = "v6"
BASE_DIR = Path(__file__).resolve().parent
PROFILE_DIR = BASE_DIR / "chrome_user_data_v6"
OUTPUT_DIR = BASE_DIR / "outputs_all"
CACHE_FILE = BASE_DIR / "cache" / "reports.jsonl"

AMAE_SITE = "https://amae-koromo.sapk.ch"
AMAE_API = "https://5-data.amae-koromo.com/api/v2/pl4"
MJAI = "https://mjai.ekyu.moe"
MJAI_FORM = f"{MJAI}/zh-cn.html"
ROOM_NAMES = {"9": "gold", "12": "jade", "16": "throne"}
CSV_FIELDS = ["index", "rating", "ai_consistency", "matches", "paipu", "report_url", "status"]
UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"
)
REPORT_RE = re.compile(r"/report/[0-9a-f]+\.json")


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

JS_SUBMIT = """
const [logUrl, model, timeoutSec, done] = arguments;
const f = document.forms.reviewForm;
const radio = f.querySelector('input[name="input-method"][value="log-url"]');
if (radio) radio.checked = true;
f.elements['log-url'].value = logUrl;
f.elements['mortal-model-tag'].value = model;
f.elements['player-id'].value = '';
f.elements['ui'].value = 'killerducky';
const ctl = new AbortController();
setTimeout(() => ctl.abort(), timeoutSec * 1000);
fetch(f.action, {method: 'POST', body: new URLSearchParams(new FormData(f)), signal: ctl.signal, credentials: 'same-origin'})
  .then(async r => {
    let text = '';
    try { text = (await r.text()).slice(0, 4000); } catch (e) {}
    done({status: r.status, url: r.url, text: text});
  })
  .catch(e => done({status: 0, url: '', text: String(e)}));
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


class MjaiSession:
    def __init__(self, driver, model, min_interval):
        self.driver = driver
        self.model = model
        self.min_interval = min_interval
        self.last_token = None
        self.last_submit = 0.0
        self.on_form = False

    def open_form(self):
        open_page(self.driver, MJAI_FORM)
        wait_until(self.driver, "return !!(document.forms.reviewForm)", "mjai 页面")
        self.on_form = True
        self.last_token = None

    def wait_token(self):
        js = JS_TOKEN
        started = time.time()
        prompted = False
        while True:
            token = self.driver.execute_script(js)
            if token and token != self.last_token:
                return token
            if not prompted and time.time() - started > 20:
                beep()
                print("[wait] Turnstile 还没通过。如果浏览器里出现验证框，请手动点一下；程序会一直等。")
                prompted = True
            time.sleep(0.3)

    def reset_token(self):
        result = self.driver.execute_script(JS_RESET)
        if result is not True:
            print(f"  [turnstile] 重置失败（{result}），重新打开表单页")
            self.open_form()

    def submit(self, paipu):
        """返回 /report/xxx.json 路径；失败抛出 RuntimeError。"""
        log_url = f"https://game.maj-soul.com/1/?paipu={paipu}"
        backoff = 20
        for attempt in range(1, 7):
            if not self.on_form:
                self.open_form()
            wait = self.min_interval - (time.time() - self.last_submit)
            if wait > 0:
                time.sleep(wait)
            t0 = time.time()
            self.last_token = self.wait_token()
            t1 = time.time()
            res = self.driver.execute_async_script(JS_SUBMIT, log_url, self.model, 180)
            t2 = time.time()
            self.last_submit = t2
            status, final_url, text = res.get("status"), res.get("url") or "", res.get("text") or ""
            match = REPORT_RE.search(urllib.parse.unquote(final_url)) or REPORT_RE.search(text)
            if match:
                print(f"  token {t1 - t0:.1f}s, 分析 {t2 - t1:.1f}s")
                self.reset_token()
                return match.group(0)
            lowered = text.lower()
            if status == 429 or "1015" in lowered or "rate limit" in lowered:
                print(f"  [限流] HTTP {status}，等 {backoff}s 后重试 ({attempt}/6)")
                time.sleep(backoff)
                backoff = min(backoff * 2, 300)
                self.open_form()
                continue
            if "captcha" in lowered:
                print(f"  [turnstile] token 无效，重新获取 ({attempt}/6)")
                self.reset_token()
                continue
            snippet = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", text))[:200]
            print(f"  [warn] 提交没拿到报告：HTTP {status} {final_url} {snippet}")
            self.open_form()
        raise RuntimeError("多次提交都没拿到报告")

    def fetch_report(self, path):
        try:
            req = urllib.request.Request(MJAI + path, headers={"User-Agent": UA, "Accept": "application/json"})
            with urllib.request.urlopen(req, timeout=60) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except (urllib.error.URLError, ValueError, OSError) as exc:
            print(f"  [report] 直接下载失败（{exc.__class__.__name__}），改用浏览器下载")
        text = self.driver.execute_async_script(JS_FETCH_JSON, path)
        return json.loads(text)


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


# ---------------------------------------------------------------- main

def write_csv(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_FIELDS)
        writer.writeheader()
        for row in rows:
            writer.writerow({k: row.get(k, "") for k in CSV_FIELDS})
        ok = [r for r in rows if r.get("status") in ("OK", "cache")]
        if ok:
            writer.writerow({
                "index": "均值",
                "rating": round(sum(r["rating"] for r in ok) / len(ok), 2),
                "ai_consistency": round(sum(r["ai_consistency"] for r in ok) / len(ok), 2),
                "status": f"{len(ok)}/{len(rows)} 局",
            })


def parse_args():
    ap = argparse.ArgumentParser(description=f"查rt工具 {VERSION}")
    ap.add_argument("--nickname", help="玩家昵称")
    ap.add_argument("--player-id", help="玩家 ID（填了就不搜昵称）")
    ap.add_argument("--room", choices=sorted(ROOM_NAMES), help="9=金之间，12=玉之间，16=王座之间")
    ap.add_argument("--games", type=int, help="查最近 N 局")
    ap.add_argument("--model", default="4.1b", help="Mortal 模型，默认 4.1b（和网页默认一致）")
    ap.add_argument("--refresh", action="store_true", help="忽略缓存，全部重新提交")
    ap.add_argument("--min-interval", type=float, default=0, help="两次提交之间至少间隔几秒，默认 0")
    return ap.parse_args()


def main():
    started = time.time()
    args = parse_args()
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
        print("[error] 没找到玩家，可以用 --player-id 指定")
        return
    nickname = nickname or player_id
    print(f"[player] {nickname} id={player_id}")

    cache = ReportCache(CACHE_FILE)
    driver = build_driver()
    try:
        paipus = fetch_recent_paipu(driver, player_id, room, games)
        if not paipus:
            print("[error] 牌谱屋没有读到对局")
            return
        safe_name = re.sub(r'[\\/:*?"<>|]+', "_", nickname)
        out = OUTPUT_DIR / f"{safe_name}_{ROOM_NAMES.get(room, room)}_recent_{games}_{VERSION}.csv"

        mjai = MjaiSession(driver, args.model, args.min_interval)
        rows = []
        for idx, paipu in enumerate(paipus, 1):
            hit = None if args.refresh else cache.get(paipu, args.model)
            if hit:
                row = dict(hit, index=idx, status="cache")
                print(f"[#{idx}] 缓存 rating={row['rating']} 一致率={row['ai_consistency']}%")
            else:
                print(f"[#{idx}] 提交 {paipu}")
                try:
                    path = mjai.submit(paipu)
                    result = parse_report(mjai.fetch_report(path))
                    result["report_url"] = f"{MJAI}/killerducky/?data={path}"
                    cache.put(paipu, args.model, result)
                    row = dict(result, index=idx, paipu=paipu, status="OK")
                    print(f"[#{idx}] rating={row['rating']} 一致率={row['ai_consistency']}% ({row['matches']})")
                except Exception as exc:
                    row = {"index": idx, "paipu": paipu, "status": f"ERROR: {exc}"}
                    print(f"[#{idx}] 失败: {exc}")
            rows.append(row)
            write_csv(out, rows)
    finally:
        try:
            driver.quit()
        except Exception:
            pass

    ok = [r for r in rows if r.get("status") in ("OK", "cache")]
    print(f"\n[done] {out}")
    if ok:
        print(f"[done] rating 均值 {sum(r['rating'] for r in ok) / len(ok):.2f}，"
              f"一致率均值 {sum(r['ai_consistency'] for r in ok) / len(ok):.2f}%，"
              f"成功 {len(ok)}/{len(rows)}，缓存命中 {sum(r['status'] == 'cache' for r in rows)}")
    print(f"[done] 用时 {time.time() - started:.0f}s")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n[stop] 已中断，查过的局都在缓存里，重跑会跳过")
        sys.exit(130)
