"""This file contains the LangGraph Agent/workflow and interactions with the LLM."""

import asyncio
import json
import re
from collections import Counter
from collections.abc import Mapping
from typing import (
    Any,
    AsyncGenerator,
    Optional,
    cast,
)

from langchain_core.messages import (
    AIMessage,
    AIMessageChunk,
    BaseMessage,
    HumanMessage,
    ToolMessage,
    convert_to_openai_messages,
)
from langgraph.errors import GraphInterrupt
from langgraph.graph import (
    END,
    StateGraph,
)
from langchain_core.runnables.config import RunnableConfig
from langgraph.graph.state import (
    Command,
    CompiledStateGraph,
)
from langgraph.types import StateSnapshot
from app.core.config import (
    Environment,
    settings,
)
from app.core.langgraph.tool_policy import ToolBudgetClass, get_tool_policy
from app.core.langgraph.tools import get_tools
from app.core.langgraph.checkpoints import CheckpointService
from app.core.langgraph.tool_executor import ToolExecutor
from app.core.logging import logger
from app.core.metrics import llm_inference_duration_seconds, tool_budget_rejections_total
from app.core.prompts.assembly import build_system_prompt
from app.core.skills.registry import skill_registry
from app.core.skills.tools import parse_activation_receipt
from app.services.user_account import user_account_service
from app.schemas import (
    ChatInputMessage,
    ChatOutputMessage,
    GraphState,
)
from app.services.llm import LLMService
from app.services.memory import memory_service
from app.services.memory_jobs import memory_job_service
from app.services.database import database_service
from app.services.user_llm_settings import user_llm_settings_service
from app.utils.graph import (
    dump_messages,
    extract_text_content,
    prepare_messages,
    process_llm_response,
)


_TOOL_GUARD_KEY = "tool_guard"
_FINAL_ANSWER_INSTRUCTION = (
    "The tool boundary for this user turn has been reached. Do not call or describe another tool call. "
    "Answer now using the tool results already present. If the available evidence is insufficient, say so clearly."
)


def _current_turn_messages(messages: list[Any]) -> list[BaseMessage]:
    """Return messages since the latest user message in the checkpointed history."""
    start = 0
    for index, message in enumerate(messages):
        if isinstance(message, HumanMessage):
            start = index
    return [message for message in messages[start:] if isinstance(message, BaseMessage)]


def _tool_call_fingerprint(tool_call: Mapping[str, Any]) -> str:
    """Build a deterministic per-turn identity without logging tool arguments."""
    arguments = json.dumps(
        tool_call.get("args") or {},
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return f"{tool_call.get('name', '')}:{arguments}"


def _prior_tool_fingerprints(messages: list[BaseMessage]) -> set[str]:
    """Collect tool requests already emitted during the current user turn."""
    return {
        _tool_call_fingerprint(tool_call)
        for message in messages
        if isinstance(message, AIMessage)
        for tool_call in message.tool_calls
    }


def _executed_tool_count(messages: list[BaseMessage]) -> int:
    """Count actual tool executions, excluding deterministic guard responses."""
    return sum(
        1
        for message in messages
        if isinstance(message, ToolMessage) and not message.additional_kwargs.get(_TOOL_GUARD_KEY)
    )


def _executed_tool_counts(messages: list[BaseMessage]) -> Counter[ToolBudgetClass]:
    """Count actual executions by independent policy budget class."""
    counts: Counter[ToolBudgetClass] = Counter()
    for message in messages:
        if isinstance(message, ToolMessage) and not message.additional_kwargs.get(_TOOL_GUARD_KEY):
            counts[get_tool_policy(message.name or "").budget_class] += 1
    return counts


def _budget_limit(budget_class: ToolBudgetClass) -> int:
    """Resolve one class limit from validated settings."""
    if budget_class == ToolBudgetClass.CONTROL:
        return settings.AGENT_CONTROL_TOOL_CALL_BUDGET
    if budget_class == ToolBudgetClass.READ_ONLY:
        return settings.AGENT_TOOL_CALL_BUDGET
    if budget_class == ToolBudgetClass.INTERACTIVE:
        return settings.AGENT_INTERACTIVE_TOOL_CALL_BUDGET
    return settings.AGENT_SIDE_EFFECT_TOOL_CALL_BUDGET


def _final_answer_reason(messages: list[Any]) -> str | None:
    """Return why the next chat node must generate a final answer without tools."""
    current_turn = _current_turn_messages(messages)
    for message in current_turn:
        if (
            isinstance(message, ToolMessage)
            and message.name == "knowledge_search"
            and "<evidence" in str(message.content)
            and "<doc " in str(message.content)
        ):
            return "knowledge_evidence_available"
    if any(
        isinstance(message, ToolMessage) and message.additional_kwargs.get(_TOOL_GUARD_KEY) for message in current_turn
    ):
        return "tool_guard_triggered"
    if _executed_tool_count(current_turn) >= settings.AGENT_TOTAL_TOOL_CALL_BUDGET:
        return "tool_budget_exhausted"
    return None


def _trusted_skill_instructions(messages: list[BaseMessage]) -> str:
    """Resolve only a server-generated activation ToolMessage from this user turn."""
    for message in reversed(messages):
        if not isinstance(message, ToolMessage) or message.name != "activate_skill":
            continue
        if message.additional_kwargs.get(_TOOL_GUARD_KEY):
            return ""
        try:
            activation = parse_activation_receipt(str(message.content))
            return skill_registry.load_instructions(activation)
        except (KeyError, OSError, UnicodeError, ValueError) as error:
            logger.warning("skill_activation_receipt_rejected", error_type=type(error).__name__)
            return ""
    return ""


def _memory_messages(messages: list[BaseMessage]) -> list[dict[str, Any]]:
    """Keep tool/control payloads out of long-term user-memory extraction."""
    converted = cast(list[dict[str, Any]], convert_to_openai_messages(messages))
    return [message for message in converted if message.get("role") in {"user", "assistant"} and message.get("content")]


def _guarded_tool_message(tool_call: dict[str, Any], *, reason: str) -> ToolMessage:
    """Return one deterministic response for a skipped duplicate or over-budget call."""
    if reason == "duplicate":
        content = "This exact tool request was already handled. Use the existing result and answer now."
    else:
        content = "The per-turn tool budget is exhausted. Use available results and answer now."
    return ToolMessage(
        content=content,
        name=str(tool_call.get("name") or "tool"),
        tool_call_id=str(tool_call.get("id") or "guarded-tool-call"),
        additional_kwargs={_TOOL_GUARD_KEY: reason},
    )


def _tool_result_summary(name: str, content: str) -> str:
    """Return a short, human-readable summary of a tool result for the stream UI."""
    if name == "knowledge_search":
        if "<evidence" not in content:
            return "未找到相关文档"
        sources = sorted({match for match in re.findall(r'source="([^"]+)"', content)})
        count = content.count("<doc")
        source_text = "、".join(sources[:4]) + (" 等" if len(sources) > 4 else "")
        return f"命中 {count} 条" + (f"（来源：{source_text}）" if source_text else "")
    if name == "duckduckgo_search":
        return "网页搜索完成"
    if name == "ask_human":
        return "等待用户确认"
    if name == "activate_skill":
        try:
            activation = parse_activation_receipt(content)
            return f"已加载 Skill：{activation.name}"
        except ValueError:
            return "Skill 加载失败"
    snippet = re.sub(r"\s+", " ", content).strip()
    return (snippet[:120] + ("…" if len(snippet) > 120 else "")) if snippet else "完成"


class LangGraphAgent:
    """Manages the LangGraph Agent/workflow and interactions with the LLM.

    This class handles the creation and management of the LangGraph workflow,
    including LLM interactions, database connections, and response processing.
    """

    def __init__(self):
        """Initialize the LangGraph Agent with necessary components."""
        self.tools_by_name = {tool.name: tool for tool in get_tools()}
        self.tool_executor = ToolExecutor()
        self.checkpoints = CheckpointService()
        self._graph: Optional[CompiledStateGraph] = None
        logger.info(
            "langgraph_agent_initialized",
            model=settings.DEFAULT_LLM_MODEL,
            environment=settings.ENVIRONMENT.value,
        )

    @property
    def is_ready(self) -> bool:
        """Return whether the graph and its checkpoint pool are initialized."""
        return self._graph is not None and self.checkpoints.is_ready

    async def close(self) -> None:
        """Close agent-owned resources."""
        await self.checkpoints.close()

    async def _chat(self, state: GraphState, config: RunnableConfig) -> Command:
        """Process the chat state and generate a response.

        Args:
            state (GraphState): The current state of the conversation.
            config (RunnableConfig): The runnable configuration for this invocation.

        Returns:
            Command: Command object with updated state and next node to execute.
        """
        user_id = config.get("metadata", {}).get("user_id")
        if user_id is None:
            raise RuntimeError("authenticated user context is required for LLM execution")
        runtime = await user_llm_settings_service.get_runtime(int(user_id))
        current_turn = _current_turn_messages(state.messages)
        final_answer_reason = _final_answer_reason(current_turn)
        request_llm_service = LLMService(runtime)
        request_tools = get_tools()
        self.tools_by_name = {tool.name: tool for tool in request_tools}
        if final_answer_reason is None:
            request_llm_service.bind_tools(request_tools)
        else:
            logger.info(
                "agent_forcing_final_answer",
                reason=final_answer_reason,
                tool_calls_used=_executed_tool_count(current_turn),
            )
        model_name = runtime.model
        reasoning_effort = config.get("metadata", {}).get("reasoning_effort", "off")
        thread_id = config.get("configurable", {}).get("thread_id")
        user = await database_service.get_user(int(user_id))
        if user is None:
            raise RuntimeError("authenticated user not found")
        profile, preferences = await asyncio.gather(user_account_service.profile(user), user_account_service.personalization(user.id))
        system_prompt = build_system_prompt(
            profile,
            preferences,
            state.long_term_memory,
            final_answer_instruction=_FINAL_ANSWER_INSTRUCTION if final_answer_reason is not None else None,
            skills_prompt=skill_registry.build_skills_prompt(),
            trusted_skill=_trusted_skill_instructions(current_turn),
        )

        # Prepare messages with system prompt
        messages = prepare_messages(state.messages, system_prompt)

        try:
            # Use LLM service with automatic retries and circular fallback
            with llm_inference_duration_seconds.labels(model=model_name).time():
                thinking_enabled = runtime.thinking_enabled and reasoning_effort in ("high", "max")
                model_options: dict[str, Any] = {
                    "extra_body": {"thinking": {"type": "enabled" if thinking_enabled else "disabled"}}
                }
                if thinking_enabled:
                    model_options["reasoning_effort"] = reasoning_effort
                else:
                    model_options["temperature"] = runtime.temperature
                response_message = await request_llm_service.call(dump_messages(messages), **model_options)

            # Process response to handle structured content blocks
            response_message = process_llm_response(response_message)

            logger.info(
                "llm_response_generated",
                session_id=thread_id,
                model=model_name,
                environment=settings.ENVIRONMENT.value,
            )

            # Determine next node based on whether there are tool calls
            if final_answer_reason is None and isinstance(response_message, AIMessage) and response_message.tool_calls:
                goto = "tool_call"
            else:
                goto = END

            return Command(update={"messages": [response_message]}, goto=goto)
        except Exception as e:
            logger.error(
                "llm_call_failed_all_models",
                session_id=thread_id,
                error=str(e),
                environment=settings.ENVIRONMENT.value,
            )
            raise Exception(f"failed to get llm response after trying all models: {str(e)}")

    # Define our tool node
    async def _tool_call(self, state: GraphState, config: RunnableConfig) -> Command:
        """Process tool calls from the last message.

        Args:
            state: The current agent state containing messages and tool calls.
            config: Trusted runtime metadata passed through to tools.

        Returns:
            Command: Command object with updated messages and routing back to chat.
        """
        tool_calls = state.messages[-1].tool_calls
        current_turn = _current_turn_messages(state.messages[:-1])
        seen = _prior_tool_fingerprints(current_turn)
        existing_counts = _executed_tool_counts(current_turn)
        pending_counts: Counter[ToolBudgetClass] = Counter()
        existing_total = _executed_tool_count(current_turn)
        outputs: list[ToolMessage | None] = [None] * len(tool_calls)
        pending_calls: list[dict[str, Any]] = []
        pending_positions: list[int] = []

        for index, tool_call in enumerate(tool_calls):
            fingerprint = _tool_call_fingerprint(tool_call)
            tool_name = str(tool_call.get("name") or "tool")
            if fingerprint in seen:
                logger.warning("duplicate_tool_call_skipped", tool_name=tool_name)
                outputs[index] = _guarded_tool_message(tool_call, reason="duplicate")
                continue
            seen.add(fingerprint)
            budget_class = get_tool_policy(tool_name).budget_class
            class_exhausted = existing_counts[budget_class] + pending_counts[budget_class] >= _budget_limit(
                budget_class
            )
            total_exhausted = existing_total + len(pending_calls) >= settings.AGENT_TOTAL_TOOL_CALL_BUDGET
            if class_exhausted or total_exhausted:
                logger.warning(
                    "tool_call_budget_exhausted",
                    tool_name=tool_name,
                    budget_class=budget_class.value,
                )
                tool_budget_rejections_total.labels(budget_class=budget_class.value).inc()
                outputs[index] = _guarded_tool_message(tool_call, reason="budget_exhausted")
                continue
            pending_calls.append(tool_call)
            pending_positions.append(index)
            pending_counts[budget_class] += 1

        if pending_calls:
            executed_outputs = await self.tool_executor.execute_many(pending_calls, self.tools_by_name, config)
            for index, output in zip(pending_positions, executed_outputs, strict=True):
                outputs[index] = output

        return Command(update={"messages": [output for output in outputs if output is not None]}, goto="chat")

    async def create_graph(self) -> Optional[CompiledStateGraph]:
        """Create and configure the LangGraph workflow.

        Returns:
            Optional[CompiledStateGraph]: The configured LangGraph instance or None if init fails
        """
        if self._graph is None:
            try:
                graph_builder = StateGraph(GraphState)
                graph_builder.add_node("chat", self._chat, destinations=("tool_call", END))
                graph_builder.add_node(
                    "tool_call",
                    self._tool_call,
                    destinations=("chat",),
                )
                graph_builder.set_entry_point("chat")
                graph_builder.set_finish_point("chat")

                # Get connection pool (may be None in production if DB unavailable)
                checkpointer = await self.checkpoints.create_saver()
                if checkpointer is None:
                    # In production, proceed without checkpointer if needed
                    checkpointer = None
                    if settings.ENVIRONMENT != Environment.PRODUCTION:
                        raise RuntimeError("checkpoint pool initialization failed")

                self._graph = graph_builder.compile(
                    checkpointer=checkpointer, name=f"{settings.PROJECT_NAME} Agent ({settings.ENVIRONMENT.value})"
                )

                logger.info(
                    "graph_created",
                    graph_name=f"{settings.PROJECT_NAME} Agent",
                    environment=settings.ENVIRONMENT.value,
                    has_checkpointer=checkpointer is not None,
                )
            except Exception as e:
                logger.error("graph_creation_failed", error=str(e), environment=settings.ENVIRONMENT.value)
                # In production, we don't want to crash the app
                if settings.ENVIRONMENT == Environment.PRODUCTION:
                    logger.warning("continuing_without_graph")
                    return None
                raise e

        return self._graph

    async def _get_graph(self) -> CompiledStateGraph:
        """Return the compiled graph, creating it on first access.

        Raises:
            RuntimeError: When ``create_graph()`` swallowed an init failure
                (production-only path) and returned ``None``. Callers can
                rely on the return being non-``None``.
        """
        if self._graph is None:
            self._graph = await self.create_graph()
        if self._graph is None:
            raise RuntimeError("graph initialization failed")
        return self._graph

    async def get_response(
        self,
        messages: list[ChatInputMessage],
        session_id: str,
        user_id: Optional[str] = None,
        username: Optional[str] = None,
        reasoning_effort: str = "off",
        knowledge_space_slugs: list[str] | None = None,
    ) -> list[ChatOutputMessage]:
        """Get a response from the LLM.

        Args:
            messages: The validated user messages to send to the LLM.
            session_id (str): The session ID for the conversation.
            user_id (Optional[str]): The user ID for the conversation.
            username (Optional[str]): The display name of the user.
            reasoning_effort: DeepSeek reasoning mode for this request.
            knowledge_space_slugs: Optional client scope, re-authorized by the server-side retrieval tool.

        Returns:
            list[ChatOutputMessage]: The response from the LLM.
        """
        graph = await self._get_graph()
        config: RunnableConfig = {
            "configurable": {"thread_id": session_id},
            "recursion_limit": settings.AGENT_RECURSION_LIMIT,
            "metadata": {
                "user_id": user_id,
                "username": username,
                "session_id": session_id,
                "environment": settings.ENVIRONMENT.value,
                "debug": settings.DEBUG,
                "reasoning_effort": reasoning_effort,
                "knowledge_space_slugs": list(knowledge_space_slugs or ()),
            },
        }

        try:
            preferences = await user_account_service.personalization(int(user_id)) if user_id is not None else None
            state = await graph.aget_state(config)
            relevant_memory = await memory_service.search(user_id, messages[-1].content) if preferences and preferences.memory_enabled else ""

            if state.next:
                logger.info("resuming_interrupted_graph", session_id=session_id, next_nodes=state.next)
                response = await graph.ainvoke(
                    Command(resume=messages[-1].content),
                    config=config,
                )
            else:
                relevant_memory = relevant_memory or "No relevant memory found."
                response = await graph.ainvoke(
                    input={"messages": dump_messages(messages), "long_term_memory": relevant_memory},
                    config=config,
                )

            # Check if the graph was interrupted during this invocation
            state = await graph.aget_state(config)
            if state.next:
                interrupt_value = state.tasks[0].interrupts[0].value if state.tasks else "Waiting for input."
                logger.info("graph_interrupted", session_id=session_id, interrupt_value=str(interrupt_value))
                return [ChatOutputMessage(role="assistant", content=str(interrupt_value))]

            openai_msgs = _memory_messages(response["messages"])
            if preferences and preferences.memory_enabled:
                await memory_job_service.enqueue(user_id, openai_msgs, config.get("metadata"))
            return self.__process_messages(response["messages"])
        except GraphInterrupt:
            state = await graph.aget_state(config)
            interrupt_value = state.tasks[0].interrupts[0].value if state.tasks else "Waiting for input."
            logger.info("graph_interrupted", session_id=session_id, interrupt_value=str(interrupt_value))
            return [ChatOutputMessage(role="assistant", content=str(interrupt_value))]
        except Exception as e:
            logger.exception("get_response_failed", error=str(e), session_id=session_id)
            raise

    async def get_stream_response(
        self,
        messages: list[ChatInputMessage],
        session_id: str,
        user_id: Optional[str] = None,
        username: Optional[str] = None,
        reasoning_effort: str = "off",
        knowledge_space_slugs: list[str] | None = None,
    ) -> AsyncGenerator[dict[str, Any], None]:
        """Get a stream response from the LLM.

        Args:
            messages: The validated user messages to send to the LLM.
            session_id (str): The session ID for the conversation.
            user_id (Optional[str]): The user ID for the conversation.
            username (Optional[str]): The display name of the user.
            reasoning_effort: DeepSeek reasoning mode for this request.
            knowledge_space_slugs: Optional client scope, re-authorized by the server-side retrieval tool.

        Yields:
            dict[str, Any]: Structured streaming events.
        """
        config: RunnableConfig = {
            "configurable": {"thread_id": session_id},
            "recursion_limit": settings.AGENT_RECURSION_LIMIT,
            "metadata": {
                "user_id": user_id,
                "username": username,
                "session_id": session_id,
                "environment": settings.ENVIRONMENT.value,
                "debug": settings.DEBUG,
                "reasoning_effort": reasoning_effort,
                "knowledge_space_slugs": list(knowledge_space_slugs or ()),
            },
        }
        graph = await self._get_graph()

        try:
            runtime = await user_llm_settings_service.get_runtime(int(user_id)) if user_id is not None else None
            model_name = runtime.model if runtime else "unconfigured"
            effective_effort = reasoning_effort if runtime and runtime.thinking_enabled else "off"
            yield {"type": "meta", "data": {"model": model_name, "reasoning_effort": effective_effort}}
            yield {"type": "stage", "data": {"stage": "memory", "status": "started"}}
            preferences = await user_account_service.personalization(int(user_id)) if user_id is not None else None
            state = await graph.aget_state(config)
            relevant_memory = await memory_service.search(user_id, messages[-1].content) if preferences and preferences.memory_enabled else ""
            memory_count = (
                len([line for line in relevant_memory.splitlines() if line.startswith("* ")]) if relevant_memory else 0
            )
            yield {"type": "stage", "data": {"stage": "memory", "status": "completed", "count": memory_count}}

            if state.next:
                logger.info("resuming_interrupted_graph_stream", session_id=session_id, next_nodes=state.next)
                graph_input = Command(resume=messages[-1].content)
            else:
                relevant_memory = relevant_memory or "No relevant memory found."
                graph_input = {"messages": dump_messages(messages), "long_term_memory": relevant_memory}

            emitted_tool_calls: set[str] = set()

            async for mode, payload in graph.astream(
                graph_input,
                config,
                stream_mode=["messages", "custom"],
            ):
                if mode == "custom":
                    if isinstance(payload, dict) and str(payload.get("type", "")).startswith("rag_"):
                        yield payload
                    continue
                token, _ = payload
                if isinstance(token, ToolMessage):
                    tool_name = token.name or "tool"
                    yield {
                        "type": "stage",
                        "data": {"stage": tool_name, "status": "completed"},
                    }
                    yield {
                        "type": "tool_result",
                        "data": {"name": tool_name, "summary": _tool_result_summary(tool_name, str(token.content))},
                    }
                    continue
                if not isinstance(token, (AIMessage, AIMessageChunk)):
                    continue

                for tool_call in getattr(token, "tool_calls", None) or []:
                    call_id = str(tool_call.get("id") or "")
                    call_key = call_id or f"{tool_call.get('name')}:{len(emitted_tool_calls)}"
                    if (
                        call_key not in emitted_tool_calls
                        and tool_call.get("name")
                        and isinstance(tool_call.get("args"), dict)
                    ):
                        emitted_tool_calls.add(call_key)
                        yield {
                            "type": "tool_call",
                            "data": {"name": str(tool_call["name"]), "args": tool_call["args"]},
                        }

                reasoning = token.additional_kwargs.get("reasoning_content")
                if settings.EXPOSE_REASONING_CONTENT and isinstance(reasoning, str) and reasoning:
                    yield {"type": "reasoning_delta", "content": reasoning, "data": {}}

                text = extract_text_content(token.content)
                if text:
                    yield {"type": "answer_delta", "content": text, "data": {}}

                usage = getattr(token, "usage_metadata", None)
                if isinstance(usage, dict) and usage:
                    yield {"type": "usage", "data": usage}

            # After streaming completes, check for interrupt or update memory
            state = await graph.aget_state(config)
            if state.next:
                interrupt_value = state.tasks[0].interrupts[0].value if state.tasks else "Waiting for input."
                logger.info("graph_interrupted_stream", session_id=session_id, interrupt_value=str(interrupt_value))
                yield {"type": "answer_delta", "content": str(interrupt_value), "data": {}}
            elif state.values and "messages" in state.values:
                openai_msgs = _memory_messages(state.values["messages"])
                if preferences and preferences.memory_enabled:
                    await memory_job_service.enqueue(user_id, openai_msgs, config.get("metadata"))
        except GraphInterrupt:
            state = await graph.aget_state(config)
            interrupt_value = state.tasks[0].interrupts[0].value if state.tasks else "Waiting for input."
            logger.info("graph_interrupted_stream", session_id=session_id, interrupt_value=str(interrupt_value))
            yield {"type": "answer_delta", "content": str(interrupt_value), "data": {}}
        except Exception as stream_error:
            logger.exception("stream_processing_failed", error=str(stream_error), session_id=session_id)
            raise stream_error

    async def get_chat_history(self, session_id: str) -> list[ChatOutputMessage]:
        """Get the chat history for a given thread ID.

        Args:
            session_id (str): The session ID for the conversation.

        Returns:
            list[ChatOutputMessage]: The chat history.
        """
        graph = await self._get_graph()

        config: RunnableConfig = {"configurable": {"thread_id": session_id}}
        state: StateSnapshot = await graph.aget_state(config=config)
        return self.__process_messages(state.values["messages"]) if state.values else []

    def __process_messages(self, messages: list[BaseMessage]) -> list[ChatOutputMessage]:
        openai_style_messages = convert_to_openai_messages(messages)
        # keep just assistant and user messages
        return [
            ChatOutputMessage(role=message["role"], content=str(message["content"]))
            for message in openai_style_messages
            if message["role"] in ["assistant", "user"] and message["content"]
        ]

    async def clear_chat_history(self, session_id: str) -> None:
        """Clear all chat history for a given thread ID.

        Args:
            session_id: The ID of the session to clear history for.

        Raises:
            Exception: If there's an error clearing the chat history.
        """
        try:
            await database_service.clear_checkpoints(session_id)
        except Exception as e:
            logger.exception(
                "clear_chat_history_operation_failed",
                session_id=session_id,
                error=str(e),
            )
            raise
