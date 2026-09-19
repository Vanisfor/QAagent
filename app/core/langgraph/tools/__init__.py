"""LangGraph tools for enhanced language model capabilities.

This package contains custom tools that can be used with LangGraph to extend
the capabilities of language models. Currently includes knowledge base
retrieval (RAG), web search, and human-in-the-loop confirmation.
"""

from langchain_core.tools.base import BaseTool

from .ask_human import ask_human
from .duckduckgo_search import duckduckgo_search_tool
from .knowledge_search import knowledge_search
from app.core.skills.registry import skill_registry
from app.core.skills.tools import activate_skill

_BASE_TOOLS: tuple[BaseTool, ...] = (knowledge_search, duckduckgo_search_tool, ask_human)


def get_tools() -> list[BaseTool]:
    """Return request-visible tools, omitting Skill activation for an empty catalog."""
    available = list(_BASE_TOOLS)
    if skill_registry.skills:
        available.append(activate_skill)
    return available
