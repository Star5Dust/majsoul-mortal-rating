# 查 RT 工具

当前最新版：`查rt工具_v5.1.py`

这是一个用于自动查询雀魂牌谱屋（`amae-koromo.sapk.ch`）最近 MJAI 对局，提交到 mortal/KillerDucky 并读取 `rating` 和 `ai_consistency` 的工具。结果会保存到 `outputs_all/` 目录下的 CSV 文件。

## 功能简介

- 自动从玩家页面抓取最近对局的 MJAI 链接
- 并行启动多个 Chrome worker，加快对局处理速度
- 自动提交 MJAI 链接到 KillerDucky
- 读取 `rating` 和 `ai_consistency` 并写入 CSV
- 支持 checkpoint，不重复处理已完成局
- v5.1 自动处理 Cloudflare 1015 速率限制并重试提交
- v5.1 worker 在非限流等待的单步流程中 15 秒内没有拿到应有输出时，会重开该 worker 浏览器并重跑这局
- v5.1 使用独立 Chrome profile 目录，避免与旧版本冲突

## 使用方法

进入仓库目录后运行最新版脚本：

```powershell
python 查rt工具_v5.1.py
```

如果你使用虚拟环境，请先激活对应环境，再运行脚本：

```powershell
python 查rt工具_v5.1.py --nickname "玩家昵称" --room 12 --games 20 --workers 2
```

如果已知 `player-id`，建议直接传入：

```powershell
python 查rt工具_v5.1.py --nickname "玩家昵称" --player-id 9139787 --room 12 --games 20 --workers 2
```

如果未传参数，脚本会交互请求 `nickname`、`room`、`games`。

## 主要参数

| 参数 | 说明 |
| --- | --- |
| `--nickname` | 玩家昵称 |
| `--player-id` | 玩家 ID，已知时可跳过昵称搜索 |
| `--room` | 房间类型，`9`=金之间，`12`=玉之间 |
| `--games` | 查询最近 N 局 |
| `--workers` | 并行 Chrome worker 数量，默认 3 |
| `--headless` | 无头运行，不推荐 Cloudflare 环境下使用 |
| `--reset-profile` | 删除 v5.1 Chrome profile 并重新创建 |
| `--rate-limit-retries` | 遇到 1015 时自动重试次数 |
| `--rate-limit-min` | 1015 重试最小等待时间 |
| `--rate-limit-max` | 1015 重试最大等待时间 |
| `--worker-stall-restart-seconds` | worker 单步卡住多久后重开浏览器，默认 15；设为 0 可关闭 |

建议：

- 推荐使用 `--workers 2` 或 `3`
- 对抗 Cloudflare 时，`--workers 1` 更稳
- `--headless` 不建议开启，容易影响验证

## 输出说明

结果保存到：

```text
outputs_all/
```

v5.1 输出文件名示例：

```text
{safe_nickname}_{room_name}_recent_{games}_v5.1.csv
```

常见字段：

- `index`：最近第几局
- `mjai_url`：原始 MJAI 链接
- `json_url`：KillerDucky JSON 链接
- `rating`：mortal 评分
- `ai_consistency`：AI 一致率
- `status`：`OK` / `PARTIAL` / `ERROR`

## 当前最新版说明

`查rt工具_v5.1.py` 是当前推荐版本，主要改进：

- 多 worker 并行查询
- 独立 `chrome_user_data_v5_1/` profile 管理
- 继续使用 checkpoint 保存已完成局
- 自动处理 Cloudflare 1015 rate limit 并重试
- 当前版本在 Cloudflare 验证失败时会自动重新打开原始 MJAI 链接重试
- 单个 worker 如果某个非限流等待步骤 15 秒内没有进入结果页或读到 rating/一致率，会重开浏览器并重跑该局一次
- 3 个可见 worker 浏览器会自动摆成左上、右上、右下，左下角留给终端/编辑器
- 避免刷新 POST 页面导致的“重新提交表单”提示
- 默认可见模式运行，更适合手动通过 Cloudflare 验证

## 版本迭代

- `查rt工具_v2.py`
  - 整理基础流程：自动查玩家、抓最近对局、打开 MJAI、提交并读取结果
- `查rt工具_v3.py`
  - 加入并行 worker，速度明显提升，增加等待和重试逻辑
- `查rt工具_v4.py`
  - 更稳定的 worker 启动，自动重启异常 worker，持续 checkpoint，最终 CSV 记录总耗时
- `查rt工具_v4.1.py`
  - 在 v4 基础上进一步优化稳定性和恢复逻辑，适合长期批量运行
- `查rt工具_v5.1.py`
  - 当前最新版，增加 Cloudflare 自动重试、原始 MJAI 链接重试、15 秒 worker 卡死重开、独立 profile、专属 v5.1 CSV 输出

## 注意事项

- 不建议启用 `--headless`，Cloudflare 验证在可见 Chrome 下更可靠。
- 初次运行遇到 Cloudflare，请手动完成验证。
- v5.1 使用 `chrome_user_data_v5_1/`，如 profile 异常可加 `--reset-profile`。
- 脚本会实时写入 CSV，途中中断也会保留已完成数据。
- 如果遇到 Error 1015 / 限流：降低 `--workers` 并稍后重试。
