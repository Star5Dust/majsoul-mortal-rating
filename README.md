# 查 RT 工具

自动查询雀魂玩家最近对局的 mortal 评分（rating）与 AI 一致率（ai_consistency），结果保存为 CSV。

## 工作原理（v6）

1. 用牌谱屋接口按昵称搜玩家 ID（纯 HTTP，不开浏览器）
2. 打开一个 Chrome，在牌谱屋玩家页读最近 N 局的牌谱 ID
3. 查过的牌谱直接从本地缓存 `cache/reports.jsonl` 读，不再提交
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

依赖：`pip install selenium undetected-chromedriver`（需安装 Chrome）。

## 常用参数

| 参数 | 说明 |
| --- | --- |
| `--nickname` / `--player-id` | 玩家昵称 / 玩家 ID（填了 ID 就不搜昵称） |
| `--room` | 9=金之间，12=玉之间，16=王座之间 |
| `--games` | 查询最近 N 局 |
| `--model` | Mortal 模型，默认 `4.1b`（和网页默认一致） |
| `--refresh` | 忽略缓存，全部重新提交 |
| `--parallel` | 同时在 mjai 上处理几局，默认 2；遇到限流会自动减少并暂停 |

## 输出

CSV 保存到 `outputs_all/{昵称}_{房间}_recent_{N}_v6.csv`，一局一行：`index`、`rating`、`ai_consistency`（%）、`matches`（一致步数/总步数）、`paipu`、`report_url`、`status`（OK / cache / ERROR），最后一行是均值。

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
| `v6` | **当前最新版**，单 Chrome + 接口读报告 + 按牌谱 ID 全局缓存 |

## 注意事项

- 请保持 Chrome 窗口可见。如果出现人机验证，程序会响一声提示，手动点一下即可，程序会一直等
- 中途断开也没关系，查过的局都在缓存里，重跑会直接跳过
