# ─── Salesforce (AI)nspector — agent/tools/audit_tool.py ─────────────────────
# LangChain tools for audit trail analysis and org change history.
# Covers: setup audit trail, login history, field history,
# permission changes, and user activity summaries.

import json
import logging
from langchain_core.tools import tool
from salesforce.client import SalesforceClient

logger = logging.getLogger(__name__)


def make_audit_tools(session_id: str, instance_url: str):
    """
    Factory — returns audit tools bound to this request's session.
    Audit tools are read-only — no production block needed.
    """

    client = SalesforceClient(session_id, instance_url)

    # ─────────────────────────────────────────────────────────────────────────

    @tool
    async def get_setup_audit_trail(
        days_ago: int = 7,
        username: str = None,
        action_filter: str = None,
        limit: int = 100,
    ) -> str:
        """
        Query the Salesforce SetupAuditTrail to see what configuration
        changes were made in the org — field changes, profile edits,
        permission updates, user modifications, etc.

        Use this for:
        - "Who changed the sharing settings last week?"
        - "What did admin@org.com change this month?"
        - "Show me all profile changes in the last 30 days"
        - "What was modified before this bug appeared?"

        Args:
            days_ago: How many days back to search. Default 7. Max 180.
            username: Filter by the username who made the change.
                      Example: 'admin@myorg.com.sandbox'
            action_filter: Keyword to filter by action type.
                           Examples: 'Profile', 'PermissionSet', 'User',
                                     'CustomField', 'Flow', 'Role', 'Queue'
            limit: Max records to return. Default 100.

        Returns:
            JSON list of audit trail entries with action, section,
            who made the change, and when.
        """
        logger.info(f'Fetching setup audit trail — last {days_ago} days')

        conditions = [f'CreatedDate = LAST_N_DAYS:{min(days_ago, 180)}']

        if username:
            conditions.append(f"CreatedByContext LIKE '%{username}%'")

        if action_filter:
            conditions.append(f"(Action LIKE '%{action_filter}%' OR Section LIKE '%{action_filter}%')")

        where = 'WHERE ' + ' AND '.join(conditions)

        soql = (
            f"SELECT Action, Display, Section, CreatedDate, CreatedByContext "
            f"FROM SetupAuditTrail {where} "
            f"ORDER BY CreatedDate DESC LIMIT {limit}"
        )

        try:
            records = await client.query_all(soql)
            clean   = [{k: v for k, v in r.items() if k != 'attributes'} for r in records]

            # Group by section for easier reading
            grouped = {}
            for r in clean:
                section = r.get('Section', 'Unknown')
                grouped.setdefault(section, []).append(r)

            return json.dumps({
                'total_returned': len(clean),
                'days_searched':  days_ago,
                'filters_applied': {
                    'username':      username,
                    'action_filter': action_filter,
                },
                'grouped_by_section': grouped,
                'all_records':        clean,
            }, indent=2, default=str)

        except Exception as e:
            logger.error(f'Get audit trail failed: {e}')
            return json.dumps({'error': str(e)})

    # ─────────────────────────────────────────────────────────────────────────

    @tool
    async def get_login_history(
        days_ago: int = 7,
        username: str = None,
        status_filter: str = None,
        limit: int = 100,
    ) -> str:
        """
        Query Salesforce LoginHistory to review user login activity,
        failed login attempts, and login patterns.

        Use this for:
        - "Has this user logged in recently?"
        - "Show me all failed login attempts this week"
        - "Which users haven't logged in for the last 30 days?"
        - Security audit — unusual login times or locations

        Args:
            days_ago: How many days back to search. Default 7.
            username: Filter by specific username.
            status_filter: Filter by login status.
                           Examples: 'Success', 'Failed', 'Failed: Wrong Password'
            limit: Max records to return. Default 100.

        Returns:
            JSON list of login events with user, time, status, IP, and login type.
        """
        logger.info(f'Fetching login history — last {days_ago} days')

        conditions = [f'LoginTime = LAST_N_DAYS:{days_ago}']

        if username:
            conditions.append(f"Username LIKE '%{username}%'")

        if status_filter:
            conditions.append(f"Status LIKE '%{status_filter}%'")

        where = 'WHERE ' + ' AND '.join(conditions)

        soql = (
            f"SELECT Username, LoginTime, Status, SourceIp, LoginType, "
            f"Browser, Platform, Application "
            f"FROM LoginHistory {where} "
            f"ORDER BY LoginTime DESC LIMIT {limit}"
        )

        try:
            records = await client.query_all(soql)
            clean   = [{k: v for k, v in r.items() if k != 'attributes'} for r in records]

            # Summary stats
            success = sum(1 for r in clean if r.get('Status') == 'Success')
            failed  = len(clean) - success

            return json.dumps({
                'total_returned': len(clean),
                'summary': {
                    'successful_logins': success,
                    'failed_logins':     failed,
                },
                'records': clean,
            }, indent=2, default=str)

        except Exception as e:
            logger.error(f'Get login history failed: {e}')
            return json.dumps({'error': str(e)})

    # ─────────────────────────────────────────────────────────────────────────

    @tool
    async def get_field_history(
        sobject: str,
        record_id: str,
        field_names: list[str] = None,
    ) -> str:
        """
        Get the change history for fields on a specific Salesforce record.
        Requires field history tracking to be enabled on the object.

        Use this for:
        - "Who changed the Stage on this Opportunity?"
        - "When was the Priority field last updated on this Case?"
        - "Show me all changes to this Account record"

        Args:
            sobject: API name of the SObject. Example: Opportunity, Case, Account
            record_id: The record ID to fetch history for.
            field_names: Optional list of specific fields to filter history.
                         If not provided, returns all tracked field changes.
                         Example: ['StageName', 'Amount', 'OwnerId']

        Returns:
            JSON list of field history entries — field name, old value,
            new value, who changed it, and when.
        """
        logger.info(f'Fetching field history for {sobject} {record_id}')

        history_object = f'{sobject}History'

        field_filter = ''
        if field_names:
            quoted = ', '.join(f"'{f}'" for f in field_names)
            field_filter = f"AND Field IN ({quoted})"

        soql = (
            f"SELECT Field, OldValue, NewValue, CreatedDate, CreatedBy.Name "
            f"FROM {history_object} "
            f"WHERE ParentId = '{record_id}' {field_filter} "
            f"ORDER BY CreatedDate DESC LIMIT 200"
        )

        try:
            records = await client.query_all(soql)
            clean   = []
            for r in records:
                clean.append({
                    'field':        r.get('Field'),
                    'old_value':    r.get('OldValue'),
                    'new_value':    r.get('NewValue'),
                    'changed_by':   r.get('CreatedBy', {}).get('Name') if r.get('CreatedBy') else None,
                    'changed_date': r.get('CreatedDate'),
                })

            return json.dumps({
                'sobject':        sobject,
                'record_id':      record_id,
                'total_changes':  len(clean),
                'history':        clean,
            }, indent=2, default=str)

        except Exception as e:
            logger.error(f'Get field history failed: {e}')
            return json.dumps({
                'error': str(e),
                'hint':  f'Ensure field history tracking is enabled for {sobject}. '
                         f'If {history_object} does not exist, history tracking is not enabled.'
            })

    # ─────────────────────────────────────────────────────────────────────────

    @tool
    async def get_permission_changes(days_ago: int = 30) -> str:
        """
        Get a focused audit trail of all permission-related changes in the org —
        profile edits, permission set changes, role assignments, and user access changes.

        Use this for:
        - Security audits
        - "What permission changes happened before this user got access?"
        - Compliance reviews

        Args:
            days_ago: How many days back to search. Default 30.

        Returns:
            JSON grouped by change type — profiles, permission sets, roles, users.
        """
        logger.info(f'Fetching permission changes — last {days_ago} days')

        soql = (
            f"SELECT Action, Display, Section, CreatedDate, CreatedByContext "
            f"FROM SetupAuditTrail "
            f"WHERE CreatedDate = LAST_N_DAYS:{min(days_ago, 180)} "
            f"AND (Section LIKE '%Profile%' OR Section LIKE '%PermissionSet%' "
            f"OR Section LIKE '%Role%' OR Section LIKE '%User%') "
            f"ORDER BY CreatedDate DESC LIMIT 200"
        )

        try:
            records = await client.query_all(soql)
            clean   = [{k: v for k, v in r.items() if k != 'attributes'} for r in records]

            grouped = {
                'profiles':        [r for r in clean if 'Profile' in r.get('Section', '')],
                'permission_sets': [r for r in clean if 'PermissionSet' in r.get('Section', '')],
                'roles':           [r for r in clean if 'Role' in r.get('Section', '')],
                'users':           [r for r in clean if 'User' in r.get('Section', '')],
                'other':           [r for r in clean if not any(
                                        k in r.get('Section', '')
                                        for k in ['Profile', 'PermissionSet', 'Role', 'User']
                                    )],
            }

            return json.dumps({
                'days_searched':  days_ago,
                'total_changes':  len(clean),
                'summary': {k: len(v) for k, v in grouped.items()},
                'changes':        grouped,
            }, indent=2, default=str)

        except Exception as e:
            logger.error(f'Get permission changes failed: {e}')
            return json.dumps({'error': str(e)})

    # ─────────────────────────────────────────────────────────────────────────

    @tool
    async def get_user_activity_summary(username: str, days_ago: int = 30) -> str:
        """
        Get a full activity summary for a specific user — what they changed
        in Setup, their login history, and records they created or modified.

        Use this for:
        - User offboarding review — "What did this user do before leaving?"
        - Access audits — "What has this admin been changing?"
        - Compliance — full activity trail for a specific user

        Args:
            username: Salesforce username to audit.
                      Example: 'john.doe@myorg.com.sandbox'
            days_ago: How many days back to search. Default 30.

        Returns:
            JSON with setup changes, login history, and record activity summary.
        """
        logger.info(f'Getting activity summary for {username} — last {days_ago} days')

        results = {}

        # Setup changes by this user
        try:
            audit_soql = (
                f"SELECT Action, Display, Section, CreatedDate "
                f"FROM SetupAuditTrail "
                f"WHERE CreatedDate = LAST_N_DAYS:{min(days_ago, 180)} "
                f"AND CreatedByContext LIKE '%{username}%' "
                f"ORDER BY CreatedDate DESC LIMIT 100"
            )
            audit_records = await client.query_all(audit_soql)
            results['setup_changes'] = {
                'total': len(audit_records),
                'records': [{k: v for k, v in r.items() if k != 'attributes'}
                            for r in audit_records],
            }
        except Exception as e:
            results['setup_changes'] = {'error': str(e)}

        # Login history
        try:
            login_soql = (
                f"SELECT LoginTime, Status, SourceIp, LoginType, Browser "
                f"FROM LoginHistory "
                f"WHERE Username = '{username}' "
                f"AND LoginTime = LAST_N_DAYS:{days_ago} "
                f"ORDER BY LoginTime DESC LIMIT 50"
            )
            login_records = await client.query_all(login_soql)
            success_logins = sum(1 for r in login_records if r.get('Status') == 'Success')
            results['login_history'] = {
                'total_logins':      len(login_records),
                'successful_logins': success_logins,
                'failed_logins':     len(login_records) - success_logins,
                'records': [{k: v for k, v in r.items() if k != 'attributes'}
                            for r in login_records],
            }
        except Exception as e:
            results['login_history'] = {'error': str(e)}

        # Get user ID for record activity
        try:
            user_result = await client.query(
                f"SELECT Id, Name, IsActive, LastLoginDate "
                f"FROM User WHERE Username = '{username}' LIMIT 1"
            )
            users = user_result.get('records', [])
            if users:
                user = users[0]
                results['user_info'] = {
                    'id':             user.get('Id'),
                    'name':           user.get('Name'),
                    'is_active':      user.get('IsActive'),
                    'last_login_date': user.get('LastLoginDate'),
                }
            else:
                results['user_info'] = {'error': f'User not found: {username}'}
        except Exception as e:
            results['user_info'] = {'error': str(e)}

        return json.dumps({
            'username':    username,
            'days_audited': days_ago,
            'summary':     results,
        }, indent=2, default=str)

    # ─────────────────────────────────────────────────────────────────────────

    return [
        get_setup_audit_trail,
        get_login_history,
        get_field_history,
        get_permission_changes,
        get_user_activity_summary,
    ]