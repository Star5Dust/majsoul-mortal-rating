# 查 RT 工具

自动查询雀魂玩家最近对局的 mortal 评分（rating）与 AI 一致率（ai_consistency），结果汇总在一个 Excel（`rt结果.xlsx`）里。

## 工作原理（v6）

1. 用牌谱屋接口按昵称搜玩家 ID（纯 HTTP，不开浏览器）
2. 打开一个 Chrome，在牌谱屋玩家页读最近 N 局的牌谱 ID
3. 查过的牌谱直接从本地缓存 `data/reports.jsonl` 读，不再提交
4. 没查过的在同一个 mjai.ekyu.moe 表单页里提交（Turnstile 通过后页内提交，不刷新页面），同时让几局一起在服务器上分析，进度用 HTTP 查询
5. 直接下载报告 JSON 读 rating 和 AI 一致率，不打开 KillerDucky

## 快速开始

```powershell
python 查rt工具.py
```

未传参数时按提示输入昵称、房间（9=金之间，12=玉之间，16=王座之间）、对局数。也可以直接传参：

```powershell
python 查rt工具.py --nickname "玩家昵称" --room 12 --games 20
```

依赖：`pip install selenium undetected-chromedriver openpyxl`（需安装 Chrome）。

## 常用参数

| 参数 | 说明 |
| --- | --- |
| `--nickname` / `--player-id` | 玩家昵称 / 玩家 ID（填了 ID 就不搜昵称） |
| `--room` | 9=金之间，12=玉之间，16=王座之间 |
| `--games` | 查询最近 N 局 |
| `--model` | Mortal 模型，默认 `4.1b`（和网页默认一致） |
| `--refresh` | 忽略缓存，全部重新提交 |
| `--parallel` | 同时在 mjai 上处理几局，默认 1；遇到限流会自动减少，并按 Retry-After 或 3/8/20/60 秒短暂停后用新 token 重提 |
| `--export` | 只重新生成 `rt结果.xlsx`（比如上次生成时 Excel 正开着） |
| `--import-csv 文件夹` | 把旧版本生成的 CSV 导入总表（按文件名去重，只导一次） |

## 输出

所有结果都在一个总表 `rt结果.xlsx` 里，每次运行结束自动更新：

- **对局**：一局一行，`玩家, 房间, 对局日期, rating, 一致率%, 一致步数, 牌谱ID, 报告链接, 查询时间, 来源`。同一玩家同一房间的同一局只保留最新一次查询。对局日期取自牌谱 ID 开头的 yymmdd。
- **查询记录**：每次运行一行，`查询时间, 玩家, 房间, 局数, 成功, rating均值, 一致率均值%, 来源`，最新的在最上面。
- **趋势**（打开就是这页）：在 B1 下拉选「玩家 | 房间」，下面的表和两张图（rating、AI 一致率，点是每局，线是最近 10 局均线）跟着换。默认显示最近一次查询的玩家。
- **排行**：每个玩家每个房间一行（局数、rating 均值、最近 10 局、最高、最低、一致率均值），旁边每个房间一张 rating 均值条形图（至少 10 局的玩家）。

运行时 Excel 如果正开着 `rt结果.xlsx`，表格没法写入，关掉后运行 `python 查rt工具.py --export` 即可，数据不会丢。

## 文件夹

```
查rt工具/
├─ 查rt工具.py      最新版脚本
├─ rt结果.xlsx      总结果表
├─ data/            运行数据，不要手动改
│  ├─ chrome_profile/   浏览器配置（含 Cloudflare 验证状态）
│  ├─ reports.jsonl     按 牌谱ID+模型 缓存的分析结果
│  └─ history.jsonl     每次查询的记录，rt结果.xlsx 由它生成
└─ old/             旧版本脚本、实验文件、旧浏览器配置和旧 CSV（不上传）
```

## 版本迭代

只保留一个脚本 `查rt工具.py`，历史版本用 git tag 管理。查看或运行旧版本：

```powershell
git tag                      # 列出所有版本
git checkout v4.1            # 切换到某个版本
git checkout main            # 回到最新版
```

| tag | 说明 |
| --- | --- |
| `legacy-1.0` ~ `legacy-3.1` | 最初的单 Chrome 串行版本（原 `旧版内容/`） |
| `stable` | 原 `查rt稳定版.py`：单浏览器前台 + checkpoint，最稳但慢 |
| `v2` | 复用 Chrome profile，Cloudflare 验证后可持续复用 |
| `v3` | 多 worker 并行，速度大幅提升 |
| `v4` / `v4.1` | 去掉冷却延迟，异常自动重启浏览器 |
| `v5` | 自动处理 Cloudflare 1015 限流重试 |
| `v5.1` | 1015 自动重试、worker 卡死重开、独立 profile、多窗口自动布局、Chrome 启动失败重试 |
| `v5.1-test` | v5.1 的测试分支：Cloudflare 手动验证等待延长到 600 秒，单 worker |
| `v6` | 单 Chrome + 接口读报告 + 按牌谱 ID 全局缓存 |
| `v6.1` | 进度改用 HTTP 查询、限流短暂停重提、可选并行（实测默认 1 最合适） |
| `v6.2` | **当前最新版**，结果改存总表 `rt结果.xlsx`（带趋势图和排行），运行数据移到 `data/` |

## 注意事项

- 请保持 Chrome 窗口可见。如果出现人机验证，程序会响一声提示，手动点一下即可，程序会一直等
- 中途断开也没关系，查过的局都在缓存里，重跑会直接跳过，已完成的局也会写进总表
