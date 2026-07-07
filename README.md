# ai-news-agent

指定来源 AI 投研情报抓取器。项目只抓取 `config/sources.yaml` 中列出的来源，不做全网搜索；默认优先保留会影响投资判断的资本开支、收入、毛利率、订单、客户、数据中心、电力、GPU、半导体供应链、融资、估值、监管、出口管制和市场反应信号。

## 安装

```bash
cd ai-news-agent
python3.11 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## 配置

```bash
cp .env.example .env
```

填写：

- `DEEPSEEK_API_KEY`
- `DEEPSEEK_MODEL=deepseek-chat`
- `SMTP_HOST`
- `SMTP_PORT`
- `SMTP_USER`
- `SMTP_PASSWORD`
- `EMAIL_FROM`
- `EMAIL_TO`

注意：DeepSeek API 和 DeepSeek 网页版会员不是一回事。网页版会员通常用于网页聊天产品，API 需要单独开通并使用 API Key，调用按 token 计费。

## 运行

Dry-run，只打印邮件内容：

```bash
python main.py --dry-run
```

测试 DeepMind 等较早文章，可放宽时间窗口到 720 小时：

```bash
python main.py --dry-run --lookback-hours 720 --no-cache
```

正式发送：

```bash
python main.py
```

## 投研过滤规则

当前配置包含 15 个 source，其中新增 Reuters AI、The Information AI Agenda、SemiAnalysis、Data Center Dynamics、SEC EDGAR、NVIDIA IR、Microsoft IR、Meta IR、财联社 AI / 环球市场、智东西 AI芯片 / 机器人 / 算力等 10 个投研源。

过滤逻辑采用 AI + 投研双重判断：

- `requires_ai_filter`：标题或正文需要命中 AI / 大模型 / 智能体 / 算力 / 机器人等关键词。
- `requires_investment_signal_filter`：需要命中 capex、收入、毛利率、业绩指引、订单、合同、数据中心、GPU、云、电力、融资、估值、IPO、监管、出口管制等投研信号。
- 重点公司白名单命中且具备投研信号时，可以保留没有明显 AI 字样的文章，例如 Microsoft / NVIDIA / Meta / Oracle / CoreWeave 的 capex、数据中心、收入、订单、毛利率和业绩指引内容。
- 技术教程、论文、模型测评、开源项目、Prompt、工具合集、产品体验和活动宣传会降权或过滤。

日报输出按投研主题组织：核心信号、AI Capex / 数据中心、算力与半导体供应链、AI 公司与商业化、二级市场相关、中国 AI 产业链，并在末尾输出 source 级抓取诊断。

## GitHub Actions

`.github/workflows/daily.yml` 已配置每天 UTC 01:00 自动运行，即北京时间 09:00；也可以通过 `workflow_dispatch` 手动触发。

在仓库 `Settings -> Secrets and variables -> Actions` 添加这些 Secrets：

- `DEEPSEEK_API_KEY`
- `SMTP_HOST`
- `SMTP_PORT`
- `SMTP_USER`
- `SMTP_PASSWORD`
- `EMAIL_FROM`
- `EMAIL_TO`

可选变量：

- `DEEPSEEK_MODEL`
- `SEC_USER_AGENT`：建议填写声明式联系信息，例如 `AI News Agent contact: your-email@example.com`。未填写时会使用保守默认值；SEC 请求已做低频限速。

## 本次抓取修复

- 量子位 QbitAI 已替换 OpenAI 中文站作为固定中文来源。
- 机器之心增加专门链接提取规则，覆盖普通 `a[href]`、常见文章卡片选择器，以及首页 script/JSON 中的 `/articles/{id}` 链接。
- `--lookback-hours` 默认 24，可在测试时设置为 720。
- 机器之心等站点如遇 httpx TLS/协议异常，会使用 `curl_cffi` 的 Chrome 指纹请求作为 fallback，并在日志中输出 source、url、error_type、error_message。
- 36氪和晚点 LatePost 启用 AI 关键词相关性过滤，避免泛科技/商业内容进入日报。
- 量子位 QbitAI 仅保留 `/YYYY/MM/*.html` 文章详情页，过滤分类页、活动页和专题页。
- Reuters 和 Data Center Dynamics 保留原始 source 抓取；当列表页受反爬或协议错误影响时，才使用 GDELT 做 fallback discovery。
- The Information 不绕过订阅墙，不登录、不破解；如只能读取列表页或标题，会标记为 `premium_limited` / title-only。
- 正文抓取失败但标题或列表页具备强投研信号时，可作为 partial article 进入候选，但会降权，不能压过 SEC、IR 或完整正文的高质量内容。
- 财联社短讯只在命中强 AI 投研关键词时保留，避免泛财经快讯进入日报。
- diagnostics 已区分 `fetch_failed`、`body_unavailable`、`partial_success`、`premium_limited`、`filtered_by_time_window` 和 `no_recent_articles`。
