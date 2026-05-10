# majsoul-mortal-rating

一个用于批量查询雀魂牌谱 Mortal/KillerDucky 评分的小工具。

工具会先在 `amae-koromo.sapk.ch` 根据昵称或 player id 找到最近牌谱，再打开 `mjai.ekyu.moe` 的 AI 工具页提交牌谱，最后从 KillerDucky 页面里的“关于 / About”窗口读取：

- `rating`
- `ai_consistency`

结果会写入 CSV，并在每局完成后保存 checkpoint，方便中断后继续跑。

## 推荐版本

推荐使用 `查rt工具_v4.py`。

v4 相比 v3 的主要变化：

- 默认 2 个 Chrome worker 并行处理牌谱。
- 去掉 v3 的每局前后 cooling time。
- 去掉默认 rate-limit 长时间 backoff。
- 进入 KillerDucky `.json` 页面后，不再固定等待页面自动加载，直接点击 `id="about"` 的“关于”按钮读取数据。
- Chrome 启动阶段加锁，避免多个 worker 同时抢 `undetected_chromedriver.exe`。
- 默认把两个 worker Chrome 窗口左右并排摆放。
- 终端结束时输出总耗时。

v3 更保守，适合遇到频繁 rate limit 或页面不稳定时回退使用。

## 安装依赖

需要 Python 环境和 Google Chrome。

```bash
pip install selenium undetected-chromedriver webdriver-manager requests
```

如果 `undetected-chromedriver` 相关依赖异常，可以先升级基础包：

```bash
pip install --upgrade pip setuptools
```

## 运行 v4

交互式运行：

```powershell
& E:\Anaconda\envs\rating_tool\python.exe c:/Users/yyt/Desktop/rating/查rt工具/查rt工具_v4.py
```

也可以直接带参数：

```powershell
& E:\Anaconda\envs\rating_tool\python.exe c:/Users/yyt/Desktop/rating/查rt工具/查rt工具_v4.py --nickname "EternityQ" --room 12 --games 20
```

已知 player id 时，可以跳过昵称搜索：

```powershell
& E:\Anaconda\envs\rating_tool\python.exe c:/Users/yyt/Desktop/rating/查rt工具/查rt工具_v4.py --nickname "EternityQ" --player-id 9139787 --room 12 --games 20
```

## 常用参数

| 参数 | 说明 |
| --- | --- |
| `--nickname` | 玩家昵称。 |
| `--player-id` | 玩家 ID；传入后跳过昵称搜索。 |
| `--room` | 房间：`9` 金之间，`12` 玉之间。 |
| `--games` | 查询最近 N 局。 |
| `--workers` | 并行 Chrome worker 数，默认 `2`。 |
| `--window-size` | Chrome 窗口大小，默认 `920,660`。 |
| `--headless` | 无头模式；不推荐，Cloudflare 验证时容易失败。 |
| `--reset-profile` | 删除 v4 的 Chrome profile 后重新运行。 |

示例：把窗口调小一点：

```powershell
& E:\Anaconda\envs\rating_tool\python.exe c:/Users/yyt/Desktop/rating/查rt工具/查rt工具_v4.py --window-size 760,560
```

## Cloudflare 验证

工具默认使用可视 Chrome，因为偶尔会遇到 Cloudflare 验证。

可能出现验证的位置：

- `amae-koromo.sapk.ch` 查询玩家和牌谱时。
- `mjai.ekyu.moe` / KillerDucky 页面打开时。

如果 Chrome 页面停在 Cloudflare 验证页，或者 mjai 页面要求验证，需要手动在打开的 Chrome 窗口里点一下通过验证。脚本会等待验证结束，然后自动继续。

建议：

- 不要使用 `--headless`，否则看不到验证页面。
- 验证通过后，Chrome profile 会保留一部分状态，后续同版本运行通常会少一些验证。
- 如果页面状态异常，可以用 `--reset-profile` 清掉 v4 profile 后再跑。

## 输出

v4 输出目录：

```text
outputs_all/
```

CSV 文件名格式：

```text
{nickname}_{room}_recent_{N}_v4.csv
```

CSV 字段：

| 字段 | 说明 |
| --- | --- |
| `index` | 最近牌谱序号。 |
| `mjai_url` | mjai 牌谱分析入口。 |
| `json_url` | KillerDucky report JSON 地址。 |
| `rating` | Mortal/KillerDucky rating。 |
| `ai_consistency` | AI 一致率。 |
| `status` | `OK` / `PARTIAL` / `ERROR`。 |

文件使用 UTF-8 BOM 写入，方便 Excel / WPS 直接打开。

## 版本说明

| 文件 | 说明 |
| --- | --- |
| `查rt工具_v4.py` | 当前推荐快版；2 worker 并行，去掉 cooling，直接读 About。 |
| `查rt工具_v3.py` | 保守并行版；有启动错峰、每局前后 cooling 和 rate-limit backoff。 |
| `查rt工具_v2.py` | 旧版单流程脚本。 |

## 常见问题

**启动时卡在下载或查找 ChromeDriver**

`undetected_chromedriver` 启动前可能需要访问 Google 的 Chrome for Testing 地址。如果当前网络连不上，可能需要让 Python 走代理。

**只有一个 Chrome 在动**

v4 为了避免 `undetected_chromedriver.exe` 文件竞争，Chrome 启动阶段是加锁串行的。两个 worker 不一定完全同时开始，但进入处理牌谱后会并行。

**出现 `PARTIAL` 或 `ERROR`**

常见原因是页面没加载完整、Cloudflare 验证未完成、mjai/KillerDucky 短时异常、或 About 数据暂时没读到。可以直接重跑，已有 `OK` 的行会被跳过。

**频繁 rate limit**

先把 `--workers` 降到 `1` 或回退 v3。v4 是个人自用快版，默认不做长时间退避。
