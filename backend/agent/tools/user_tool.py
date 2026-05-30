# ─── Salesforce (AI)nspector — agent/tools/user_tool.py ──────────────────────
# LangChain tools for Salesforce user management.
# Covers: create, update, activate, deactivate, freeze, unfreeze users.
# All write operations blocked on production orgs.

import json
import logging
from langchain_core.tools import tool
from salesforce.client import SalesforceClient

logger = logging.getLogger(__name__)


def make_user_tools(session_id: str, instance_url: str, is_production: bool):
    """
    Factory — returns user management tools bound to this request's session.
    is_production: if True, all write operations return a blocked error.
    """

    client = SalesforceClient(session_id, instance_url)

    def _prod_block(operation: str) -> str:
        return json.dumps({
            'error': f'PRODUCTION ORG DETECTED — {operation} is blocked. '
                     f'Write operations are only permitted in sandbox orgs.'
        })

    # ─────────────────────────────────────────────────────────────────────────

    @tool
    async def create_user(
        first_name: str,
        last_name: str,
        email: str,
        username: str,
        profile_name: str,
        alias: str = None,
        time_zone_sid_key: str = 'America/Los_Angeles',
        locale_sid_key: str = 'en_US',
        email_encoding_key: str = 'UTF-8',
        language_locale_key: str = 'en_US',
    ) -> str:
        """
        Create a new Salesforce user.

        Before calling this tool, use run_soql_query to look up the Profile ID:
            SELECT Id, Name FROM Profile WHERE Name = '<profile_name>'

        Args:
            first_name: User's first name.
            last_name: User's last name.
            email: User's email address.
            username: Unique Salesforce username — must be in email format and globally unique.
                      Typically: firstname.lastname@yourorg.sandbox.com
            profile_name: Name of the profile to assign. Tool will look up the ID automatically.
            alias: Short alias (max 8 chars). Auto-generated from first+last if not provided.
            time_zone_sid_key: Timezone. Examples: 'Asia/Kolkata', 'America/New_York', 'Europe/London'
            locale_sid_key: Locale. Examples: 'en_US', 'en_IN', 'en_GB'
            email_encoding_key: Encoding. Default 'UTF-8'.
            language_locale_key: Language. Default 'en_US'.

        Returns:
            JSON with created user ID and username, or error message.
        """
        if is_production:
            return _prod_block('create_user')

        logger.info(f'Creating user: {username}')

        try:
            # Look up Profile ID by name
            profile_result = await client.query(
                f"SELECT Id FROM Profile WHERE Name = '{profile_name}' LIMIT 1"
            )
            profiles = profile_result.get('records', [])
            if not profiles:
                return json.dumps({'error': f'Profile not found: {profile_name}. '
                                            f'Use run_soql_query to list available profiles: '
                                            f'SELECT Id, Name FROM Profile'})
            profile_id = profiles[0]['Id']

            # Auto-generate alias if not provided
            if not alias:
                alias = (first_name[0] + last_name[:7]).lower()[:8]

            user_fields = {
                'FirstName':          first_name,
                'LastName':           last_name,
                'Email':              email,
                'Username':           username,
                'Alias':              alias,
                'ProfileId':          profile_id,
                'TimeZoneSidKey':     time_zone_sid_key,
                'LocaleSidKey':       locale_sid_key,
                'EmailEncodingKey':   email_encoding_key,
                'LanguageLocaleKey':  language_locale_key,
                'IsActive':           True,
            }

            result = await client.create_record('User', user_fields)

            return json.dumps({
                'success':  True,
                'user_id':  result.get('id'),
                'username': username,
                'message':  f'User {first_name} {last_name} created successfully.',
            })

        except Exception as e:
            logger.error(f'Create user failed: {e}')
            return json.dumps({'error': str(e)})

    # ─────────────────────────────────────────────────────────────────────────

    @tool
    async def update_user(user_id: str, fields: dict) -> str:
        """
        Update fields on an existing Salesforce user.

        Args:
            user_id: Salesforce User record ID (18-char).
                     Use run_soql_query to find it:
                     SELECT Id FROM User WHERE Username = '<username>'
            fields: Dictionary of field API names to new values.
                    Example: {"Title": "Senior Developer", "Department": "Engineering"}

        Returns:
            JSON with success confirmation or error.
        """
        if is_production:
            return _prod_block('update_user')

        logger.info(f'Updating user {user_id}: {fields}')
        try:
            await client.update_record('User', user_id, fields)
            return json.dumps({
                'success': True,
                'user_id': user_id,
                'updated_fields': list(fields.keys()),
                'message': f'User {user_id} updated successfully.',
            })
        except Exception as e:
            logger.error(f'Update user failed: {e}')
            return json.dumps({'error': str(e)})

    # ─────────────────────────────────────────────────────────────────────────

    @tool
    async def deactivate_user(user_id: str) -> str:
        """
        Deactivate a Salesforce user. The user will no longer be able to log in
        but their records and history are preserved.

        Args:
            user_id: Salesforce User record ID (18-char).
                     Use run_soql_query to find it:
                     SELECT Id, Name FROM User WHERE Username = '<username>'

        Returns:
            JSON with success confirmation or error.
        """
        if is_production:
            return _prod_block('deactivate_user')

        logger.info(f'Deactivating user: {user_id}')
        try:
            await client.update_record('User', user_id, {'IsActive': False})
            return json.dumps({
                'success': True,
                'user_id': user_id,
                'message': f'User {user_id} deactivated successfully.',
            })
        except Exception as e:
            logger.error(f'Deactivate user failed: {e}')
            return json.dumps({'error': str(e)})

    # ─────────────────────────────────────────────────────────────────────────

    @tool
    async def activate_user(user_id: str) -> str:
        """
        Reactivate a previously deactivated Salesforce user.

        Args:
            user_id: Salesforce User record ID (18-char).

        Returns:
            JSON with success confirmation or error.
        """
        if is_production:
            return _prod_block('activate_user')

        logger.info(f'Activating user: {user_id}')
        try:
            await client.update_record('User', user_id, {'IsActive': True})
            return json.dumps({
                'success': True,
                'user_id': user_id,
                'message': f'User {user_id} activated successfully.',
            })
        except Exception as e:
            logger.error(f'Activate user failed: {e}')
            return json.dumps({'error': str(e)})

    # ─────────────────────────────────────────────────────────────────────────

    @tool
    async def freeze_user(user_id: str) -> str:
        """
        Freeze a Salesforce user. Freezing prevents login immediately without
        deactivating — useful when you need to temporarily block access
        while reviewing before full deactivation.

        Note: This operates on the UserLogin object, not the User object directly.

        Args:
            user_id: Salesforce User record ID (18-char).

        Returns:
            JSON with success confirmation or error.
        """
        if is_production:
            return _prod_block('freeze_user')

        logger.info(f'Freezing user: {user_id}')
        try:
            user_login = await client.get_user_login(user_id)
            await client.freeze_user(user_login['Id'], freeze=True)
            return json.dumps({
                'success': True,
                'user_id': user_id,
                'message': f'User {user_id} frozen successfully. They cannot log in.',
            })
        except Exception as e:
            logger.error(f'Freeze user failed: {e}')
            return json.dumps({'error': str(e)})

    # ─────────────────────────────────────────────────────────────────────────

    @tool
    async def unfreeze_user(user_id: str) -> str:
        """
        Unfreeze a previously frozen Salesforce user, restoring login access.

        Args:
            user_id: Salesforce User record ID (18-char).

        Returns:
            JSON with success confirmation or error.
        """
        if is_production:
            return _prod_block('unfreeze_user')

        logger.info(f'Unfreezing user: {user_id}')
        try:
            user_login = await client.get_user_login(user_id)
            await client.freeze_user(user_login['Id'], freeze=False)
            return json.dumps({
                'success': True,
                'user_id': user_id,
                'message': f'User {user_id} unfrozen successfully. Login access restored.',
            })
        except Exception as e:
            logger.error(f'Unfreeze user failed: {e}')
            return json.dumps({'error': str(e)})

    # ─────────────────────────────────────────────────────────────────────────

    @tool
    async def reset_user_password(user_id: str) -> str:
        """
        Trigger a password reset email for a Salesforce user.
        The user will receive an email with a reset link.

        Args:
            user_id: Salesforce User record ID (18-char).
                     Use run_soql_query to find it:
                     SELECT Id, Name, Email FROM User WHERE Username = '<username>'

        Returns:
            JSON with success confirmation or error.
        """
        if is_production:
            return _prod_block('reset_user_password')

        logger.info(f'Resetting password for user: {user_id}')
        try:
            result = await client.reset_password(user_id)
            return json.dumps({
                'success': True,
                'user_id': user_id,
                'message': f'Password reset email sent to user {user_id}.',
            })
        except Exception as e:
            logger.error(f'Reset password failed: {e}')
            return json.dumps({'error': str(e)})

    # ─────────────────────────────────────────────────────────────────────────

    return [
        create_user,
        update_user,
        deactivate_user,
        activate_user,
        freeze_user,
        unfreeze_user,
        reset_user_password,
    ]