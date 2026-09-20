# quota-watch 🔍⏱

通用 AI API 额度巡检工具。任何提供额度/余额查询接口的平台都能接入——标准方式是
兼容 `GET /v1/usage` 配额窗口的平台/中转站，另为常用订阅平台提供内置适配。
按**周窗口理想消耗曲线**判定当前用量偏快还是偏慢，帮你决定
「该收敛还是可以放量」。支持 HTML 邮件周报与 macOS 本地通知，适合配合
crontab 或智能体做每小时自动巡检。

## 特性

- 📊 **理想曲线对比**：剩余 6 天时应已用 14.3%、剩 3 天时应已用 50%……
  每次巡检给出 `实际已用% vs 理想已用%` 与偏差判定（✅正常 / ⚡偏快 / 🐢偏慢）
- ⏰ **耗尽预警**：按当前速度外推额度耗尽时间点，早于窗口结束即红色告警
- 📧 **邮件推送**：卡片式 HTML 周报，邮件主题即状态一眼可扫
  （例：`[额度用量] 主力套餐 7%【🐢偏慢7%】 备用中转 32%【✅正常】 14:00`，名称即配置里的 provider `name`）
- 🔔 **macOS 通知**：通知中心弹窗，异常时带提示音
- 📈 **小时增量**：本地 JSONL 历史，每次报告带与上次巡检的差值
- 🔐 **密钥安全**：配置只存本地，支持 `env:` 环境变量引用，不上传任何数据

## 接入方式

**通用接入（推荐）**：`type: relay_quota` — 任何兼容 `GET /v1/usage` 配额窗口的
平台或中转站，填 `base_url` 即可接入，可查询 5h/1d/7d 窗口余量与累计消费。

**内置适配**：常见平台的快捷接入，免填 `base_url`：

| type | 适用形态 | 查询内容 |
|---|---|---|
| `zhipu` | Coding Plan 订阅 | 周窗口用量 + 5 小时窗口 |
| `minimax` | Token Plan 订阅（sk-cp- Key） | general/video 周窗口与当日次数 |
| `deepseek` | 按量计费开放平台 | 账户余额 |

## 快速开始

```bash
git clone https://github.com/huyikai/skills.git
cd skills/skills/quota-watch
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
diff <  −5pp  🐢 偏慢（可放量）
diff < −15pp  🐌 明显偏慢（额度闲置）
```

## 配置参考

见 [config.example.json](config.example.json)。要点：

- `providers[].api_key` 支持 `"env:VARNAME"` 引用环境变量
- `email.*` 填 SMTP 信息；QQ/163 邮箱需先在网页版开启 SMTP 并生成**授权码**（非登录密码）
- `mac_notify: true` 开启本地通知；`history_path` 为增量历史存放位置

## 作为 Skill 使用

本目录即标准 Skill 形态（含 `SKILL.md`），不绑定特定智能体：放入任意 agent 的
skills 目录（如 `~/.zcode/skills/`、`~/.agents/skills/` 或项目 `.claude/skills/`）
即可被发现，配合定时任务即可实现全自动巡检播报。

## 致谢

- 智谱用量端点来自 [cc-switch #1588](https://github.com/farion1231/cc-switch/issues/1588) 的社区整理
- MiniMax Token Plan 端点来自 [官方文档](https://platform.minimax.io/docs/token-plan/other-tools)

## License

[MIT](LICENSE)
