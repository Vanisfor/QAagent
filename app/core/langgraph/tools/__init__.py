"""LangGraph tools for enhanced language model capabilities.

This package contains custom tools that can be used with LangGraph to extend
the capabilities of language models. Currently includes knowledge base
retrieval (RAG), web search, and human-in-the-loop confirmation.
"""

from langchain_core.tools.base import BaseTool

from .ask_human import ask_human
from .duckduckgo_search import duckduckgo_search_tool
from .knowledge_search import knowledge_search

tools: list[BaseTool] = [knowledge_search, duckduckgo_search_tool, ask_human]
