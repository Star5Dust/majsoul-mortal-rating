# -*- coding: utf-8 -*-
"""
Conservative v2 for the RT/rating lookup tool.

Compared with the known-working version:
- keeps one Chrome instance for the whole run
- uses a persistent Chrome profile so manual Cloudflare verification can be reused
- defaults to visible Chrome; headless is optional and not recommended
- waits for manual Cloudflare verification instead of trying to bypass it
- writes a CSV checkpoint after every game, so reruns can skip completed rows
- supports --player-id to skip nickname search
"""

import argparse
import csv
import os
import random
import re
import shutil
import subprocess
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
PROFILE_DIR = BASE_DIR / "chrome_user_data_v2"
OUTPUT_DIR = BASE_DIR / "outputs_v2"
OUTPUT_DIR.mkdir(exist_ok=True)

ROOM_NAMES = {"9": "gold", "12": "jade"}
CSV_FIELDS = ["index", "mjai_url", "json_url", "rating", "ai_consistency", "status"]
KILLERDUCKY_SETTLE_SECONDS = 8
ABOUT_VALUES_MAX_WAIT = 150


def human_sleep(a=0.6, b=1.4):
    time.sleep(random.uniform(a, b))


def get_chrome_major_version():
    """Return the installed Chrome major version, or None if it cannot be detected."""
    if sys.platform.startswith("win"):
        try:
            import winreg

            registry_paths = [
                (winreg.HKEY_CURRENT_USER, r"Software\Google\Chrome\BLBeacon"),
                (winreg.HKEY_LOCAL_MACHINE, r"Software\Google\Chrome\BLBeacon"),
                (winreg.HKEY_LOCAL_MACHINE, r"Software\WOW6432Node\Google\Chrome\BLBeacon"),
            ]
            for hive, key_path in registry_paths:
                try:
                    with winreg.OpenKey(hive, key_path) as key:
                        version, _ = winreg.QueryValueEx(key, "version")
                    match = re.match(r"(\d+)\.", str(version))
                    if match:
                        return int(match.group(1))
                except OSError:
                    continue
        except Exception:
            pass

        chrome_exes = [
            Path(os.environ.get("PROGRAMFILES", "")) / "Google/Chrome/Application/chrome.exe",
            Path(os.environ.get("PROGRAMFILES(X86)", "")) / "Google/Chrome/Application/chrome.exe",
            Path(os.environ.get("LOCALAPPDATA", "")) / "Google/Chrome/Application/chrome.exe",
        ]
        for chrome_exe in chrome_exes:
            if not chrome_exe.exists():
                continue
            try:
                output = subprocess.check_output(
                    [str(chrome_exe), "--version"],
                    text=True,
                    stderr=subprocess.STDOUT,
                    timeout=5,
                )
            except Exception:
                continue
            match = re.search(r"(\d+)\.", output)
            if match:
                return int(match.group(1))

    return None


def build_driver(headless=False):
    PROFILE_DIR.mkdir(exist_ok=True)

    options = uc.ChromeOptions()
    options.add_argument(f"--user-data-dir={PROFILE_DIR}")
    options.add_argument("--profile-directory=Default")
    options.add_argument("--lang=zh-CN")
    options.add_argument("--no-first-run")
    options.add_argument("--disable-extensions")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--disable-gpu")
    options.add_argument("--disable-background-timer-throttling")
    options.add_argument("--disable-renderer-backgrounding")
    options.add_argument("--disable-backgrounding-occluded-windows")
    options.add_argument("--window-size=1280,900")

    if headless:
        options.add_argument("--headless=new")

    chrome_major = get_chrome_major_version()
    if chrome_major:
        print(f"[mode] Detected Chrome major version: {chrome_major}")
        driver = uc.Chrome(options=options, use_subprocess=True, version_main=chrome_major)
    else:
        print("[warn] Could not detect Chrome version; using undetected_chromedriver default.")
        driver = uc.Chrome(options=options, use_subprocess=True)
    driver.set_page_load_timeout(45)
    return driver


def safe_get(driver, url, tries=3):
    for attempt in range(1, tries + 1):
        try:
            driver.get(url)
            return True
        except TimeoutException:
            print(f"  [warn] Page load timeout {attempt}/{tries}: {url}")
            try:
                driver.execute_script("window.stop();")
                return True
            except Exception:
                pass
        except WebDriverException as exc:
            print(f"  [warn] Browser error {attempt}/{tries}: {exc.__class__.__name__}")
        time.sleep(3 * attempt)
    return False


def looks_like_cloudflare(driver):
    try:
        url = (driver.current_url or "").lower()
        title = (driver.title or "").lower()
        html = (driver.page_source or "")[:4000].lower()
    except Exception:
        return False

    return (
        "challenges.cloudflare.com" in url
        or "just a moment" in title
        or "checking your browser" in html
        or "cf-challenge" in html
        or "cloudflare" in title
    )


def wait_if_cloudflare(driver, max_wait=240):
    if not looks_like_cloudflare(driver):
        return True

    print("\n[cloudflare] Verification page detected.")
    print("[cloudflare] Please complete it manually in the opened Chrome window.")
    print("[cloudflare] The script will continue automatically after verification.\n")

    start = time.time()
    while time.time() - start < max_wait:
        if not looks_like_cloudflare(driver):
            human_sleep(0.8, 1.5)
            print("[cloudflare] Verification appears complete; continuing.")
            return True
        time.sleep(3)

    print("[cloudflare] Still on verification page after waiting. You can rerun later; completed rows are saved.")
    return False


def wait_for_submit_or_killerducky(driver, max_wait=300):
    """Wait through slow Cloudflare/page loads until submit is clickable or output page opens."""
    start = time.time()
    last_notice = 0

    while time.time() - start < max_wait:
        if "/killerducky/" in (driver.current_url or ""):
            return "killerducky"

        if looks_like_cloudflare(driver):
            if not wait_if_cloudflare(driver, max_wait=min(240, max_wait)):
                return None
            continue

        try:
            submit_btn = WebDriverWait(driver, 5).until(
                EC.element_to_be_clickable((By.CSS_SELECTOR, 'button[type="submit"][name="submitBtn"]'))
            )
            return submit_btn
        except TimeoutException:
            now = time.time()
            if now - last_notice > 30:
                remaining = int(max_wait - (now - start))
                print(f"  [wait] Submit button not ready yet; waiting up to {remaining}s more.")
                last_notice = now
            time.sleep(2)

    print("  [warn] Submit button still not found after extended wait.")
    return None


def get_player_id_by_nickname(driver, nickname):
    print(f"[search] Searching player nickname: {nickname}")
    if not safe_get(driver, "https://amae-koromo.sapk.ch/"):
        return None
    if not wait_if_cloudflare(driver):
        return None

    selectors = [
        (By.ID, "mui-3"),
        (By.CSS_SELECTOR, "input[type='text']"),
        (By.CSS_SELECTOR, "input[role='combobox']"),
    ]

    search_box = None
    for by, selector in selectors:
        try:
            search_box = WebDriverWait(driver, 12).until(
                EC.element_to_be_clickable((by, selector))
            )
            break
        except TimeoutException:
            continue

    if search_box is None:
        print("[error] Search box not found. Try using --player-id to skip nickname search.")
        return None

    search_box.clear()
    search_box.send_keys(nickname)
    human_sleep()

    try:
        first_option = WebDriverWait(driver, 12).until(
            EC.element_to_be_clickable((By.CSS_SELECTOR, "ul[role='listbox'] li"))
        )
        first_option.click()
        WebDriverWait(driver, 15).until(lambda d: "/player/" in d.current_url)
    except TimeoutException:
        print("[error] No player option appeared or player page did not open.")
        return None

    match = re.search(r"/player/(\d+)", driver.current_url)
    if not match:
        print(f"[error] Could not parse player id from URL: {driver.current_url}")
        return None

    player_id = match.group(1)
    print(f"[search] Player id: {player_id}")
    return player_id


def extract_latest_mjai_links(driver, player_id, room_code, num_games):
    url = f"https://amae-koromo.sapk.ch/player/{player_id}/{room_code}"
    print(f"[links] Opening player room page: {url}")
    if not safe_get(driver, url):
        return []
    if not wait_if_cloudflare(driver):
        return []

    time.sleep(4)
    seen_indices = set()
    link_records = []
    no_new_rounds = 0
    scrolls = 0

    while len(link_records) < num_games and no_new_rounds < 10 and scrolls < 140:
        rows = driver.find_elements(By.XPATH, "//div[@aria-rowindex]")
        before = len(link_records)

        for row in rows:
            try:
                idx_text = row.get_attribute("aria-rowindex")
                if not idx_text:
                    continue
                idx = int(idx_text)
                if idx in seen_indices or idx > num_games:
                    continue

                ai_tags = row.find_elements(
                    By.XPATH,
                    ".//a[contains(@title, 'AI') or contains(@href, 'mjai.ekyu.moe')]",
                )
                for tag in ai_tags:
                    href = tag.get_attribute("href")
                    if href and "mjai.ekyu.moe" in href:
                        seen_indices.add(idx)
                        link_records.append((idx, href))
                        print(f"  [links] #{idx}: {href}")
                        break
            except Exception:
                continue

        no_new_rounds = no_new_rounds + 1 if len(link_records) == before else 0
        if len(link_records) >= num_games:
            break

        driver.execute_script("window.scrollBy(0, 700);")
        scrolls += 1
        time.sleep(0.7)

    link_records.sort(key=lambda item: item[0])
    result = link_records[:num_games]
    print(f"[links] Found {len(result)}/{num_games} MJAI links.")
    return result


def read_existing_csv(path):
    if not path.exists():
        return {}

    rows_by_mjai = {}
    with path.open("r", encoding="utf-8-sig", newline="") as file:
        for row in csv.DictReader(file):
            mjai_url = row.get("mjai_url")
            if mjai_url:
                rows_by_mjai[mjai_url] = row
    return rows_by_mjai


def average_rating(rows):
    ratings = [to_number(row.get("rating")) for row in rows]
    ratings = [value for value in ratings if value is not None]
    return sum(ratings) / len(ratings) if ratings else None


def average_ai_consistency(rows):
    consistencies = [to_percentage(row.get("ai_consistency")) for row in rows]
    consistencies = [value for value in consistencies if value is not None]
    return sum(consistencies) / len(consistencies) if consistencies else None


def write_csv(path, rows):
    tmp_path = path.with_suffix(".tmp")
    with tmp_path.open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.writer(file)
        rating_avg = average_rating(rows)
        consistency_avg = average_ai_consistency(rows)

        writer.writerow(
            CSV_FIELDS
            + ["", ""]
            + ["rating均值：", f"{rating_avg:.2f}" if rating_avg is not None else ""]
        )

        if not rows:
            writer.writerow([""] * len(CSV_FIELDS) + ["", ""] + ["一致率均值：", ""])
        else:
            for row_index, row in enumerate(rows):
                summary_cells = ["", ""]
                if row_index == 0:
                    summary_cells = [
                        "一致率均值：",
                        f"{consistency_avg:.2f}%" if consistency_avg is not None else "",
                    ]
                writer.writerow([row.get(field, "") for field in CSV_FIELDS] + ["", ""] + summary_cells)
    tmp_path.replace(path)


def click_submit_and_wait_killerducky(driver, mjai_url):
    if not safe_get(driver, mjai_url):
        return None
    if not wait_if_cloudflare(driver):
        return None

    submit_btn = wait_for_submit_or_killerducky(driver, max_wait=300)
    if submit_btn is None:
        return None
    if submit_btn != "killerducky":
        try:
            driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", submit_btn)
            human_sleep(0.4, 0.9)
            submit_btn.click()
        except WebDriverException as exc:
            print(f"  [warn] Could not click submit button: {exc.__class__.__name__}")
            return None

    start = time.time()
    while time.time() - start < 150:
        if "/killerducky/" in driver.current_url:
            return driver.current_url
        wait_if_cloudflare(driver, max_wait=12)
        time.sleep(2)

    return None


def wait_after_killerducky_open(driver, seconds=KILLERDUCKY_SETTLE_SECONDS):
    print(f"  [wait] KillerDucky JSON page opened; waiting {seconds}s for analysis to finish.")
    end = time.time() + seconds
    while time.time() < end:
        wait_if_cloudflare(driver, max_wait=5)
        time.sleep(1)


def read_cell_text(element):
    return (element.get_attribute("innerText") or element.text or "").strip()


def find_about_button(driver, timeout=45):
    selectors = [
        (By.XPATH, "//button[contains(normalize-space(.), 'About')]"),
        (By.XPATH, "//button[contains(translate(normalize-space(.), 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'about')]"),
        (By.CSS_SELECTOR, "button[data-bs-target='#about-modal']"),
        (By.CSS_SELECTOR, "button[aria-controls='about-modal']"),
    ]

    end = time.time() + timeout
    while time.time() < end:
        if looks_like_cloudflare(driver):
            wait_if_cloudflare(driver, max_wait=30)
            continue

        for by, selector in selectors:
            try:
                button = WebDriverWait(driver, 3).until(EC.element_to_be_clickable((by, selector)))
                return button
            except TimeoutException:
                continue
        time.sleep(1)

    return None


def values_are_complete(rating, ai_consistency):
    return rating not in ("ERROR", "N/A", "") and ai_consistency not in ("ERROR", "N/A", "")


def extract_about_values(driver, max_wait=ABOUT_VALUES_MAX_WAIT):
    start = time.time()
    last_row_count = 0
    last_text_sample = ""

    rating = "N/A"
    ai_consistency = "N/A"

    while time.time() - start < max_wait:
        about_btn = find_about_button(driver, timeout=8)
        if about_btn is None:
            print("  [wait] About button not ready yet.")
            time.sleep(2)
            continue

        try:
            driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", about_btn)
            human_sleep(0.2, 0.5)
            about_btn.click()
            WebDriverWait(driver, 15).until(EC.presence_of_element_located((By.ID, "about-modal")))
        except (TimeoutException, WebDriverException):
            time.sleep(2)
            continue

        rows = driver.find_elements(By.XPATH, '//*[@id="about-modal"]//table//tr')
        last_row_count = len(rows)
        row_texts = []

        for tr in rows:
            cells = tr.find_elements(By.TAG_NAME, "td")
            if len(cells) < 2:
                continue

            key = read_cell_text(cells[0]).lower()
            value = read_cell_text(cells[1])
            row_texts.append(f"{key}={value}")

            if "rating" in key:
                rating = value or rating
            if "consistency" in key or "一致" in key:
                ai_consistency = value or ai_consistency

        # Keep the old working assumption as fallback: row 8 is consistency, row 9 is rating.
        if ai_consistency in ("", "N/A") and len(rows) > 7:
            cells = rows[7].find_elements(By.TAG_NAME, "td")
            if len(cells) >= 2:
                ai_consistency = read_cell_text(cells[1]) or ai_consistency
        if rating in ("", "N/A") and len(rows) > 8:
            cells = rows[8].find_elements(By.TAG_NAME, "td")
            if len(cells) >= 2:
                rating = read_cell_text(cells[1]) or rating

        if values_are_complete(rating, ai_consistency):
            return rating, ai_consistency

        last_text_sample = " | ".join(row_texts[:12])
        print(
            f"  [wait] About opened but values are not ready "
            f"(rows={last_row_count}, rating={rating}, ai_consistency={ai_consistency})."
        )
        time.sleep(4)

    print(f"  [warn] About values still missing after {max_wait}s. rows={last_row_count}; sample={last_text_sample}")

    return rating, ai_consistency


def process_one(driver, idx, mjai_url, retries=2):
    for attempt in range(1, retries + 2):
        print(f"\n[game] #{idx} attempt {attempt}/{retries + 1}")
        killerducky_url = click_submit_and_wait_killerducky(driver, mjai_url)
        if not killerducky_url:
            print("  [warn] Could not reach KillerDucky page.")
            time.sleep(4 * attempt)
            continue

        data_path = parse_qs(urlparse(killerducky_url).query).get("data", [None])[0]
        json_url = f"https://mjai.ekyu.moe{data_path}" if data_path and data_path.endswith(".json") else "ERROR"

        wait_after_killerducky_open(driver)
        rating, ai_consistency = extract_about_values(driver)
        status = "OK" if values_are_complete(rating, ai_consistency) else "PARTIAL"
        print(f"  [game] rating={rating}, ai_consistency={ai_consistency}, status={status}")
        if status == "OK":
            return {
                "index": idx,
                "mjai_url": mjai_url,
                "json_url": json_url,
                "rating": rating,
                "ai_consistency": ai_consistency,
                "status": status,
            }

        print("  [warn] JSON exists, but rating values were not ready; retrying this game.")
        time.sleep(5 * attempt)

    return {
        "index": idx,
        "mjai_url": mjai_url,
        "json_url": "ERROR",
        "rating": "ERROR",
        "ai_consistency": "ERROR",
        "status": "ERROR",
    }


def to_number(text):
    if not text or text in ("ERROR", "N/A"):
        return None
    match = re.search(r"([0-9]+(?:\.[0-9]+)?)", str(text))
    return float(match.group(1)) if match else None


def to_percentage(text):
    if not text or text in ("ERROR", "N/A"):
        return None
    text = str(text)
    percent_matches = re.findall(r"([0-9]+(?:\.[0-9]+)?)\s*%", text)
    if percent_matches:
        return float(percent_matches[-1])
    if "=" in text:
        return to_number(text.rsplit("=", 1)[-1])
    return to_number(text)


def summarize(rows):
    ratings = [to_number(row.get("rating")) for row in rows]
    ratings = [value for value in ratings if value is not None]
    consistencies = [to_percentage(row.get("ai_consistency")) for row in rows]
    consistencies = [value for value in consistencies if value is not None]

    print("\n[summary]")
    print(f"  valid ratings: {len(ratings)} / {len(rows)}")
    if ratings:
        print(f"  average rating: {sum(ratings) / len(ratings):.2f}")
    if consistencies:
        print(f"  average AI consistency: {sum(consistencies) / len(consistencies):.2f}%")


def parse_args():
    parser = argparse.ArgumentParser(description="Conservative v2 RT/rating lookup tool.")
    parser.add_argument("--nickname", help="player nickname")
    parser.add_argument("--player-id", help="skip nickname search and use this player id")
    parser.add_argument("--room", choices=["9", "12"], help="9=gold room, 12=jade room")
    parser.add_argument("--games", type=int, help="number of recent games to query")
    parser.add_argument("--headless", action="store_true", help="run headless; not recommended with Cloudflare")
    parser.add_argument("--reset-profile", action="store_true", help="delete v2 Chrome profile before running")
    return parser.parse_args()


def main():
    args = parse_args()

    if args.reset_profile and PROFILE_DIR.exists():
        shutil.rmtree(PROFILE_DIR)
        print(f"[profile] Removed profile: {PROFILE_DIR}")

    nickname = args.nickname or input("nickname: ").strip()
    room = args.room or input("room id (9=gold, 12=jade): ").strip()
    games_text = args.games or input("number of recent games: ").strip()
    games = int(games_text)

    if not args.headless:
        print("[mode] Visible Chrome is the default. This is recommended for manual Cloudflare verification.")
    else:
        print("[mode] Headless enabled. If Cloudflare appears, this may fail.")

    driver = build_driver(headless=args.headless)
    try:
        player_id = args.player_id or get_player_id_by_nickname(driver, nickname)
        if not player_id:
            print("[error] No player id. Rerun with --player-id if you know it.")
            return

        room_name = ROOM_NAMES.get(room, room)
        safe_nickname = re.sub(r'[\\/:*?"<>|]+', "_", nickname) or player_id
        output_file = OUTPUT_DIR / f"{safe_nickname}_{room_name}_recent_{games}_v2.csv"
        existing = read_existing_csv(output_file)

        links = extract_latest_mjai_links(driver, player_id, room, games)
        if not links:
            print("[error] No MJAI links found.")
            return

        results = []
        for idx, mjai_url in links:
            old_row = existing.get(mjai_url)
            if old_row and old_row.get("status") == "OK":
                print(f"\n[skip] #{idx} already completed.")
                old_row["index"] = idx
                results.append(old_row)
                continue

            row = process_one(driver, idx, mjai_url)
            results.append(row)
            write_csv(output_file, results)
            print(f"  [save] checkpoint: {output_file}")
            human_sleep(2.5, 5.0)

        write_csv(output_file, results)
        print(f"\n[done] CSV saved: {output_file}")
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
        print("\n[stop] Interrupted. Completed rows already written to CSV.")
        sys.exit(130)
