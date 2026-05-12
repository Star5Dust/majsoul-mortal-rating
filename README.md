# 查 RT 工具

自动从 雀魂牌谱屋`amae-koromo.sapk.ch` 找到最近牌谱的 MJAI 链接，提交到 mortal`mjai.ekyu.moe` / KillerDucky，读取 About 里的：

- mortal评分`rating`
- 一致率`ai_consistency`

结果会保存到 `outputs_all/` 下的 CSV。

## 推荐版本

目前推荐使用：

```text
查rt工具_v4.py
```

v4 是当前最顺手的版本：并行更稳定、速度快、会自动保存 checkpoint，也会在最终 CSV 里记录总耗时。

## 版本迭代

### 稳定版

文件：`查rt稳定版.py`

早期可用版本，流程比较保守，适合单线程慢慢跑。优点是简单稳定，缺点是速度慢，自动化程度较低。

### v2

文件：`查rt工具_v2.py`

开始把流程整理成更完整的工具脚本，支持自动查玩家、抓最近对局、打开 MJAI、提交分析并读取结果。

### v3

文件：`查rt工具_v3.py`

加入并行 worker，多个 Chrome 同时处理牌谱，速度明显提升。也加入了更多等待、重试和 rate-limit backoff 逻辑。

### v4

文件：`查rt工具_v4.py`

当前推荐版。相比 v3：

- worker 启动更稳，避免多个 Chrome 同时启动时互相打架
- 默认去掉每局前后的 cooling delay，速度更快
- KillerDucky JSON 页面打开后会尽快点击 About
- 如果浏览器掉到新标签页/首页，会自动重启该 worker 并重试当前局
- CSV 会持续 checkpoint，跑到一半中断也能保留已完成结果
- 最终 CSV 会记录总耗时

## 使用方式

交互式运行：

```powershell
& E:\Anaconda\envs\rating_tool\python.exe c:/Users/yyt/Desktop/rating/查rt工具/查rt工具_v4.py
```

直接传参数：

```powershell
& E:\Anaconda\envs\rating_tool\python.exe c:/Users/yyt/Desktop/rating/查rt工具/查rt工具_v4.py --nickname "EternityQ" --room 12 --games 20 --workers 2
```

如果已知 player id，建议直接传，能少一次搜索：

```powershell
& E:\Anaconda\envs\rating_tool\python.exe c:/Users/yyt/Desktop/rating/查rt工具/查rt工具_v4.py --nickname "EternityQ" --player-id 9139787 --room 12 --games 20 --workers 2
```

## 参数建议

常用参数：

| 参数 | 说明 |
| --- | --- |
| `--nickname` | 玩家昵称 |
| `--player-id` | 玩家 ID，已知时推荐填写 |
| `--room` | 房间，`9`=金之间，`12`=玉之间 |
| `--games` | 查询最近 N 局 |
| `--workers` | 并行 Chrome 数量 |
| `--headless` | 无头模式，不推荐，Cloudflare 时容易失败 |
| `--reset-profile` | 删除当前版本 Chrome profile 后重新跑 |

worker 建议：

- 推荐：`--workers 2`
- 更稳但慢：`--workers 1`
- 不太推荐：`--workers 3` 或更多，比较容易触发 rate limited

## Rate Limited

如果出现 Cloudflare `Error 1015` / `You are being rate limited`，一般不是脚本坏了，而是请求太密。

处理建议：

- 等一会儿再继续
- 手动刷新几次，有时会恢复
- 如果 Chrome 弹出“确认重新提交表单”，点“继续”
- 降低并行数，优先用 `--workers 2`
- 频繁触发时改用 `--workers 1`

可以加大等待：


## 输出

结果目录：

```text
outputs_all/
```

v4 输出文件名：

```text
{nickname}_{room}_recent_{N}_v4.csv
```

CSV 字段：

| 字段 | 说明 |
| --- | --- |
| `index` | 最近第几局 |
| `mjai_url` | 原始 MJAI 链接 |
| `json_url` | KillerDucky JSON 链接 |
| `rating` | rating |
| `ai_consistency` | AI 一致率 |
| `status` | `OK` / `PARTIAL` / `ERROR` |

## 注意事项

- 不建议开 `--headless`，Cloudflare 验证时需要可见 Chrome。
- 第一次跑如果遇到 Cloudflare，请在打开的 Chrome 里手动完成验证。
- v4 使用独立的 `chrome_user_data_v4/`，如果状态异常，可以加 `--reset-profile` 重建 profile。
- 脚本会边跑边写 checkpoint，中断后重新运行会跳过已完成的 `OK` 行。
