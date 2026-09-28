---
name: zentao17
description: 通过 zentao17 命令行工具查询和操作公司自部署禅道（ZenTao 17.8，v1 RESTful API）。当用户提到禅道、zentao、查 Bug、获取 Bug 列表、解决 Bug、关闭 Bug、激活 Bug、指派 Bug 等操作时使用本技能。不适用于禅道 22+（那应该用官方 zentao-cli）。
---

# 禅道 17.8 CLI（zentao17）

公司禅道自部署于固定 IP，版本 **17.8**。官方 `zentao-cli` 需要 API 2.0（禅道 22+），在本环境不可用（会误报"用户名和密码不正确"）。本技能使用 `zentao17` 命令，基于 v1 RESTful API（禅道 12.0+ 均支持）。

## 首次使用：安装 CLI 命令

`zentao17` 命令即本技能目录下的 `scripts/zentao17.py`（自带 shebang，仅依赖 Python 标准库）。软链到 PATH 中任意目录即可：

```bash
ln -sf /path/to/skills/zentao17/scripts/zentao17.py ~/.local/bin/zentao17
zentao17 --help
```

安装后创建本地私有配置：`cp LOCAL.example.md LOCAL.md`，填写禅道地址、当前账号与课题号映射（该文件已 gitignore，不入库）。最后按「凭证安全」一节在终端执行 `zentao17 login` 完成配置。

## 凭证安全

- 严禁在对话中收集账号密码。未登录时引导用户在终端执行 `zentao17 login` 交互式配置（凭证存于 `~/.config/zentao17/`，权限 600）。
- 严禁读取 `~/.config/zentao17/` 下的凭证文件。所有数据通过 `zentao17` 命令获取。

## 命令速查

| 意图 | 命令 |
|------|------|
| 项目(课题)列表/搜索 | `zentao17 projects [--search 名称或编号] [--all]` |
| 某项目下的执行/迭代 | `zentao17 executions --project <id> --status all` |
| 产品列表（查产品 ID） | `zentao17 products` |
| 产品 Bug 列表 | `zentao17 bugs --product <id>` |
| 执行/迭代 Bug 列表 | `zentao17 bugs --execution <id> [--all]`（`--all` 自动翻页取全部） |
| 指派给我的未关闭 Bug | `zentao17 bugs --execution <id> --status assignedtome` |
| Bug 详情 | `zentao17 bug <id>` |
| 解决 Bug | `zentao17 resolve <id> -c "备注(如 commit hash)"` |
| 解决(其他方案) | `zentao17 resolve <id> -r duplicate --duplicate-bug <id>`（可选: fixed/notrepro/bydesign/duplicate/external/postponed/willnotfix/tostory） |
| 关闭 Bug | `zentao17 close <id> -c "备注"` |
| 激活 Bug | `zentao17 activate <id> [-c "备注"]` |

## 意图识别与解析规则（重要）

**禅道网页链接解析**（Path-Info 格式 `http://<禅道主机>/<模块>-<方法>-<ID>.html`，主机地址见本目录 `LOCAL.md`）：
- `execution-bug-<执行ID>.html` → `zentao17 bugs --execution <执行ID>`
- `product-browse-<产品ID>.html` → `zentao17 bugs --product <产品ID>`
- 链接中的 `?tid=xxx` 是网页会话参数，忽略

**课题号映射表**维护在本目录 `LOCAL.md`（课题号是公司外部系统编号，不在禅道数据中，人工维护）。需要把课题号对应到禅道项目/执行时，先读该文件。

用户给出**新课题号**时：先 `zentao17 projects --search <课题号> --all` 查编号；查不到则用项目名搜或询问用户对应哪个禅道项目，确认后把映射补进 `LOCAL.md` 的映射表。

**权限边界**：当前账号（见 `LOCAL.md`）访问项目级接口（`/projects/{id}/bugs`、`/projects/{id}`）返回 403，**必须用执行级接口**。"某项目的全部 Bug" = `executions --project <id> --status all` 列出所有执行后逐个 `bugs --execution <id> --all` 合并。

常用选项：`--limit 50 --page 1` 分页；`--order id_desc` 排序；`--json` 输出 JSON 供程序处理；`--insecure` 忽略自签名证书（全局参数，放在子命令前）。

## 使用策略

1. **不知道产品 ID 时**先执行 `zentao17 products`。
2. **写操作前确认**：resolve/close/activate 属于写操作，执行前向用户复述操作对象与备注内容。
3. **推荐闭环**：拉取 `--status assignedtome` 的 Bug → 修复 → `resolve <id> -c "已在 commit xxx 修复，请验证"`（解决后禅道会自动指回创建者验证）。
4. 表格输出中"指派给/创建人"为禅道账号或真实姓名；状态为中文（激活/已解决/已关闭等）。
5. 若报"无法连接"检查地址格式（应形如 `http://IP:端口/zentao`）；报 403 是账号权限不足；报"登录失败"重新执行 `zentao17 login`。

## 错误对照

| 现象 | 处理 |
|------|------|
| 尚未配置禅道地址 | 引导用户执行 `zentao17 login` |
| 登录失败 | 账号密码错/地址错/版本低于12，让用户终端重新 login |
| HTTP 403 | 账号对该产品无权限，换权限更高的账号 |
| HTTP 404 | 服务端无此接口，确认禅道版本 |
| 无法连接 | 地址或网络问题，HTTPS 自签名证书加 `--insecure` |
