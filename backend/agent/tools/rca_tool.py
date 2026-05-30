# ─── Salesforce (AI)nspector — agent/tools/rca_tool.py ───────────────────────
# LangChain tools for root cause analysis.
# Performs recursive dependency search across the entire org —
# triggers, flows, validation rules, workflow rules, assignment rules,
# process builders, and Apex classes — to identify what is causing a bug.

import json
import logging
import re
from langchain_core.tools import tool
from salesforce.client import SalesforceClient

logger = logging.getLogger(__name__)


def make_rca_tools(session_id: str, instance_url: str):
    """
    Factory — returns RCA tools bound to this request's session.
    All RCA tools are read-only.
    """

    client = SalesforceClient(session_id, instance_url)

    # ─────────────────────────────────────────────────────────────────────────

    @tool
    async def find_all_automation_on_object(object_api_name: str) -> str:
        """
        Find ALL automation that runs on a given Salesforce object —
        Apex triggers, Flows, Workflow Rules, Process Builders, and Assignment Rules.

        Use this as the first step in any RCA investigation.
        Once you know what automation exists, you can drill into each one.

        Args:
            object_api_name: API name of the object. Example: Account, Lead, Order__c

        Returns:
            JSON map of all automation types on the object with names,
            active status, and trigger conditions.
        """
        logger.info(f'Finding all automation on: {object_api_name}')
        results = {}

        # Apex Triggers
        try:
            trigger_result = await client.tooling_query(
                f"SELECT Id, Name, Status, TableEnumOrId, UsageBeforeInsert, "
                f"UsageAfterInsert, UsageBeforeUpdate, UsageAfterUpdate, "
                f"UsageBeforeDelete, UsageAfterDelete "
                f"FROM ApexTrigger WHERE TableEnumOrId = '{object_api_name}'"
            )
            results['apex_triggers'] = [
                {
                    'name':          r.get('Name'),
                    'id':            r.get('Id'),
                    'status':        r.get('Status'),
                    'before_insert': r.get('UsageBeforeInsert'),
                    'after_insert':  r.get('UsageAfterInsert'),
                    'before_update': r.get('UsageBeforeUpdate'),
                    'after_update':  r.get('UsageAfterUpdate'),
                    'before_delete': r.get('UsageBeforeDelete'),
                    'after_delete':  r.get('UsageAfterDelete'),
                }
                for r in trigger_result.get('records', [])
            ]
        except Exception as e:
            results['apex_triggers'] = {'error': str(e)}

        # Flows (Record-Triggered)
        try:
            flow_result = await client.tooling_query(
                f"SELECT Id, ApiName, Status, ProcessType, TriggerType, Description "
                f"FROM Flow "
                f"WHERE TriggerType IN ('RecordAfterSave', 'RecordBeforeSave') "
                f"AND Status = 'Active'"
            )
            # Filter by object — Flow metadata needed for exact match
            all_flows = flow_result.get('records', [])
            # Return all active record-triggered flows — agent will filter by context
            results['record_triggered_flows'] = [
                {
                    'name':         r.get('ApiName'),
                    'id':           r.get('Id'),
                    'status':       r.get('Status'),
                    'process_type': r.get('ProcessType'),
                    'trigger_type': r.get('TriggerType'),
                    'description':  r.get('Description'),
                }
                for r in all_flows
            ]
        except Exception as e:
            results['record_triggered_flows'] = {'error': str(e)}

        # Workflow Rules (legacy)
        try:
            wf_result = await client.tooling_query(
                f"SELECT Id, Name, Active, Description "
                f"FROM WorkflowRule WHERE TableEnumOrId = '{object_api_name}'"
            )
            results['workflow_rules'] = [
                {k: v for k, v in r.items() if k != 'attributes'}
                for r in wf_result.get('records', [])
            ]
        except Exception as e:
            results['workflow_rules'] = {'error': str(e)}

        # Validation Rules
        try:
            vr_result = await client.tooling_query(
                f"SELECT Id, ValidationName, Active, Description, ErrorMessage "
                f"FROM ValidationRule "
                f"WHERE EntityDefinition.QualifiedApiName = '{object_api_name}'"
            )
            results['validation_rules'] = [
                {k: v for k, v in r.items() if k != 'attributes'}
                for r in vr_result.get('records', [])
            ]
        except Exception as e:
            results['validation_rules'] = {'error': str(e)}

        # Assignment Rules
        try:
            ar_result = await client.tooling_query(
                f"SELECT Id, Name, Active "
                f"FROM AssignmentRule WHERE SobjectType = '{object_api_name}'"
            )
            results['assignment_rules'] = [
                {k: v for k, v in r.items() if k != 'attributes'}
                for r in ar_result.get('records', [])
            ]
        except Exception as e:
            results['assignment_rules'] = {'error': str(e)}

        # Summary counts
        summary = {}
        for key, val in results.items():
            if isinstance(val, list):
                summary[key] = len(val)
            else:
                summary[key] = 'error'

        return json.dumps({
            'object':    object_api_name,
            'summary':   summary,
            'automation': results,
        }, indent=2, default=str)

    # ─────────────────────────────────────────────────────────────────────────

    @tool
    async def get_apex_trigger_body(trigger_name: str) -> str:
        """
        Read the full source code of an Apex trigger.
        Use this after finding triggers via find_all_automation_on_object
        to understand exactly what logic runs and on what fields.

        Args:
            trigger_name: Name of the trigger. Example: 'OrderTrigger', 'AccountTrigger'

        Returns:
            JSON with trigger name, object, events, and full source code.
        """
        logger.info(f'Reading trigger body: {trigger_name}')
        try:
            result = await client.tooling_query(
                f"SELECT Id, Name, Body, TableEnumOrId, Status, "
                f"UsageBeforeInsert, UsageAfterInsert, UsageBeforeUpdate, UsageAfterUpdate "
                f"FROM ApexTrigger WHERE Name = '{trigger_name}' LIMIT 1"
            )
            records = result.get('records', [])
            if not records:
                return json.dumps({'error': f'Trigger not found: {trigger_name}'})

            r = records[0]
            return json.dumps({
                'name':          r.get('Name'),
                'object':        r.get('TableEnumOrId'),
                'status':        r.get('Status'),
                'events': {
                    'before_insert': r.get('UsageBeforeInsert'),
                    'after_insert':  r.get('UsageAfterInsert'),
                    'before_update': r.get('UsageBeforeUpdate'),
                    'after_update':  r.get('UsageAfterUpdate'),
                },
                'body':          r.get('Body', ''),
            }, indent=2)
        except Exception as e:
            logger.error(f'Get trigger body failed: {e}')
            return json.dumps({'error': str(e)})

    # ─────────────────────────────────────────────────────────────────────────

    @tool
    async def get_apex_class_body(class_name: str) -> str:
        """
        Read the full source code of an Apex class.
        Use this when a user uploads a class name or when a trigger
        delegates to a handler class you need to inspect.

        Args:
            class_name: Name of the Apex class. Example: 'OrderHandler', 'LeadService'

        Returns:
            JSON with class name, API version, status, and full source code.
        """
        logger.info(f'Reading Apex class: {class_name}')
        try:
            result = await client.tooling_query(
                f"SELECT Id, Name, Body, ApiVersion, Status "
                f"FROM ApexClass WHERE Name = '{class_name}' LIMIT 1"
            )
            records = result.get('records', [])
            if not records:
                return json.dumps({'error': f'Apex class not found: {class_name}'})

            r = records[0]
            return json.dumps({
                'name':        r.get('Name'),
                'api_version': r.get('ApiVersion'),
                'status':      r.get('Status'),
                'body':        r.get('Body', ''),
            }, indent=2)
        except Exception as e:
            logger.error(f'Get Apex class body failed: {e}')
            return json.dumps({'error': str(e)})

    # ─────────────────────────────────────────────────────────────────────────

    @tool
    async def search_field_references(
        object_api_name: str,
        field_api_name: str,
    ) -> str:
        """
        Search for all automation that references a specific field —
        across Apex triggers, Apex classes, Flows, Workflow Rules,
        and Validation Rules.

        Use this when a field is behaving unexpectedly — find every place
        something reads from or writes to that field.

        Args:
            object_api_name: API name of the object. Example: Order, Lead
            field_api_name: API name of the field. Example: Revenue__c, StageName, Status

        Returns:
            JSON with every piece of automation that references the field,
            grouped by type.
        """
        logger.info(f'Searching references to {object_api_name}.{field_api_name}')
        results = {}

        # Search Apex triggers for field reference
        try:
            trigger_result = await client.tooling_query(
                f"SELECT Id, Name, Body FROM ApexTrigger "
                f"WHERE TableEnumOrId = '{object_api_name}' AND Status = 'Active'"
            )
            matching_triggers = [
                {'name': r.get('Name'), 'id': r.get('Id')}
                for r in trigger_result.get('records', [])
                if field_api_name.lower() in (r.get('Body') or '').lower()
            ]
            results['apex_triggers'] = matching_triggers
        except Exception as e:
            results['apex_triggers'] = {'error': str(e)}

        # Search Apex classes for field reference
        try:
            class_result = await client.tooling_query(
                f"SELECT Id, Name, Body FROM ApexClass WHERE Status = 'Active'"
            )
            matching_classes = [
                {'name': r.get('Name'), 'id': r.get('Id')}
                for r in class_result.get('records', [])
                if field_api_name.lower() in (r.get('Body') or '').lower()
            ]
            results['apex_classes'] = matching_classes
        except Exception as e:
            results['apex_classes'] = {'error': str(e)}

        # Search Validation Rules
        try:
            vr_result = await client.tooling_query(
                f"SELECT Id, ValidationName, ErrorConditionFormula, ErrorMessage "
                f"FROM ValidationRule "
                f"WHERE EntityDefinition.QualifiedApiName = '{object_api_name}' "
                f"AND Active = true"
            )
            matching_vrs = [
                {
                    'name':    r.get('ValidationName'),
                    'formula': r.get('ErrorConditionFormula'),
                    'message': r.get('ErrorMessage'),
                }
                for r in vr_result.get('records', [])
                if field_api_name.lower() in (r.get('ErrorConditionFormula') or '').lower()
            ]
            results['validation_rules'] = matching_vrs
        except Exception as e:
            results['validation_rules'] = {'error': str(e)}

        # Search Workflow Rules
        try:
            wf_result = await client.tooling_query(
                f"SELECT Id, Name FROM WorkflowRule "
                f"WHERE TableEnumOrId = '{object_api_name}' AND Active = true"
            )
            results['workflow_rules'] = [
                {k: v for k, v in r.items() if k != 'attributes'}
                for r in wf_result.get('records', [])
            ]
        except Exception as e:
            results['workflow_rules'] = {'error': str(e)}

        total_refs = sum(
            len(v) for v in results.values()
            if isinstance(v, list)
        )

        return json.dumps({
            'field':             f'{object_api_name}.{field_api_name}',
            'total_references':  total_refs,
            'references':        results,
        }, indent=2, default=str)

    # ─────────────────────────────────────────────────────────────────────────

    @tool
    async def analyse_execution_order(object_api_name: str, operation: str = 'update') -> str:
        """
        Analyse the execution order of all active automation on an object
        for a given DML operation (insert, update, delete).

        Salesforce executes automation in a defined order:
        1. Before Triggers
        2. Validation Rules
        3. Assignment Rules
        4. Before-Save Flows
        5. After Triggers
        6. After-Save Flows
        7. Workflow Rules
        8. Process Builders

        Use this during RCA to understand sequencing issues —
        e.g. a Flow overwriting a value set by a trigger.

        Args:
            object_api_name: API name of the object. Example: Opportunity, Order
            operation: DML operation. Options: 'insert', 'update', 'delete'. Default: 'update'

        Returns:
            JSON showing execution order with all active automation at each step,
            highlighting potential conflicts.
        """
        logger.info(f'Analysing execution order for {object_api_name} {operation}')

        op_map = {
            'insert': ('UsageBeforeInsert', 'UsageAfterInsert'),
            'update': ('UsageBeforeUpdate', 'UsageAfterUpdate'),
            'delete': ('UsageBeforeDelete', 'UsageAfterDelete'),
        }

        before_field, after_field = op_map.get(operation.lower(), op_map['update'])

        execution_order = []

        # Step 1 — Before Triggers
        try:
            result = await client.tooling_query(
                f"SELECT Name, Status FROM ApexTrigger "
                f"WHERE TableEnumOrId = '{object_api_name}' "
                f"AND {before_field} = true AND Status = 'Active'"
            )
            before_triggers = [r.get('Name') for r in result.get('records', [])]
            execution_order.append({
                'step':  1,
                'phase': 'Before Triggers',
                'items': before_triggers,
                'note':  'Runs before record is saved. Can modify field values.',
            })
        except Exception as e:
            execution_order.append({'step': 1, 'phase': 'Before Triggers', 'error': str(e)})

        # Step 2 — Validation Rules
        try:
            result = await client.tooling_query(
                f"SELECT ValidationName FROM ValidationRule "
                f"WHERE EntityDefinition.QualifiedApiName = '{object_api_name}' "
                f"AND Active = true"
            )
            vrs = [r.get('ValidationName') for r in result.get('records', [])]
            execution_order.append({
                'step':  2,
                'phase': 'Validation Rules',
                'items': vrs,
                'note':  'Blocks save if formula evaluates to true.',
            })
        except Exception as e:
            execution_order.append({'step': 2, 'phase': 'Validation Rules', 'error': str(e)})

        # Step 3 — Assignment Rules
        try:
            result = await client.tooling_query(
                f"SELECT Name FROM AssignmentRule "
                f"WHERE SobjectType = '{object_api_name}' AND Active = true"
            )
            ars = [r.get('Name') for r in result.get('records', [])]
            execution_order.append({
                'step':  3,
                'phase': 'Assignment Rules',
                'items': ars,
                'note':  'Assigns owner/queue based on criteria.',
            })
        except Exception as e:
            execution_order.append({'step': 3, 'phase': 'Assignment Rules', 'error': str(e)})

        # Step 4 — Before-Save Flows
        try:
            result = await client.tooling_query(
                f"SELECT ApiName FROM Flow "
                f"WHERE TriggerType = 'RecordBeforeSave' AND Status = 'Active'"
            )
            before_flows = [r.get('ApiName') for r in result.get('records', [])]
            execution_order.append({
                'step':  4,
                'phase': 'Before-Save Flows',
                'items': before_flows,
                'note':  'Runs before record is committed. Can update fields on the triggering record.',
            })
        except Exception as e:
            execution_order.append({'step': 4, 'phase': 'Before-Save Flows', 'error': str(e)})

        # Step 5 — After Triggers
        try:
            result = await client.tooling_query(
                f"SELECT Name FROM ApexTrigger "
                f"WHERE TableEnumOrId = '{object_api_name}' "
                f"AND {after_field} = true AND Status = 'Active'"
            )
            after_triggers = [r.get('Name') for r in result.get('records', [])]
            execution_order.append({
                'step':  5,
                'phase': 'After Triggers',
                'items': after_triggers,
                'note':  'Runs after record is saved. Cannot modify triggering record directly.',
            })
        except Exception as e:
            execution_order.append({'step': 5, 'phase': 'After Triggers', 'error': str(e)})

        # Step 6 — After-Save Flows
        try:
            result = await client.tooling_query(
                f"SELECT ApiName FROM Flow "
                f"WHERE TriggerType = 'RecordAfterSave' AND Status = 'Active'"
            )
            after_flows = [r.get('ApiName') for r in result.get('records', [])]
            execution_order.append({
                'step':  6,
                'phase': 'After-Save Flows',
                'items': after_flows,
                'note':  'Runs after record commit. Can create/update other records.',
            })
        except Exception as e:
            execution_order.append({'step': 6, 'phase': 'After-Save Flows', 'error': str(e)})

        # Step 7 — Workflow Rules (legacy)
        try:
            result = await client.tooling_query(
                f"SELECT Name FROM WorkflowRule "
                f"WHERE TableEnumOrId = '{object_api_name}' AND Active = true"
            )
            wfs = [r.get('Name') for r in result.get('records', [])]
            execution_order.append({
                'step':  7,
                'phase': 'Workflow Rules',
                'items': wfs,
                'note':  'Legacy automation. Runs after after-save flows.',
            })
        except Exception as e:
            execution_order.append({'step': 7, 'phase': 'Workflow Rules', 'error': str(e)})

        # Detect potential conflicts
        conflicts = _detect_conflicts(execution_order)

        return json.dumps({
            'object':          object_api_name,
            'operation':       operation,
            'execution_order': execution_order,
            'potential_conflicts': conflicts,
        }, indent=2, default=str)

    # ─────────────────────────────────────────────────────────────────────────

    @tool
    async def find_classes_referencing_object(object_api_name: str) -> str:
        """
        Find all Apex classes that reference a specific Salesforce object —
        useful for understanding the full scope of code that could affect
        records on that object.

        Args:
            object_api_name: API name of the object. Example: Order, Account

        Returns:
            JSON list of Apex class names that contain references to the object.
        """
        logger.info(f'Finding classes referencing: {object_api_name}')
        try:
            result = await client.tooling_query(
                "SELECT Id, Name, Body FROM ApexClass WHERE Status = 'Active'"
            )
            object_lower   = object_api_name.lower()
            matching_classes = []

            for r in result.get('records', []):
                body = (r.get('Body') or '').lower()
                if object_lower in body:
                    # Count occurrences as a relevance signal
                    count = body.count(object_lower)
                    matching_classes.append({
                        'name':       r.get('Name'),
                        'id':         r.get('Id'),
                        'references': count,
                    })

            # Sort by reference count — most relevant first
            matching_classes.sort(key=lambda x: -x['references'])

            return json.dumps({
                'object':          object_api_name,
                'classes_found':   len(matching_classes),
                'classes':         matching_classes,
            }, indent=2)

        except Exception as e:
            logger.error(f'Find classes referencing object failed: {e}')
            return json.dumps({'error': str(e)})

    # ─────────────────────────────────────────────────────────────────────────

    return [
        find_all_automation_on_object,
        get_apex_trigger_body,
        get_apex_class_body,
        search_field_references,
        analyse_execution_order,
        find_classes_referencing_object,
    ]


# ─── HELPERS ──────────────────────────────────────────────────────────────────

def _detect_conflicts(execution_order: list) -> list:
    """
    Detect potential automation conflicts in the execution order.
    Looks for cases where multiple steps touch the same phase
    or where after-save flows might overwrite trigger values.
    """
    conflicts = []

    before_triggers = next(
        (s['items'] for s in execution_order if s.get('phase') == 'Before Triggers'), []
    )
    before_flows = next(
        (s['items'] for s in execution_order if s.get('phase') == 'Before-Save Flows'), []
    )
    after_triggers = next(
        (s['items'] for s in execution_order if s.get('phase') == 'After Triggers'), []
    )
    after_flows = next(
        (s['items'] for s in execution_order if s.get('phase') == 'After-Save Flows'), []
    )

    if before_triggers and before_flows:
        conflicts.append({
            'type':        'Field Value Race',
            'description': f'Both Before Triggers ({before_triggers}) and Before-Save Flows '
                           f'({before_flows}) can write to the triggering record. '
                           f'Before-Save Flows run AFTER Before Triggers — '
                           f'a Flow may overwrite values set by a trigger.',
            'severity':    'HIGH',
        })

    if after_triggers and after_flows:
        conflicts.append({
            'type':        'DML Ordering',
            'description': f'Both After Triggers ({after_triggers}) and After-Save Flows '
                           f'({after_flows}) run after the record is committed. '
                           f'If both perform DML on related records, order matters.',
            'severity':    'MEDIUM',
        })

    if len(before_triggers) > 1:
        conflicts.append({
            'type':        'Multiple Before Triggers',
            'description': f'Multiple before triggers found: {before_triggers}. '
                           f'Trigger execution order is not guaranteed in Salesforce. '
                           f'Consider consolidating into a single trigger with a handler.',
            'severity':    'MEDIUM',
        })

    return conflicts