# ai-news-agent

指定来源 AI / 科技日报抓取器。项目只抓取 `config/sources.yaml` 中列出的来源，不做全网搜索。

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

## GitHub Actions

`.github/workflows/daily.yml` 已配置每天 UTC 01:00 运行，即北京时间 09:00。

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

## 本次抓取修复

- 量子位 QbitAI 已替换 OpenAI 中文站作为固定中文来源。
- 机器之心增加专门链接提取规则，覆盖普通 `a[href]`、常见文章卡片选择器，以及首页 script/JSON 中的 `/articles/{id}` 链接。
- `--lookback-hours` 默认 48，可在测试时设置为 720。
