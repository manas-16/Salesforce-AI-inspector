# ─── Salesforce (AI)nspector — agent/tools/permission_tool.py ────────────────
# LangChain tools for managing user permissions.
# Covers: profile assignment, permission set assign/revoke, role assignment,
# and listing available profiles, permission sets, and roles.

import json
import logging
from langchain_core.tools import tool
from salesforce.client import SalesforceClient

logger = logging.getLogger(__name__)


def make_permission_tools(session_id: str, instance_url: str, is_production: bool):
    """
    Factory — returns permission tools bound to this request's session.
    """

    client = SalesforceClient(session_id, instance_url)

    def _prod_block(operation: str) -> str:
        return json.dumps({
            'error': f'PRODUCTION ORG DETECTED — {operation} is blocked. '
                     f'Write operations are only permitted in sandbox orgs.'
        })

    # ─────────────────────────────────────────────────────────────────────────

    @tool
    async def assign_profile(user_id: str, profile_name: str) -> str:
        """
        Assign a different profile to an existing Salesforce user.

        Args:
            user_id: Salesforce User record ID (18-char).
            profile_name: Name of the profile to assign.
                          Example: 'Standard User', 'System Administrator',
                                   'Sales User', 'Custom: Dev Profile'

        Returns:
            JSON with success confirmation or error.
        """
        if is_production:
            return _prod_block('assign_profile')

        logger.info(f'Assigning profile {profile_name} to user {user_id}')
        try:
            # Look up Profile ID
            result = await client.query(
                f"SELECT Id, Name FROM Profile WHERE Name = '{profile_name}' LIMIT 1"
            )
            records = result.get('records', [])
            if not records:
                # Profile not found — list available profiles to help agent
                all_profiles = await client.query(
                    "SELECT Name FROM Profile ORDER BY Name LIMIT 50"
                )
                names = [r['Name'] for r in all_profiles.get('records', [])]
                return json.dumps({
                    'error': f'Profile not found: {profile_name}',
                    'available_profiles': names,
                })

            profile_id = records[0]['Id']
            await client.update_record('User', user_id, {'ProfileId': profile_id})

            return json.dumps({
                'success':      True,
                'user_id':      user_id,
                'profile_name': profile_name,
                'profile_id':   profile_id,
                'message':      f'Profile "{profile_name}" assigned to user {user_id}.',
            })

        except Exception as e:
            logger.error(f'Assign profile failed: {e}')
            return json.dumps({'error': str(e)})

    # ─────────────────────────────────────────────────────────────────────────

    @tool
    async def assign_permission_set(user_id: str, permission_set_name: str) -> str:
        """
        Assign a permission set to a Salesforce user.
        Creates a PermissionSetAssignment record.

        Args:
            user_id: Salesforce User record ID (18-char).
            permission_set_name: API name of the permission set.
                                 Example: 'Sales_Manager_Permissions', 'API_Access'
                                 Use list_permission_sets to discover available ones.

        Returns:
            JSON with assignment ID, or error if already assigned or not found.
        """
        if is_production:
            return _prod_block('assign_permission_set')

        logger.info(f'Assigning permission set {permission_set_name} to user {user_id}')
        try:
            # Look up Permission Set ID
            ps_result = await client.query(
                f"SELECT Id, Name, Label FROM PermissionSet "
                f"WHERE Name = '{permission_set_name}' AND IsOwnedByProfile = false LIMIT 1"
            )
            ps_records = ps_result.get('records', [])
            if not ps_records:
                return json.dumps({
                    'error': f'Permission set not found: {permission_set_name}. '
                             f'Use list_permission_sets to see available permission sets.'
                })

            ps_id = ps_records[0]['Id']

            # Check if already assigned
            existing = await client.query(
                f"SELECT Id FROM PermissionSetAssignment "
                f"WHERE AssigneeId = '{user_id}' AND PermissionSetId = '{ps_id}' LIMIT 1"
            )
            if existing.get('records'):
                return json.dumps({
                    'success': False,
                    'message': f'Permission set "{permission_set_name}" is already assigned to user {user_id}.',
                })

            # Create assignment
            result = await client.create_record('PermissionSetAssignment', {
                'AssigneeId':      user_id,
                'PermissionSetId': ps_id,
            })

            return json.dumps({
                'success':             True,
                'user_id':             user_id,
                'permission_set_name': permission_set_name,
                'assignment_id':       result.get('id'),
                'message':             f'Permission set "{permission_set_name}" assigned to user {user_id}.',
            })

        except Exception as e:
            logger.error(f'Assign permission set failed: {e}')
            return json.dumps({'error': str(e)})

    # ─────────────────────────────────────────────────────────────────────────

    @tool
    async def revoke_permission_set(user_id: str, permission_set_name: str) -> str:
        """
        Remove a permission set assignment from a Salesforce user.

        Args:
            user_id: Salesforce User record ID (18-char).
            permission_set_name: API name of the permission set to remove.

        Returns:
            JSON with success confirmation or error.
        """
        if is_production:
            return _prod_block('revoke_permission_set')

        logger.info(f'Revoking permission set {permission_set_name} from user {user_id}')
        try:
            # Look up Permission Set ID
            ps_result = await client.query(
                f"SELECT Id FROM PermissionSet WHERE Name = '{permission_set_name}' "
                f"AND IsOwnedByProfile = false LIMIT 1"
            )
            ps_records = ps_result.get('records', [])
            if not ps_records:
                return json.dumps({'error': f'Permission set not found: {permission_set_name}'})

            ps_id = ps_records[0]['Id']

            # Find the assignment record
            assignment = await client.query(
                f"SELECT Id FROM PermissionSetAssignment "
                f"WHERE AssigneeId = '{user_id}' AND PermissionSetId = '{ps_id}' LIMIT 1"
            )
            assignments = assignment.get('records', [])
            if not assignments:
                return json.dumps({
                    'success': False,
                    'message': f'Permission set "{permission_set_name}" is not assigned to user {user_id}.',
                })

            assignment_id = assignments[0]['Id']
            await client.delete_record('PermissionSetAssignment', assignment_id)

            return json.dumps({
                'success':             True,
                'user_id':             user_id,
                'permission_set_name': permission_set_name,
                'message':             f'Permission set "{permission_set_name}" removed from user {user_id}.',
            })

        except Exception as e:
            logger.error(f'Revoke permission set failed: {e}')
            return json.dumps({'error': str(e)})

    # ─────────────────────────────────────────────────────────────────────────

    @tool
    async def assign_role(user_id: str, role_name: str) -> str:
        """
        Assign a role to a Salesforce user.

        Args:
            user_id: Salesforce User record ID (18-char).
            role_name: Name of the role to assign.
                       Example: 'CEO', 'Sales Manager - Western', 'VP of Sales'
                       Use list_roles to discover available roles.

        Returns:
            JSON with success confirmation or error.
        """
        if is_production:
            return _prod_block('assign_role')

        logger.info(f'Assigning role {role_name} to user {user_id}')
        try:
            # Look up Role ID
            role_result = await client.query(
                f"SELECT Id, Name FROM UserRole WHERE Name = '{role_name}' LIMIT 1"
            )
            roles = role_result.get('records', [])
            if not roles:
                return json.dumps({
                    'error': f'Role not found: {role_name}. '
                             f'Use list_roles to see available roles.'
                })

            role_id = roles[0]['Id']
            await client.update_record('User', user_id, {'UserRoleId': role_id})

            return json.dumps({
                'success':   True,
                'user_id':   user_id,
                'role_name': role_name,
                'role_id':   role_id,
                'message':   f'Role "{role_name}" assigned to user {user_id}.',
            })

        except Exception as e:
            logger.error(f'Assign role failed: {e}')
            return json.dumps({'error': str(e)})

    # ─────────────────────────────────────────────────────────────────────────

    @tool
    async def list_profiles() -> str:
        """
        List all profiles available in the org.
        Use this before assigning a profile to find the correct profile name.

        Returns:
            JSON list of profiles with Id, Name, and UserType.
        """
        logger.info('Listing profiles')
        try:
            result = await client.query(
                "SELECT Id, Name, UserType FROM Profile ORDER BY Name"
            )
            profiles = [
                {k: v for k, v in r.items() if k != 'attributes'}
                for r in result.get('records', [])
            ]
            return json.dumps({
                'total': len(profiles),
                'profiles': profiles,
            }, indent=2)
        except Exception as e:
            logger.error(f'List profiles failed: {e}')
            return json.dumps({'error': str(e)})

    # ─────────────────────────────────────────────────────────────────────────

    @tool
    async def list_permission_sets() -> str:
        """
        List all custom permission sets available in the org.
        (Excludes permission sets owned by profiles.)
        Use this before assigning a permission set to find the correct API name.

        Returns:
            JSON list of permission sets with Id, Name, and Label.
        """
        logger.info('Listing permission sets')
        try:
            result = await client.query(
                "SELECT Id, Name, Label, Description FROM PermissionSet "
                "WHERE IsOwnedByProfile = false ORDER BY Label"
            )
            psets = [
                {k: v for k, v in r.items() if k != 'attributes'}
                for r in result.get('records', [])
            ]
            return json.dumps({
                'total': len(psets),
                'permission_sets': psets,
            }, indent=2)
        except Exception as e:
            logger.error(f'List permission sets failed: {e}')
            return json.dumps({'error': str(e)})

    # ─────────────────────────────────────────────────────────────────────────

    @tool
    async def list_roles() -> str:
        """
        List all roles available in the org.
        Use this before assigning a role to find the correct role name.

        Returns:
            JSON list of roles with Id, Name, and DeveloperName.
        """
        logger.info('Listing roles')
        try:
            result = await client.query(
                "SELECT Id, Name, DeveloperName, ParentRoleId FROM UserRole ORDER BY Name"
            )
            roles = [
                {k: v for k, v in r.items() if k != 'attributes'}
                for r in result.get('records', [])
            ]
            return json.dumps({
                'total': len(roles),
                'roles': roles,
            }, indent=2)
        except Exception as e:
            logger.error(f'List roles failed: {e}')
            return json.dumps({'error': str(e)})

    # ─────────────────────────────────────────────────────────────────────────

    @tool
    async def get_user_permissions(user_id: str) -> str:
        """
        Get a summary of a user's current profile, role, and assigned permission sets.
        Use this to audit a user's access before making changes.

        Args:
            user_id: Salesforce User record ID (18-char).

        Returns:
            JSON with profile name, role name, and list of assigned permission sets.
        """
        logger.info(f'Getting permissions for user: {user_id}')
        try:
            # Get user profile and role
            user_result = await client.query(
                f"SELECT Id, Name, Username, Profile.Name, UserRole.Name, IsActive "
                f"FROM User WHERE Id = '{user_id}' LIMIT 1"
            )
            users = user_result.get('records', [])
            if not users:
                return json.dumps({'error': f'User not found: {user_id}'})

            user = users[0]

            # Get permission set assignments
            ps_result = await client.query(
                f"SELECT PermissionSet.Name, PermissionSet.Label "
                f"FROM PermissionSetAssignment "
                f"WHERE AssigneeId = '{user_id}' AND PermissionSet.IsOwnedByProfile = false"
            )
            permission_sets = [
                r.get('PermissionSet', {}).get('Label', r.get('PermissionSet', {}).get('Name', ''))
                for r in ps_result.get('records', [])
            ]

            return json.dumps({
                'user_id':         user_id,
                'name':            user.get('Name'),
                'username':        user.get('Username'),
                'is_active':       user.get('IsActive'),
                'profile':         user.get('Profile', {}).get('Name') if user.get('Profile') else None,
                'role':            user.get('UserRole', {}).get('Name') if user.get('UserRole') else None,
                'permission_sets': permission_sets,
            }, indent=2)

        except Exception as e:
            logger.error(f'Get user permissions failed: {e}')
            return json.dumps({'error': str(e)})

    # ─────────────────────────────────────────────────────────────────────────

    return [
        assign_profile,
        assign_permission_set,
        revoke_permission_set,
        assign_role,
        list_profiles,
        list_permission_sets,
        list_roles,
        get_user_permissions,
    ]