# 出海企业商机发现 Agent

面向聚合跨境支付产品的企业商机发现工具。搜索采用 DeepSeek Search，事实提取模型为 `deepseek-flash`，最终等级由本地规则决定。

当前版本为首批基础开发版：配置、SQLite 迁移、预算服务、搜索适配器、模型提取适配器及部分CLI已经实现。企业原文采集/归一、完整事实缓存和分级、Excel日报、FastAPI、Worker和系统调度尚未实现。`enqueue`只入库，当前不会自动执行任务。

## 安装与离线验证

开发验证环境：Python 3.12.14；支持Python 3.12系列。项目运行/开发依赖记录在 `requirements.lock`，构建依赖固定于 `pyproject.toml`。无需 SearXNG、Docker、Redis 或平台账号。

```bash
python -m venv .venv
# Windows: .venv\Scripts\activate
. .venv/bin/activate
python -m pip install -r requirements.lock
python -m pip install -e .
python -m app.cli check-config
python -m app.cli init-db
python -m app.cli budget
python -m app.cli plan-queries
python -m app.cli enqueue --key manual-example-001
python -m app.cli status
python -m pytest -q
python -m ruff check app tests
```

安装后的 `vbao` 与 `python -m app.cli` 等价。非默认配置通过前置参数指定：`vbao --config /absolute/path/settings.toml check-config`。运行目录按TOML文件所在目录解析，因此示例中的 `../data/agent.db` 指向项目 `data/agent.db`。

配置默认 `live_enabled=false`；所有测试使用模拟HTTP，不需要真实密钥，不产生API费用。数据库初始化可重复执行；已有高版本结构会拒绝启动。当前迁移仅支持初始版本，维护命令应停服执行，不要在多个进程中同时初始化同一新库。

## 一次性搜索接入验证

先完成初始化，然后按实际环境设置 `DEEPSEEK_API_KEY` 环境变量，在受控配置中设 `live_enabled=true`、`source_terms_confirmed=true`，并核对 `pricing.verified_on` 及单价。`.env.example`是字段说明，程序不会自动读取 `.env`。不要将密钥粘贴到配置、源码或命令参数中。

```bash
python -m app.cli search '珠海 外贸 出口 企业 联系我们'
python -m app.cli budget
```

此命令只验证发现能力，返回结果仍是候选，不是合格商机；运行记录以 `PARTIAL / SEARCH_ONLY`结束，不会冒充完整成功。查询前原子预留费用，重试单独记账。缓存命中不发起收费请求；每天尝试数包含失败和重试。搜索返回链接尚未经过原网页抓取验证，不可直接作为已核实业务证据。

模型适配器目前提供Python调用接口 `app.analysis.llm.DeepSeekLLM.extract`，输入要求调用者已完成企业与页面归属核实。当前可验证JSON和引用存在/原文子串匹配；引用语义、事实缓存和本地A/B/C规则将在后续任务实现，不把字符串匹配视为业务真实性验证。

月预算以人民币整数微元保存（1元=1,000,000微元），上限500元，预留与结算均入SQLite。超时/无用量响应保留未结算费用，实际超预留会冻结后续收费调用；自动解冻与账单对账维护入口尚未实现，禁止手工清空台账后重跑。模型费用按保守高峰单价估算，不依赖缓存折扣。

当前执行环境的网络白名单尚未包含 `api.deepseek.com`；本批次没有真实调用，密钥没有持久化。真实联调需要运行环境允许该域名并注入凭据。持续采集另外需要配置保存期限及允许访问域名，尚不能仅凭打开 `live_enabled` 启动持续任务。

## 开发进度

详见[任务拆分](docs/出海企业商机发现Agent_MVP开发任务拆分_v1.0.md)、[CSV台账](docs/MVP开发任务台账_v1.0.csv)和[首批开发记录](docs/开发记录_001_基础工程与DeepSeek适配.md)。本批次只完成可离线验证的基础交付，不代表MVT真实来源及业务闭环已通过。

## 真实接口联调命令

`python -m app.probe --config data/live-check.toml` 可在网络白名单生效、进程已注入密钥后执行。配置模板可复制 `config/settings.toml` 并仅为本次联调启用真实调用、确认来源条件；相对路径按配置文件位置解析。当前受忽略的 `data/live-check.toml` 已准备，内无密钥。

命令复用原台账，强制跳过搜索缓存，各调用一次搜索和模型；最高配置预估不超过0.1元，模型证据为明确标注的虚构测试文字。它验证接口契约，不验证真实商机，成功也不将业务任务标成完整成功。见[网络与联调准备记录](docs/联调记录_001_DeepSeek网络准备.md)。
