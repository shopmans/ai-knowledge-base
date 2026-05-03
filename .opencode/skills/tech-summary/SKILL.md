---
name: tech-summary
description: 当需要对采集的技术内容进行深度分析总结时使用此技能
allowed-tools:
  - Read
  - Grep
  - Glob
  - WebFetch
---

# 技术内容深度分析技能

## 使用场景

- 对采集 Agent 输出的原始数据进行二次深度分析
- 为知识库条目提供摘要提炼、亮点提取、质量评分和标签建议
- 发现跨项目的共同主题与新兴技术趋势
- 用户明确要求分析或总结技术动态时触发

## 执行步骤

### 1. 读取最新采集文件

从 `knowledge/raw/` 目录读取最新的采集结果文件。

- 使用 Glob 工具匹配 `knowledge/raw/github-trending-*.json` 和 `knowledge/raw/hackernews-*.json` 文件
- 按文件名中的日期降序排列，取最新一份（或当天所有文件）
- 使用 Read 工具读取文件内容并解析 JSON
- 若当天无新文件，检查最近 3 天内是否有未分析的文件

### 2. 逐条深度分析

对每个项目/条目进行以下四维分析：

#### 2a. 摘要提炼

- 生成不超过 **50 字**的中文一句话摘要
- 要求涵盖核心功能与关键价值，去除修饰性描述
- 示例：`开源推理模型，数学与代码推理能力接近闭源水平，采用 RL 混合训练。`

#### 2b. 技术亮点提取

- 提取 **2-3 个**技术亮点
- 必须用事实说话：引用具体数据、技术方案、性能指标，避免空泛描述
- 可通过 WebFetch 访问项目 README 或文档获取补充信息
- 示例：
  - `在 MATH 基准上达到 79.8%，超过 GPT-4 的 76.6%`
  - `采用 Group Relative Policy Optimization (GRPO) 替代传统 PPO`

#### 2c. 质量评分

按以下标准打分（1-10 分）：

| 分数区间 | 等级     | 含义                                   |
| -------- | -------- | -------------------------------------- |
| 9-10     | 改变格局 | 开创性工作，将显著影响行业方向         |
| 7-8      | 直接有帮助 | 可立即应用于生产，解决实际问题       |
| 5-6      | 值得了解 | 有参考价值，但短期内应用场景有限       |
| 1-4      | 可略过   | 增量有限或与核心领域关联度低           |

**评分必须附简要理由**（一句话说明为什么是这个分数而非相邻分数）。

**评分约束**：15 个项目中，9-10 分的项目**不超过 2 个**。若初步评分中高分项目超过 2 个，需重新审视并调整至最合理的 2 个保留高分，其余下调至 8 分。

#### 2d. 标签建议

- 从预设词表中选取 3-5 个标签
- 预设词表：`LLM`、`Agent`、`RAG`、`reasoning`、`fine-tuning`、`open-source`、`inference`、`training`、`evaluation`、`benchmark`、`framework`、`tool`、`model`、`dataset`、`RLHF`、`DPO`、`quantization`、`serving`、`multimodal`、`code-generation`、`prompt-engineering`、`safety`
- 若预设词表无法覆盖，可新增标签但需以小写 kebab-case 命名

### 3. 趋势发现

在逐条分析完成后，从全局视角识别：

- **共同主题**：本次采集的多个项目中反复出现的技术方向或方法论
- **新概念**：首次出现或尚处于早期阶段的技术概念与范式
- 将发现以 2-3 句话的形式记录在输出的 `trends` 字段中

### 4. 输出分析结果 JSON

将完整的分析结果写入 `knowledge/raw/tech-summary-YYYY-MM-DD.json`，其中日期为分析当日（UTC）。

## 注意事项

- 分析应基于事实和数据，避免主观臆断；若信息不足，使用 WebFetch 补充后再评分
- 评分要有区分度，禁止全部打同一分数（如全部 7 分）
- 严格遵守 9-10 分不超过 2 个的约束，宁可略低不可虚高
- 标签必须从预设词表中优先选取，保证后续检索和聚合的一致性
- 若输入数据中包含 `status: error` 的条目，跳过不参与分析
- 禁止在输出中暴露任何 API Key 或 Token

## 输出格式

```json
{
  "source": "github_trending",
  "skill": "tech-summary",
  "analyzed_at": "2026-05-02T10:00:00Z",
  "input_file": "github-trending-2026-05-02.json",
  "trends": "本次采集中多模态能力成为共识，多家项目集成视觉理解。模型量化与端侧部署方案持续涌现，预示推理成本将进一步降低。",
  "items": [
    {
      "name": "owner/repo",
      "url": "https://github.com/owner/repo",
      "summary": "开源推理模型，数学与代码推理能力接近闭源水平。",
      "highlights": [
        "在 MATH 基准上达到 79.8%，超过 GPT-4 的 76.6%",
        "采用 GRPO 替代 PPO，训练稳定性提升 30%"
      ],
      "score": 9,
      "score_reason": "在推理能力上实现开源模型重大突破，多个基准超越闭源对手",
      "suggested_tags": ["LLM", "reasoning", "open-source", "RLHF"]
    }
  ]
}
```

**字段说明：**

| 字段                    | 类型    | 必填 | 说明                                      |
| ----------------------- | ------- | ---- | ----------------------------------------- |
| `source`                | string  | 是   | 来源类型，与输入文件一致                  |
| `skill`                 | string  | 是   | 固定值 `tech-summary`                     |
| `analyzed_at`           | string  | 是   | ISO 8601 格式的分析完成时间（UTC）        |
| `input_file`            | string  | 是   | 所分析的原始采集文件名                    |
| `trends`                | string  | 是   | 趋势发现，2-3 句话描述共同主题与新概念    |
| `items`                 | array   | 是   | 分析结果列表，数量与输入条目数一致        |
| `items[].name`          | string  | 是   | 项目/条目名称                             |
| `items[].url`           | string  | 是   | 原始链接                                  |
| `items[].summary`       | string  | 是   | 中文摘要，不超过 50 字                    |
| `items[].highlights`    | array   | 是   | 技术亮点，2-3 条，基于事实               |
| `items[].score`         | integer | 是   | 质量评分，1-10                            |
| `items[].score_reason`  | string  | 是   | 评分理由，一句话                          |
| `items[].suggested_tags`| array   | 是   | 建议标签，3-5 个，优先从预设词表选取     |
