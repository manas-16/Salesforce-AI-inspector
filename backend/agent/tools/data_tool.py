# ─── Salesforce (AI)nspector — agent/tools/data_tool.py ──────────────────────
# LangChain tools for record-level CRUD and bulk data operations.
# Covers: create, update, delete, get single record, bulk create/update from CSV.

import csv
import io
import json
import logging
from langchain_core.tools import tool
from salesforce.client import SalesforceClient

logger = logging.getLogger(__name__)


def make_data_tools(session_id: str, instance_url: str, is_production: bool):
    """
    Factory — returns data tools bound to this request's session.
    """

    client = SalesforceClient(session_id, instance_url)

    def _prod_block(operation: str) -> str:
        return json.dumps({
            'error': f'PRODUCTION ORG DETECTED — {operation} is blocked. '
                     f'Write operations are only permitted in sandbox orgs.'
        })

    # ─────────────────────────────────────────────────────────────────────────

    @tool
    async def create_record(sobject: str, fields: dict) -> str:
        """
        Create a new record on any Salesforce SObject.

        Before calling this tool, always use describe_salesforce_object to check:
        - Mandatory fields
        - Valid picklist values
        - Field API names (not labels)

        Args:
            sobject: API name of the SObject.
                     Examples: Account, Contact, Opportunity, Case, Lead,
                               MyCustomObject__c
            fields: Dictionary of field API names to values.
                    Example: {
                        "Name": "Acme Corp",
                        "Industry": "Technology",
                        "BillingCity": "San Francisco"
                    }

        Returns:
            JSON with created record ID or error.
        """
        if is_production:
            return _prod_block('create_record')

        logger.info(f'Creating {sobject} record')
        try:
            result = await client.create_record(sobject, fields)
            return json.dumps({
                'success':   True,
                'sobject':   sobject,
                'record_id': result.get('id'),
                'message':   f'{sobject} record created successfully.',
            })
        except Exception as e:
            logger.error(f'Create record failed: {e}')
            return json.dumps({'error': str(e)})

    # ─────────────────────────────────────────────────────────────────────────

    @tool
    async def update_record(sobject: str, record_id: str, fields: dict) -> str:
        """
        Update fields on an existing Salesforce record.

        Args:
            sobject: API name of the SObject. Example: Account, Contact
            record_id: Salesforce record ID (15 or 18-char).
                       Use run_soql_query to find it first.
            fields: Dictionary of field API names to new values.
                    Only include fields you want to change.
                    Example: {"StageName": "Closed Won", "CloseDate": "2025-12-31"}

        Returns:
            JSON with success confirmation or error.
        """
        if is_production:
            return _prod_block('update_record')

        logger.info(f'Updating {sobject} record {record_id}')
        try:
            await client.update_record(sobject, record_id, fields)
            return json.dumps({
                'success':        True,
                'sobject':        sobject,
                'record_id':      record_id,
                'updated_fields': list(fields.keys()),
                'message':        f'{sobject} record {record_id} updated successfully.',
            })
        except Exception as e:
            logger.error(f'Update record failed: {e}')
            return json.dumps({'error': str(e)})

    # ─────────────────────────────────────────────────────────────────────────

    @tool
    async def delete_record(sobject: str, record_id: str) -> str:
        """
        Delete a Salesforce record. This is a destructive operation.

        IMPORTANT: Always confirm with the user before deleting records.
        Query the record first using get_record or run_soql_query to
        show the user what will be deleted.

        Args:
            sobject: API name of the SObject. Example: Contact, Lead
            record_id: Salesforce record ID (15 or 18-char).

        Returns:
            JSON with success confirmation or error.
        """
        if is_production:
            return _prod_block('delete_record')

        logger.info(f'Deleting {sobject} record {record_id}')
        try:
            await client.delete_record(sobject, record_id)
            return json.dumps({
                'success':   True,
                'sobject':   sobject,
                'record_id': record_id,
                'message':   f'{sobject} record {record_id} deleted successfully.',
            })
        except Exception as e:
            logger.error(f'Delete record failed: {e}')
            return json.dumps({'error': str(e)})

    # ─────────────────────────────────────────────────────────────────────────

    @tool
    async def get_record(sobject: str, record_id: str, fields: list[str] = None) -> str:
        """
        Retrieve a single Salesforce record by its ID.
        Use this to verify a record before updating or deleting it.

        Args:
            sobject: API name of the SObject. Example: Account, Case
            record_id: Salesforce record ID (15 or 18-char).
            fields: Optional list of specific field API names to retrieve.
                    If not provided, returns all fields.
                    Example: ["Name", "Email", "Phone", "AccountId"]

        Returns:
            JSON with the record's field values or error.
        """
        logger.info(f'Getting {sobject} record {record_id}')
        try:
            result = await client.get_record(sobject, record_id, fields)
            clean  = {k: v for k, v in result.items() if k != 'attributes'}
            return json.dumps({
                'success': True,
                'sobject': sobject,
                'record':  clean,
            }, indent=2, default=str)
        except Exception as e:
            logger.error(f'Get record failed: {e}')
            return json.dumps({'error': str(e)})

    # ─────────────────────────────────────────────────────────────────────────

    @tool
    async def bulk_create_from_csv(sobject: str, csv_content: str) -> str:
        """
        Create multiple records on any SObject from CSV file content.
        Uses Salesforce Bulk API 2.0 for efficient large-scale inserts.

        CSV column headers must match the Salesforce field API names exactly.
        Use describe_salesforce_object first to verify correct API names.

        Args:
            sobject: API name of the SObject to create records on.
                     Example: Contact, Lead, Account, MyCustomObject__c
            csv_content: Raw CSV content as a string.
                         First row must be field API names (column headers).
                         Example:
                         FirstName,LastName,Email,AccountId
                         John,Doe,john@test.com,001XX000003GYWQ

        Returns:
            JSON with job summary — records processed, failed count,
            and job ID for reference.
        """
        if is_production:
            return _prod_block('bulk_create_from_csv')

        logger.info(f'Bulk creating {sobject} records from CSV')
        try:
            records = _parse_csv_to_records(csv_content)
            if isinstance(records, dict) and 'error' in records:
                return json.dumps(records)

            if not records:
                return json.dumps({'error': 'CSV contains no data rows.'})

            logger.info(f'Parsed {len(records)} records for bulk insert')
            result = await client.bulk_create(sobject, records)

            return json.dumps({
                'success':           result.get('success', False),
                'sobject':           sobject,
                'records_processed': result.get('records_processed', 0),
                'records_failed':    result.get('records_failed', 0),
                'job_id':            result.get('job_id'),
                'message':           f'Bulk insert complete. '
                                     f'{result.get("records_processed", 0)} records created, '
                                     f'{result.get("records_failed", 0)} failed.',
            }, indent=2)

        except Exception as e:
            logger.error(f'Bulk create failed: {e}')
            return json.dumps({'error': str(e)})

    # ─────────────────────────────────────────────────────────────────────────

    @tool
    async def bulk_update_from_csv(sobject: str, csv_content: str) -> str:
        """
        Update multiple existing records on any SObject from CSV file content.
        Uses Salesforce Bulk API 2.0.

        CSV must include an 'Id' column with valid Salesforce record IDs.
        Other columns are fields to update — use API names not labels.

        Args:
            sobject: API name of the SObject to update records on.
            csv_content: Raw CSV content as a string.
                         Must include 'Id' column.
                         Example:
                         Id,StageName,CloseDate
                         006XX000004CDRQ,Closed Won,2025-12-31
                         006XX000004CDRS,Negotiation,2025-11-30

        Returns:
            JSON with job summary — records processed, failed count.
        """
        if is_production:
            return _prod_block('bulk_update_from_csv')

        logger.info(f'Bulk updating {sobject} records from CSV')
        try:
            records = _parse_csv_to_records(csv_content)
            if isinstance(records, dict) and 'error' in records:
                return json.dumps(records)

            if not records:
                return json.dumps({'error': 'CSV contains no data rows.'})

            # Validate Id column exists
            if 'Id' not in records[0]:
                return json.dumps({
                    'error': 'CSV must contain an "Id" column for bulk updates.',
                    'columns_found': list(records[0].keys()),
                })

            logger.info(f'Parsed {len(records)} records for bulk update')
            result = await client.bulk_update(sobject, records)

            return json.dumps({
                'success':           result.get('success', False),
                'sobject':           sobject,
                'records_processed': result.get('records_processed', 0),
                'records_failed':    result.get('records_failed', 0),
                'job_id':            result.get('job_id'),
                'message':           f'Bulk update complete. '
                                     f'{result.get("records_processed", 0)} records updated, '
                                     f'{result.get("records_failed", 0)} failed.',
            }, indent=2)

        except Exception as e:
            logger.error(f'Bulk update failed: {e}')
            return json.dumps({'error': str(e)})

    # ─────────────────────────────────────────────────────────────────────────

    @tool
    async def create_related_records(parent_sobject: str, parent_fields: dict,
                                     children: list[dict]) -> str:
        """
        Create a parent record and multiple related child records in sequence.
        Use this for test data creation or setting up complete record hierarchies.

        Example use cases:
        - Account + Contacts + Opportunities
        - Case + CaseComments
        - Order + OrderItems

        Args:
            parent_sobject: API name of the parent SObject.
                            Example: Account
            parent_fields: Field values for the parent record.
                           Example: {"Name": "Test Corp", "Industry": "Technology"}
            children: List of child record definitions. Each item is a dict with:
                      - sobject: child SObject API name (e.g. Contact)
                      - fields: field values dict
                      - parent_field: the lookup field on the child that links to parent
                      Example: [
                          {
                              "sobject": "Contact",
                              "parent_field": "AccountId",
                              "fields": {"FirstName": "John", "LastName": "Doe"}
                          }
                      ]

        Returns:
            JSON with parent record ID and all created child record IDs.
        """
        if is_production:
            return _prod_block('create_related_records')

        logger.info(f'Creating {parent_sobject} with {len(children)} child records')
        created = {'parent': None, 'children': []}

        try:
            # Create parent
            parent_result = await client.create_record(parent_sobject, parent_fields)
            parent_id = parent_result.get('id')
            created['parent'] = {
                'sobject':   parent_sobject,
                'record_id': parent_id,
            }
            logger.info(f'Created parent {parent_sobject}: {parent_id}')

            # Create each child
            for child in children:
                child_sobject      = child.get('sobject')
                child_fields       = child.get('fields', {})
                parent_field       = child.get('parent_field')

                # Link to parent
                if parent_field:
                    child_fields[parent_field] = parent_id

                try:
                    child_result = await client.create_record(child_sobject, child_fields)
                    created['children'].append({
                        'sobject':   child_sobject,
                        'record_id': child_result.get('id'),
                        'success':   True,
                    })
                    logger.info(f'Created child {child_sobject}: {child_result.get("id")}')

                except Exception as child_err:
                    created['children'].append({
                        'sobject': child_sobject,
                        'success': False,
                        'error':   str(child_err),
                    })

            success_count = sum(1 for c in created['children'] if c.get('success'))
            fail_count    = len(created['children']) - success_count

            return json.dumps({
                'success':        True,
                'parent_id':      parent_id,
                'children_total': len(children),
                'children_ok':    success_count,
                'children_failed': fail_count,
                'records':        created,
            }, indent=2)

        except Exception as e:
            logger.error(f'Create related records failed: {e}')
            return json.dumps({
                'error':   str(e),
                'created': created,
            })

    # ─────────────────────────────────────────────────────────────────────────

    return [
        create_record,
        update_record,
        delete_record,
        get_record,
        bulk_create_from_csv,
        bulk_update_from_csv,
        create_related_records,
    ]


# ─── HELPERS ──────────────────────────────────────────────────────────────────

def _parse_csv_to_records(csv_content: str) -> list[dict] | dict:
    """Parse raw CSV string into a list of dicts. Returns error dict on failure."""
    try:
        reader  = csv.DictReader(io.StringIO(csv_content.strip()))
        records = [
            {k.strip(): v.strip() for k, v in row.items() if k}
            for row in reader
        ]
        return records
    except Exception as e:
        return {'error': f'Failed to parse CSV: {str(e)}'}