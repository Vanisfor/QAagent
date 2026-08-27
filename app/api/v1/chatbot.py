"""Chatbot API endpoints for handling chat interactions.

This module provides endpoints for chat interactions, including regular chat,
streaming chat, message history management, and chat history clearing.
"""

import asyncio
import json
import time

from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    Request,
)
from fastapi.responses import StreamingResponse

from app.api.v1.auth import get_current_session
from app.core.config import settings
from app.core.langgraph.graph import LangGraphAgent
from app.core.limiter import limiter
from app.core.logging import logger
from app.core.metrics import llm_stream_duration_seconds
from app.core.tracing import (
    current_span_id,
    current_trace_id,
    trace_span,
)
from app.models.session import Session
from app.schemas.chat import (
    ChatRequest,
    ChatResponse,
    StreamResponse,
)
from app.services.session_naming import maybe_name_session
from app.services.memory import memory_service
from app.services.user_llm_settings import (
    UserLLMSettingsNotConfigured,
    UserLLMRuntimeConfig,
    UserLLMSettingsUnavailable,
    user_llm_settings_service,
)

router = APIRouter()
agent = LangGraphAgent()


async def _require_user_llm(user_id: int) -> UserLLMRuntimeConfig:
    """Resolve BYOK settings before a response begins, especially for SSE."""
    try:
        return await user_llm_settings_service.get_runtime(user_id)
    except UserLLMSettingsNotConfigured as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    except UserLLMSettingsUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc))


@router.post("/chat", response_model=ChatResponse)
@limiter.limit(settings.RATE_LIMIT_ENDPOINTS["chat"][0])
async def chat(
    request: Request,
    chat_request: ChatRequest,
    session: Session = Depends(get_current_session),
):
    """Process a chat request using LangGraph.

    Args:
        request: The FastAPI request object for rate limiting.
        chat_request: The chat request containing messages.
        session: The current session from the auth token.

    Returns:
        ChatResponse: The processed chat response.

    Raises:
        HTTPException: If there's an error processing the request.
    """
    await _require_user_llm(session.user_id)
    try:
        logger.info(
            "chat_request_received",
            session_id=session.id,
            message_count=len(chat_request.messages),
        )

        if settings.SESSION_NAMING_ENABLED:
            await maybe_name_session(session.id, session.user_id, session.name, chat_request.messages)

        with trace_span("agent.run", streaming=False):
            result = await agent.get_response(
                chat_request.messages,
                session.id,
                user_id=str(session.user_id),
                username=session.username,
                reasoning_effort=chat_request.reasoning_effort,
            )

        logger.info("chat_request_processed", session_id=session.id)

        return ChatResponse(messages=result)
    except Exception as e:
        logger.exception("chat_request_failed", session_id=session.id, error=str(e))
        raise HTTPException(status_code=500, detail="Agent request failed. Use X-Request-ID for support.")


@router.post("/chat/stream")
@limiter.limit(settings.RATE_LIMIT_ENDPOINTS["chat_stream"][0])
async def chat_stream(
    request: Request,
    chat_request: ChatRequest,
    session: Session = Depends(get_current_session),
):
    """Process a chat request using LangGraph with streaming response.

    Args:
        request: The FastAPI request object for rate limiting.
        chat_request: The chat request containing messages.
        session: The current session from the auth token.

    Returns:
        StreamingResponse: A streaming response of the chat completion.

    Raises:
        HTTPException: If there's an error processing the request.
    """
    runtime = await _require_user_llm(session.user_id)
    model_name = runtime.model
    del runtime
    try:
        logger.info(
            "stream_chat_request_received",
            session_id=session.id,
            message_count=len(chat_request.messages),
        )

        if settings.SESSION_NAMING_ENABLED:
            await maybe_name_session(session.id, session.user_id, session.name, chat_request.messages)

        stream_trace_id = current_trace_id()
        stream_parent_span_id = current_span_id()

        async def event_generator():
            """Generate streaming events.

            Yields:
                str: Server-sent events in JSON format.

            Raises:
                Exception: If there's an error during streaming.
            """
            with trace_span(
                "agent.run",
                trace_id=stream_trace_id,
                parent_span_id=stream_parent_span_id,
                streaming=True,
            ):
                with trace_span("stream.response", streaming=True) as stream_span:
                    started = time.perf_counter()
                    chunk_count = 0
                    first_token_seen = False
                    try:
                        with llm_stream_duration_seconds.labels(model=model_name).time():
                            async for event in agent.get_stream_response(
                                chat_request.messages,
                                session.id,
                                user_id=str(session.user_id),
                                username=session.username,
                                reasoning_effort=chat_request.reasoning_effort,
                            ):
                                if event["type"] in ("reasoning_delta", "answer_delta") and not first_token_seen:
                                    stream_span.set_attribute(
                                        "time_to_first_token_ms", round((time.perf_counter() - started) * 1000, 3)
                                    )
                                    first_token_seen = True
                                chunk_count += 1
                                response = StreamResponse(
                                    type=event["type"],
                                    content=str(event.get("content", "")),
                                    data=event.get("data", {}),
                                    done=False,
                                )
                                yield f"data: {json.dumps(response.model_dump(mode='json'))}\n\n"

                        stream_span.set_attribute("chunk_count", chunk_count)
                        cache_response = StreamResponse(type="cache", data=memory_service.cache_stats())
                        yield f"data: {json.dumps(cache_response.model_dump(mode='json'))}\n\n"
                        final_response = StreamResponse(type="done", content="", done=True)
                        yield f"data: {json.dumps(final_response.model_dump(mode='json'))}\n\n"
                    except asyncio.CancelledError as e:
                        stream_span.set_attribute("client_disconnected", True)
                        stream_span.record_error(e, code="client_disconnected", retryable=False)
                        raise
                    except Exception as e:
                        stream_span.record_error(e)
                        logger.exception("stream_chat_request_failed", session_id=session.id, error=str(e))
                        error_response = StreamResponse(
                            type="error",
                            content="The response stream failed.",
                            data={"code": "stream_failed"},
                            done=True,
                        )
                        yield f"data: {json.dumps(error_response.model_dump(mode='json'))}\n\n"
                    finally:
                        stream_span.set_attribute("chunk_count", chunk_count)

        return StreamingResponse(
            event_generator(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache, no-transform",
                "X-Accel-Buffering": "no",
                "Connection": "keep-alive",
            },
        )

    except Exception as e:
        logger.exception(
            "stream_chat_request_failed",
            session_id=session.id,
            error=str(e),
        )
        raise HTTPException(status_code=500, detail="Agent stream failed. Use X-Request-ID for support.")


@router.get("/messages", response_model=ChatResponse)
@limiter.limit(settings.RATE_LIMIT_ENDPOINTS["messages"][0])
async def get_session_messages(
    request: Request,
    session: Session = Depends(get_current_session),
):
    """Get all messages for a session.

    Args:
        request: The FastAPI request object for rate limiting.
        session: The current session from the auth token.

    Returns:
        ChatResponse: All messages in the session.

    Raises:
        HTTPException: If there's an error retrieving the messages.
    """
    try:
        messages = await agent.get_chat_history(session.id)
        return ChatResponse(messages=messages)
    except Exception as e:
        logger.exception("get_messages_failed", session_id=session.id, error=str(e))
        raise HTTPException(status_code=500, detail="Unable to load messages.")


@router.delete("/messages")
@limiter.limit(settings.RATE_LIMIT_ENDPOINTS["messages"][0])
async def clear_chat_history(
    request: Request,
    session: Session = Depends(get_current_session),
):
    """Clear all messages for a session.

    Args:
        request: The FastAPI request object for rate limiting.
        session: The current session from the auth token.

    Returns:
        dict: A message indicating the chat history was cleared.
    """
    try:
        await agent.clear_chat_history(session.id)
        return {"message": "Chat history cleared successfully"}
    except Exception as e:
        logger.exception("clear_chat_history_failed", session_id=session.id, error=str(e))
        raise HTTPException(status_code=500, detail="Unable to clear messages.")
