# ─── Salesforce (AI)nspector — agent/tools/metadata_tool.py ──────────────────
# LangChain tools for Salesforce metadata management.
# Covers: create custom fields, custom objects, picklist values,
# validation rules, and reading existing metadata.
# Uses Tooling API for all write operations.

import json
import logging
from langchain_core.tools import tool
from salesforce.client import SalesforceClient

logger = logging.getLogger(__name__)

# Valid Salesforce field types for reference
VALID_FIELD_TYPES = [
    'Text', 'TextArea', 'LongTextArea', 'RichTextArea',
    'Number', 'Currency', 'Percent',
    'Checkbox', 'Date', 'DateTime', 'Time',
    'Email', 'Phone', 'Url',
    'Picklist', 'MultiselectPicklist',
    'Lookup', 'MasterDetail',
    'AutoNumber', 'Formula',
    'EncryptedText', 'ExternalLookup',
]


def make_metadata_tools(session_id: str, instance_url: str, is_production: bool):
    """
    Factory — returns metadata tools bound to this request's session.
    """

    client = SalesforceClient(session_id, instance_url)

    def _prod_block(operation: str) -> str:
        return json.dumps({
            'error': f'PRODUCTION ORG DETECTED — {operation} is blocked. '
                     f'Write operations are only permitted in sandbox orgs.'
        })

    # ─────────────────────────────────────────────────────────────────────────

    @tool
    async def create_custom_field(
        object_api_name: str,
        field_label: str,
        field_type: str,
        length: int = None,
        decimal_places: int = None,
        required: bool = False,
        unique: bool = False,
        description: str = None,
        picklist_values: list[str] = None,
        formula: str = None,
        reference_to: str = None,
    ) -> str:
        """
        Create a new custom field on a Salesforce object using the Tooling API.

        Always call describe_salesforce_object FIRST to:
        - Check if a field with the same name already exists
        - Verify the object API name is correct

        Supported field types:
        Text, TextArea, LongTextArea, Number, Currency, Percent,
        Checkbox, Date, DateTime, Email, Phone, Url, Picklist,
        MultiselectPicklist, Lookup, Formula, AutoNumber

        Args:
            object_api_name: API name of the parent object.
                             Examples: Account, Contact, MyObject__c
            field_label: Human-readable label for the field.
                         The API name will be auto-derived as Label__c.
                         Example: 'Customer Tier' -> 'Customer_Tier__c'
            field_type: Salesforce field type. Must be one of the supported types above.
            length: Required for Text (max chars), LongTextArea, Number.
                    Text: 1-255. LongTextArea: up to 131072.
            decimal_places: Required for Number, Currency, Percent fields.
            required: Whether the field is mandatory. Default False.
            unique: Whether the field enforces uniqueness. Default False.
            description: Optional description for the field metadata.
            picklist_values: Required for Picklist and MultiselectPicklist fields.
                             Example: ['Hot', 'Warm', 'Cold']
            formula: Required for Formula fields. Salesforce formula syntax.
            reference_to: Required for Lookup fields. API name of the referenced object.
                          Example: 'Account', 'Contact'

        Returns:
            JSON with created field ID and API name, or error.
        """
        if is_production:
            return _prod_block('create_custom_field')

        logger.info(f'Creating {field_type} field "{field_label}" on {object_api_name}')

        # Validate field type
        if field_type not in VALID_FIELD_TYPES:
            return json.dumps({
                'error': f'Invalid field type: {field_type}',
                'valid_types': VALID_FIELD_TYPES,
            })

        # Derive API name from label
        field_api_name = _label_to_api_name(field_label)

        try:
            # Build metadata dict based on field type
            metadata = _build_field_metadata(
                field_type=field_type,
                field_label=field_label,
                length=length,
                decimal_places=decimal_places,
                required=required,
                unique=unique,
                description=description,
                picklist_values=picklist_values,
                formula=formula,
                reference_to=reference_to,
            )

            if 'error' in metadata:
                return json.dumps(metadata)

            body = {
                'FullName': f'{object_api_name}.{field_api_name}',
                'Metadata': metadata,
            }

            result = await client.tooling_create('CustomField', body)

            return json.dumps({
                'success':       True,
                'object':        object_api_name,
                'field_label':   field_label,
                'field_api_name': field_api_name,
                'field_type':    field_type,
                'record_id':     result.get('id'),
                'message':       f'Field "{field_label}" ({field_api_name}) created on {object_api_name}.',
            })

        except Exception as e:
            logger.error(f'Create custom field failed: {e}')
            return json.dumps({'error': str(e)})

    # ─────────────────────────────────────────────────────────────────────────

    @tool
    async def create_custom_fields_from_csv(object_api_name: str, csv_content: str) -> str:
        """
        Create multiple custom fields on an object from CSV file content.

        CSV must have these columns (case-sensitive):
        - Label        (required) — field label
        - Type         (required) — field type
        - Length       (optional) — for Text/Number fields
        - DecimalPlaces (optional) — for Number/Currency/Percent
        - Required     (optional) — true/false
        - Description  (optional) — field description
        - PicklistValues (optional) — semicolon-separated for Picklist fields
                                      Example: "Hot;Warm;Cold"

        Args:
            object_api_name: API name of the object to add fields to.
            csv_content: Raw CSV string with field definitions.

        Returns:
            JSON summary with created fields, skipped (already exist), and failed.
        """
        if is_production:
            return _prod_block('create_custom_fields_from_csv')

        logger.info(f'Creating fields on {object_api_name} from CSV')

        try:
            import csv as csv_module
            import io
            reader = csv_module.DictReader(io.StringIO(csv_content.strip()))
            rows   = list(reader)
        except Exception as e:
            return json.dumps({'error': f'Failed to parse CSV: {str(e)}'})

        if not rows:
            return json.dumps({'error': 'CSV is empty.'})

        required_cols = {'Label', 'Type'}
        missing = required_cols - set(rows[0].keys())
        if missing:
            return json.dumps({
                'error': f'CSV missing required columns: {missing}',
                'columns_found': list(rows[0].keys()),
                'required_columns': ['Label', 'Type', 'Length', 'DecimalPlaces',
                                     'Required', 'Description', 'PicklistValues'],
            })

        # Get existing fields to detect conflicts
        try:
            describe = await client.describe_object(object_api_name)
            existing_names = {
                f['name'].lower() for f in describe.get('fields', [])
            }
        except Exception:
            existing_names = set()

        created = []
        skipped = []
        failed  = []

        for row in rows:
            label  = row.get('Label', '').strip()
            ftype  = row.get('Type', '').strip()

            if not label or not ftype:
                failed.append({'row': row, 'reason': 'Missing Label or Type'})
                continue

            api_name = _label_to_api_name(label)

            # Check for existing field
            if api_name.lower() in existing_names:
                skipped.append({'label': label, 'api_name': api_name, 'reason': 'Field already exists'})
                continue

            # Parse picklist values
            pv_raw   = row.get('PicklistValues', '').strip()
            pv_list  = [v.strip() for v in pv_raw.split(';') if v.strip()] if pv_raw else None

            metadata = _build_field_metadata(
                field_type     = ftype,
                field_label    = label,
                length         = int(row['Length']) if row.get('Length', '').strip().isdigit() else None,
                decimal_places = int(row['DecimalPlaces']) if row.get('DecimalPlaces', '').strip().isdigit() else None,
                required       = row.get('Required', '').strip().lower() == 'true',
                description    = row.get('Description', '').strip() or None,
                picklist_values= pv_list,
            )

            if 'error' in metadata:
                failed.append({'label': label, 'reason': metadata['error']})
                continue

            try:
                result = await client.tooling_create('CustomField', {
                    'FullName': f'{object_api_name}.{api_name}',
                    'Metadata': metadata,
                })
                created.append({
                    'label':    label,
                    'api_name': api_name,
                    'type':     ftype,
                    'id':       result.get('id'),
                })
            except Exception as e:
                failed.append({'label': label, 'api_name': api_name, 'reason': str(e)})

        return json.dumps({
            'summary': {
                'total_in_csv': len(rows),
                'created':      len(created),
                'skipped':      len(skipped),
                'failed':       len(failed),
            },
            'created': created,
            'skipped': skipped,
            'failed':  failed,
        }, indent=2)

    # ─────────────────────────────────────────────────────────────────────────

    @tool
    async def create_custom_object(
        object_label: str,
        plural_label: str,
        description: str = None,
        name_field_label: str = 'Name',
        name_field_type: str = 'Text',
    ) -> str:
        """
        Create a new custom object in the Salesforce org.

        Args:
            object_label: Singular label for the object.
                          Example: 'Project Task'
            plural_label: Plural label for the object.
                          Example: 'Project Tasks'
            description: Optional description for the object.
            name_field_label: Label for the standard Name field. Default: 'Name'
            name_field_type: Type for the Name field — 'Text' or 'AutoNumber'.
                             Default: 'Text'

        Returns:
            JSON with the created object API name and ID, or error.
        """
        if is_production:
            return _prod_block('create_custom_object')

        api_name = _label_to_api_name(object_label)
        logger.info(f'Creating custom object: {api_name}')

        try:
            metadata = {
                'label':              object_label,
                'pluralLabel':        plural_label,
                'nameField': {
                    'label': name_field_label,
                    'type':  name_field_type,
                },
                'deploymentStatus': 'Deployed',
                'sharingModel':     'ReadWrite',
            }

            if description:
                metadata['description'] = description

            result = await client.tooling_create('CustomObject', {
                'FullName': api_name,
                'Metadata': metadata,
            })

            return json.dumps({
                'success':    True,
                'api_name':   api_name,
                'label':      object_label,
                'record_id':  result.get('id'),
                'message':    f'Custom object "{object_label}" ({api_name}) created successfully.',
            })

        except Exception as e:
            logger.error(f'Create custom object failed: {e}')
            return json.dumps({'error': str(e)})

    # ─────────────────────────────────────────────────────────────────────────

    @tool
    async def add_picklist_values(
        object_api_name: str,
        field_api_name: str,
        new_values: list[str],
    ) -> str:
        """
        Add new values to an existing Picklist or MultiselectPicklist field.

        Args:
            object_api_name: API name of the object. Example: Lead, Opportunity
            field_api_name: API name of the picklist field. Example: LeadSource__c
            new_values: List of new picklist values to add.
                        Example: ['Digital', 'Referral - Partner', 'Cold Call']

        Returns:
            JSON with success confirmation or error.
        """
        if is_production:
            return _prod_block('add_picklist_values')

        logger.info(f'Adding picklist values to {object_api_name}.{field_api_name}')

        try:
            # Fetch existing field metadata
            result = await client.tooling_query(
                f"SELECT Id, Metadata FROM CustomField "
                f"WHERE TableEnumOrId = '{object_api_name}' "
                f"AND DeveloperName = '{field_api_name.replace('__c', '')}' LIMIT 1"
            )
            records = result.get('records', [])
            if not records:
                return json.dumps({
                    'error': f'Field {field_api_name} not found on {object_api_name}.'
                })

            field_id = records[0]['Id']
            existing_metadata = records[0].get('Metadata', {})
            existing_values   = [
                v['fullName']
                for v in existing_metadata.get('valueSet', {}).get('valueSetDefinition', {}).get('value', [])
            ]

            # Merge existing + new, deduplicate
            all_values   = existing_values + [v for v in new_values if v not in existing_values]
            value_objects = [
                {'fullName': v, 'label': v, 'default': False, 'isActive': True}
                for v in all_values
            ]

            updated_metadata = {
                **existing_metadata,
                'valueSet': {
                    'restricted': existing_metadata.get('valueSet', {}).get('restricted', False),
                    'valueSetDefinition': {'sorted': False, 'value': value_objects},
                },
            }

            await client.tooling_update('CustomField', field_id, {'Metadata': updated_metadata})

            return json.dumps({
                'success':       True,
                'field':         field_api_name,
                'object':        object_api_name,
                'added_values':  [v for v in new_values if v not in existing_values],
                'skipped_values': [v for v in new_values if v in existing_values],
                'message':       f'Picklist values updated on {object_api_name}.{field_api_name}.',
            })

        except Exception as e:
            logger.error(f'Add picklist values failed: {e}')
            return json.dumps({'error': str(e)})

    # ─────────────────────────────────────────────────────────────────────────

    @tool
    async def create_validation_rule(
        object_api_name: str,
        rule_name: str,
        error_condition_formula: str,
        error_message: str,
        description: str = None,
        active: bool = True,
    ) -> str:
        """
        Create a validation rule on a Salesforce object.

        The error_condition_formula uses Salesforce formula syntax.
        The rule fires (blocks save) when the formula evaluates to TRUE.

        Args:
            object_api_name: API name of the object. Example: Account, Lead
            rule_name: API name for the validation rule (no spaces, no __c).
                       Example: 'Require_Revenue_For_Enterprise'
            error_condition_formula: Salesforce formula that returns TRUE when invalid.
                       Example: "AND(ISPICKVAL(Type, 'Enterprise'), Revenue__c = 0)"
            error_message: Message shown to the user when validation fails.
                       Example: 'Enterprise accounts must have a Revenue value.'
            description: Optional description of the rule's purpose.
            active: Whether the rule is active immediately. Default True.

        Returns:
            JSON with created rule ID or error.
        """
        if is_production:
            return _prod_block('create_validation_rule')

        logger.info(f'Creating validation rule {rule_name} on {object_api_name}')

        try:
            metadata = {
                'active':                 active,
                'errorConditionFormula':  error_condition_formula,
                'errorMessage':           error_message,
            }

            if description:
                metadata['description'] = description

            result = await client.tooling_create('ValidationRule', {
                'FullName': f'{object_api_name}.{rule_name}',
                'Metadata': metadata,
            })

            return json.dumps({
                'success':     True,
                'object':      object_api_name,
                'rule_name':   rule_name,
                'active':      active,
                'record_id':   result.get('id'),
                'message':     f'Validation rule "{rule_name}" created on {object_api_name}.',
            })

        except Exception as e:
            logger.error(f'Create validation rule failed: {e}')
            return json.dumps({'error': str(e)})

    # ─────────────────────────────────────────────────────────────────────────

    @tool
    async def list_validation_rules(object_api_name: str) -> str:
        """
        List all validation rules on a Salesforce object.
        Use this before creating a new rule to check for conflicts,
        or during RCA to understand what rules could be blocking a save.

        Args:
            object_api_name: API name of the object. Example: Account, Lead

        Returns:
            JSON list of validation rules with name, active status, formula, and message.
        """
        logger.info(f'Listing validation rules for {object_api_name}')
        try:
            result = await client.tooling_query(
                f"SELECT Id, ValidationName, Active, ErrorMessage, Description "
                f"FROM ValidationRule WHERE EntityDefinition.QualifiedApiName = '{object_api_name}'"
            )
            rules = [
                {k: v for k, v in r.items() if k != 'attributes'}
                for r in result.get('records', [])
            ]
            return json.dumps({
                'object': object_api_name,
                'total':  len(rules),
                'rules':  rules,
            }, indent=2)
        except Exception as e:
            logger.error(f'List validation rules failed: {e}')
            return json.dumps({'error': str(e)})

    # ─────────────────────────────────────────────────────────────────────────

    @tool
    async def list_custom_fields(object_api_name: str) -> str:
        """
        List all custom fields on a Salesforce object via the Tooling API.
        Returns richer metadata than describe_salesforce_object —
        includes field IDs needed for updates and deployment status.

        Args:
            object_api_name: API name of the object. Example: Account, MyObject__c

        Returns:
            JSON list of custom fields with ID, name, type, and label.
        """
        logger.info(f'Listing custom fields for {object_api_name}')
        try:
            result = await client.tooling_query(
                f"SELECT Id, DeveloperName, MasterLabel, DataType "
                f"FROM CustomField WHERE TableEnumOrId = '{object_api_name}'"
            )
            fields = [
                {k: v for k, v in r.items() if k != 'attributes'}
                for r in result.get('records', [])
            ]
            return json.dumps({
                'object':      object_api_name,
                'total':       len(fields),
                'fields':      fields,
            }, indent=2)
        except Exception as e:
            logger.error(f'List custom fields failed: {e}')
            return json.dumps({'error': str(e)})

    # ─────────────────────────────────────────────────────────────────────────

    return [
        create_custom_field,
        create_custom_fields_from_csv,
        create_custom_object,
        add_picklist_values,
        create_validation_rule,
        list_validation_rules,
        list_custom_fields,
    ]


# ─── HELPERS ──────────────────────────────────────────────────────────────────

def _label_to_api_name(label: str) -> str:
    """Convert a field label to a Salesforce API name with __c suffix."""
    import re
    # Replace spaces and special chars with underscore
    name = re.sub(r'[^a-zA-Z0-9]', '_', label.strip())
    # Remove consecutive underscores
    name = re.sub(r'_+', '_', name)
    # Strip leading/trailing underscores
    name = name.strip('_')
    return f'{name}__c'


def _build_field_metadata(
    field_type: str,
    field_label: str,
    length: int = None,
    decimal_places: int = None,
    required: bool = False,
    unique: bool = False,
    description: str = None,
    picklist_values: list[str] = None,
    formula: str = None,
    reference_to: str = None,
) -> dict:
    """Build the Metadata dict for a CustomField Tooling API call."""

    metadata = {
        'label':    field_label,
        'type':     field_type,
        'required': required,
    }

    if description:
        metadata['description'] = description

    # Type-specific validation and metadata
    if field_type == 'Text':
        metadata['length'] = length or 255

    elif field_type == 'TextArea':
        pass  # no extra params needed

    elif field_type in ('LongTextArea', 'RichTextArea'):
        metadata['length']      = length or 32768
        metadata['visibleLines'] = 5

    elif field_type in ('Number', 'Currency', 'Percent'):
        metadata['precision']      = (length or 18)
        metadata['scale']          = decimal_places if decimal_places is not None else 2

    elif field_type == 'Checkbox':
        metadata['defaultValue'] = False

    elif field_type in ('Picklist', 'MultiselectPicklist'):
        if not picklist_values:
            return {'error': f'{field_type} field requires picklist_values to be provided.'}
        value_objects = [
            {'fullName': v, 'label': v, 'default': False, 'isActive': True}
            for v in picklist_values
        ]
        metadata['valueSet'] = {
            'restricted': False,
            'valueSetDefinition': {'sorted': False, 'value': value_objects},
        }
        if field_type == 'MultiselectPicklist':
            metadata['visibleLines'] = 4

    elif field_type == 'Formula':
        if not formula:
            return {'error': 'Formula field requires a formula expression.'}
        metadata['formula']      = formula
        metadata['formulaTreatBlanksAs'] = 'BlankAsZero'

    elif field_type == 'Lookup':
        if not reference_to:
            return {'error': 'Lookup field requires reference_to (the referenced object API name).'}
        metadata['referenceTo']    = reference_to
        metadata['relationshipName'] = _label_to_api_name(field_label).replace('__c', '_r')

    elif field_type == 'AutoNumber':
        metadata['startingNumber']  = 1
        metadata['displayFormat']   = 'AN-{0000}'

    # unique only applies to Text/Number/Email
    if unique and field_type in ('Text', 'Number', 'Email', 'Phone'):
        metadata['unique'] = True

    return metadata