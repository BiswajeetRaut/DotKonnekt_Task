"""Chat completion loop with tool-calling — the "agentic" part of agentic
RAG. The model decides which of the two tools (if any) it needs per
question and can call more than one before answering; this module just
executes whatever it asks for and feeds the result back.
"""

import json
import logging

from openai import AsyncOpenAI
from sqlalchemy.orm import Session

from app.chat.tools import (
    RUN_AGGREGATE_QUERY_SCHEMA,
    SEARCH_SIMILAR_SCHEMA,
    AggregateArgs,
    run_aggregate_query,
    search_similar,
)
from app.config import settings
from app.models import ChatMessage

logger = logging.getLogger(__name__)

CHAT_MODEL = "gpt-4o-mini"
MAX_TOOL_ITERATIONS = 4
HISTORY_LIMIT = 10

SYSTEM_PROMPT = (
    "You are a helpful assistant for a personal expense tracker. Answer only "
    "using the tools provided — never invent numbers or specific expenses. "
    "Use run_aggregate_query for questions about totals, counts, or averages. "
    "Use search_similar for questions about why something was flagged, or to "
    "find specific expenses by description. If a question needs both, call "
    "both before giving your final answer. Keep answers brief and concrete."
)

_client: AsyncOpenAI | None = None


def _get_client() -> AsyncOpenAI | None:
    global _client
    if not settings.openai_api_key:
        return None
    if _client is None:
        _client = AsyncOpenAI(api_key=settings.openai_api_key)
    return _client


async def _execute_tool(db: Session, user_id: int, name: str, arguments: dict) -> dict:
    if name == "search_similar":
        chunks = await search_similar(db, user_id, arguments["query"])
        return {"results": chunks or ["No matching expenses or alerts found."]}
    if name == "run_aggregate_query":
        args = AggregateArgs(**arguments)
        return run_aggregate_query(db, user_id, args)
    return {"error": f"unknown tool {name}"}


async def answer(db: Session, user_id: int, message: str) -> str:
    client = _get_client()
    if client is None:
        return "The chatbot is not configured — set OPENAI_API_KEY to enable it."

    history = (
        db.query(ChatMessage)
        .filter(ChatMessage.user_id == user_id)
        .order_by(ChatMessage.created_at.desc())
        .limit(HISTORY_LIMIT)
        .all()
    )
    history.reverse()

    messages: list[dict] = [{"role": "system", "content": SYSTEM_PROMPT}]
    messages += [{"role": m.role, "content": m.content} for m in history]
    messages.append({"role": "user", "content": message})

    for _ in range(MAX_TOOL_ITERATIONS):
        completion = await client.chat.completions.create(
            model=CHAT_MODEL,
            messages=messages,
            tools=[SEARCH_SIMILAR_SCHEMA, RUN_AGGREGATE_QUERY_SCHEMA],
        )
        choice = completion.choices[0]
        tool_calls = choice.message.tool_calls

        if not tool_calls:
            return choice.message.content or ""

        messages.append(choice.message.model_dump(exclude_none=True))
        for call in tool_calls:
            try:
                args = json.loads(call.function.arguments)
                result = await _execute_tool(db, user_id, call.function.name, args)
            except Exception:
                logger.exception("Tool execution failed for %s", call.function.name)
                result = {"error": "tool execution failed"}
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": call.id,
                    "content": json.dumps(result, default=str),
                }
            )

    return "I was not able to finish looking that up — try rephrasing your question."
