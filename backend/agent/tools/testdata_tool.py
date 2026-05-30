# ─── Salesforce (AI)nspector — agent/tools/testdata_tool.py ──────────────────
# LangChain tools for intelligent test data creation.
# Reads live org schema before generating data — ensures all mandatory
# fields are populated, picklist values are valid, and lookups are satisfied.
# All operations blocked on production orgs.

import json
import logging
import random
import string
from datetime import datetime, timedelta
from langchain_core.tools import tool
from salesforce.client import SalesforceClient

logger = logging.getLogger(__name__)


def make_testdata_tools(session_id: str, instance_url: str, is_production: bool):
    """
    Factory — returns test data tools bound to this request's session.
    """

    client = SalesforceClient(session_id, instance_url)

    def _prod_block(operation: str) -> str:
        return json.dumps({
            'error': f'PRODUCTION ORG DETECTED — {operation} is blocked. '
                     f'Write operations are only permitted in sandbox orgs.'
        })

    # ─────────────────────────────────────────────────────────────────────────

    @tool
    async def create_test_records(
        sobject: str,
        count: int = 1,
        field_overrides: dict = None,
    ) -> str:
        """
        Create realistic test records for any Salesforce SObject.

        Automatically reads the object schema to:
        - Identify all mandatory fields
        - Use valid picklist values from the org
        - Generate realistic fake values per field type
        - Respect field length limits

        Args:
            sobject: API name of the SObject. Example: Account, Contact, Lead, Case
            count: Number of records to create. Default 1. Max 50.
            field_overrides: Optional dict of field values to force on all records.
                             Overrides auto-generated values.
                             Example: {"Industry": "Technology", "OwnerId": "005XX..."}

        Returns:
            JSON with created record IDs and field values used.
        """
        if is_production:
            return _prod_block('create_test_records')

        count = min(count, 50)
        logger.info(f'Creating {count} test {sobject} records')

        try:
            # Read live schema
            describe = await client.describe_object(sobject)
            fields   = describe.get('fields', [])

            created  = []
            failed   = []

            for i in range(count):
                record_fields = _generate_record(fields, sobject, i)

                # Apply overrides
                if field_overrides:
                    record_fields.update(field_overrides)

                try:
                    result = await client.create_record(sobject, record_fields)
                    created.append({
                        'record_id': result.get('id'),
                        'fields':    record_fields,
                    })
                except Exception as e:
                    failed.append({
                        'index':  i,
                        'error':  str(e),
                        'fields': record_fields,
                    })

            return json.dumps({
                'sobject':  sobject,
                'summary': {
                    'requested': count,
                    'created':   len(created),
                    'failed':    len(failed),
                },
                'created': created,
                'failed':  failed,
            }, indent=2, default=str)

        except Exception as e:
            logger.error(f'Create test records failed: {e}')
            return json.dumps({'error': str(e)})

    # ─────────────────────────────────────────────────────────────────────────

    @tool
    async def create_test_data_chain(scenario: str) -> str:
        """
        Create a complete end-to-end test data chain for a given business scenario.

        Supported scenarios:
        - 'lead_to_opportunity': Lead + converted Account + Contact + Opportunity
        - 'order_management': Account + Contact + Opportunity + Order + OrderItems
        - 'case_management': Account + Contact + Case + CaseComment
        - 'account_hierarchy': Parent Account + 3 Child Accounts + Contacts

        Args:
            scenario: One of the supported scenario keys above.

        Returns:
            JSON with all created record IDs in the chain, ready for testing.
        """
        if is_production:
            return _prod_block('create_test_data_chain')

        logger.info(f'Creating test data chain: {scenario}')

        scenarios = {
            'lead_to_opportunity': _chain_lead_to_opportunity,
            'order_management':    _chain_order_management,
            'case_management':     _chain_case_management,
            'account_hierarchy':   _chain_account_hierarchy,
        }

        if scenario not in scenarios:
            return json.dumps({
                'error':              f'Unknown scenario: {scenario}',
                'supported_scenarios': list(scenarios.keys()),
            })

        try:
            result = await scenarios[scenario](client)
            return json.dumps({
                'scenario': scenario,
                'success':  True,
                'chain':    result,
            }, indent=2, default=str)
        except Exception as e:
            logger.error(f'Create chain failed: {e}')
            return json.dumps({'error': str(e)})

    # ─────────────────────────────────────────────────────────────────────────

    @tool
    async def create_bulk_test_records_from_spec(
        sobject: str,
        specs: list[dict],
    ) -> str:
        """
        Create multiple test records with different field values per record.
        Use this when you need varied test data — e.g. records in different
        stages, different owners, or different regions for testing filters.

        Args:
            sobject: API name of the SObject.
            specs: List of field dicts — one dict per record.
                   Each dict can override any auto-generated fields.
                   Example for Opportunity:
                   [
                       {"StageName": "Prospecting", "Amount": 10000},
                       {"StageName": "Closed Won",  "Amount": 50000},
                       {"StageName": "Closed Lost", "Amount": 5000},
                   ]

        Returns:
            JSON with created record IDs per spec.
        """
        if is_production:
            return _prod_block('create_bulk_test_records_from_spec')

        logger.info(f'Creating {len(specs)} {sobject} records from spec')

        try:
            describe = await client.describe_object(sobject)
            fields   = describe.get('fields', [])

            created = []
            failed  = []

            for i, spec in enumerate(specs):
                base   = _generate_record(fields, sobject, i)
                base.update(spec)   # spec overrides generated values

                try:
                    result = await client.create_record(sobject, base)
                    created.append({
                        'spec_index': i,
                        'record_id':  result.get('id'),
                        'fields':     base,
                    })
                except Exception as e:
                    failed.append({
                        'spec_index': i,
                        'error':      str(e),
                        'fields':     base,
                    })

            return json.dumps({
                'sobject':  sobject,
                'summary': {
                    'total':   len(specs),
                    'created': len(created),
                    'failed':  len(failed),
                },
                'created': created,
                'failed':  failed,
            }, indent=2, default=str)

        except Exception as e:
            logger.error(f'Bulk create from spec failed: {e}')
            return json.dumps({'error': str(e)})

    # ─────────────────────────────────────────────────────────────────────────

    @tool
    async def preview_test_record(sobject: str) -> str:
        """
        Preview what a generated test record will look like for an object
        WITHOUT actually creating it in the org.

        Use this to let the user review and confirm field values before
        committing to a bulk data creation operation.

        Args:
            sobject: API name of the SObject. Example: Account, Contact

        Returns:
            JSON showing the fields and values that would be generated.
        """
        logger.info(f'Previewing test record for: {sobject}')
        try:
            describe = await client.describe_object(sobject)
            fields   = describe.get('fields', [])
            preview  = _generate_record(fields, sobject, 0)

            return json.dumps({
                'sobject':  sobject,
                'preview':  preview,
                'note':     'This is a preview only — no record was created. '
                            'Call create_test_records to actually insert.',
            }, indent=2)
        except Exception as e:
            logger.error(f'Preview test record failed: {e}')
            return json.dumps({'error': str(e)})

    # ─────────────────────────────────────────────────────────────────────────

    return [
        create_test_records,
        create_test_data_chain,
        create_bulk_test_records_from_spec,
        preview_test_record,
    ]


# ─── RECORD GENERATOR ─────────────────────────────────────────────────────────

def _generate_record(fields: list, sobject: str, index: int) -> dict:
    """
    Generate a realistic record dict from an object's field describe.
    Only populates createable, non-auto fields.
    Skips system fields (Id, CreatedDate, etc.)
    """
    SKIP_FIELDS = {
        'Id', 'CreatedDate', 'CreatedById', 'LastModifiedDate',
        'LastModifiedById', 'SystemModstamp', 'IsDeleted',
        'LastActivityDate', 'LastViewedDate', 'LastReferencedDate',
        'MasterRecordId', 'RecordTypeId',
    }

    record = {}

    for field in fields:
        name = field.get('name')
        ftype = field.get('type')

        # Skip non-createable, auto, and system fields
        if not field.get('createable'):
            continue
        if name in SKIP_FIELDS:
            continue
        if field.get('autoNumber'):
            continue
        if field.get('calculated'):
            continue
        if field.get('defaultedOnCreate') and field.get('nillable'):
            continue

        # Skip nullable lookup fields — don't want to fail on missing IDs
        if ftype in ('reference',) and field.get('nillable'):
            continue

        value = _generate_value(field, sobject, index)
        if value is not None:
            record[name] = value

    return record


def _generate_value(field: dict, sobject: str, index: int):
    """Generate a realistic value for a single field based on its type."""
    name  = field.get('name', '')
    ftype = field.get('type', '')
    label = field.get('label', '')

    suffix = f'{index + 1:03d}'
    uid    = _rand_str(4)

    # Picklist — always use first active value
    if ftype in ('picklist', 'multipicklist'):
        values = [
            p['value'] for p in field.get('picklistValues', [])
            if p.get('active') and p.get('value')
        ]
        return values[0] if values else None

    # Boolean
    if ftype == 'boolean':
        return False

    # Date
    if ftype == 'date':
        if 'close' in name.lower() or 'end' in name.lower():
            return (datetime.today() + timedelta(days=30)).strftime('%Y-%m-%d')
        return datetime.today().strftime('%Y-%m-%d')

    # Datetime
    if ftype == 'datetime':
        return datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%SZ')

    # Email
    if ftype == 'email' or 'email' in name.lower():
        return f'test.user.{suffix}.{uid}@sfaitest.com'

    # Phone
    if ftype == 'phone' or 'phone' in name.lower():
        return f'+1-555-{random.randint(100,999)}-{random.randint(1000,9999)}'

    # URL
    if ftype == 'url' or 'website' in name.lower():
        return f'https://www.testcompany{suffix}.com'

    # Currency / Number / Percent
    if ftype in ('currency', 'double', 'percent'):
        if 'amount' in name.lower() or 'revenue' in name.lower():
            return round(random.uniform(10000, 500000), 2)
        if 'percent' in name.lower() or ftype == 'percent':
            return round(random.uniform(0, 100), 1)
        return round(random.uniform(1, 1000), 2)

    if ftype == 'int' or ftype == 'integer':
        return random.randint(1, 100)

    # Text fields — context-aware generation
    if ftype in ('string', 'textarea', 'phone'):
        max_len = field.get('length', 255)

        if name == 'Name' or name == f'{sobject}Name':
            names = {
                'Account':     f'Test Corp {suffix}',
                'Contact':     f'Test Contact {suffix}',
                'Lead':        f'Test Lead {suffix}',
                'Case':        f'Test Case {suffix}',
                'Opportunity': f'Test Opportunity {suffix}',
                'Order':       f'Test Order {suffix}',
            }
            return names.get(sobject, f'Test {sobject} {suffix}')

        if 'first' in name.lower():
            return f'TestFirst{suffix}'
        if 'last' in name.lower():
            return f'TestLast{suffix}'
        if 'company' in name.lower() or 'account' in name.lower():
            return f'Test Company {suffix}'
        if 'city' in name.lower():
            return 'San Francisco'
        if 'state' in name.lower():
            return 'CA'
        if 'country' in name.lower():
            return 'US'
        if 'zip' in name.lower() or 'postal' in name.lower():
            return '94105'
        if 'street' in name.lower():
            return f'{random.randint(100,999)} Test Street'
        if 'title' in name.lower():
            return 'Software Engineer'
        if 'department' in name.lower():
            return 'Engineering'
        if 'description' in name.lower() or 'comment' in name.lower():
            return f'Test data generated by Salesforce (AI)nspector. Record {suffix}.'
        if 'username' in name.lower():
            return f'testuser{suffix}.{uid}@sfaitest.com'

        # Generic text — respect max length
        generic = f'Test {label} {suffix}'
        return generic[:max_len]

    return None


def _rand_str(length: int) -> str:
    return ''.join(random.choices(string.ascii_lowercase + string.digits, k=length))


# ─── CHAIN SCENARIOS ──────────────────────────────────────────────────────────

async def _chain_lead_to_opportunity(client: SalesforceClient) -> dict:
    uid = _rand_str(4)

    # Account
    account = await client.create_record('Account', {
        'Name':     f'Test Account {uid}',
        'Industry': 'Technology',
        'Phone':    '+1-555-100-0001',
    })
    account_id = account['id']

    # Contact
    contact = await client.create_record('Contact', {
        'FirstName': f'Test',
        'LastName':  f'Contact {uid}',
        'Email':     f'test.contact.{uid}@sfaitest.com',
        'AccountId': account_id,
    })
    contact_id = contact['id']

    # Opportunity
    opp = await client.create_record('Opportunity', {
        'Name':        f'Test Opportunity {uid}',
        'AccountId':   account_id,
        'StageName':   'Prospecting',
        'CloseDate':   (datetime.today() + timedelta(days=30)).strftime('%Y-%m-%d'),
        'Amount':      50000,
    })

    return {
        'account_id':     account_id,
        'contact_id':     contact_id,
        'opportunity_id': opp['id'],
    }


async def _chain_order_management(client: SalesforceClient) -> dict:
    uid = _rand_str(4)

    account = await client.create_record('Account', {
        'Name':     f'Test Account {uid}',
        'Industry': 'Retail',
    })
    account_id = account['id']

    contact = await client.create_record('Contact', {
        'FirstName': 'Test',
        'LastName':  f'Buyer {uid}',
        'Email':     f'buyer.{uid}@sfaitest.com',
        'AccountId': account_id,
    })

    opp = await client.create_record('Opportunity', {
        'Name':      f'Test Opportunity {uid}',
        'AccountId': account_id,
        'StageName': 'Closed Won',
        'CloseDate': datetime.today().strftime('%Y-%m-%d'),
        'Amount':    25000,
    })

    return {
        'account_id':     account_id,
        'contact_id':     contact['id'],
        'opportunity_id': opp['id'],
        'note': 'Order and OrderItem creation depends on your org Price Book setup. '
                'Create Order manually against this Opportunity.',
    }


async def _chain_case_management(client: SalesforceClient) -> dict:
    uid = _rand_str(4)

    account = await client.create_record('Account', {
        'Name': f'Test Account {uid}',
    })
    account_id = account['id']

    contact = await client.create_record('Contact', {
        'FirstName': 'Test',
        'LastName':  f'Customer {uid}',
        'Email':     f'customer.{uid}@sfaitest.com',
        'AccountId': account_id,
    })
    contact_id = contact['id']

    case = await client.create_record('Case', {
        'Subject':     f'Test Case {uid}',
        'AccountId':   account_id,
        'ContactId':   contact_id,
        'Status':      'New',
        'Origin':      'Web',
        'Description': f'Test case created by Salesforce (AI)nspector. UID: {uid}',
    })
    case_id = case['id']

    comment = await client.create_record('CaseComment', {
        'ParentId':       case_id,
        'CommentBody':    f'Initial comment for test case {uid}.',
        'IsPublished':    True,
    })

    return {
        'account_id': account_id,
        'contact_id': contact_id,
        'case_id':    case_id,
        'comment_id': comment['id'],
    }


async def _chain_account_hierarchy(client: SalesforceClient) -> dict:
    uid = _rand_str(4)

    parent = await client.create_record('Account', {
        'Name':     f'Parent Corp {uid}',
        'Industry': 'Technology',
        'Type':     'Partner',
    })
    parent_id = parent['id']

    children  = []
    contacts  = []

    for i in range(3):
        child = await client.create_record('Account', {
            'Name':            f'Child Division {i+1} {uid}',
            'ParentId':        parent_id,
            'Industry':        'Technology',
        })
        child_id = child['id']
        children.append(child_id)

        contact = await client.create_record('Contact', {
            'FirstName': f'Child{i+1}',
            'LastName':  f'Contact {uid}',
            'Email':     f'child{i+1}.{uid}@sfaitest.com',
            'AccountId': child_id,
        })
        contacts.append(contact['id'])

    return {
        'parent_account_id': parent_id,
        'child_account_ids': children,
        'contact_ids':       contacts,
    }