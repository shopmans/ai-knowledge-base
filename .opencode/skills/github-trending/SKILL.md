---
name: github-trending
description: >
  Scrape GitHub Trending page to collect the top 50 trending repositories,
  filtered by AI/LLM/Agent/ML related topics, and output a structured JSON array.
  Use when user mentions "采集 github"、"抓 trending"、"爬 github 热门"、"github trending"、
  "热门开源项目"、"github 趋势"、"开源项目排行"、"github 热门 repo"、
  "collect github trending"、"scrape trending repos"、"github 热门项目"、
  "fetch trending"、"crawl github"、"github trending 抓取"、"热门仓库"、
  or wants to gather fresh GitHub Trending data for the knowledge base pipeline.
allowed-tools:
  - Read
  - Grep
  - Glob
  - WebFetch
---

# GitHub Trending 采集技能

## Quick Start

1. 使用 WebFetch 抓取 `https://github.com/trending` HTML 页面
2. 解析 HTML 提取仓库信息，过滤 AI 相关项目
3. 输出 JSON 数组到 `knowledge/raw/github-trending-YYYY-MM-DD.json`

## 执行步骤

### 1. 抓取 GitHub Trending 页面

使用 WebFetch 访问：

```
https://github.com/trending?since=daily
```

- 仅抓取此 HTML 页面，**不调用 GitHub REST API**（API rate limit 太紧）
- 若 daily 页面项目不足 50 个，可追加抓取 `?since=weekly` 补充

### 2. 解析 HTML 提取仓库信息

从 HTML 中提取以下字段：

| 字段          | 提取来源                     |
| ------------- | ---------------------------- |
| `name`        | 仓库全名 `owner/repo`        |
| `url`         | 拼接 `https://github.com/` + 相对路径 |
| `stars`       | Star 计数（数字）            |
| `topics`      | 页面上的 topic 标签列表      |
| `description` | 项目简介文本                 |

### 3. 过滤 AI/LLM/Agent/ML 相关项目

**纳入条件**（topics 或 description 满足其一）：

- 含关键词：`ai`、`llm`、`agent`、`ml`、`machine-learning`、`deep-learning`、
  `gpt`、`transformer`、`rag`、`fine-tuning`、`nlp`、`diffusion`、
  `reinforcement-learning`、`neural-network`、`large-language-model`
- 或 topics 中包含上述相关标签

**排除条件**：

- `awesome-*` 列表类项目
- 纯教程 / 书籍 / 面试题仓库（无实际代码）

### 4. 排序取 Top 50

- 按 stars 数降序排列
- 截取最多 50 个项目
- 不做去重（由 caller 处理）

### 5. 输出 JSON

写入 `knowledge/raw/github-trending-YYYY-MM-DD.json`，日期为采集当日 UTC。

## 边界条件

- **单次执行 < 10s**：若 HTML 抓取超时，立即返回空数组
- **失败时返回空数组，不抛异常**：网络错误、解析失败等均输出 `[]`
- **输出必须通过 jsonschema 验证**：每条记录包含 `name`、`url`、`stars`、`topics`、`description` 五个字段

## 验证方式

```bash
# 执行后检查输出是合法 JSON 且字段完整
# items 数组中每条记录包含 name / url / stars / topics / description
```

## 输出格式

```json
{
  "source": "github_trending",
  "skill": "github-trending",
  "collected_at": "2026-05-02T08:00:00Z",
  "items": [
    {
      "name": "owner/repo",
      "url": "https://github.com/owner/repo",
      "stars": 45000,
      "topics": ["llm", "agent", "open-source"],
      "description": "An open-source reasoning model..."
    }
  ]
}
```

**字段说明：**

| 字段                | 类型    | 必填 | 说明                            |
| ------------------- | ------- | ---- | ------------------------------- |
| `source`            | string  | 是   | 固定值 `github_trending`        |
| `skill`             | string  | 是   | 固定值 `github-trending`        |
| `collected_at`      | string  | 是   | ISO 8601 采集时间（UTC）        |
| `items`             | array   | 是   | 最多 50 条                     |
| `items[].name`      | string  | 是   | 仓库全名 `owner/repo`          |
| `items[].url`       | string  | 是   | 仓库主页 URL                   |
| `items[].stars`     | integer | 是   | 当前 star 数                   |
| `items[].topics`    | array   | 是   | 项目标签列表                   |
| `items[].description` | string | 是  | 项目简介                       |
