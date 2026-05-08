# majsoul-mortal-rating

雀魂对局评分批量提取工具。脚本会从雀魂牌谱屋提取指定玩家在金之间 / 玉之间的最近对局链接，提交到 mortal 页面分析，并把 Rating 与 AI 一致率导出为 CSV。

数据来源：

- 雀魂牌谱屋：https://amae-koromo.sapk.ch
- mortal / KillerDucky：https://mjai.ekyu.moe

## 功能

- 按昵称搜索玩家 ID，也支持直接传入 `--player-id`
- 提取金之间 `9` / 玉之间 `12` 最近 N 场带 AI 链接的对局
- 自动提交到 mortal 分析页面
- 从 About 弹窗读取 `rating` 与 `ai_consistency`
- 每完成一局写入一次 CSV，失败后可重跑续查
- 终端输出平均 Rating 与平均 AI 一致率

## 版本选择

| 脚本 | 适合场景 |
| --- | --- |
| `查rt工具_v2.py` | 稳定优先，单 Chrome 顺序处理 |
| `查rt工具_v3.py` | 加速版，多 Chrome 并行处理，默认 2 workers |

建议先用 v2 确认环境可用；批量查询再用 v3。v3 不建议开太多并发，同一 IP 高频访问可能触发 Cloudflare 或 rate limit。

## 安装

需要 Python 3.8+ 与 Google Chrome。

```bash
pip install selenium undetected-chromedriver webdriver-manager requests
```

如果安装 `undetected-chromedriver` 失败：

```bash
pip install --upgrade pip setuptools
```

## 使用

前台 Chrome 更稳定，遇到 Cloudflare 验证时请在打开的浏览器窗口中手动完成。

### v2 稳定版

```bash
python 查rt工具_v2.py --nickname "玩家昵称" --room 12 --games 10
```

已知玩家 ID 时可跳过昵称搜索：

```bash
python 查rt工具_v2.py --nickname "玩家昵称" --player-id 123456789 --room 12 --games 10
```

### v3 并行版

```bash
python 查rt工具_v3.py --nickname "玩家昵称" --room 12 --games 20 --workers 2
```

如果 2 开稳定，可以谨慎尝试 3 开并增加冷却：

```bash
python 查rt工具_v3.py --nickname "玩家昵称" --room 12 --games 20 --workers 3 --start-stagger 20 --pre-delay-min 8 --pre-delay-max 25 --post-delay-min 15 --post-delay-max 40
```

不建议长期使用 `--workers 4` 以上；如果出现 rate limit，降低 workers 或等待一段时间再跑。

## 参数

| 参数 | 说明 |
| --- | --- |
| `--nickname` | 玩家昵称 |
| `--player-id` | 玩家 ID，传入后跳过昵称搜索 |
| `--room` | 房间：`9` 金之间，`12` 玉之间 |
| `--games` | 查询最近 N 场 |
| `--headless` | 后台模式，不推荐 |
| `--reset-profile` | 删除对应版本的 Chrome profile 后重跑 |
| `--workers` | v3 并行 Chrome 数量，默认 2 |
| `--start-stagger` | v3 worker 启动错峰秒数 |
| `--pre-delay-min/max` | v3 每局开始前随机等待 |
| `--post-delay-min/max` | v3 每局结束后随机等待 |

## 输出

v2 输出到 `outputs_v2/`，v3 输出到 `outputs_v3/`。

CSV 文件名格式：

```text
{昵称}_{房间}_recent_{N}_v2.csv
{昵称}_{房间}_recent_{N}_v3.csv
```

字段：

| 字段 | 说明 |
| --- | --- |
| `index` | 最近对局序号 |
| `mjai_url` | mortal 对局页面链接 |
| `json_url` | 对局 JSON 链接 |
| `rating` | mortal Rating |
| `ai_consistency` | AI 一致率 |
| `status` | `OK` / `PARTIAL` / `ERROR` |

CSV 使用 UTF-8 BOM 编码，通常可直接用 Excel / WPS 打开。

## 常见问题

**触发 Cloudflare 验证**

在打开的 Chrome 窗口中手动完成验证，脚本会自动继续。验证状态会保存在对应的 `chrome_user_data_v2` 或 `chrome_user_data_v3` 目录。

**v3 出现 rate limit**

降低 `--workers`，推荐先用 2。若 3 开也不稳定，增加 `--start-stagger` 和每局前后 delay，或等待一段时间后重跑。

**没有找到 AI 对局链接**

该玩家最近在对应房间可能没有带 AI 标签的对局，换房间、增加场数或换玩家再试。

**结果中有 ERROR / PARTIAL**

可能是页面加载慢、网络波动、Cloudflare、rate limit 或 About 数据尚未生成。脚本会保存已完成结果，可直接重跑补齐。

## 说明

本工具仅用于个人学习与研究。请控制查询频率，遵守目标网站的使用条款。
