# 查3.0_优化版.py
import os
import time
import json
import requests
import csv
from urllib.parse import urlparse, parse_qs
import undetected_chromedriver as uc
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
import re
import tempfile


def setup_chrome_options(headless=False, user_data_dir=None):
    """
    统一的 Chrome 选项配置
    """
    options = uc.ChromeOptions()

    if user_data_dir:
        abs_path = os.path.abspath(user_data_dir)
        options.add_argument(f"--user-data-dir={abs_path}")
        options.add_argument("--no-first-run")
        options.add_argument("--disable-extensions")

    options.add_argument("--lang=zh-CN")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--disable-gpu")
    options.add_argument("--no-proxy-server")
    options.add_argument("--proxy-server='direct://'")
    options.add_argument("--proxy-bypass-list=*")
    options.add_argument("--disable-background-timer-throttling")
    options.add_argument("--disable-renderer-backgrounding")
    options.add_argument("--disable-backgrounding-occluded-windows")
    options.add_argument("--disable-ipc-flooding-protection")

    # 添加额外的反检测参数
    options.add_argument("--disable-blink-features=AutomationControlled")
    #options.add_experimental_option("excludeSwitches", ["enable-automation"])
    #options.add_experimental_option('useAutomationExtension', False)

    if headless:
        options.add_argument("--headless=new")

    return options


def get_player_id_by_nickname(nickname, driver):
    """
    通过昵称获取玩家ID（复用现有浏览器）
    返回: player_id 或 None
    """
    print(f"🔍 正在搜索玩家: {nickname}")

    try:
        driver.get("https://amae-koromo.sapk.ch/")

        # 设置页面加载超时
        driver.set_page_load_timeout(30)

        # 等待搜索框
        search_box = WebDriverWait(driver, 10).until(
            EC.element_to_be_clickable((By.ID, "mui-3"))
        )
        search_box.clear()
        search_box.send_keys(nickname)
        print(f"✅ 已输入昵称: {nickname}")

        # 等待并点击第一个下拉选项
        try:
            first_option = WebDriverWait(driver, 10).until(
                EC.element_to_be_clickable((By.CSS_SELECTOR, "ul[role='listbox'] li"))
            )
            print("✅ 检测到匹配项，正在点击...")
            first_option.click()

            # 等待跳转完成
            WebDriverWait(driver, 15).until(
                lambda d: "/player/" in d.current_url
            )

            # 🔑 提取玩家 ID
            current_url = driver.current_url
            match = re.search(r'/player/(\d+)', current_url)
            if match:
                player_id = match.group(1)
                print(f"🎯 玩家ID: {player_id}")
                return player_id
            else:
                print("❌ 无法从 URL 提取玩家 ID")
                return None

        except Exception as e:
            print("❌ 未找到匹配的玩家（下拉列表未出现）")
            print(f"当前页面: {driver.current_url}")
            return None

    except Exception as e:
        print(f"🚨 搜索过程异常: {repr(e)}")
        return None


def extract_latest_mjai_links(player_id, room_code, num_games, driver):
    """
    提取 MJAI 链接（复用现有浏览器）
    """
    url = f"https://amae-koromo.sapk.ch/player/{player_id}/{room_code}"
    print(f"🌐 正在加载: {url}")

    try:
        driver.get(url)
        time.sleep(5)  # 🔥 确保页面完全加载

        print(f"🔽 开始滚动，寻找前 {num_games} 个对局...")
        seen_indices = set()
        link_records = []
        consecutive_no_new = 0
        max_no_new = 5
        total_scrolls = 0

        while consecutive_no_new < max_no_new and len(link_records) < num_games * 2:
            driver.execute_script(f"window.scrollBy(0, 500);")
            total_scrolls += 1
            time.sleep(0.5)  # 🔥 确保滚动后元素加载

            # 🔥 等待元素出现后再查找
            try:
                # 等待至少有一个带 aria-rowindex 的元素出现
                WebDriverWait(driver, 10).until(
                    EC.presence_of_element_located((By.XPATH, "//div[@aria-rowindex]"))
                )
            except:
                print("  ⚠️ 未找到任何行元素，继续滚动...")
                pass

            current_rows = driver.find_elements(By.XPATH, "//div[@aria-rowindex]")
            new_links = 0

            for row in current_rows:
                try:
                    idx_str = row.get_attribute("aria-rowindex")
                    if not idx_str:
                        continue
                    idx = int(idx_str)
                    if idx not in seen_indices and idx <= num_games:
                        seen_indices.add(idx)
                        ai_tags = row.find_elements(By.XPATH, ".//a[contains(@title, 'AI')]")
                        for tag in ai_tags:
                            href = tag.get_attribute("href")
                            if href and "mjai.ekyu.moe" in href:
                                link_records.append((idx, href))
                                new_links += 1
                except Exception:
                    continue

            if new_links > 0:
                consecutive_no_new = 0
            else:
                consecutive_no_new += 1

            print(f"  第 {total_scrolls} 次 → 新增: {new_links}, 累计: {len(link_records)} (目标: {num_games})")

            if len(link_records) >= num_games:
                break

        link_records.sort(key=lambda x: x[0])
        result = link_records[:num_games]
        print(f"✅ 找到 {len(result)} 个链接")
        return result

    except Exception as e:
        print(f"❌ 提取链接失败: {e}")
        return []


def get_and_extract_rating_from_killerducky(mjai_url, driver):
    """
    增强版：支持人工过 Cloudflare 后自动继续
    """
    try:
        print(f"  🌐 访问: {mjai_url}")
        driver.get(mjai_url)
        time.sleep(2)  # 增加等待时间

        # 尝试找到并点击Submit按钮
        try:
            submit_btn = WebDriverWait(driver, 15).until(
                EC.element_to_be_clickable((By.CSS_SELECTOR, 'button[type="submit"][name="submitBtn"]'))
            )
            driver.execute_script("arguments[0].scrollIntoView();", submit_btn)
            time.sleep(1)
            submit_btn.click()
            print("  ✅ 已点击 Submit")
        except:
            print("  ⚠️ 未找到Submit按钮，可能是Cloudflare验证页面")
            # 等待用户手动完成验证
            print("  ⚠️ 如遇到Cloudflare验证，请手动完成，完成后脚本将继续执行...")
            wait_time = 0
            max_wait = 120  # 最多等待2分钟
            while wait_time < max_wait:
                current_url = driver.current_url
                if "challenges.cloudflare.com" not in current_url and "/killerducky/" in current_url:
                    print("  ✅ 检测到已通过验证，继续处理...")
                    break
                time.sleep(5)
                wait_time += 5

        print("  ⏳ 等待跳转到 KillerDucky...")
        start_wait = time.time()
        killerducky_url = None

        while time.time() - start_wait < 60:
            current_url = driver.current_url
            if "/killerducky/" in current_url:
                killerducky_url = current_url
                break
            elif "challenges.cloudflare.com" in current_url:
                print("  ⚠️ 检测到 Cloudflare 验证，请人工完成（完成后脚本将自动继续）")
                time.sleep(5)
            else:
                time.sleep(2)

        if not killerducky_url:
            print("  ❌ 超时：未进入 KillerDucky 页面")
            return "ERROR", "ERROR", "ERROR"

        print(f"  ✅ 成功进入: {killerducky_url}")

        # 提取 JSON 链接
        parsed = urlparse(killerducky_url)
        data_path = parse_qs(parsed.query).get("data", [None])[0]
        json_url = f"https://mjai.ekyu.moe{data_path}" if data_path and data_path.endswith(".json") else "ERROR"
        print(f"  📁 JSON 链接: {json_url}")

        # 等待页面加载完成
        time.sleep(3)

        # 点击 About 并提取数据
        try:
            about_btn = WebDriverWait(driver, 15).until(
                EC.element_to_be_clickable((By.XPATH, '//button[contains(text(), "About")]'))
            )
            about_btn.click()
            WebDriverWait(driver, 10).until(EC.presence_of_element_located((By.ID, "about-modal")))

            trs = driver.find_elements(By.XPATH, '//*[@id="about-modal"]//table/tbody/tr')
            ai_consistency = rating = "N/A"
            if len(trs) >= 9:
                if len(trs[7].find_elements(By.TAG_NAME, "td")) >= 2:
                    ai_consistency = trs[7].find_elements(By.TAG_NAME, "td")[1].text.strip()
                if len(trs[8].find_elements(By.TAG_NAME, "td")) >= 2:
                    rating = trs[8].find_elements(By.TAG_NAME, "td")[1].text.strip()

            return json_url, rating, ai_consistency
        except Exception as e:
            print(f"  ❌ 提取数据时出错: {repr(e)}")
            return json_url, "ERROR", "ERROR"

    except Exception as e:
        print(f"  ❌ 异常: {repr(e)}")
        return "ERROR", "ERROR", "ERROR"


def main():
    print("🎯 请输入查询参数：")

    nickname = input("玩家昵称: ").strip()
    if not nickname:
        print("❌ 昵称不能为空。")
        return

    room_code = input("房间ID (9=金之间, 12=玉之间): ").strip()
    num_games = int(input("查找对局数: ").strip())

    # 🔥 询问是否后台运行
    headless_input = input("是否后台运行？(Y/n): ").strip().lower()
    HEADLESS = headless_input != 'n' and headless_input != 'N'

    # 🔥 创建持久化用户数据目录（保留 Cloudflare 验证状态）
    user_data_dir = os.path.join(tempfile.gettempdir(), f"mjai_uc_{nickname}_{int(time.time())}")

    print(f"\n📁 用户数据目录: {user_data_dir}")
    print("\n🔧 启动浏览器...")

    options = setup_chrome_options(headless=HEADLESS, user_data_dir=user_data_dir)
    driver = uc.Chrome(options=options, use_subprocess=True)

    # 隐藏webdriver特征
    driver.execute_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")

    try:
        # 🔥 获取玩家ID（复用浏览器）
        print("\n🔍 正在获取玩家ID...")
        player_id = get_player_id_by_nickname(nickname, driver)
        if not player_id:
            print("❌ 无法获取玩家ID，程序退出。")
            return

        print(f"✅ 成功获取玩家ID: {player_id}")

        ROOM_NAMES = {"9": "金", "12": "玉"}
        room_name = ROOM_NAMES.get(room_code, f"{room_code}房")

        mode_text = "后台" if HEADLESS else "前台"
        print(f"\n🚀 开始处理玩家 {nickname} ({player_id}) 房间 {room_name}（{room_code}）的前 {num_games} 场对局")
        print(f"   运行模式: {mode_text}")

        # 阶段1：获取 mjai 链接（复用浏览器）
        print("\n1️⃣ 获取 MJAI 链接...")
        index_mjai_pairs = extract_latest_mjai_links(player_id, room_code, num_games, driver)
        if not index_mjai_pairs:
            print("❌ 未找到任何对局链接")
            return

        print(f"✅ 获取到 {len(index_mjai_pairs)} 个链接")

        # 阶段2：获取评分（复用同一个浏览器！）
        print("\n2️⃣ 获取评分数据（此阶段可能较慢，请耐心）...")
        results = []

        for i, (idx, mjai_url) in enumerate(index_mjai_pairs):
            print(f"\n  [{i + 1}/{len(index_mjai_pairs)}] 处理 index={idx}")

            json_url, rating, ai_consistency = get_and_extract_rating_from_killerducky(mjai_url, driver)

            results.append({
                "mjai链接": mjai_url,
                "json链接": json_url,
                "rating": rating,
                "ai一致率": ai_consistency
            })

            print(f"    → rating: {rating}, 一致率: {ai_consistency}")

            # 在处理完一个链接后稍微休息，模拟人类行为
            if i < len(index_mjai_pairs) - 1:  # 不是最后一个
                time.sleep(3 + i % 3)  # 随机延迟

        # 输出 CSV（使用昵称命名）
        output_file = f"{nickname}_{room_name}_近{num_games}场.csv"
        with open(output_file, "w", encoding="utf-8-sig", newline="") as f:
            fieldnames = ["mjai链接", "json链接", "rating", "ai一致率"]
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(results)

        print(f"\n🎉 完成！结果已保存到: {os.path.abspath(output_file)}")
        print(f"📊 共处理 {len(results)} 个对局")

        # 🔥 简化版：只提取百分比数值
        valid_ratings = []
        valid_consistencies = []

        for row in results:
            rating = row["rating"]
            consistency = row["ai一致率"]

            # 处理 rating（数字或百分比）
            if rating != "ERROR" and rating != "N/A":
                try:
                    if isinstance(rating, str) and rating.endswith("%"):
                        valid_ratings.append(float(rating.rstrip("%")))
                    else:
                        valid_ratings.append(float(rating))
                except:
                    pass

            # 🔥 只提取百分比数值，忽略分式部分
            if consistency != "ERROR" and consistency != "N/A":
                try:
                    consistency_str = str(consistency)

                    # 如果包含百分号，直接提取数值
                    if "%" in consistency_str:
                        # 找到最后一个 % 前的数字部分
                        percent_pos = consistency_str.rfind("%")
                        # 向前找数字开始的位置
                        start_pos = percent_pos - 1
                        while start_pos >= 0 and (
                                consistency_str[start_pos].isdigit() or consistency_str[start_pos] == "."):
                            start_pos -= 1
                        start_pos += 1

                        if start_pos < percent_pos:
                            percentage_value = consistency_str[start_pos:percent_pos]
                            valid_consistencies.append(float(percentage_value))
                    else:
                        # 如果没有百分号，直接尝试转换为数字
                        valid_consistencies.append(float(consistency_str))

                except:
                    pass

        avg_rating = sum(valid_ratings) / len(valid_ratings) if valid_ratings else 0
        avg_consistency = sum(valid_consistencies) / len(valid_consistencies) if valid_consistencies else 0

        print(f"\n📈 统计结果:")
        print(f"  平均Rating: {avg_rating:.2f}")
        print(f"  平均一致率: {avg_consistency:.2f}%")
        print(f"  有效对局数: {len(valid_ratings)} / {len(results)}")

        print("\n📋 结果预览:")
        for i, row in enumerate(results[:3]):
            print(f"  {i + 1}. rating={row['rating']}, 一致率={row['ai一致率']}")

    finally:
        # 🔥 统一关闭浏览器
        driver.quit()
        print("\n👋 浏览器已关闭")


if __name__ == "__main__":
    main()



