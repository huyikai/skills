---
name: zentao17
description: 通过 zentao17 命令行工具查询和操作公司自部署禅道（ZenTao 17.8，v1 RESTful API）。当用户提到禅道、zentao、查 Bug、获取 Bug 列表、解决 Bug、关闭 Bug、激活 Bug、指派/转派 Bug、给 Bug 加备注、贴禅道链接等操作时使用本技能。不适用于禅道 22+（那应该用官方 zentao-cli）。
---

# 禅道 17.8 CLI（zentao17）

公司禅道自部署于固定 IP，版本 **17.8**。官方 `zentao-cli` 需要 API 2.0（禅道 22+），在本环境不可用（会误报"用户名和密码不正确"）。本技能使用 `zentao17` 命令，基于 v1 RESTful API（禅道 12.0+ 均支持）；v1 不开放的激活、转派、独立备注走网页会话通道。

本技能只管「怎么读写禅道」。具体项目的处理流程（何时 resolve、推送顺序、流转给谁）由上层技能规定，例如 LSP 项目的 zentao-bug-handling／zentao-unattended；**上层技能已授权的写操作，按上层规定执行，不再逐条向用户复述确认**。

**CLI 版本：2026-10-28**（`zentao17 --version` 应输出同一日期）。

## 能力以 `--help` 为准（先查再断言）

- **本技能的命令集会持续演进**。长会话里早先读过的版本可能已经过期：2026-10-28 曾有一个开了 18 天的会话，凭 10-20 读到的旧文档断言「CLI 不能转派」，误用 `resolve -r external` 销账，最后还要用户手动指出才纠正（禅道 48151）。
- **每次被调用都核对版本**：执行 `zentao17 --version`，输出日期与上一行不同，或者对话里记得的命令表与 `zentao17 --help` 不一致时，先重新读本文件，以及同目录的 `LOCAL.md`。
- **说「CLI 不支持某操作」之前，必须先执行 `zentao17 --help`**（必要时再执行 `zentao17 <子命令> --help`）。只有确认命令不存在，或已经实际执行并失败（退出码 1/3/4），才可以说做不到，并且附上命令输出。不得凭记忆、旧文档或 spec 历史记录断言做不到。spec `tasks.md` 里「CLI 无独立评论命令」这类记录只代表当时的情况。
- **做不到时不改用别的写操作凑结果**。例如不能转派时，不得用 resolve（含 `-r external`）代替；应保持原状态，带上命令输出向用户说明。

## 本地配置与路径

- `LOCAL.md` 与本文件在同一目录。本机路径是 `~/.agents/skills/zentao17/LOCAL.md`，Claude Code 通过 `~/.claude/skills/zentao17/` 软链访问的是同一份。里面有禅道地址、Bug 拉取口径、课题映射和**常用角色账号**（本人／产品／后端）。转派前先在这里查对方账号，查不到再用 `zentao17 users --search <姓名>`，不要把整张用户表交给用户去挑。
- shell 报 `command not found: zentao17` 时，说明 `~/.local/bin` 不在该工具的 PATH 里，不代表没装。改用 `~/.local/bin/zentao17 …` 或 `python3 ~/.agents/skills/zentao17/scripts/zentao17.py …`。

## 首次使用：安装 CLI 命令

`zentao17` 命令即本技能目录下的 `scripts/zentao17.py`（自带 shebang，仅依赖 Python 标准库）。软链到 PATH 中任意目录即可：

```bash
ln -sf /path/to/skills/zentao17/scripts/zentao17.py ~/.local/bin/zentao17
zentao17 --help
```

安装后创建本地私有配置：`cp LOCAL.example.md LOCAL.md`，填写禅道地址、Bug 拉取口径与课题号映射（该文件已 gitignore，不入库）。最后按「凭证安全」一节在终端执行 `zentao17 login` 完成配置。

## 凭证安全

- 严禁在对话中收集账号密码。未登录时引导用户在终端执行 `zentao17 login` 交互式配置（凭证存于 `~/.config/zentao17/`，权限 600）。
- 严禁读取 `~/.config/zentao17/` 下的凭证文件。所有数据通过 `zentao17` 命令获取；需要知道「我是谁」时用 `zentao17 whoami`。

## 命令速查

| 意图 | 命令 |
|------|------|
| 当前操作账号 | `zentao17 whoami`（工号＋姓名；**actions 的 actor 字段是姓名**，判断「本人动作」用这里的姓名比对） |
| 查账号/工号 | `zentao17 users --search <姓名或工号>` |
| 项目(课题)列表/搜索 | `zentao17 projects [--search 名称或编号] [--all]` |
| 某项目下的执行/迭代 | `zentao17 executions --project <id> --status all` |
| 产品列表（查产品 ID） | `zentao17 products` |
| 产品 Bug 列表（推荐口径） | `zentao17 bugs --product <id> [--status unclosed] [--keyword 词 ...] [--assigned me]` |
| 执行/迭代 Bug 列表 | `zentao17 bugs --execution <id> [--status ...]`（只含已挂执行的单，见「拉取口径」） |
| 指派给我的未关闭 Bug | `zentao17 bugs --product <id> --status assignedtome` |
| Bug 详情 | `zentao17 bug <id>`：步骤文本（截图位置标 `[图N]`）＋截图/附件清单＋actions 全历史（含字段变化与完整备注） |
| 下载截图/附件 | `zentao17 download <id> [--dir 目录]`（默认 `$TMPDIR/zentao17-files/<id>`） |
| 巡检预检 | `zentao17 changes --product <id> --state <快照.json> [--own-prefix 前缀] [--set-tracked ID,...] [--update]`（见下文） |
| 解决 Bug | `zentao17 resolve <id> -c "备注(如 commit hash)"` |
| 解决(其他方案) | `zentao17 resolve <id> -r duplicate --duplicate-bug <id>`（可选: fixed/notrepro/bydesign/duplicate/external/postponed/willnotfix/tostory） |
| 关闭 Bug | `zentao17 close <id> -c "备注"` |
| 激活 Bug | `zentao17 activate <id> [-c "备注"] [--assign 账号]`（网页会话通道；默认指派给上次解决人） |
| 转派 Bug | `zentao17 assign <id> --to <账号> [-c "备注"]`（网页会话通道，保留原状态） |
| 添加备注 | `zentao17 comment <id> -c "备注"`（网页会话通道，不改状态） |

常用选项：`--limit 50 --page 1` 分页；`--all` 取全部页；`--order id_desc` 排序；`--json` 输出原始 JSON 供程序处理；`--insecure` 忽略自签名证书（全局参数，放在子命令前）。

## 写命令的回读与退出码（防重复写）

resolve／close／activate／assign／comment 都会**先读、再写、再回读 actions 自验证**，只认本次新增的 action：

| 退出码 | 含义 | 下一步 |
|---|---|---|
| 0 | 已生效；或目标状态已满足（如已是 resolved），提示「未重复执行」 | 继续 |
| 1 | 服务端明确拒绝、参数错误 | 按错误信息处理 |
| 3 | 回读确认**未生效**（含网络中断后回读确认未写入） | 核对后可重试 |
| 4 | **结果未知**：写请求中断且回读也失败 | 先 `zentao17 bug <id>` 核对 actions，确认未生效再重试；**切勿直接重试** |

读请求遇到超时、连接重置会自动重试 3 次；写请求不自动重试，以回读为准。超时不等于失败。

## 意图识别与解析规则（重要）

**禅道网页链接解析**（Path-Info 格式 `http://<禅道主机>/<模块>-<方法>-<ID>.html`，主机地址见本目录 `LOCAL.md`）：
- `bug-view-<BugID>.html` → `zentao17 bug <BugID>`
- `execution-bug-<执行ID>.html` → `zentao17 bugs --execution <执行ID>`
- `product-browse-<产品ID>.html` → `zentao17 bugs --product <产品ID>`
- 链接中的 `?tid=xxx` 是网页会话参数，忽略

**课题号映射表与 Bug 拉取口径**维护在本目录 `LOCAL.md`（课题号是公司外部系统编号，不在禅道数据中，人工维护）。需要把课题号对应到禅道产品/项目/执行时，先读该文件。

用户给出**新课题号**时：先 `zentao17 projects --search <课题号> --all` 查编号；查不到则用项目名搜或询问用户对应哪个禅道项目，确认后把映射补进 `LOCAL.md` 的映射表。

**拉取口径**（2026-10-26／10-28 实测）：
- **产品级是权威来源**：测试员从产品入口提单，新单常常不挂执行。执行级查询只能看到已挂执行的子集，会漏单。批量处理一律用 `bugs --product <id>`，执行级仅作补充。
- 项目级接口（`/projects/{id}/bugs`）对普通账号返回 403。
- **状态筛选在客户端执行**：执行级接口忽略 `status` 参数（`unclosed` 也会返回已关闭单），产品级默认只返回未关闭单；脚本对 `--status`／`--keyword`／`--assigned` 一律取全部页后在本地过滤，输出首行会注明。

## 巡检预检（changes）

给定时巡检用，目的是「没有变化就不做重活」：

- `zentao17 changes --product <id> --state <快照>`：拉产品未关闭全量，与快照比对，输出 `NEW`（快照里没见过的未关闭单，`--keyword` 只做命中标注不过滤）、`CHANGED`（跟踪单的状态／指派人／最新 action 变化，附新增 actions）、`CLOSED`（跟踪单已关闭），末行 `RESULT: none|changed`。
- 跟踪集合用 `--set-tracked`（整体替换）／`--track`／`--untrack` 维护；新纳入跟踪的单首次只建基线，不算变化。
- `--own-prefix "<前缀>"`：actor 是本人（`whoami` 的姓名）且备注以该前缀开头的动作视为本方动作，不算变化（如自动代理自己的备注、转派、resolve）。不带前缀的本人动作仍算变化（那是开发者亲手操作）。
- **`--update` 只在本轮处理完成后执行**：把当前未关闭单全部记为「已见过」、刷新跟踪单指纹。预检阶段不带 `--update`，否则新单会在未分诊前被标记为已见。收尾时先不带 `--update` 再跑一次，确认处理期间没有新出现的变化，再带 `--update` 落盘。
- 首次运行（快照不存在）时全部视为新出现。

## 网页会话通道（activate / assign / comment）

v1 API 在禅道 17.8 上不开放激活、独立备注与转派（activate 路由 404；`PUT /bugs/{id}` 忽略 assignedTo/comment），这三个命令走网页路由：`bug-activate-{id}.html`、`bug-assignTo-{id}.html` 与 `action-comment-bug-{id}.html`。脚本用 `login` 已存凭证自动换 zentaosid 会话（缓存于 `~/.config/zentao17/web-session.json`，权限 600，失效自动重登）。实测要点（已写死在脚本里）：
- 登录 POST 必须带 `X-Requested-With: XMLHttpRequest` 才返回 success JSON；登录后的页面请求必须带 `Referer`，否则会被渲染成访客首页（假登录）。
- 转派表单有隐藏字段 `status`，值为单子的**当前状态**，脚本原样回传。旧版脚本把它写死为 `active`，转派已解决的单时会悄悄把单子改回激活。
- 激活表单默认指派给上次解决人，影响版本沿用原值；`--assign` 可改指派人。
- 附件 `file-read-*.png` 无需登录即可下载。

## 使用策略

1. **不知道产品 ID 时**先执行 `zentao17 products`。
2. **读 Bug 必须读全**：用 `zentao17 bug <id>` 一次拿到步骤、截图清单、actions 全历史；截图是测试员标注的证据，定性前用 `download` 拉下来核对。激活（activated）记录和评论常是诉求的追加层，漏读会错认问题对象。只读 steps、不读 actions，等于没读全。`--json` 只在需要程序处理原始字段时使用。
3. **写操作**：没有上层技能授权时，resolve／close／activate／assign／comment 执行前向用户复述操作对象与备注内容；有上层技能授权时按上层规定执行。写命令的结果以退出码与回读为准（见上表）。
4. **推荐闭环**：拉取 `--status assignedtome` 的 Bug → 修复并上线 → `resolve <id> -c "已在 commit xxx 修复，请验证"`（解决后禅道会自动指回创建者验证）。代码上线前不要 resolve，否则测试员会去验证一个还没部署的修复；各项目的推送与同步步骤以项目规则为准。
5. 表格输出中「指派给/创建人」显示姓名；列表状态 `激活(已确认)` 对应接口的 `confirmed`。
6. 若报「无法连接」，检查地址格式（应形如 `http://IP:端口/zentao`）或网络；报 403 是账号权限不足；报「登录失败」重新执行 `zentao17 login`。

## 状态口径（批量统计/分析必读）

- 状态链：active（激活）→ resolved（已解决待验证）→ closed（已关闭）。**activated = 修复后复测不通过被打回**——有 activated 历史的单子是"争议中"，不是"已解决"。
- **列表与详情的 status 取值不同**：列表接口对「激活且已确认」的单返回 `confirmed`，详情接口返回 `active`＋`confirmed: 1`（#48217 实证）。判断单条的当前状态、指派人一律以 `bug <id>` 为准；列表做批量统计后，对严重级单子与多轮陈单逐条复核 actions。
- **反复 resolved→activated 的单**（如 #48217 两轮修复两轮打回）通常意味着根因未定位或存在诊断盲区——按"活问题"处理：看最后一条 activated 的评论/截图与最新指派，不要按已解决叙事；再次 resolve 前必须先解释"上轮为什么没修好"。

## 错误对照

| 现象 | 处理 |
|------|------|
| 尚未配置禅道地址 | 引导用户执行 `zentao17 login` |
| 登录失败 | 账号密码错/地址错/版本低于12，让用户终端重新 login |
| HTTP 403 | 账号对该产品无权限，换权限更高的账号 |
| HTTP 404 | 服务端无此接口，确认禅道版本 |
| 无法连接（已自动重试 3 次） | 地址或网络问题，HTTPS 自签名证书加 `--insecure` |
| 写命令退出码 3/4 | 见「写命令的回读与退出码」 |
