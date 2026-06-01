# ─── Salesforce (AI)nspector — main.py ───────────────────────────────────────

import asyncio
import html
import json
import logging
from typing import AsyncGenerator

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, StreamingResponse
from pydantic import BaseModel

from models import ChatRequest, HealthResponse
from salesforce.oAuth import validate_session, is_sandbox
from salesforce.oAuth import get_token_username_password
from salesforce.oAuth import create_authorization_url, complete_authorization_code_flow, get_authorization_status, fail_authorization
from agent.agent import build_agent, run_agent_stream

# ─── LOGGING ──────────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s  %(levelname)s  %(name)s — %(message)s'
)
logger = logging.getLogger(__name__)

# ─── APP ──────────────────────────────────────────────────────────────────────

app = FastAPI(
    title='Salesforce (AI)nspector',
    description='AI-powered Salesforce org management backend.',
    version='1.0.0',
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=['*'],
    allow_credentials=True,
    allow_methods=['*'],
    allow_headers=['*'],
)

# ─── MODELS ───────────────────────────────────────────────────────────────────

class OAuthRequest(BaseModel):
    consumer_key:    str
    consumer_secret: str
    username:        str
    password:        str
    security_token:  str = ''
    is_sandbox:      bool = False


class OAuthAuthorizeRequest(BaseModel):
    consumer_key:    str
    consumer_secret: str = ''
    is_sandbox:      bool = False
    redirect_uri:    str = 'http://127.0.0.1:8000/oauth/callback'

# ─── HEALTH ───────────────────────────────────────────────────────────────────

@app.get('/health', response_model=HealthResponse)
async def health():
    return HealthResponse(status='ok', version='1.0.0')

# ─── OAUTH ────────────────────────────────────────────────────────────────────

@app.post('/oauth/token')
async def oauth_token(request: OAuthRequest):
    """
    Exchange Salesforce credentials for a REST API access token.
    Called once from the settings page — token stored in chrome.storage.local.
    """
    try:
        result = await get_token_username_password(
            consumer_key   = request.consumer_key,
            consumer_secret= request.consumer_secret,
            username       = request.username,
            password       = request.password,
            security_token = request.security_token,
            is_sandbox     = request.is_sandbox,
        )
        return {
            'access_token': result['access_token'],
            'instance_url': result['instance_url'],
        }
    except Exception as e:
        logger.error(f'OAuth token exchange failed: {e}')
        raise HTTPException(status_code=401, detail=str(e))


@app.post('/oauth/authorize')
async def oauth_authorize(request: OAuthAuthorizeRequest):
    """
    Start Salesforce Authorization Code + PKCE flow.
    The settings page opens the returned URL and polls /oauth/status/{state}.
    """
    if not request.consumer_key.strip():
        raise HTTPException(status_code=400, detail='Consumer Key is required.')

    try:
        return create_authorization_url(
            consumer_key=request.consumer_key,
            consumer_secret=request.consumer_secret,
            is_sandbox=request.is_sandbox,
            redirect_uri=request.redirect_uri,
        )
    except Exception as e:
        logger.error(f'Failed to start OAuth authorization: {e}')
        raise HTTPException(status_code=500, detail=str(e))


@app.get('/oauth/callback', response_class=HTMLResponse)
async def oauth_callback(code: str | None = None, state: str | None = None, error: str | None = None, error_description: str | None = None):
    if error:
        message = html.escape(error_description or error)
        if state:
            fail_authorization(state, error_description or error)
        logger.error(f'OAuth callback error: {error} - {message}')
        return HTMLResponse(
            f'<html><body><h2>Salesforce login failed</h2><p>{message}</p><p>You can close this tab.</p></body></html>',
            status_code=400,
        )

    if not code or not state:
        return HTMLResponse(
            '<html><body><h2>Salesforce login failed</h2><p>Missing authorization code or state.</p><p>You can close this tab.</p></body></html>',
            status_code=400,
        )

    try:
        await complete_authorization_code_flow(state, code)
        return HTMLResponse(
            '<html><body><h2>Salesforce connected</h2><p>You can close this tab and return to Salesforce (AI)nspector.</p></body></html>'
        )
    except Exception as e:
        logger.error(f'OAuth callback exchange failed: {e}')
        message = html.escape(str(e))
        return HTMLResponse(
            f'<html><body><h2>Salesforce login failed</h2><p>{message}</p><p>You can close this tab.</p></body></html>',
            status_code=401,
        )


@app.get('/oauth/status/{state}')
async def oauth_status(state: str):
    return get_authorization_status(state)

# ─── CHAT ─────────────────────────────────────────────────────────────────────

@app.post('/chat')
async def chat(request: ChatRequest):
    """
    Main endpoint. Receives user message + Salesforce session context,
    runs the LangChain agent, streams the response as SSE.
    """
    logger.info(f'Chat request from {request.instance_url}')

    # 1. Validate session
    try:
        session_valid = await validate_session(
            session_id   = request.session_id,
            instance_url = request.instance_url,
        )
    except Exception as e:
        logger.error(f'Session validation failed: {e}')
        raise HTTPException(status_code=401, detail='Invalid or expired Salesforce session.')

    if not session_valid:
        raise HTTPException(status_code=401, detail='Invalid or expired Salesforce session.')

    # 2. Sandbox check
    try:
        org_is_sandbox = await is_sandbox(
            session_id   = request.session_id,
            instance_url = request.instance_url,
        )
    except Exception as e:
        logger.warning(f'Sandbox check failed: {e}. Defaulting to read-only.')
        org_is_sandbox = False

    is_production = not org_is_sandbox

    if is_production:
        logger.info('Production org detected — write operations blocked.')

    # 3. Build agent
    try:
        agent_executor = build_agent(
            api_key      = request.api_key,
            llm_provider = request.llm_provider,
            session_id   = request.session_id,
            instance_url = request.instance_url,
            is_production= is_production,
        )
    except Exception as e:
        logger.error(f'Failed to build agent: {e}')
        raise HTTPException(status_code=500, detail=f'Failed to initialize agent: {str(e)}')

    # 4. Stream response
    return StreamingResponse(
        stream_agent_response(agent_executor, request, is_production),
        media_type='text/event-stream',
        headers={
            'Cache-Control':    'no-cache',
            'X-Accel-Buffering': 'no',
        },
    )


async def stream_agent_response(
    agent_executor,
    request: ChatRequest,
    is_production: bool,
) -> AsyncGenerator[str, None]:
    try:
        async for token in run_agent_stream(
            agent_executor = agent_executor,
            message        = request.message,
            history        = request.history,
            file_name      = request.file_name,
            file_content   = request.file_content,
            file_type      = request.file_type,
            page_context   = request.page_context,
            is_production  = is_production,
        ):
            payload = json.dumps({'token': token})
            yield f'data: {payload}\n\n'
            await asyncio.sleep(0)

    except Exception as e:
        logger.error(f'Agent stream error: {e}')
        yield f'data: {json.dumps({"error": str(e)})}\n\n'

    finally:
        yield 'data: [DONE]\n\n'


# ─── RUN ──────────────────────────────────────────────────────────────────────

if __name__ == '__main__':
    import uvicorn
    uvicorn.run(
        'main:app',
        host='127.0.0.1',
        port=8000,
        reload=True,
        log_level='info',
    )
