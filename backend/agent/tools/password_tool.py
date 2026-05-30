# ─── Salesforce (AI)nspector — agent/tools/password_tool.py ──────────────────
# LangChain tools for password management.
# Covers: single reset, bulk reset from list, bulk reset from CSV.

import csv
import io
import json
import logging
from langchain_core.tools import tool
from salesforce.client import SalesforceClient

logger = logging.getLogger(__name__)


def make_password_tools(session_id: str, instance_url: str, is_production: bool):
    """
    Factory — returns password tools bound to this request's session.
    """

    client = SalesforceClient(session_id, instance_url)

    def _prod_block(operation: str) -> str:
        return json.dumps({
            'error': f'PRODUCTION ORG DETECTED — {operation} is blocked. '
                     f'Write operations are only permitted in sandbox orgs.'
        })

    # ─────────────────────────────────────────────────────────────────────────

    @tool
    async def reset_password_by_username(username: str) -> str:
        """
        Reset the password for a single user by their Salesforce username.
        Triggers a password reset email to the user.

        Args:
            username: The Salesforce username (email format).
                      Example: john.doe@mycompany.com.sandbox

        Returns:
            JSON with success confirmation, user ID, and email address, or error.
        """
        if is_production:
            return _prod_block('reset_password')

        logger.info(f'Resetting password for username: {username}')
        try:
            # Look up user by username
            result = await client.query(
                f"SELECT Id, Name, Email, IsActive FROM User "
                f"WHERE Username = '{username}' LIMIT 1"
            )
            records = result.get('records', [])
            if not records:
                return json.dumps({'error': f'User not found with username: {username}'})

            user = records[0]

            if not user.get('IsActive'):
                return json.dumps({
                    'success': False,
                    'message': f'User {username} is inactive. Cannot reset password for inactive users.',
                })

            await client.reset_password(user['Id'])

            return json.dumps({
                'success':  True,
                'user_id':  user['Id'],
                'username': username,
                'email':    user.get('Email'),
                'message':  f'Password reset email sent to {user.get("Email")}.',
            })

        except Exception as e:
            logger.error(f'Reset password failed: {e}')
            return json.dumps({'error': str(e)})

    # ─────────────────────────────────────────────────────────────────────────

    @tool
    async def reset_password_by_user_id(user_id: str) -> str:
        """
        Reset the password for a single user by their Salesforce User record ID.
        Use this when you already have the user ID from a prior query.

        Args:
            user_id: Salesforce User record ID (18-char).

        Returns:
            JSON with success confirmation or error.
        """
        if is_production:
            return _prod_block('reset_password')

        logger.info(f'Resetting password for user ID: {user_id}')
        try:
            # Verify user exists and is active
            result = await client.query(
                f"SELECT Id, Name, Email, Username, IsActive FROM User "
                f"WHERE Id = '{user_id}' LIMIT 1"
            )
            records = result.get('records', [])
            if not records:
                return json.dumps({'error': f'User not found: {user_id}'})

            user = records[0]

            if not user.get('IsActive'):
                return json.dumps({
                    'success': False,
                    'message': f'User {user.get("Username")} is inactive. '
                               f'Cannot reset password for inactive users.',
                })

            await client.reset_password(user_id)

            return json.dumps({
                'success':  True,
                'user_id':  user_id,
                'username': user.get('Username'),
                'email':    user.get('Email'),
                'message':  f'Password reset email sent to {user.get("Email")}.',
            })

        except Exception as e:
            logger.error(f'Reset password by ID failed: {e}')
            return json.dumps({'error': str(e)})

    # ─────────────────────────────────────────────────────────────────────────

    @tool
    async def bulk_reset_passwords(usernames: list[str]) -> str:
        """
        Reset passwords for multiple users at once given a list of usernames.
        Processes each user individually and returns a summary of successes and failures.

        Use this when the user provides a list of usernames directly in the chat,
        or when you have extracted usernames from a prior query.

        Args:
            usernames: List of Salesforce usernames.
                       Example: ["john@org.com", "jane@org.com", "bob@org.com"]

        Returns:
            JSON summary with succeeded list, failed list, skipped (inactive) list.
        """
        if is_production:
            return _prod_block('bulk_reset_passwords')

        if not usernames:
            return json.dumps({'error': 'No usernames provided.'})

        logger.info(f'Bulk resetting passwords for {len(usernames)} users')

        succeeded = []
        failed    = []
        skipped   = []

        for username in usernames:
            try:
                result = await client.query(
                    f"SELECT Id, Name, Email, IsActive FROM User "
                    f"WHERE Username = '{username}' LIMIT 1"
                )
                records = result.get('records', [])

                if not records:
                    failed.append({'username': username, 'reason': 'User not found'})
                    continue

                user = records[0]

                if not user.get('IsActive'):
                    skipped.append({'username': username, 'reason': 'User is inactive'})
                    continue

                await client.reset_password(user['Id'])
                succeeded.append({
                    'username': username,
                    'email':    user.get('Email'),
                    'name':     user.get('Name'),
                })

            except Exception as e:
                failed.append({'username': username, 'reason': str(e)})

        return json.dumps({
            'summary': {
                'total':     len(usernames),
                'succeeded': len(succeeded),
                'skipped':   len(skipped),
                'failed':    len(failed),
            },
            'succeeded': succeeded,
            'skipped':   skipped,
            'failed':    failed,
        }, indent=2)

    # ─────────────────────────────────────────────────────────────────────────

    @tool
    async def bulk_reset_passwords_from_csv(csv_content: str, username_column: str = 'Username') -> str:
        """
        Reset passwords for multiple users from CSV file content.
        Use this when the user uploads a CSV file containing a list of usernames.

        The CSV must contain a column with Salesforce usernames.
        Column name defaults to 'Username' but can be overridden.

        Args:
            csv_content: Raw CSV file content as a string.
            username_column: Name of the column containing usernames. Default: 'Username'

        Returns:
            JSON summary with succeeded, failed, and skipped counts and details.
        """
        if is_production:
            return _prod_block('bulk_reset_passwords_from_csv')

        logger.info('Bulk resetting passwords from CSV')

        try:
            reader = csv.DictReader(io.StringIO(csv_content))
            rows   = list(reader)
        except Exception as e:
            return json.dumps({'error': f'Failed to parse CSV: {str(e)}'})

        if not rows:
            return json.dumps({'error': 'CSV file is empty.'})

        # Validate column exists
        available_columns = list(rows[0].keys()) if rows else []
        if username_column not in available_columns:
            return json.dumps({
                'error': f'Column "{username_column}" not found in CSV.',
                'available_columns': available_columns,
                'hint': 'Set username_column to the correct column name from available_columns.',
            })

        usernames = [
            row[username_column].strip()
            for row in rows
            if row.get(username_column, '').strip()
        ]

        if not usernames:
            return json.dumps({'error': f'No usernames found in column "{username_column}".'})

        logger.info(f'Extracted {len(usernames)} usernames from CSV')

        # Reuse bulk reset logic
        succeeded = []
        failed    = []
        skipped   = []

        for username in usernames:
            try:
                result = await client.query(
                    f"SELECT Id, Name, Email, IsActive FROM User "
                    f"WHERE Username = '{username}' LIMIT 1"
                )
                records = result.get('records', [])

                if not records:
                    failed.append({'username': username, 'reason': 'User not found'})
                    continue

                user = records[0]

                if not user.get('IsActive'):
                    skipped.append({'username': username, 'reason': 'User is inactive'})
                    continue

                await client.reset_password(user['Id'])
                succeeded.append({
                    'username': username,
                    'email':    user.get('Email'),
                    'name':     user.get('Name'),
                })

            except Exception as e:
                failed.append({'username': username, 'reason': str(e)})

        return json.dumps({
            'summary': {
                'total_in_csv': len(usernames),
                'succeeded':    len(succeeded),
                'skipped':      len(skipped),
                'failed':       len(failed),
            },
            'succeeded': succeeded,
            'skipped':   skipped,
            'failed':    failed,
        }, indent=2)

    # ─────────────────────────────────────────────────────────────────────────

    return [
        reset_password_by_username,
        reset_password_by_user_id,
        bulk_reset_passwords,
        bulk_reset_passwords_from_csv,
    ]