"""Knowledge base search tool for RAG-based Q&A.

This tool lets the agent retrieve relevant passages from the local
pgvector knowledge base. The agent should call it first when a question
may be covered by ingested documents (product docs, course material,
FAQs, internal knowledge, etc.).
"""

from langchain_core.tools import tool
from langchain_core.runnables import RunnableConfig

from app.core.logging import logger
from app.core.evidence import build_evidence_block
from app.core.langgraph.rag_workflow import agentic_rag_workflow
from app.services.knowledge import (
    KnowledgeBaseNotConfigured,
)
from app.services.knowledge_access import knowledge_access_service
from app.services.user_llm_settings import user_llm_settings_service
from app.schemas.knowledge import RetrievalContext


@tool
async def knowledge_search(query: str, config: RunnableConfig, top_k: int = 5) -> str:
    """Search the local knowledge base for passages relevant to the question.

    Use this tool FIRST whenever the user asks about facts, documents,
    products, courses, policies, or anything that could be in the
    ingested knowledge base. It returns the top matching passages with
    their sources so you can answer with citations.

    Args:
        query: The search query, usually the user's question verbatim.
        config: Server-provided authenticated runtime metadata.
        top_k: How many passages to return (1-10, default 5).

    Returns:
        The matching passages formatted with source and relevance, or a
        message stating that nothing relevant was found.
    """
    k = max(1, min(top_k, 10))
    try:
        requested_context = RetrievalContext.from_config(config)
        user_id = int(requested_context.user_id)
        context = await knowledge_access_service.context_for_user(
            user_id,
            requested_spaces=requested_context.space_slugs,
        )
        runtime = await user_llm_settings_service.get_runtime(user_id)
        bundle = await agentic_rag_workflow.run(query, context, runtime, intent="qa", top_k=k, config=config)
        hits = bundle.hits
    except KnowledgeBaseNotConfigured as e:
        logger.warning("knowledge_search_not_configured", error=str(e))
        return (
            "The internal knowledge base is unavailable. Do not answer internal-company questions "
            "from general model knowledge. Use web search only for explicitly public information."
        )
    except Exception as e:
        logger.exception("knowledge_search_failed", error=str(e), query_length=len(query))
        raise

    if not hits:
        return (
            "No accessible internal evidence was found. Say that the requested information was not found "
            "in the user's authorized knowledge spaces; do not infer private facts from general model knowledge."
        )

    logger.info("knowledge_search_tool_used", hits=len(hits), query_length=len(query))
    return build_evidence_block(hits)
