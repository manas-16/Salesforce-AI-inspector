# ─── Salesforce (AI)nspector — agent/tools/testclass_tool.py ─────────────────
# LangChain tools for Apex test class generation and execution.
# User provides a user story + uploads relevant Apex classes.
# Agent generates a targeted test class, deploys it, runs it,
# and returns per-method pass/fail results.

import io
import json
import logging
import zipfile
import base64
from langchain_core.tools import tool
from salesforce.client import SalesforceClient

logger = logging.getLogger(__name__)


def make_testclass_tools(session_id: str, instance_url: str, is_production: bool):
    """
    Factory — returns test class tools bound to this request's session.
    """

    client = SalesforceClient(session_id, instance_url)

    def _prod_block(operation: str) -> str:
        return json.dumps({
            'error': f'PRODUCTION ORG DETECTED — {operation} is blocked. '
                     f'Write operations are only permitted in sandbox orgs.'
        })

    # ─────────────────────────────────────────────────────────────────────────

    @tool
    async def generate_test_class_prompt(
        user_story: str,
        apex_class_names: list[str],
        class_bodies: list[str],
    ) -> str:
        """
        Analyse a user story and the provided Apex class source code,
        then generate a complete Apex test class covering:
        - Happy path scenarios
        - Edge cases
        - Negative/error scenarios
        - Bulk test (200 records)

        This tool does NOT deploy or run the test — it returns the generated
        Apex test class as a string. Use deploy_and_run_test_class to execute it.

        Args:
            user_story: Plain English description of the feature to test.
                        Example: "When a Lead is converted, an Opportunity should be
                                  created with StageName = 'Prospecting' and the
                                  Lead's Company as Account name."
            apex_class_names: List of Apex class names provided by the developer.
                              Example: ['LeadConversionHandler', 'LeadTrigger']
            class_bodies: List of Apex class source code strings — one per class name.
                          Must match the order of apex_class_names.

        Returns:
            JSON with the generated test class name and full Apex source code,
            ready to deploy.
        """
        logger.info(f'Generating test class for: {user_story[:60]}...')

        if len(apex_class_names) != len(class_bodies):
            return json.dumps({
                'error': 'apex_class_names and class_bodies must have the same length.'
            })

        # Build a combined context string for the LLM to reason over
        class_context = '\n\n'.join([
            f'// === {name} ===\n{body}'
            for name, body in zip(apex_class_names, class_bodies)
        ])

        # This tool returns a structured prompt result that the agent
        # uses in its own reasoning to generate the test class.
        # The agent (Claude/GPT) is the one that writes the Apex — this tool
        # structures the context and returns it cleanly for the agent to act on.

        return json.dumps({
            'instruction': (
                'Based on the user story and Apex classes below, generate a complete '
                'Apex test class. Requirements:\n'
                '1. Use @isTest annotation on the class\n'
                '2. Use @isTest(SeeAllData=false) — create all test data within the test\n'
                '3. Use Test.startTest() / Test.stopTest() around DML and assertions\n'
                '4. Include a @TestSetup method for shared data if needed\n'
                '5. Cover: happy path, edge cases, negative scenarios, bulk (200 records)\n'
                '6. Use System.assert(), System.assertEquals(), System.assertNotEquals()\n'
                '7. Include meaningful assertion messages\n'
                '8. Class name must end with "Test" — e.g. LeadConversionHandlerTest\n'
                '9. Return ONLY valid compilable Apex — no markdown, no explanation\n'
            ),
            'user_story':    user_story,
            'class_context': class_context,
            'class_names':   apex_class_names,
        }, indent=2)

    # ─────────────────────────────────────────────────────────────────────────

    @tool
    async def deploy_and_run_test_class(
        class_name: str,
        apex_source: str,
    ) -> str:
        """
        Deploy an Apex test class to the org and run it, then return
        per-method pass/fail results with error messages and stack traces.

        Use this after generate_test_class_prompt — once the agent has
        produced the Apex source code, pass it here to deploy and execute.

        Args:
            class_name: Name of the test class. Must end with 'Test'.
                        Example: 'LeadConversionHandlerTest'
            apex_source: Full Apex source code of the test class.

        Returns:
            JSON with deployment status and per-method test results.
        """
        if is_production:
            return _prod_block('deploy_and_run_test_class')

        logger.info(f'Deploying and running test class: {class_name}')

        if not class_name.endswith('Test'):
            return json.dumps({
                'error': f'Test class name must end with "Test". Got: {class_name}'
            })

        # Step 1 — Deploy via Metadata API (zip package)
        try:
            zip_bytes = _build_apex_package(class_name, apex_source)
            deploy_result = await client.deploy_metadata(zip_bytes)
            logger.info(f'Deploy initiated for {class_name}')
        except Exception as e:
            logger.error(f'Deploy failed: {e}')
            return json.dumps({
                'stage':  'deployment',
                'error':  str(e),
                'hint':   'Check that the Apex source code is valid and compilable. '
                          'Syntax errors will cause deployment to fail.',
            })

        # Step 2 — Find the deployed class ID
        try:
            class_result = await client.tooling_query(
                f"SELECT Id FROM ApexClass WHERE Name = '{class_name}' LIMIT 1"
            )
            records = class_result.get('records', [])

            if not records:
                return json.dumps({
                    'stage': 'lookup',
                    'error': f'Class {class_name} not found after deployment. '
                             f'Deployment may have failed silently.',
                })

            class_id = records[0]['Id']
            logger.info(f'Class ID: {class_id}')

        except Exception as e:
            return json.dumps({'stage': 'lookup', 'error': str(e)})

        # Step 3 — Run tests async
        try:
            run_result = await client.run_tests([class_id])

            # run_result might be just a job ID string or a dict
            if isinstance(run_result, str):
                job_id = run_result
            else:
                job_id = run_result.get('id') or str(run_result)

            logger.info(f'Test run started. Job ID: {job_id}')

        except Exception as e:
            return json.dumps({'stage': 'test_run', 'error': str(e)})

        # Step 4 — Poll for results
        try:
            results = await client.get_test_results(job_id)
            summary = results.get('summary', {})
            methods = results.get('methods', [])

            passed  = [m for m in methods if m.get('Outcome') == 'Pass']
            failed  = [m for m in methods if m.get('Outcome') == 'Fail']
            errored = [m for m in methods if m.get('Outcome') not in ('Pass', 'Fail')]

            return json.dumps({
                'class_name': class_name,
                'class_id':   class_id,
                'summary': {
                    'status':            summary.get('Status'),
                    'total_methods':     len(methods),
                    'passed':            len(passed),
                    'failed':            len(failed),
                    'errors':            len(errored),
                    'methods_completed': summary.get('MethodsCompleted'),
                    'methods_failed':    summary.get('MethodsFailed'),
                },
                'passed_methods': [
                    {'method': m.get('MethodName'), 'outcome': m.get('Outcome')}
                    for m in passed
                ],
                'failed_methods': [
                    {
                        'method':      m.get('MethodName'),
                        'outcome':     m.get('Outcome'),
                        'message':     m.get('Message'),
                        'stack_trace': m.get('StackTrace'),
                    }
                    for m in failed
                ],
                'conclusion': (
                    f'{len(passed)}/{len(methods)} tests passed. '
                    f'{len(failed)} failed.'
                    + (f' Failures: {", ".join(m.get("MethodName","?") for m in failed)}' if failed else '')
                ),
            }, indent=2, default=str)

        except Exception as e:
            return json.dumps({'stage': 'results', 'error': str(e)})

    # ─────────────────────────────────────────────────────────────────────────

    @tool
    async def run_existing_test_class(class_name: str) -> str:
        """
        Run an existing Apex test class that is already deployed in the org.
        Use this to re-run tests without redeploying.

        Args:
            class_name: Name of an existing Apex test class in the org.
                        Use run_soql_query to list test classes:
                        SELECT Name FROM ApexClass WHERE Name LIKE '%Test%'

        Returns:
            JSON with per-method pass/fail results.
        """
        logger.info(f'Running existing test class: {class_name}')

        try:
            class_result = await client.tooling_query(
                f"SELECT Id FROM ApexClass WHERE Name = '{class_name}' LIMIT 1"
            )
            records = class_result.get('records', [])
            if not records:
                return json.dumps({'error': f'Test class not found: {class_name}'})

            class_id   = records[0]['Id']
            run_result = await client.run_tests([class_id])

            if isinstance(run_result, str):
                job_id = run_result
            else:
                job_id = run_result.get('id') or str(run_result)

            results = await client.get_test_results(job_id)
            summary = results.get('summary', {})
            methods = results.get('methods', [])

            passed = [m for m in methods if m.get('Outcome') == 'Pass']
            failed = [m for m in methods if m.get('Outcome') == 'Fail']

            return json.dumps({
                'class_name': class_name,
                'summary': {
                    'total':  len(methods),
                    'passed': len(passed),
                    'failed': len(failed),
                },
                'passed_methods': [m.get('MethodName') for m in passed],
                'failed_methods': [
                    {
                        'method':      m.get('MethodName'),
                        'message':     m.get('Message'),
                        'stack_trace': m.get('StackTrace'),
                    }
                    for m in failed
                ],
                'conclusion': f'{len(passed)}/{len(methods)} passed.',
            }, indent=2, default=str)

        except Exception as e:
            logger.error(f'Run existing test class failed: {e}')
            return json.dumps({'error': str(e)})

    # ─────────────────────────────────────────────────────────────────────────

    @tool
    async def get_org_test_coverage() -> str:
        """
        Get the overall Apex test coverage for the org and identify
        classes with low or zero coverage.

        Use this for:
        - "What is our overall test coverage?"
        - "Which classes have no tests?"
        - Identifying gaps before a deployment

        Returns:
            JSON with org-wide coverage percentage and a list of
            classes sorted by coverage (lowest first).
        """
        logger.info('Getting org test coverage')
        try:
            result = await client.tooling_query(
                "SELECT ApexClassOrTrigger.Name, NumLinesCovered, NumLinesUncovered "
                "FROM ApexCodeCoverageAggregate "
                "ORDER BY NumLinesCovered ASC"
            )
            records = result.get('records', [])

            coverage_list = []
            total_covered   = 0
            total_uncovered = 0

            for r in records:
                covered   = r.get('NumLinesCovered', 0) or 0
                uncovered = r.get('NumLinesUncovered', 0) or 0
                total     = covered + uncovered
                pct       = round((covered / total * 100), 1) if total > 0 else 0

                total_covered   += covered
                total_uncovered += uncovered

                coverage_list.append({
                    'class_name':     r.get('ApexClassOrTrigger', {}).get('Name') if r.get('ApexClassOrTrigger') else 'Unknown',
                    'lines_covered':  covered,
                    'lines_total':    total,
                    'coverage_pct':   pct,
                })

            # Sort lowest coverage first
            coverage_list.sort(key=lambda x: x['coverage_pct'])

            total_lines = total_covered + total_uncovered
            org_coverage = round(total_covered / total_lines * 100, 1) if total_lines > 0 else 0

            zero_coverage = [c for c in coverage_list if c['coverage_pct'] == 0]
            low_coverage  = [c for c in coverage_list if 0 < c['coverage_pct'] < 75]

            return json.dumps({
                'org_coverage_pct':   org_coverage,
                'total_lines':        total_lines,
                'covered_lines':      total_covered,
                'classes_analysed':   len(coverage_list),
                'zero_coverage_count': len(zero_coverage),
                'low_coverage_count': len(low_coverage),
                'zero_coverage_classes': [c['class_name'] for c in zero_coverage],
                'low_coverage_classes':  low_coverage[:10],
                'all_coverage':           coverage_list,
            }, indent=2, default=str)

        except Exception as e:
            logger.error(f'Get org coverage failed: {e}')
            return json.dumps({'error': str(e)})

    # ─────────────────────────────────────────────────────────────────────────

    return [
        generate_test_class_prompt,
        deploy_and_run_test_class,
        run_existing_test_class,
        get_org_test_coverage,
    ]


# ─── HELPERS ──────────────────────────────────────────────────────────────────

def _build_apex_package(class_name: str, apex_source: str) -> bytes:
    """
    Build a Salesforce Metadata API deployment package as a zip file.
    Returns raw zip bytes ready for base64 encoding and SOAP deploy call.
    """
    buf = io.BytesIO()

    with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as zf:

        # package.xml
        package_xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<Package xmlns="http://soap.sforce.com/2006/04/metadata">
    <types>
        <members>{class_name}</members>
        <name>ApexClass</name>
    </types>
    <version>59.0</version>
</Package>"""
        zf.writestr('package.xml', package_xml)

        # Apex class file
        zf.writestr(f'classes/{class_name}.cls', apex_source)

        # Apex class metadata file
        meta_xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<ApexClass xmlns="http://soap.sforce.com/2006/04/metadata">
    <apiVersion>59.0</apiVersion>
    <status>Active</status>
</ApexClass>"""
        zf.writestr(f'classes/{class_name}.cls-meta.xml', meta_xml)

    return buf.getvalue()