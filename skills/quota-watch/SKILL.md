---
name: quota-watch
description: 查询并巡检 AI API 订阅额度与余额（智谱GLM Coding Plan / MiniMax Token Plan / DeepSeek 余额 / 兼容 /v1/usage 配额窗口的中转站），按周窗口理想消耗曲线判定用量偏快还是偏慢，支持 HTML 邮件与 macOS 本地通知推送。当用户想查 API 余额、剩余额度、套餐用量，或要做定时额度巡检时使用。
---

# quota-watch — AI API 额度巡检

查询订阅制 AI 平台的额度消耗进度，与「按剩余时间线性均摊」的理想曲线对比，
直观回答：**现在用得偏快了还是偏慢了？要不要调整使用力度？**

## 使用步骤

### 1. 配置

复制 `config.example.json` 为 `./quota-watch.json` 或 `~/.config/quota-watch/config.json`，
填入各平台的 API Key。密钥支持 `"env:VARNAME"` 形式引用环境变量，避免明文落盘。

各平台密钥获取位置：

| type | 平台 | 密钥来源 |
|---|---|---|
| `zhipu` | 智谱 GLM Coding Plan | bigmodel.cn 控制台 API Keys |
| `minimax` | MiniMax Token Plan | platform.minimaxi.com（sk-cp- 开头的套餐专用 Key） |
| `deepseek` | DeepSeek 按量余额 | platform.deepseek.com API Keys |
| `relay_quota` | 兼容中转站 | 站点控制台，`base_url` 填 API 根地址 |

### 2. 运行

```bash
python3 scripts/quota_check.py              # 巡检 + 按配置推送
python3 scripts/quota_check.py --dry-run    # 只看报告，不推送
python3 scripts/quota_check.py --json       # JSON 输出（便于其他工具消费）
python3 scripts/quota_check.py --config ~/my.json
```

### 3. 解读判定

偏差 = 实际已用% − 理想已用%（理想 = 已过周窗口时间 ÷ 窗口总长）：

| 偏差 | 判定 | 建议 |
|---|---|---|
| > +15pp | 🔥 明显偏快 | 大幅收敛用量 |
| > +5pp | ⚡ 偏快 | 收敛用量 |
| −5 ~ +5pp | ✅ 正常 | 贴近理想曲线 |
| < −5pp | 🌱 偏慢 | 可以加大用量 |
| < −15pp | 🐢 明显偏慢 | 额度在闲置 |

脚本还会按当前平均速度外推耗尽时间点：若早于窗口结束，会在卡片中给出 ⚠ 预警。

### 4. 定时自动化

crontab 方式（每小时）：

```cron
0 * * * * /usr/bin/python3 /path/to/quota-watch/scripts/quota_check.py >> ~/.quota-watch/cron.log 2>&1
```

在 ZCode / Claude Code 等智能体里，可用定时任务每小时执行本脚本并汇报结果。

## 输出说明

- **终端**：纯文本摘要
- **邮件**（`email.enabled: true`）：HTML 卡片周报，主题即状态，
  如 `[quota-watch] GLM 7%【🌱偏慢7%】 MiniMax 32%【✅正常】 14:00`
- **macOS 通知**（`mac_notify: true`）：通知中心弹窗，存在偏快/耗尽预警时带提示音
- **历史**：每次运行追加到 `history_path`（JSONL），报告自动带「小时增量」

## 注意事项

- 邮件用 SMTP 授权码（不是登录密码）：QQ 邮箱在 设置→账号→开启 SMTP服务 处生成
- 密钥与配置只存本地，脚本不上传任何数据
- 各平台接口若变更（鉴权方式、路径），以官方文档为准
