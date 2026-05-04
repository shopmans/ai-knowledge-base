"""
LangGraph 工作流共享状态定义

定义 KBState 作为 LangGraph 图中各节点之间传递的共享状态。
遵循"报告式通信"原则：每个字段是结构化摘要，而非原始数据透传。
节点之间通过交换"报告"而非"原始材料"来通信，确保每步输出都是
可审计、可追溯的结构化结果。

数据流向:
    sources → (采集节点) → analyses → (分析节点) → articles → (整理节点) → 保存
                                                        ↑
                                            review_feedback / review_passed ← (审核节点)
"""

from __future__ import annotations

from typing import TypedDict


class KBState(TypedDict, total=False):
    """知识库流水线的 LangGraph 共享状态。

    每个字段对应流水线中一个阶段的**结构化输出**，而不是中间过程的原始数据。
    节点读取上游字段的摘要报告，处理后写入下游字段的新报告。

    Fields:
        sources: 采集阶段产出的原始数据摘要列表
        analyses: LLM 分析后的结构化结果列表
        articles: 格式化、去重后的标准化知识条目
        review_feedback: 审核节点的反馈意见（不通过时的改进建议）
        review_passed: 审核是否通过
        iteration: 当前审核循环的累计次数（上限 3 次）
        cost_tracker: 全流水线 Token 用量与成本追踪
    """

    # ---- 采集阶段输出 ----
    # 格式: [{"title": str, "url": str, "source": str, "description": str, "collected_at": str, ...}]
    # 由 collector 节点写入，包含从 GitHub / RSS 等渠道采集的原始条目摘要
    sources: list[dict]

    # ---- 分析阶段输出 ----
    # 格式: [{"title": str, "summary_zh": str, "highlights": list[str],
    #         "score": int, "tags": list[str], "category": str, "recommendation": str, ...}]
    # 由 analyzer 节点写入，每条是对应 source 条目经 LLM 深度分析后的结构化报告
    analyses: list[dict]

    # ---- 整理阶段输出 ----
    # 格式: [{"id": str, "title": str, "source_url": str, "summary": str,
    #         "highlights": list[str], "score": int, "tags": list[str],
    #         "category": str, "recommendation": str, "status": str, ...}]
    # 由 organizer 节点写入，经过去重、归一化、标签映射后的最终知识条目
    articles: list[dict]

    # ---- 审核阶段输出 ----
    # 审核节点的反馈意见文本；通过时为 "质量合格"，未通过时包含具体改进建议
    # 用于下一轮 analyzer / organizer 修正的参考依据
    review_feedback: str

    # 审核是否通过；为 True 时跳过后续循环，直接进入保存阶段
    review_passed: bool

    # 当前审核循环的累计次数（从 0 开始）
    # 达到 MAX_ITERATIONS（默认 3）时强制通过，防止无限循环
    iteration: int

    # ---- 成本追踪 ----
    # 格式: {"prompt_tokens": int, "completion_tokens": int, "total_cost_yuan": float}
    # 所有节点共享同一个累加器，每次 LLM 调用后通过 accumulate_usage 更新
    cost_tracker: dict
