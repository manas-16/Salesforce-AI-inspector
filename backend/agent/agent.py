# ─── Salesforce (AI)nspector — agent/agent.py ────────────────────────────────
# LangChain agent definition.
# Responsibilities:
#   1. Build the LLM from the user's chosen provider + API key
#   2. Assemble all tools for the request
#   3. Create the agent executor
#   4. Stream tokens back to main.py

import logging
from typing import AsyncGenerator

try:
    from langchain.agents import AgentExecutor, create_tool_calling_agent
    from langchain.agents import create_agent
    _HAS_AGENT_EXECUTOR = True
except Exception:
    # Newer langchain versions expose a different API (create_agent)
    from langchain.agents import create_agent
    AgentExecutor = None
    _HAS_AGENT_EXECUTOR = False
from langchain_core.messages import HumanMessage, AIMessage, SystemMessage
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder

from agent.prompts import SYSTEM_PROMPT
from agent.tools.query_tool        import make_query_tools
from agent.tools.user_tool         import make_user_tools
from agent.tools.permission_tool   import make_permission_tools
from agent.tools.password_tool     import make_password_tools
from agent.tools.data_tool         import make_data_tools
from agent.tools.metadata_tool     import make_metadata_tools
from agent.tools.audit_tool        import make_audit_tools
from agent.tools.debug_tool        import make_debug_tools
from agent.tools.rca_tool          import make_rca_tools
from agent.tools.testdata_tool     import make_testdata_tools
from agent.tools.testclass_tool    import make_testclass_tools
from agent.tools.file_parser_tool  import make_file_parser_tools

logger = logging.getLogger(__name__)


# ─── LLM FACTORY ──────────────────────────────────────────────────────────────

def get_llm(provider: str, api_key: str):
    """
    Return a LangChain chat model for the given provider.
    All providers expose the same interface — tools and agent logic
    require zero changes when switching providers.
    """
    provider = provider.lower()

    if provider == 'anthropic':
        from langchain_anthropic import ChatAnthropic
        llm = ChatAnthropic(
            model='claude-sonnet-4-20250514',
            api_key=api_key,
            streaming=True,
            max_tokens=8096,
        )
        llm._sfai_provider = provider
        return llm

    elif provider == 'openai':
        from langchain_openai import ChatOpenAI
        llm = ChatOpenAI(
            model='gpt-4o',
            api_key=api_key,
            streaming=True,
            max_tokens=8096,
        )
        llm._sfai_provider = provider
        return llm

    elif provider == 'google':
        from langchain_google_genai import ChatGoogleGenerativeAI
        llm = ChatGoogleGenerativeAI(
            model='gemini-3-flash-preview',
            google_api_key=api_key,
            streaming=True,
            max_output_tokens=8096,
        )
        llm._sfai_provider = provider
        return llm

    else:
        raise ValueError(
            f'Unsupported LLM provider: {provider}. '
            f'Supported: anthropic, openai, google'
        )


# ─── TOOL ASSEMBLY ────────────────────────────────────────────────────────────

def assemble_tools(
    session_id: str,
    instance_url: str,
    is_production: bool,
) -> list:
    """
    Assemble all tools for a request.
    Write tools receive is_production — they block if True.
    Read-only tools (audit, debug, rca, query) are always available.
    """
    tools = []

    # Read tools — always available
    tools.extend(make_query_tools(session_id, instance_url))
    tools.extend(make_audit_tools(session_id, instance_url))
    tools.extend(make_debug_tools(session_id, instance_url))
    tools.extend(make_rca_tools(session_id, instance_url))
    tools.extend(make_file_parser_tools())

    # Write tools — blocked on production
    tools.extend(make_user_tools(session_id, instance_url, is_production))
    tools.extend(make_permission_tools(session_id, instance_url, is_production))
    tools.extend(make_password_tools(session_id, instance_url, is_production))
    tools.extend(make_data_tools(session_id, instance_url, is_production))
    tools.extend(make_metadata_tools(session_id, instance_url, is_production))
    tools.extend(make_testdata_tools(session_id, instance_url, is_production))
    tools.extend(make_testclass_tools(session_id, instance_url, is_production))

    logger.info(f'Assembled {len(tools)} tools. Production mode: {is_production}')
    return tools


# ─── AGENT BUILDER ────────────────────────────────────────────────────────────

def build_agent(
    api_key: str,
    llm_provider: str,
    session_id: str,
    instance_url: str,
    is_production: bool,
) -> object:
    """
    Build and return a LangChain AgentExecutor for a single request.
    Called once per /chat request in main.py.
    """
    llm   = get_llm(llm_provider, api_key)
    tools = assemble_tools(session_id, instance_url, is_production)

    prompt = ChatPromptTemplate.from_messages([
        SystemMessage(content=SYSTEM_PROMPT),
        MessagesPlaceholder(variable_name='chat_history'),
        ('human', '{input}'),
        MessagesPlaceholder(variable_name='agent_scratchpad'),
    ])

    if _HAS_AGENT_EXECUTOR and getattr(llm, '_sfai_provider', None) != 'google':
        agent = create_tool_calling_agent(llm, tools, prompt)

        executor = AgentExecutor(
            agent=agent,
            tools=tools,
            verbose=True,
            max_iterations=15,          # prevent infinite loops
            max_execution_time=120,     # 2 min hard timeout
            handle_parsing_errors=True, # recover from malformed tool calls
            return_intermediate_steps=False,
        )

        return executor

    # Fallback for newer LangChain and Google/Gemini: use create_agent
    # (returns compiled graph). We pass the system prompt separately; messages
    # are supplied at run time.
    compiled = create_agent(
        model=llm,
        tools=tools,
        system_prompt=SYSTEM_PROMPT,
        debug=False,
    )

    return compiled


# ─── STREAMING ────────────────────────────────────────────────────────────────

async def run_agent_stream(
    agent_executor: object,
    message: str,
    history: list,
    file_name: str = None,
    file_content: str = None,
    file_type: str = None,
    page_context: dict = None,
    is_production: bool = False,
) -> AsyncGenerator[str, None]:
    """
    Run the agent and stream response tokens.
    Builds the full input message with file context and page context injected.
    """

    # Build the full input string
    full_input = _build_input(
        message=message,
        file_name=file_name,
        file_content=file_content,
        file_type=file_type,
        page_context=page_context,
        is_production=is_production,
    )

    # Convert history to LangChain message objects
    chat_history = _build_history(history)

    logger.info(f'Running agent. Input length: {len(full_input)}. History: {len(chat_history)} messages.')

    def _message_dicts(chat_history, full_input):
        messages = [
            *_langchain_messages_to_dicts(chat_history),
            {'role': 'user', 'content': full_input},
        ]
        return [msg for msg in messages if msg['content']]

    def _build_agent_inputs(agent_executor, full_input, chat_history):
        # Prefer explicit input key when the compiled agent expects an input string.
        input_keys = getattr(agent_executor, 'input_keys', None)
        if input_keys is not None:
            try:
                keys = list(input_keys)
            except TypeError:
                keys = [input_keys]
            if 'input' in keys:
                inputs = {'input': full_input}
                if 'chat_history' in keys:
                    inputs['chat_history'] = chat_history
                if 'messages' in keys:
                    inputs['messages'] = _message_dicts(chat_history, full_input)
                return inputs
            if 'messages' in keys:
                return {'messages': _message_dicts(chat_history, full_input)}

        # Compiled LangChain agents accept messages; classic executors accept input.
        return {'messages': _message_dicts(chat_history, full_input)}

    inputs = _build_agent_inputs(agent_executor, full_input, chat_history)
    is_classic_executor = AgentExecutor is not None and isinstance(agent_executor, AgentExecutor)

    # Classic AgentExecutor exposes token events and expects input/chat_history.
    if is_classic_executor and hasattr(agent_executor, 'astream_events'):
        async for event in agent_executor.astream_events(
            inputs,
            version='v2',
        ):
            kind = event.get('event')
            name = event.get('name', '')

            # Yield tokens from the final LLM response only
            # Skip tool call tokens and intermediate steps
            if kind == 'on_chat_model_stream':
                chunk = event.get('data', {}).get('chunk')
                if chunk and hasattr(chunk, 'content'):
                    content = chunk.content
                    if isinstance(content, str) and content:
                        yield content
                    elif isinstance(content, list):
                        for block in content:
                            if isinstance(block, dict) and block.get('type') == 'text':
                                text = block.get('text', '')
                                if text:
                                    yield text
        return

    # Try async streaming if available
    if hasattr(agent_executor, 'astream'):
        async for chunk in agent_executor.astream(inputs, stream_mode="messages"):
            # chunk may be str, dict, or object — handle common shapes
            if chunk is None:
                continue
            if isinstance(chunk, str):
                yield chunk
                continue
            if isinstance(chunk, tuple) and chunk:
                chunk = chunk[0]
            if hasattr(chunk, 'content'):
                content = chunk.content
                if isinstance(content, str) and content:
                    yield content
                    continue
                if isinstance(content, list):
                    text = ''.join(block.get('text', '') for block in content if isinstance(block, dict))
                    if text:
                        yield text
                        continue
            # dict-like
            if isinstance(chunk, dict):
                # common shape: {'delta': 'text'} or {'content': '...'}
                text = chunk.get('delta') or chunk.get('content') or chunk.get('text')
                if isinstance(text, str) and text:
                    yield text
                    continue
                # nested possibilities
                data = chunk.get('data') or chunk.get('value')
                if isinstance(data, str):
                    yield data
                    continue
    else:
        # synchronous stream fallback
        for chunk in agent_executor.stream(inputs, stream_mode="messages"):
            if chunk is None:
                continue
            if isinstance(chunk, str):
                yield chunk
                continue
            if isinstance(chunk, tuple) and chunk:
                chunk = chunk[0]
            if hasattr(chunk, 'content'):
                content = chunk.content
                if isinstance(content, str) and content:
                    yield content
                    continue
                if isinstance(content, list):
                    text = ''.join(block.get('text', '') for block in content if isinstance(block, dict))
                    if text:
                        yield text
                        continue
            if isinstance(chunk, dict):
                text = chunk.get('delta') or chunk.get('content') or chunk.get('text')
                if isinstance(text, str) and text:
                    yield text
                    continue


# ─── INPUT BUILDER ────────────────────────────────────────────────────────────

def _build_input(
    message: str,
    file_name: str = None,
    file_content: str = None,
    file_type: str = None,
    page_context: dict = None,
    is_production: bool = False,
) -> str:
    """
    Construct the full input string for the agent.
    Injects file content and page context as structured context blocks.
    """
    parts = []

    # Normalize Pydantic PageContext models to dicts
    if page_context and not isinstance(page_context, dict):
        if hasattr(page_context, 'dict'):
            page_context = page_context.dict()
        else:
            page_context = {
                key: value
                for key, value in vars(page_context).items()
                if not key.startswith('_')
            }

    # Production warning — reinforce the system prompt
    if is_production:
        parts.append(
            '[SYSTEM NOTE: This is a PRODUCTION org. '
            'All write operations are blocked. Read-only mode.]'
        )

    # Page context — what page the user is on
    if page_context:
        ctx_parts = []
        if page_context.get('heading'):
            ctx_parts.append(f'Page: {page_context["heading"]}')
        if page_context.get('breadcrumb'):
            ctx_parts.append(f'Location: {page_context["breadcrumb"]}')
        if page_context.get('url'):
            ctx_parts.append(f'URL: {page_context["url"]}')
        if page_context.get('error_messages'):
            errors = '; '.join(page_context['error_messages'])
            ctx_parts.append(f'Visible errors: {errors}')
        if ctx_parts:
            parts.append('[PAGE CONTEXT]\n' + '\n'.join(ctx_parts))

    # File content
    if file_name and file_content:
        # Truncate very large files — agent doesn't need more than 8k chars
        truncated  = len(file_content) > 8000
        content    = file_content[:8000]
        parts.append(
            f'[ATTACHED FILE: {file_name} ({file_type})]\n'
            f'```\n{content}\n```'
            + ('\n[File truncated at 8000 chars]' if truncated else '')
        )

    # User message
    parts.append(message)

    return '\n\n'.join(parts)


# ─── HISTORY BUILDER ──────────────────────────────────────────────────────────

def _build_history(history: list) -> list:
    """
    Convert raw history dicts to LangChain message objects.
    history: [{'role': 'user'|'assistant', 'content': str}]
    """
    messages = []
    for msg in history:
        if isinstance(msg, dict):
            role = msg.get('role', '')
            content = msg.get('content', '')
        else:
            role = getattr(msg, 'role', '')
            content = getattr(msg, 'content', '')

        content = content or ''
        if not content:
            continue

        if role == 'user':
            messages.append(HumanMessage(content=content))
        elif role == 'assistant':
            messages.append(AIMessage(content=content))
    return messages


def _langchain_messages_to_dicts(messages: list) -> list:
    """
    Convert LangChain or Pydantic message objects to provider-friendly dicts.
    Compiled LangChain agents receive the system prompt separately, so this only
    returns user/assistant turns.
    """
    converted = []
    for msg in messages:
        if isinstance(msg, dict):
            role = msg.get('role', 'assistant')
            content = msg.get('content', '')
        else:
            role = getattr(msg, 'role', None) or getattr(msg, 'type', 'assistant')
            content = getattr(msg, 'content', '')

        if role in ('human', 'user'):
            role = 'user'
        elif role in ('ai', 'assistant'):
            role = 'assistant'
        else:
            continue

        if content:
            converted.append({'role': role, 'content': content})

    return converted
