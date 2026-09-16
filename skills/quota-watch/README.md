# quota-watch 🔍⏱

订阅制 AI API 额度巡检工具。查询智谱 GLM、MiniMax、DeepSeek 及兼容中转站的
额度/余额，按**周窗口理想消耗曲线**判定当前用量偏快还是偏慢，帮你决定
「该收敛还是可以放量」。支持 HTML 邮件周报与 macOS 本地通知，适合配合
crontab 或智能体（ZCode / Claude Code）做每小时自动巡检。

## 特性

- 📊 **理想曲线对比**：剩余 6 天时应已用 14.3%、剩 3 天时应已用 50%……
  每次巡检给出 `实际已用% vs 理想已用%` 与偏差判定（✅正常 / ⚡偏快 / 🌱偏慢）
- ⏰ **耗尽预警**：按当前速度外推额度耗尽时间点，早于窗口结束即红色告警
- 📧 **邮件推送**：卡片式 HTML 周报，邮件主题即状态一眼可扫
  （例：`[quota-watch] GLM 7%【🌱偏慢7%】 MiniMax 32%【✅正常】 14:00`）
- 🔔 **macOS 通知**：通知中心弹窗，异常时带提示音
- 📈 **小时增量**：本地 JSONL 历史，每次报告带与上次巡检的差值
- 🔐 **密钥安全**：配置只存本地，支持 `env:` 环境变量引用，不上传任何数据

## 支持平台

| type | 平台 | 查询内容 |
|---|---|---|
| `zhipu` | 智谱 GLM Coding Plan | 周窗口用量 + 5 小时窗口 |
| `minimax` | MiniMax Token Plan（sk-cp- Key） | general/video 周窗口与当日次数 |
| `deepseek` | DeepSeek 开放平台 | 按量账户余额 |
| `relay_quota` | 兼容 `GET /v1/usage` 配额窗口的中转站 | 5h/1d/7d 窗口余量与累计消费 |

## 快速开始

```bash
git clone https://github.com/you/quota-watch.git
cp config.example.json ~/.config/quota-watch/config.json   # 填入你的 Key
python3 scripts/quota_check.py --dry-run                   # 先看效果，不推送
```

定时巡检（每小时）：

```cron
0 * * * * /usr/bin/python3 /path/to/quota-watch/scripts/quota_check.py >> ~/.quota-watch/cron.log 2>&1
```

## 判定规则

```
理想已用% = 已过窗口时间 ÷ 窗口总长 × 100
偏差 diff = 实际已用% − 理想已用%

diff > +15pp  🔥 明显偏快（额度将提前耗尽）
diff >  +5pp  ⚡ 偏快（建议收敛）
diff ≈ 0      ✅ 正常
diff <  −5pp  🌱 偏慢（可放量）
diff < −15pp  🐢 明显偏慢（额度闲置）
```

## 配置参考

见 [config.example.json](config.example.json)。要点：

- `providers[].api_key` 支持 `"env:VARNAME"` 引用环境变量
- `email.*` 填 SMTP 信息；QQ/163 邮箱需先在网页版开启 SMTP 并生成**授权码**（非登录密码）
- `mac_notify: true` 开启本地通知；`history_path` 为增量历史存放位置

## 作为 Skill 使用

本仓库即 [ZCode](https://zcode.ai) / Claude Code 形态的 Skill：包含 `SKILL.md`。
放入 `~/.zcode/skills/quota-watch/`（或项目 `.claude/skills/`）即可被智能体发现，
配合定时任务即可实现全自动巡检播报。

## 致谢

- 智谱用量端点来自 [cc-switch #1588](https://github.com/farion1231/cc-switch/issues/1588) 的社区整理
- MiniMax Token Plan 端点来自 [官方文档](https://platform.minimax.io/docs/token-plan/other-tools)

## License

[MIT](LICENSE)
