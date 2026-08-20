"""Knowledge base search tool for RAG-based Q&A.

This tool lets the agent retrieve relevant passages from the local
pgvector knowledge base. The agent should call it first when a question
may be covered by ingested documents (product docs, course material,
FAQs, internal knowledge, etc.).
"""

from langchain_core.tools import tool

from app.core.logging import logger
from app.core.evidence import build_evidence_block
from app.services.knowledge import (
    KnowledgeBaseNotConfigured,
    knowledge_service,
)


@tool
async def knowledge_search(query: str, top_k: int = 5) -> str:
    """Search the local knowledge base for passages relevant to the question.

    Use this tool FIRST whenever the user asks about facts, documents,
    products, courses, policies, or anything that could be in the
    ingested knowledge base. It returns the top matching passages with
    their sources so you can answer with citations.

    Args:
        query: The search query, usually the user's question verbatim.
        top_k: How many passages to return (1-10, default 5).

    Returns:
        The matching passages formatted with source and relevance, or a
        message stating that nothing relevant was found.
    """
    k = max(1, min(top_k, 10))
    try:
        hits = await knowledge_service.search(query, top_k=k)
    except KnowledgeBaseNotConfigured as e:
        logger.warning("knowledge_search_not_configured", error=str(e))
        return "The knowledge base is not configured yet. Answer using web search or your own knowledge."
    except Exception as e:
        logger.exception("knowledge_search_failed", error=str(e), query_length=len(query))
        return "Knowledge base search failed. Answer using web search or your own knowledge."

    if not hits:
        return "No relevant information found in the knowledge base. Answer from web search or your own knowledge."

    logger.info("knowledge_search_tool_used", hits=len(hits), query_length=len(query))
    return build_evidence_block(hits)
