# ─── Salesforce (AI)nspector — salesforce/auth.py ────────────────────────────
# Responsibilities:
#   1. Validate Salesforce session token against the org
#   2. Detect if org is sandbox or production via Organisation object
#   3. Provide a reusable httpx client with auth headers

import logging
from urllib.parse import urlparse

import httpx

logger = logging.getLogger(__name__)

# ─── HTTP CLIENT ──────────────────────────────────────────────────────────────

def get_headers(session_id: str) -> dict:
    return {
        'Authorization': f'Bearer {session_id}',
        'Content-Type': 'application/json',
        'Accept': 'application/json',
    }

def get_base_url(instance_url: str) -> str:
    return f'{get_api_instance_url(instance_url)}/services/data/v59.0'


def get_api_instance_url(instance_url: str) -> str:
    """
    Return the Salesforce API host for a UI or API URL.
    Lightning hosts redirect for /services/data; REST/SOAP APIs should use the
    matching My Domain host.
    """
    parsed = urlparse(instance_url if '://' in instance_url else f'https://{instance_url}')
    hostname = (parsed.hostname or '').lower()

    if hostname.endswith('.trailblaze.lightning.force.com'):
        hostname = hostname.replace('.trailblaze.lightning.force.com', '.trailblaze.my.salesforce.com')
    elif hostname.endswith('.lightning.force.com'):
        hostname = hostname.replace('.lightning.force.com', '.my.salesforce.com')

    return f'{parsed.scheme or "https"}://{hostname}'.rstrip('/')


def get_api_instance_url_candidates(instance_url: str) -> list[str]:
    """Return likely API hosts for a Salesforce UI/API URL, in preference order."""
    parsed = urlparse(instance_url if '://' in instance_url else f'https://{instance_url}')
    scheme = parsed.scheme or 'https'
    hostname = (parsed.hostname or '').lower()
    candidates = []

    def add(host: str):
        url = f'{scheme}://{host}'.rstrip('/')
        if url not in candidates:
            candidates.append(url)

    if hostname.endswith('.trailblaze.lightning.force.com'):
        add(hostname.replace('.trailblaze.lightning.force.com', '.trailblaze.my.salesforce.com'))
        add(hostname.replace('.trailblaze.lightning.force.com', '.my.salesforce.com'))
    elif hostname.endswith('.lightning.force.com'):
        add(hostname.replace('.lightning.force.com', '.my.salesforce.com'))

    add(hostname)
    return candidates

# ─── SESSION VALIDATION ───────────────────────────────────────────────────────

async def validate_session(session_id: str, instance_url: str) -> bool:
    """
    Validates the Salesforce session by calling the identity endpoint.
    Returns True if valid, False if invalid.
    """
    primary_url = f'{get_api_instance_url(instance_url)}/services/oauth2/userinfo'
    fallback_urls = _get_auth_fallback_urls(instance_url)

    async with httpx.AsyncClient(timeout=10.0, follow_redirects=False) as client:
        try:
            response = await client.get(primary_url, headers=get_headers(session_id))

            if response.status_code == 200:
                data = response.json()
                logger.info(f'Session valid for user: {data.get("preferred_username", "unknown")}')
                return True

            if response.status_code in (301, 302, 303, 307, 308):
                logger.warning(
                    f'Session validation redirected from identity endpoint ({primary_url}): {response.status_code}. '
                    'Trying auth host fallback.'
                )
            elif response.status_code == 401:
                logger.warning('Session invalid — 401 from Salesforce identity endpoint.')
                return False
            else:
                logger.warning(f'Unexpected status from identity endpoint ({primary_url}): {response.status_code}')

            for fallback_url in fallback_urls:
                if fallback_url == primary_url:
                    continue
                response = await client.get(fallback_url, headers=get_headers(session_id))
                if response.status_code == 200:
                    data = response.json()
                    logger.info(f'Session valid via fallback auth endpoint: {fallback_url}')
                    return True
                if response.status_code == 401:
                    logger.warning(f'Session invalid via fallback auth endpoint: {fallback_url}')
                    return False
                logger.warning(f'Fallback auth endpoint {fallback_url} returned {response.status_code}.')

            return False

        except httpx.TimeoutException:
            logger.error('Timeout validating Salesforce session.')
            raise Exception('Salesforce connection timed out.')

        except httpx.RequestError as e:
            logger.error(f'Request error validating session: {e}')
            raise Exception(f'Could not reach Salesforce org: {str(e)}')


def _get_auth_fallback_urls(instance_url: str) -> list[str]:
    """Return auth host URLs to try when instance validation fails."""
    urls = [
        f'{api_url}/services/oauth2/userinfo'
        for api_url in get_api_instance_url_candidates(instance_url)
    ]
    if _sandbox_url_heuristic(instance_url):
        urls.append('https://test.salesforce.com/services/oauth2/userinfo')
    urls.append('https://login.salesforce.com/services/oauth2/userinfo')
    return urls

# ─── SANDBOX DETECTION ────────────────────────────────────────────────────────

async def is_sandbox(session_id: str, instance_url: str) -> bool:
    # instance_url at this point should already be my.salesforce.com
    base_url = get_base_url(instance_url)
    query = 'SELECT+Id,+IsSandbox,+Name+FROM+Organization+LIMIT+1'
    url = f'{base_url}/query?q={query}'

    async with httpx.AsyncClient(timeout=10.0) as client:
        try:
            response = await client.get(url, headers=get_headers(session_id))
            if response.status_code == 200:
                records = response.json().get('records', [])
                if records:
                    result = records[0].get('IsSandbox', False)
                    logger.info(f'Org: {records[0].get("Name")} | IsSandbox: {result}')
                    return result
        except Exception as e:
            logger.warning(f'Sandbox check failed: {e}')

    return _sandbox_url_heuristic(instance_url)

def _get_data_fallback_urls(instance_url: str, query: str) -> list[str]:
    """Return fallback data endpoints to determine sandbox status."""
    urls = [
        f'{api_url}/services/data/v59.0/query?q={query}'
        for api_url in get_api_instance_url_candidates(instance_url)
    ]
    if _sandbox_url_heuristic(instance_url):
        urls.append(f'https://test.salesforce.com/services/data/v59.0/query?q={query}')
    urls.append(f'https://login.salesforce.com/services/data/v59.0/query?q={query}')
    return urls

# ─── URL HEURISTIC FALLBACK ───────────────────────────────────────────────────

def _sandbox_url_heuristic(instance_url: str) -> bool:
    """
    Fallback sandbox detection based on URL patterns.
    Sandboxes typically contain '--' in the subdomain or 'sandbox' in the hostname.
    This is NOT definitive — only used when API check fails.
    """
    import re
    hostname = instance_url.lower()
    sandbox_patterns = [
        r'--\w+\.sandbox\.my\.salesforce\.com',
        r'--\w+\.lightning\.force\.com',
        r'--\w+\.my\.salesforce\.com',
        r'\.sandbox\.',
        r'scratch\.',
    ]
    is_sb = any(re.search(p, hostname) for p in sandbox_patterns)
    logger.info(f'URL heuristic sandbox result for {hostname}: {is_sb}')
    return is_sb

# ─── Salesforce (AI)nspector — salesforce/oauth.py ───────────────────────────
# OAuth username-password flow for getting a proper REST API access token.
# This bypasses the Lightning session limitation.

import logging
import httpx
import base64
import hashlib
import secrets
import time
from urllib.parse import urlencode

logger = logging.getLogger(__name__)

_OAUTH_STATES: dict[str, dict] = {}
_OAUTH_STATE_TTL_SECONDS = 600


def _oauth_login_url(is_sandbox: bool) -> str:
    return 'https://test.salesforce.com' if is_sandbox else 'https://login.salesforce.com'


def _cleanup_oauth_states() -> None:
    now = time.time()
    expired_states = [
        state
        for state, session in _OAUTH_STATES.items()
        if now - session.get('created_at', now) > _OAUTH_STATE_TTL_SECONDS
    ]
    for state in expired_states:
        _OAUTH_STATES.pop(state, None)


def _pkce_challenge(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode('ascii')).digest()
    return base64.urlsafe_b64encode(digest).decode('ascii').rstrip('=')


def create_authorization_url(
    consumer_key: str,
    consumer_secret: str = '',
    is_sandbox: bool = False,
    redirect_uri: str = 'http://localhost:8000/oauth/callback',
) -> dict:
    _cleanup_oauth_states()

    state = secrets.token_urlsafe(32)
    code_verifier = secrets.token_urlsafe(64)
    login_url = _oauth_login_url(is_sandbox)

    _OAUTH_STATES[state] = {
        'consumer_key': consumer_key.strip(),
        'consumer_secret': consumer_secret.strip(),
        'code_verifier': code_verifier,
        'redirect_uri': redirect_uri,
        'login_url': login_url,
        'created_at': time.time(),
        'status': 'pending',
    }

    params = {
        'response_type': 'code',
        'client_id': consumer_key.strip(),
        'redirect_uri': redirect_uri,
        'scope': 'api refresh_token',
        'state': state,
        'code_challenge': _pkce_challenge(code_verifier),
        'code_challenge_method': 'S256',
    }
    return {
        'authorization_url': f'{login_url}/services/oauth2/authorize?{urlencode(params)}',
        'state': state,
    }


async def complete_authorization_code_flow(state: str, code: str) -> dict:
    _cleanup_oauth_states()
    session = _OAUTH_STATES.get(state)
    if not session:
        raise Exception('OAuth session expired or was not found. Start Salesforce login again.')

    payload = {
        'grant_type': 'authorization_code',
        'client_id': session['consumer_key'],
        'redirect_uri': session['redirect_uri'],
        'code': code,
        'code_verifier': session['code_verifier'],
    }
    if session.get('consumer_secret'):
        payload['client_secret'] = session['consumer_secret']

    async with httpx.AsyncClient(timeout=15.0) as client:
        response = await client.post(
            f'{session["login_url"]}/services/oauth2/token',
            data=payload,
        )

    try:
        data = response.json()
    except Exception:
        data = {'error': 'unknown_error', 'error_description': response.text}

    if response.status_code != 200:
        error = data.get('error', 'unknown_error')
        error_desc = data.get('error_description', 'Unknown error')
        session['status'] = 'error'
        session['error'] = error_desc
        logger.error(f'Authorization-code OAuth failed: {error} - {error_desc}')
        raise Exception(error_desc)

    session['status'] = 'complete'
    session['token'] = data
    logger.info(f'Authorization-code OAuth success at {data.get("instance_url")}')
    return data


def get_authorization_status(state: str) -> dict:
    _cleanup_oauth_states()
    session = _OAUTH_STATES.get(state)
    if not session:
        return {'status': 'expired'}

    if session.get('status') == 'complete':
        token = session.get('token', {})
        _OAUTH_STATES.pop(state, None)
        return {
            'status': 'complete',
            'access_token': token.get('access_token'),
            'refresh_token': token.get('refresh_token'),
            'instance_url': token.get('instance_url'),
        }

    if session.get('status') == 'error':
        error = session.get('error', 'OAuth failed.')
        _OAUTH_STATES.pop(state, None)
        return {'status': 'error', 'error': error}

    return {'status': 'pending'}


def fail_authorization(state: str, error: str) -> None:
    session = _OAUTH_STATES.get(state)
    if session:
        session['status'] = 'error'
        session['error'] = error


def _password_grant_payload(
    consumer_key: str,
    consumer_secret: str,
    username: str,
    password: str,
    security_token: str,
) -> dict:
    return {
        'grant_type':    'password',
        'client_id':     consumer_key.strip(),
        'client_secret': consumer_secret.strip(),
        'username':      username.strip(),
        'password':      password + security_token.strip(),
    }


def _oauth_error_message(error: str, error_desc: str) -> str:
    if error == 'invalid_grant' and 'authentication failure' in error_desc.lower():
        return (
            'Salesforce rejected the username-password login. Check that the '
            'password is correct, the security token is appended/entered when '
            'your IP is not trusted, the Connected App allows the '
            'username-password OAuth flow, the user has API Enabled, and the '
            'user is not required to complete MFA for this flow.'
        )
    return error_desc


async def get_token_username_password(
    consumer_key: str,
    consumer_secret: str,
    username: str,
    password: str,
    security_token: str = '',
    is_sandbox: bool = False,
) -> dict:
    """
    Exchange username + password + security token for a REST API access token
    using OAuth 2.0 username-password flow.

    Returns dict with: access_token, instance_url, token_type, id
    """
    login_urls = (
        ['https://test.salesforce.com', 'https://login.salesforce.com']
        if is_sandbox
        else ['https://login.salesforce.com', 'https://test.salesforce.com']
    )
    login_url = login_urls[0]
    fallback_login_url = login_urls[1]

    async with httpx.AsyncClient(timeout=15.0) as client:
        response = await client.post(
            f'{login_url}/services/oauth2/token',
            data=_password_grant_payload(
                consumer_key,
                consumer_secret,
                username,
                password,
                security_token,
            )
        )

        data = response.json()

        if response.status_code != 200:
            error     = data.get('error', 'unknown_error')
            error_desc = data.get('error_description', 'Unknown error')
            if error == 'invalid_grant' and 'authentication failure' in error_desc.lower():
                retry_response = await client.post(
                    f'{fallback_login_url}/services/oauth2/token',
                    data=_password_grant_payload(
                        consumer_key,
                        consumer_secret,
                        username,
                        password,
                        security_token,
                    )
                )
                retry_data = retry_response.json()
                if retry_response.status_code == 200:
                    logger.info(f'OAuth success for {username} via {fallback_login_url} at {retry_data.get("instance_url")}')
                    return retry_data
                retry_error = retry_data.get('error', 'unknown_error')
                retry_desc = retry_data.get('error_description', 'Unknown error')
                logger.error(f'OAuth failed via {fallback_login_url}: {retry_error} - {retry_desc}')
            logger.error(f'OAuth failed: {error} — {error_desc}')
            raise Exception(_oauth_error_message(error, error_desc))

        logger.info(f'OAuth success for {username} at {data.get("instance_url")}')
        return data
