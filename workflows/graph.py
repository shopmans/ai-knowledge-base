"""
LangGraph 工作流图组装

构建 collect → analyze → organize → review ⟲ save 的有向图。
review 节点通过条件边决定：通过 → save → END，不通过 → 回到 organize。
"""

from langgraph.graph import END, StateGraph

from workflows.nodes import analyze_node, collect_node, organize_node, review_node, save_node
from workflows.state import KBState

MAX_ITERATIONS = 3


def _route_after_review(state: KBState) -> str:
    if state.get("review_passed", False):
        return "save"
    if state.get("iteration", 0) >= MAX_ITERATIONS:
        return "save"
    return "organize"


def build_graph() -> StateGraph:
    graph = StateGraph(KBState)

    graph.add_node("collect", collect_node)
    graph.add_node("analyze", analyze_node)
    graph.add_node("organize", organize_node)
    graph.add_node("review", review_node)
    graph.add_node("save", save_node)

    graph.set_entry_point("collect")

    graph.add_edge("collect", "analyze")
    graph.add_edge("analyze", "organize")
    graph.add_edge("organize", "review")

    graph.add_conditional_edges(
        "review",
        _route_after_review,
        {"save": "save", "organize": "organize"},
    )

    graph.add_edge("save", END)

    return graph.compile()


if __name__ == "__main__":
    app = build_graph()

    initial_state: KBState = {
        "sources": [],
        "analyses": [],
        "articles": [],
        "review_feedback": "",
        "review_passed": False,
        "iteration": 0,
        "cost_tracker": {},
    }

    print("=" * 60)
    print("启动知识库流水线")
    print("=" * 60)

    for event in app.stream(initial_state, stream_mode="values"):
        if sources := event.get("sources"):
            print(f"  📦 采集: {len(sources)} 条")
        if analyses := event.get("analyses"):
            print(f"  🔍 分析: {len(analyses)} 条")
        if articles := event.get("articles"):
            print(f"  📋 整理: {len(articles)} 条")
        if event.get("review_passed") is not None:
            status = "✅ 通过" if event["review_passed"] else "❌ 未通过"
            print(f"  🛡️ 审核: {status} (iter={event.get('iteration', 0)})")
        cost = event.get("cost_tracker", {})
        if cost:
            print(f"  💰 累计成本: ¥{cost.get('total_cost_yuan', 0):.4f}")

    print("=" * 60)
    print("流水线执行完毕")
    print("=" * 60)
