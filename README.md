# majsoul-mortal-rating
雀魂对局评分批量提取工具（查 RT 工具）

> 自动从雀魂牌谱屋（https://amae-koromo.sapk.ch） 提取指定玩家在 金之间 / 玉之间 的对局链接，提交至 mortal(https://mjai.ekyu.moe) 获取 Rating 评分 与 AI 一致率，最终输出为 CSV 文件。


✅ 功能概览

1. 自动搜索昵称 → 获取玩家 ID
2. 爬取指定房间（9=金 / 12=玉）中最近 N 场对局链接
3. 自动提交每场对局到mortal复盘工具页面
4. 从弹窗中提取：Rating 分数 + AI 一致率（百分比）
5. 保存完整结果（含 mjai 链接、json 链接、评分）到 CSV
6. 终端输出平均 Rating 与平均一致率统计


⚠️ 重要限制（必读！）

- 必须使用前台模式运行（显示浏览器窗口）
  第二阶段（提交到mortal）依赖真实用户交互。若启用后台（headless），点击按钮、弹窗加载会失败，导致无法提取评分。
  
- 运行时请勿最小化或频繁切换窗口
  Selenium 在后台或失焦状态下可能无法正确触发事件。


🛠 使用前提

- 操作系统：Windows / macOS / Linux
- Python 版本：≥ 3.8（推荐 3.9+）
- 已安装 Google Chrome 浏览器（脚本不包含浏览器，仅自动下载驱动）
- 网络可访问：
  - https://amae-koromo.sapk.ch
  - https://mjai.ekyu.moe


🔧 安装依赖

建议在虚拟环境中操作：

pip install selenium undetected-chromedriver webdriver-manager requests

如遇 undetected-chromedriver 安装失败，请先升级构建工具：

pip install --upgrade pip setuptools


▶️ 运行方式

将脚本保存为 查rt工具.py，在终端执行：

python 查rt工具.py

按提示依次输入：

1. 玩家昵称（如 七段AI）→ 脚本会自动搜索并提取 ID（无需手动查 ID！）
2. 房间ID：
   - 9 → 金之间
   - 12 → 玉之间
3. 要分析的对局数量（如 5 或 10）
4. 是否后台运行？→ 必须输入 n 或直接回车（选择前台）！

💡 脚本会自动打开 Chrome 窗口，请保持可见直至完成。


📁 输出说明

- 文件名格式：{昵称}_{房间名}_近{N}场.csv  
  示例：时崎狂三V_玉_10场.csv
- 保存位置：脚本所在目录
- 编码：UTF-8 with BOM（兼容 Excel/WPS 直接打开不乱码）

字段含义：

| 字段        | 说明                                      |
|-------------|-------------------------------------------|
| mjai链接    | 雀魂牌谱屋中的原始对局页面              |
| json链接    | 对应牌谱的 JSON 地址（可用于回放）        |
| rating      | mortal 给出的评分（数字）|
| ai一致率    | 玩家与 AI 行为的一致率（如 76.2%）        |

终端还会显示：
- 平均 Rating
- 平均 AI 一致率（仅提取 % 前的数值）
- 有效对局数量 / 总处理数量


❓ 常见问题

Q：提示“未找到任何 AI 对局链接”？
A：该玩家近期在指定房间没有带 AI 标签的对局。可尝试换房间或查其他玩家。

Q：KillerDucky 页面卡住 / 没跳转 / 弹窗没出现？
A：可能是网络波动或反爬机制触发。
解决方法：
1. 关闭所有 Chrome 窗口
2. 删除项目目录下的 ./chrome_user_data 和 ./chrome_user_data_for_search 文件夹
3. 重新运行脚本

Q：Excel 打开 CSV 乱码？
A：这是编码问题。推荐以下任一方式：
- 用 WPS 直接打开（自动识别 UTF-8-BOM）
- 在 Excel 中：数据 → 从文本/CSV 导入 → 编码选 65001: Unicode (UTF-8)


🙏 致谢

- 数据来源：https://amae-koromo.sapk.ch
- mortal：https://mjai.ekyu.moe

> 本工具仅用于个人学习与研究，请遵守网站使用条款，切勿高频请求或用于商业用途。


🀄 祝你顺利提取数据！如有问题，欢迎附上截图与错误日志进一步排查。
