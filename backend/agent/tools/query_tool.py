# ─── Salesforce (AI)nspector — agent/tools/query_tool.py ─────────────────────
# LangChain tool for executing SOQL queries against the Salesforce org.
# Supports single queries, paginated queries, and natural language
# to SOQL translation via the agent's own reasoning.

import json
import logging
from langchain_core.tools import tool
from salesforce.client import SalesforceClient

logger = logging.getLogger(__name__)


def make_query_tools(session_id: str, instance_url: str):
    """
    Factory — returns query tools bound to this request's session.
    Called once per request in agent.py.
    """

    client = SalesforceClient(session_id, instance_url)

    # ─────────────────────────────────────────────────────────────────────────

    @tool
    async def run_soql_query(soql: str) -> str:
        """
        Execute a SOQL query against the Salesforce org and return the results.

        Use this tool to:
        - Retrieve records from any Salesforce object
        - Look up user IDs, record IDs, field values
        - Validate data before performing write operations
        - Cross-reference records for analysis

        Args:
            soql: A valid SOQL query string.
                  Example: "SELECT Id, Name, Email FROM User WHERE IsActive = true LIMIT 10"

        Returns:
            JSON string with totalSize and records list.
        """
        logger.info(f'Executing SOQL: {soql}')
        try:
            result = await client.query(soql)
            records = result.get('records', [])
            total   = result.get('totalSize', len(records))

            # Strip Salesforce metadata attributes from each record
            clean = [_strip_attributes(r) for r in records]

            return json.dumps({
                'total_size': total,
                'returned':   len(clean),
                'records':    clean,
            }, indent=2, default=str)

        except Exception as e:
            logger.error(f'SOQL query failed: {e}')
            return json.dumps({'error': str(e)})

    # ─────────────────────────────────────────────────────────────────────────

    @tool
    async def run_soql_query_all(soql: str) -> str:
        """
        Execute a SOQL query and automatically paginate through ALL results.
        Use this instead of run_soql_query when you expect more than 2000 records
        or when you need a complete dataset for analysis.

        Args:
            soql: A valid SOQL query string without LIMIT clause (or with a high LIMIT).

        Returns:
            JSON string with total record count and full records list.
        """
        logger.info(f'Executing paginated SOQL: {soql}')
        try:
            records = await client.query_all(soql)
            clean   = [_strip_attributes(r) for r in records]

            return json.dumps({
                'total_returned': len(clean),
                'records':        clean,
            }, indent=2, default=str)

        except Exception as e:
            logger.error(f'Paginated SOQL failed: {e}')
            return json.dumps({'error': str(e)})

    # ─────────────────────────────────────────────────────────────────────────

    @tool
    async def describe_salesforce_object(object_name: str) -> str:
        """
        Get the full schema description of a Salesforce object —
        all fields, their types, lengths, picklist values, and relationships.

        Use this tool before:
        - Creating new fields (to check for name conflicts)
        - Building SOQL queries (to verify field names)
        - Creating test data (to know mandatory fields and valid picklist values)
        - Any operation where you need to understand the object's structure

        Args:
            object_name: API name of the SObject. Examples: Account, Contact,
                         Opportunity, Lead, Case, MyCustomObject__c

        Returns:
            JSON with fields list, each containing name, type, length, required,
            picklist values (if applicable), and relationship info.
        """
        logger.info(f'Describing object: {object_name}')
        try:
            result = await client.describe_object(object_name)

            # Return only the useful subset — full describe is massive
            fields = [
                {
                    'name':             f.get('name'),
                    'label':            f.get('label'),
                    'type':             f.get('type'),
                    'length':           f.get('length'),
                    'required':         not f.get('nillable', True) and not f.get('defaultedOnCreate', False),
                    'unique':           f.get('unique', False),
                    'external_id':      f.get('externalId', False),
                    'picklist_values':  [p['value'] for p in f.get('picklistValues', []) if p.get('active')],
                    'relationship_name': f.get('relationshipName'),
                    'reference_to':     f.get('referenceTo', []),
                }
                for f in result.get('fields', [])
            ]

            return json.dumps({
                'object_name':  result.get('name'),
                'label':        result.get('label'),
                'field_count':  len(fields),
                'fields':       fields,
            }, indent=2, default=str)

        except Exception as e:
            logger.error(f'Describe object failed: {e}')
            return json.dumps({'error': str(e)})

    # ─────────────────────────────────────────────────────────────────────────

    @tool
    async def list_salesforce_objects() -> str:
        """
        List all available SObjects in the Salesforce org — both standard and custom.
        Use this when you need to discover what objects exist before querying them.

        Returns:
            JSON list of objects with their API name, label, and whether they are custom.
        """
        logger.info('Listing all SObjects')
        try:
            result = await client.describe_global()
            sobjects = [
                {
                    'name':        s.get('name'),
                    'label':       s.get('label'),
                    'custom':      s.get('custom', False),
                    'queryable':   s.get('queryable', False),
                    'updateable':  s.get('updateable', False),
                    'createable':  s.get('createable', False),
                }
                for s in result.get('sobjects', [])
                if s.get('queryable')
            ]

            return json.dumps({
                'total_objects': len(sobjects),
                'objects':       sobjects,
            }, indent=2, default=str)

        except Exception as e:
            logger.error(f'List objects failed: {e}')
            return json.dumps({'error': str(e)})

    # ─────────────────────────────────────────────────────────────────────────

    @tool
    async def search_metadata_by_name(search_term: str) -> str:
        """
        Search for metadata records (CustomLabels, CustomMetadata, CustomSettings)
        that match a given name or keyword.

        Use this when the user gives a vague instruction without specifying
        the metadata type — e.g. "update the welcome message" or "find the
        API timeout setting".

        The tool will search across:
        - CustomLabel (via Tooling API)
        - CustomMetadataType records
        - CustomSetting records (via SObject query)

        Args:
            search_term: Keyword to search for. Example: "welcome", "timeout", "api_key"

        Returns:
            JSON listing all matching metadata records across all types.
        """
        logger.info(f'Searching metadata for: {search_term}')
        results = {}

        # Search CustomLabels
        try:
            label_result = await client.tooling_query(
                f"SELECT Id, Name, Value, MasterLabel FROM ExternalString "
                f"WHERE Name LIKE '%{search_term}%' OR MasterLabel LIKE '%{search_term}%' LIMIT 10"
            )
            results['custom_labels'] = [_strip_attributes(r) for r in label_result.get('records', [])]
        except Exception as e:
            results['custom_labels'] = {'error': str(e)}

        # Search CustomMetadata records
        try:
            cmdt_result = await client.tooling_query(
                f"SELECT Id, DeveloperName, MasterLabel, QualifiedApiName "
                f"FROM CustomObject WHERE DeveloperName LIKE '%{search_term}%' "
                f"AND ManageableState = 'unmanaged' LIMIT 10"
            )
            results['custom_metadata_types'] = [_strip_attributes(r) for r in cmdt_result.get('records', [])]
        except Exception as e:
            results['custom_metadata_types'] = {'error': str(e)}

        # Search CustomSettings instances via REST
        try:
            cs_result = await client.query(
                f"SELECT Id, Name FROM CustomSetting__c WHERE Name LIKE '%{search_term}%' LIMIT 10"
            )
            results['custom_settings'] = [_strip_attributes(r) for r in cs_result.get('records', [])]
        except Exception:
            # Custom settings vary by org — silently skip if not found
            results['custom_settings'] = []

        return json.dumps(results, indent=2, default=str)

    # ─────────────────────────────────────────────────────────────────────────

    return [
        run_soql_query,
        run_soql_query_all,
        describe_salesforce_object,
        list_salesforce_objects,
        search_metadata_by_name,
    ]


# ─── HELPERS ──────────────────────────────────────────────────────────────────

def _strip_attributes(record: dict) -> dict:
    """Remove Salesforce internal 'attributes' key from records."""
    return {k: v for k, v in record.items() if k != 'attributes'}