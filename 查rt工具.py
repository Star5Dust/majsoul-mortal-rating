# -*- coding: utf-8 -*-
"""
查 rt 稳定版
目标：
1) 只启动一个 Chrome，降低 Cloudflare / 网络波动触发概率
2) 默认前台运行，方便人工通过 Cloudflare；通过后复用同一个 Chrome Profile
3) 每处理一局就落盘 checkpoint，崩了也能续跑
4) 自动重试、超时可控、结果 CSV 始终可保存

首次使用建议：
    python 查rt稳定版.py
如果已经知道 player_id，也可以跳过昵称搜索：
    python 查rt稳定版.py --player-id 12345678 --room 12 --games 20
"""

import argparse
import csv
import os
import random
import re
import sys
import time
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import undetected_chromedriver as uc
from selenium.common.exceptions import TimeoutException, WebDriverException
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

BASE_DIR = Path(__file__).resolve().parent
PROFILE_DIR = BASE_DIR / "chrome_profile_mjai"       # 固定目录：保留 Cloudflare 验证状态
OUTPUT_DIR = BASE_DIR / "outputs"
OUTPUT_DIR.mkdir(exist_ok=True)

ROOM_NAMES = {"9": "金", "12": "玉"}


def build_driver(headless: bool = False):
    """创建一个尽量稳定的浏览器。默认不建议 headless。"""
    PROFILE_DIR.mkdir(exist_ok=True)
    options = uc.ChromeOptions()
    options.add_argument(f"--user-data-dir={PROFILE_DIR}")
    options.add_argument("--profile-directory=Default")
    options.add_argument("--lang=zh-CN")
    options.add_argument("--no-first-run")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--disable-gpu")
    options.add_argument("--disable-background-timer-throttling")
    options.add_argument("--disable-renderer-backgrounding")
    options.add_argument("--disable-backgrounding-occluded-windows")
    options.add_argument("--window-size=1280,900")

    # 不主动设置 direct proxy，避免把系统/校园网/VPN 代理配置覆盖掉。
    # Cloudflare 场景下 headless 更容易失败，所以默认前台。
    if headless:
        options.add_argument("--headless=new")

    driver = uc.Chrome(options=options, use_subprocess=True)
    driver.set_page_load_timeout(45)
    return driver


def human_sleep(a=0.8, b=1.8):
    time.sleep(random.uniform(a, b))


def safe_get(driver, url: str, tries: int = 3) -> bool:
    """带重试的 driver.get。"""
    for attempt in range(1, tries + 1):
        try:
            driver.get(url)
            return True
        except TimeoutException:
            print(f"  ⚠️ 页面加载超时，第 {attempt}/{tries} 次")
            try:
                driver.execute_script("window.stop();")
            except Exception:
                pass
        except WebDriverException as e:
            print(f"  ⚠️ 浏览器访问失败，第 {attempt}/{tries} 次: {e.__class__.__name__}")
        time.sleep(3 * attempt)
    return False


def wait_if_cloudflare(driver, max_wait=180) -> bool:
    """如果遇到 Cloudflare，等待人工验证。不会尝试绕过验证。"""
    start = time.time()
    announced = False
    while time.time() - start < max_wait:
        url = driver.current_url
        title = (driver.title or "").lower()
        page = ""
        try:
            page = driver.page_source[:2000].lower()
        except Exception:
            pass

        cf = (
            "challenges.cloudflare.com" in url
            or "just a moment" in title
            or "checking your browser" in page
            or "cf-challenge" in page
            or "cloudflare" in title
        )
        if not cf:
            return True
        if not announced:
            print("  ⚠️ 检测到 Cloudflare：请在打开的浏览器里手动点验证。验证完成后脚本会继续。")
            announced = True
        time.sleep(3)
    return False


def get_player_id_by_nickname(driver, nickname: str):
    print(f"🔍 搜索玩家: {nickname}")
    if not safe_get(driver, "https://amae-koromo.sapk.ch/"):
        return None
    wait_if_cloudflare(driver)

    # MUI 的 id 可能变，因此不用只依赖 mui-3。
    selectors = [
        (By.CSS_SELECTOR, "input[placeholder*='昵称']"),
        (By.CSS_SELECTOR, "input[type='text']"),
        (By.ID, "mui-3"),
    ]
    search_box = None
    for by, sel in selectors:
        try:
            search_box = WebDriverWait(driver, 12).until(EC.element_to_be_clickable((by, sel)))
            break
        except TimeoutException:
            continue
    if not search_box:
        print("❌ 找不到搜索框。建议稍后重试，或用 --player-id 跳过昵称搜索。")
        return None

    search_box.clear()
    search_box.send_keys(nickname)
    human_sleep(0.5, 1.0)

    try:
        first_option = WebDriverWait(driver, 12).until(
            EC.element_to_be_clickable((By.CSS_SELECTOR, "ul[role='listbox'] li"))
        )
        first_option.click()
        WebDriverWait(driver, 15).until(lambda d: "/player/" in d.current_url)
    except TimeoutException:
        print("❌ 没等到玩家下拉项/跳转。可以换网络重试，或手动打开雀魂牌谱屋确认 player_id。")
        return None

    m = re.search(r"/player/(\d+)", driver.current_url)
    if m:
        print(f"🎯 玩家ID: {m.group(1)}")
        return m.group(1)
    return None


def extract_latest_mjai_links(driver, player_id: str, room_code: str, num_games: int):
    url = f"https://amae-koromo.sapk.ch/player/{player_id}/{room_code}"
    print(f"🌐 获取对局列表: {url}")
    if not safe_get(driver, url):
        return []
    wait_if_cloudflare(driver)
    time.sleep(4)

    seen_indices = set()
    link_records = []
    no_new = 0
    scrolls = 0

    while len(link_records) < num_games and no_new < 10 and scrolls < 120:
        rows = driver.find_elements(By.XPATH, "//div[@aria-rowindex]")
        before = len(link_records)

        for row in rows:
            try:
                idx_str = row.get_attribute("aria-rowindex")
                if not idx_str:
                    continue
                idx = int(idx_str)
                if idx in seen_indices or idx > num_games:
                    continue
                ai_tags = row.find_elements(By.XPATH, ".//a[contains(@title, 'AI') or contains(@href, 'mjai.ekyu.moe')]")
                for tag in ai_tags:
                    href = tag.get_attribute("href")
                    if href and "mjai.ekyu.moe" in href:
                        seen_indices.add(idx)
                        link_records.append((idx, href))
                        print(f"  ✅ #{idx}: {href}")
                        break
            except Exception:
                continue

        if len(link_records) == before:
            no_new += 1
        else:
            no_new = 0

        if len(link_records) >= num_games:
            break

        driver.execute_script("window.scrollBy(0, 700);")
        scrolls += 1
        time.sleep(0.7)

    link_records.sort(key=lambda x: x[0])
    print(f"✅ 共找到 {len(link_records[:num_games])}/{num_games} 个 MJAI 链接")
    return link_records[:num_games]


def read_existing_csv(path: Path):
    done = {}
    if not path.exists():
        return done
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            mjai = row.get("mjai链接")
            if mjai:
                done[mjai] = row
    return done


def write_csv(path: Path, rows):
    fields = ["序号", "mjai链接", "json链接", "rating", "ai一致率", "状态"]
    tmp = path.with_suffix(".tmp")
    with tmp.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)
    tmp.replace(path)


def click_submit_and_wait_killerducky(driver, mjai_url: str):
    if not safe_get(driver, mjai_url):
        return None
    if not wait_if_cloudflare(driver):
        return None

    try:
        btn = WebDriverWait(driver, 20).until(
            EC.element_to_be_clickable((By.CSS_SELECTOR, 'button[type="submit"][name="submitBtn"]'))
        )
        driver.execute_script("arguments[0].scrollIntoView({block:'center'});", btn)
        human_sleep(0.3, 0.8)
        btn.click()
    except TimeoutException:
        # 有时已经跳转，或页面卡在 CF。
        if "/killerducky/" not in driver.current_url:
            wait_if_cloudflare(driver)

    start = time.time()
    while time.time() - start < 120:
        if "/killerducky/" in driver.current_url:
            return driver.current_url
        wait_if_cloudflare(driver, max_wait=10)
        time.sleep(2)
    return None


def extract_about_values(driver):
    try:
        about_btn = WebDriverWait(driver, 30).until(
            EC.element_to_be_clickable((By.XPATH, "//button[contains(., 'About')]"))
        )
        about_btn.click()
        WebDriverWait(driver, 15).until(EC.presence_of_element_located((By.ID, "about-modal")))
        rows = driver.find_elements(By.XPATH, '//*[@id="about-modal"]//table/tbody/tr')
    except TimeoutException:
        return "ERROR", "ERROR"

    ai_consistency = "N/A"
    rating = "N/A"

    # 优先按行名找，避免页面表格顺序变化。
    for tr in rows:
        tds = tr.find_elements(By.TAG_NAME, "td")
        if len(tds) < 2:
            continue
        key = tds[0].text.strip().lower()
        val = tds[1].text.strip()
        if "consistency" in key or "一致" in key:
            ai_consistency = val
        if "rating" in key:
            rating = val

    # 兼容旧逻辑：第 8/9 行。
    if rating in ("N/A", "") and len(rows) > 8:
        tds = rows[8].find_elements(By.TAG_NAME, "td")
        if len(tds) >= 2:
            rating = tds[1].text.strip()
    if ai_consistency in ("N/A", "") and len(rows) > 7:
        tds = rows[7].find_elements(By.TAG_NAME, "td")
        if len(tds) >= 2:
            ai_consistency = tds[1].text.strip()

    return rating or "N/A", ai_consistency or "N/A"


def process_one(driver, mjai_url: str, retries: int = 2):
    for attempt in range(1, retries + 2):
        print(f"  ▶️ 处理：{mjai_url}  尝试 {attempt}/{retries + 1}")
        killer_url = click_submit_and_wait_killerducky(driver, mjai_url)
        if not killer_url:
            print("  ⚠️ 未能进入 KillerDucky，准备重试")
            time.sleep(5 * attempt)
            continue

        data_path = parse_qs(urlparse(killer_url).query).get("data", [None])[0]
        json_url = f"https://mjai.ekyu.moe{data_path}" if data_path and data_path.endswith(".json") else "ERROR"
        rating, ai_consistency = extract_about_values(driver)
        status = "OK" if rating not in ("ERROR", "N/A", "") else "PARTIAL"
        return json_url, rating, ai_consistency, status

    return "ERROR", "ERROR", "ERROR", "ERROR"


def to_float_percent(text):
    if not text or text in ("ERROR", "N/A"):
        return None
    m = re.search(r"([0-9]+(?:\.[0-9]+)?)\s*%", str(text))
    if m:
        return float(m.group(1))
    try:
        return float(str(text).strip())
    except Exception:
        return None


def summarize(rows):
    ratings = []
    consistencies = []
    for r in rows:
        try:
            if r.get("rating") not in ("ERROR", "N/A", ""):
                ratings.append(float(str(r["rating"]).rstrip("%")))
        except Exception:
            pass
        c = to_float_percent(r.get("ai一致率"))
        if c is not None:
            consistencies.append(c)
    print("\n📈 统计结果")
    print(f"  有效 Rating: {len(ratings)} / {len(rows)}")
    if ratings:
        print(f"  平均 Rating: {sum(ratings) / len(ratings):.2f}")
    if consistencies:
        print(f"  平均 AI 一致率: {sum(consistencies) / len(consistencies):.2f}%")


def parse_args():
    p = argparse.ArgumentParser()
    p.add_argument("--nickname", help="玩家昵称")
    p.add_argument("--player-id", help="已知玩家 ID；提供后跳过昵称搜索")
    p.add_argument("--room", choices=["9", "12"], help="9=金之间，12=玉之间")
    p.add_argument("--games", type=int, help="查询最近 N 场")
    p.add_argument("--headless", action="store_true", help="后台运行。不推荐用于 Cloudflare 场景")
    p.add_argument("--reset-profile", action="store_true", help="重置浏览器 profile。验证状态会丢失")
    return p.parse_args()


def main():
    args = parse_args()

    if args.reset_profile and PROFILE_DIR.exists():
        import shutil
        shutil.rmtree(PROFILE_DIR)
        print("🧹 已重置 Chrome profile")

    nickname = args.nickname or input("玩家昵称（如果用 --player-id 可随便填输出名）: ").strip()
    room = args.room or input("房间ID (9=金之间, 12=玉之间): ").strip()
    games = args.games or int(input("查找对局数: ").strip())

    if not args.headless:
        ans = input("是否前台运行？建议选 Y，方便 Cloudflare 验证。(Y/n): ").strip().lower()
        headless = ans == "n"
    else:
        headless = True

    driver = build_driver(headless=headless)
    try:
        player_id = args.player_id or get_player_id_by_nickname(driver, nickname)
        if not player_id:
            print("❌ 无法获得 player_id，退出。")
            return

        room_name = ROOM_NAMES.get(room, room)
        output_file = OUTPUT_DIR / f"{nickname}_{room_name}_近{games}场_稳定版.csv"
        existing = read_existing_csv(output_file)

        links = extract_latest_mjai_links(driver, player_id, room, games)
        if not links:
            print("❌ 没找到 MJAI 链接。")
            return

        results = []
        for idx, mjai_url in links:
            if mjai_url in existing and existing[mjai_url].get("状态") == "OK":
                print(f"  ⏭️ 跳过已完成 #{idx}")
                row = existing[mjai_url]
                row["序号"] = idx
                results.append(row)
                continue

            json_url, rating, ai_consistency, status = process_one(driver, mjai_url)
            row = {
                "序号": idx,
                "mjai链接": mjai_url,
                "json链接": json_url,
                "rating": rating,
                "ai一致率": ai_consistency,
                "状态": status,
            }
            results.append(row)
            write_csv(output_file, results)
            print(f"  💾 已保存进度：{output_file}")
            human_sleep(2.5, 5.0)

        write_csv(output_file, results)
        print(f"\n🎉 完成：{output_file}")
        summarize(results)

    finally:
        try:
            driver.quit()
        except Exception:
            pass


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n⛔ 用户中断。已写入的 CSV 可下次续跑。")
        sys.exit(130)
