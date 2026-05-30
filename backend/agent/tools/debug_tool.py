# ─── Salesforce (AI)nspector — agent/tools/debug_tool.py ─────────────────────
# LangChain tools for debug log analysis and flow error diagnosis.
# Covers: fetch and analyse Apex debug logs, flow execution errors,
# and pattern analysis across multiple logs.

import json
import logging
import re
from langchain_core.tools import tool
from salesforce.client import SalesforceClient

logger = logging.getLogger(__name__)

# Lines to filter out when cleaning debug logs — pure noise
NOISE_PATTERNS = [
    r'^EXECUTION_STARTED',
    r'^EXECUTION_FINISHED',
    r'^CODE_UNIT_STARTED',
    r'^CODE_UNIT_FINISHED',
    r'^ENTERING_MANAGED_PKG',
    r'^STATEMENT_EXECUTE',
    r'^VARIABLE_SCOPE_BEGIN',
    r'^VARIABLE_ASSIGNMENT',
    r'^SYSTEM_METHOD_ENTRY',
    r'^SYSTEM_METHOD_EXIT',
    r'^HEAP_ALLOCATE',
    r'^LIMIT_USAGE_FOR_NS',
    r'^\d+\.\d+\s+\(\d+\)',
]

NOISE_REGEX = re.compile('|'.join(NOISE_PATTERNS))

# Lines that are always meaningful
SIGNAL_PATTERNS = [
    r'EXCEPTION_THROWN',
    r'FATAL_ERROR',
    r'ERROR',
    r'SOQL_EXECUTE_BEGIN',
    r'SOQL_EXECUTE_END',
    r'DML_BEGIN',
    r'DML_END',
    r'VALIDATION_FAIL',
    r'VALIDATION_RULE',
    r'FLOW_ELEMENT_ERROR',
    r'FLOW_FAULT',
    r'TRIGGER_',
    r'USER_DEBUG',
    r'CALLOUT_',
    r'WF_',
]

SIGNAL_REGEX = re.compile('|'.join(SIGNAL_PATTERNS))


def make_debug_tools(session_id: str, instance_url: str):
    """
    Factory — returns debug tools bound to this request's session.
    Debug tools are read-only — no production block needed.
    """

    client = SalesforceClient(session_id, instance_url)

    # ─────────────────────────────────────────────────────────────────────────

    @tool
    async def get_apex_logs_for_user(
        username: str,
        limit: int = 5,
        operation_filter: str = None,
    ) -> str:
        """
        Fetch the most recent Apex debug logs for a specific Salesforce user.
        Returns log metadata — use read_apex_log_body to get the actual log content.

        Use this first to find the relevant log ID before reading its content.

        Args:
            username: Salesforce username to fetch logs for.
                      Example: 'john.doe@myorg.com.sandbox'
            limit: Number of recent logs to return. Default 5, max 20.
            operation_filter: Optional filter by operation type.
                              Examples: 'Trigger', 'Flow', 'Batch', 'API'

        Returns:
            JSON list of log entries with ID, operation, status, size, and timestamp.
        """
        logger.info(f'Fetching logs for user: {username}')

        conditions = [f"LogUser.Username = '{username}'"]

        if operation_filter:
            conditions.append(f"Operation LIKE '%{operation_filter}%'")

        where = 'WHERE ' + ' AND '.join(conditions)

        soql = (
            f"SELECT Id, LogUser.Username, Operation, Application, "
            f"Status, LogLength, LastModifiedDate, Request, Location "
            f"FROM ApexLog {where} "
            f"ORDER BY LastModifiedDate DESC LIMIT {min(limit, 20)}"
        )

        try:
            result = await client.tooling_query(soql)
            records = result.get('records', [])
            clean   = []

            for r in records:
                clean.append({
                    'log_id':      r.get('Id'),
                    'username':    r.get('LogUser', {}).get('Username') if r.get('LogUser') else None,
                    'operation':   r.get('Operation'),
                    'application': r.get('Application'),
                    'status':      r.get('Status'),
                    'size_bytes':  r.get('LogLength'),
                    'timestamp':   r.get('LastModifiedDate'),
                    'request':     r.get('Request'),
                    'location':    r.get('Location'),
                })

            return json.dumps({
                'username':      username,
                'total_found':   len(clean),
                'logs':          clean,
                'next_step':     'Use read_apex_log_body with a log_id to read the full log content.',
            }, indent=2, default=str)

        except Exception as e:
            logger.error(f'Fetch logs failed: {e}')
            return json.dumps({'error': str(e)})

    # ─────────────────────────────────────────────────────────────────────────

    @tool
    async def read_apex_log_body(log_id: str, filter_noise: bool = True) -> str:
        """
        Read the full content of an Apex debug log and return a cleaned,
        analysed version with errors, exceptions, and key events highlighted.

        Automatically filters out noise lines (variable assignments, heap allocations,
        system method entries) and surfaces only meaningful events.

        Args:
            log_id: The Apex log ID from get_apex_logs_for_user.
                    Example: '07LXX000000abcdef'
            filter_noise: If True (default), returns only meaningful signal lines.
                          If False, returns the raw full log (may be very large).

        Returns:
            JSON with extracted errors, exceptions, DML operations,
            SOQL queries, validation failures, and key events.
        """
        logger.info(f'Reading log body: {log_id}')

        try:
            raw_log = await client.get_apex_log(log_id)

            if not filter_noise:
                return json.dumps({
                    'log_id':    log_id,
                    'raw_log':   raw_log[:50000],  # cap at 50k chars
                    'truncated': len(raw_log) > 50000,
                })

            # Parse and filter the log
            analysis = _analyse_log(raw_log)

            return json.dumps({
                'log_id':           log_id,
                'analysis':         analysis,
                'next_step':        'Review the errors and exceptions above to identify the root cause.',
            }, indent=2, default=str)

        except Exception as e:
            logger.error(f'Read log body failed: {e}')
            return json.dumps({'error': str(e)})

    # ─────────────────────────────────────────────────────────────────────────

    @tool
    async def analyse_logs_for_pattern(
        username: str,
        days_ago: int = 3,
        max_logs: int = 5,
    ) -> str:
        """
        Fetch and analyse multiple recent logs for a user to identify
        recurring errors or patterns. Useful when a bug happens repeatedly
        and you want to confirm it's consistent.

        Args:
            username: Salesforce username to analyse logs for.
            days_ago: How many days of logs to search. Default 3.
            max_logs: Maximum number of logs to analyse. Default 5.

        Returns:
            JSON with aggregated error patterns across all logs — showing
            which errors recur most frequently and in what context.
        """
        logger.info(f'Analysing log patterns for {username} — last {days_ago} days')

        # Fetch log list
        soql = (
            f"SELECT Id, Operation, Status, LastModifiedDate, LogLength "
            f"FROM ApexLog "
            f"WHERE LogUser.Username = '{username}' "
            f"AND LastModifiedDate = LAST_N_DAYS:{days_ago} "
            f"ORDER BY LastModifiedDate DESC LIMIT {max_logs}"
        )

        try:
            result    = await client.tooling_query(soql)
            log_metas = result.get('records', [])

            if not log_metas:
                return json.dumps({
                    'message': f'No logs found for {username} in the last {days_ago} days.'
                })

            all_errors      = []
            all_exceptions  = []
            all_validations = []
            log_summaries   = []

            for meta in log_metas:
                log_id = meta.get('Id')
                try:
                    raw = await client.get_apex_log(log_id)
                    analysis = _analyse_log(raw)

                    log_summaries.append({
                        'log_id':    log_id,
                        'operation': meta.get('Operation'),
                        'timestamp': meta.get('LastModifiedDate'),
                        'errors':    len(analysis.get('errors', [])),
                        'exceptions': len(analysis.get('exceptions', [])),
                    })

                    all_errors.extend(analysis.get('errors', []))
                    all_exceptions.extend(analysis.get('exceptions', []))
                    all_validations.extend(analysis.get('validation_failures', []))

                except Exception as log_err:
                    log_summaries.append({
                        'log_id': log_id,
                        'error':  str(log_err),
                    })

            # Find recurring patterns
            error_counts = {}
            for err in all_errors + all_exceptions:
                key = err.get('message', '')[:120]  # normalise by first 120 chars
                error_counts[key] = error_counts.get(key, 0) + 1

            recurring = [
                {'pattern': k, 'occurrences': v}
                for k, v in sorted(error_counts.items(), key=lambda x: -x[1])
            ]

            return json.dumps({
                'username':       username,
                'logs_analysed':  len(log_summaries),
                'log_summaries':  log_summaries,
                'total_errors':   len(all_errors),
                'total_exceptions': len(all_exceptions),
                'validation_failures': all_validations,
                'recurring_patterns': recurring,
                'conclusion': (
                    f'Found {len(recurring)} distinct error pattern(s) across {len(log_summaries)} logs. '
                    f'Most frequent: {recurring[0]["pattern"] if recurring else "None"}'
                ),
            }, indent=2, default=str)

        except Exception as e:
            logger.error(f'Analyse log patterns failed: {e}')
            return json.dumps({'error': str(e)})

    # ─────────────────────────────────────────────────────────────────────────

    @tool
    async def get_flow_execution_errors(
        flow_name: str = None,
        days_ago: int = 7,
        limit: int = 50,
    ) -> str:
        """
        Query FlowExecutionErrorEvent to find Flow runtime errors.
        Use this when a user reports a Flow is failing — this gives you
        the exact element, error message, and context without needing to
        manually read debug logs.

        Args:
            flow_name: Optional — API name of the specific flow to filter by.
                       If not provided, returns errors for all flows.
                       Example: 'Order_Post_Processing', 'Lead_Conversion_Flow'
            days_ago: How many days back to look. Default 7.
            limit: Max records to return. Default 50.

        Returns:
            JSON list of flow errors with flow name, failing element,
            error message, and when it occurred.
        """
        logger.info(f'Fetching flow execution errors — flow: {flow_name}, last {days_ago} days')

        conditions = [f'CreatedDate = LAST_N_DAYS:{days_ago}']
        if flow_name:
            conditions.append(f"FlowApiName = '{flow_name}'")

        where = 'WHERE ' + ' AND '.join(conditions)

        soql = (
            f"SELECT FlowApiName, FlowVersionNumber, ElementApiName, "
            f"ErrorMessage, InterviewGuid, CreatedDate "
            f"FROM FlowExecutionErrorEvent {where} "
            f"ORDER BY CreatedDate DESC LIMIT {limit}"
        )

        try:
            result  = await client.query_all(soql)
            records = [{k: v for k, v in r.items() if k != 'attributes'} for r in result]

            if not records:
                return json.dumps({
                    'message': (
                        f'No flow execution errors found'
                        f'{" for " + flow_name if flow_name else ""} '
                        f'in the last {days_ago} days.'
                    )
                })

            # Group by flow name
            grouped = {}
            for r in records:
                fname = r.get('FlowApiName', 'Unknown')
                grouped.setdefault(fname, []).append(r)

            # Identify most common failing element per flow
            insights = []
            for fname, errors in grouped.items():
                element_counts = {}
                for e in errors:
                    el = e.get('ElementApiName', 'Unknown')
                    element_counts[el] = element_counts.get(el, 0) + 1
                most_common = max(element_counts, key=element_counts.get)
                insights.append({
                    'flow_name':         fname,
                    'total_errors':      len(errors),
                    'most_failing_element': most_common,
                    'sample_error':      errors[0].get('ErrorMessage', ''),
                })

            return json.dumps({
                'total_errors':    len(records),
                'flows_affected':  len(grouped),
                'insights':        insights,
                'all_errors':      records,
            }, indent=2, default=str)

        except Exception as e:
            logger.error(f'Get flow errors failed: {e}')
            return json.dumps({
                'error': str(e),
                'hint':  'FlowExecutionErrorEvent may require Platform Event access. '
                         'Alternatively check debug logs for FLOW_FAULT entries.',
            })

    # ─────────────────────────────────────────────────────────────────────────

    @tool
    async def get_flow_metadata(flow_api_name: str) -> str:
        """
        Read the metadata of an existing Flow — its elements, decisions,
        assignments, and record operations.

        Use this during Flow debugging — after finding the failing element
        via get_flow_execution_errors, read the flow metadata to understand
        what that element was supposed to do.

        Args:
            flow_api_name: API name of the flow. Example: 'Order_Post_Processing'

        Returns:
            JSON with flow version, status, and a simplified breakdown of
            its elements — decisions, assignments, record lookups, record updates.
        """
        logger.info(f'Reading flow metadata: {flow_api_name}')

        try:
            result = await client.tooling_query(
                f"SELECT Id, ApiName, Status, VersionNumber, Description, "
                f"ProcessType, TriggerType, Metadata "
                f"FROM Flow WHERE ApiName = '{flow_api_name}' "
                f"AND Status = 'Active' LIMIT 1"
            )
            records = result.get('records', [])

            if not records:
                # Try getting inactive version
                result = await client.tooling_query(
                    f"SELECT Id, ApiName, Status, VersionNumber, Description, "
                    f"ProcessType, TriggerType "
                    f"FROM Flow WHERE ApiName = '{flow_api_name}' "
                    f"ORDER BY VersionNumber DESC LIMIT 1"
                )
                records = result.get('records', [])

            if not records:
                return json.dumps({'error': f'Flow not found: {flow_api_name}'})

            flow = records[0]
            metadata = flow.get('Metadata', {}) or {}

            # Extract meaningful elements
            elements_summary = _summarise_flow_elements(metadata)

            return json.dumps({
                'flow_api_name':  flow.get('ApiName'),
                'status':         flow.get('Status'),
                'version':        flow.get('VersionNumber'),
                'process_type':   flow.get('ProcessType'),
                'trigger_type':   flow.get('TriggerType'),
                'description':    flow.get('Description'),
                'elements':       elements_summary,
            }, indent=2, default=str)

        except Exception as e:
            logger.error(f'Get flow metadata failed: {e}')
            return json.dumps({'error': str(e)})

    # ─────────────────────────────────────────────────────────────────────────

    return [
        get_apex_logs_for_user,
        read_apex_log_body,
        analyse_logs_for_pattern,
        get_flow_execution_errors,
        get_flow_metadata,
    ]


# ─── LOG ANALYSIS HELPERS ─────────────────────────────────────────────────────

def _analyse_log(raw_log: str) -> dict:
    """
    Parse a raw Apex debug log into structured categories.
    Returns errors, exceptions, DML ops, SOQL queries, validation failures.
    """
    lines      = raw_log.splitlines()
    errors     = []
    exceptions = []
    dml_ops    = []
    soql_ops   = []
    validations= []
    user_debug = []
    triggers   = []

    for line in lines:
        if not line.strip():
            continue

        # Skip pure noise
        if NOISE_REGEX.search(line):
            continue

        upper = line.upper()

        if 'EXCEPTION_THROWN' in upper or 'FATAL_ERROR' in upper:
            exceptions.append(_parse_log_line(line))

        elif 'VALIDATION_FAIL' in upper or 'VALIDATION_RULE' in upper:
            validations.append(_parse_log_line(line))

        elif 'SOQL_EXECUTE_BEGIN' in upper:
            soql_ops.append(_parse_log_line(line))

        elif 'DML_BEGIN' in upper:
            dml_ops.append(_parse_log_line(line))

        elif 'USER_DEBUG' in upper:
            user_debug.append(_parse_log_line(line))

        elif 'TRIGGER_' in upper:
            triggers.append(_parse_log_line(line))

        elif any(e in upper for e in ['ERROR', 'FLOW_FAULT', 'FLOW_ELEMENT_ERROR']):
            errors.append(_parse_log_line(line))

    # Extract the most critical message for a quick summary
    top_error = None
    if exceptions:
        top_error = exceptions[0].get('message', '')
    elif errors:
        top_error = errors[0].get('message', '')

    return {
        'summary': {
            'total_exceptions':     len(exceptions),
            'total_errors':         len(errors),
            'total_soql_queries':   len(soql_ops),
            'total_dml_operations': len(dml_ops),
            'validation_failures':  len(validations),
            'user_debug_statements': len(user_debug),
        },
        'top_error':         top_error,
        'exceptions':        exceptions[:10],    # cap for readability
        'errors':            errors[:10],
        'validation_failures': validations[:10],
        'dml_operations':    dml_ops[:20],
        'soql_queries':      soql_ops[:20],
        'triggers_fired':    triggers[:10],
        'user_debug':        user_debug[:20],
    }


def _parse_log_line(line: str) -> dict:
    """Extract timestamp, event type, and message from a log line."""
    parts = line.split('|', 3)
    if len(parts) >= 3:
        return {
            'timestamp':  parts[0].strip() if parts else '',
            'event_type': parts[1].strip() if len(parts) > 1 else '',
            'message':    '|'.join(parts[2:]).strip() if len(parts) > 2 else line,
        }
    return {'message': line}


def _summarise_flow_elements(metadata: dict) -> dict:
    """Extract a readable summary of flow elements from flow metadata."""
    return {
        'decisions':       [
            {'name': d.get('name'), 'label': d.get('label')}
            for d in metadata.get('decisions', [])
        ],
        'assignments':     [
            {'name': a.get('name'), 'label': a.get('label')}
            for a in metadata.get('assignments', [])
        ],
        'record_lookups':  [
            {'name': r.get('name'), 'object': r.get('object')}
            for r in metadata.get('recordLookups', [])
        ],
        'record_updates':  [
            {'name': r.get('name'), 'object': r.get('object')}
            for r in metadata.get('recordUpdates', [])
        ],
        'record_creates':  [
            {'name': r.get('name'), 'object': r.get('object')}
            for r in metadata.get('recordCreates', [])
        ],
        'subflows':        [
            {'name': s.get('name'), 'flow_name': s.get('flowName')}
            for s in metadata.get('subflows', [])
        ],
        'apex_calls':      [
            {'name': a.get('name'), 'apex_class': a.get('apexClass')}
            for a in metadata.get('actionCalls', [])
            if a.get('actionType') == 'apex'
        ],
    }