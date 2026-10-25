# 查 RT 工具

自动查询雀魂玩家最近对局的 mortal 评分（rating）与 AI 一致率（ai_consistency），结果保存为 CSV。

## 工作原理

1. 在牌谱屋 [amae-koromo.sapk.ch](https://amae-koromo.sapk.ch) 搜索玩家，抓取指定房间最近 N 局的 MJAI 链接
2. 把 MJAI 链接提交到 [mjai.ekyu.moe](https://mjai.ekyu.moe) 的 KillerDucky 分析页
3. 读取 rating 和 AI 一致率，逐局写入 CSV checkpoint（中断可续跑）

## 快速开始

```powershell
python 查rt工具.py
```

未传参数时按提示输入昵称、房间（9=金之间，12=玉之间）、对局数。已知玩家 ID 可跳过搜索：

```powershell
python 查rt工具.py --nickname "玩家昵称" --player-id 9139787 --room 12 --games 20 --workers 2
```

依赖：`pip install selenium undetected-chromedriver`（需安装 Chrome）。

## 常用参数

| 参数 | 说明 |
| --- | --- |
| `--nickname` / `--player-id` | 玩家昵称 / 玩家 ID |
| `--room` | 9=金之间，12=玉之间 |
| `--games` | 查询最近 N 局 |
| `--workers` | 并行 Chrome 数量，建议 2~3，默认 2 |
| `--headless` | 无头模式，不建议（Cloudflare 下容易失败） |
| `--reset-profile` | 重置 v5.1 Chrome profile |
| `--rate-limit-retries` | 遇到 Error 1015 限流时自动重试次数 |
| `--worker-stall-restart-seconds` | worker 卡住多少秒后重开浏览器，默认 15 |

## 输出

CSV 保存到 `outputs_all/{昵称}_{房间}_recent_{N}_v5.1.csv`，字段：`index`、`mjai_url`、`json_url`、`rating`、`ai_consistency`、`status`，末尾附带均值统计。

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
| `v5.1` | **当前最新版**，1015 自动重试、worker 卡死重开、独立 profile、多窗口自动布局、Chrome 启动失败重试 |
| `v5.1-test` | v5.1 的测试分支：Cloudflare 手动验证等待延长到 600 秒，单 worker |

## 注意事项

- 请保持 Chrome 窗口可见，Cloudflare 验证需手动完成
- 不要开太多 worker，避免触发限流
- 途中断开也没关系，已完成的局会保留在 CSV 中
