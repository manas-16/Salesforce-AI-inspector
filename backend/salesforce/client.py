# ─── Salesforce (AI)nspector — salesforce/client.py ─────────────────────────
# Central Salesforce API client.
# Wraps REST, Tooling, Metadata, and Bulk API 2.0 calls.
# All tools import this — never call httpx directly from a tool.
# Responsibilities:
#   1. REST API  — SOQL queries, SObject CRUD
#   2. Tooling API — Apex, CustomField, ValidationRule, debug logs
#   3. Metadata API — deploy/retrieve metadata components
#   4. Bulk API 2.0 — large data operations via CSV

import csv
import io
import json
import logging
import time
from typing import Any

import httpx

from .oAuth import get_api_instance_url

logger = logging.getLogger(__name__)

API_VERSION = 'v59.0'


class SalesforceClient:
    """
    Async Salesforce API client.
    Instantiated per-request with session_id + instance_url.
    """

    def __init__(self, session_id: str, instance_url: str):
        self.session_id   = session_id
        self.instance_url = get_api_instance_url(instance_url)
        self.base_url     = f'{self.instance_url}/services/data/{API_VERSION}'
        self.tooling_url  = f'{self.base_url}/tooling'
        self.headers      = {
            'Authorization': f'Bearer {session_id}',
            'Content-Type':  'application/json',
            'Accept':        'application/json',
        }

    # ─── INTERNAL HELPERS ─────────────────────────────────────────────────────

    async def _get(self, url: str, params: dict = None) -> dict:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(url, headers=self.headers, params=params)
            return self._handle(response)

    async def _post(self, url: str, body: dict) -> dict:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(url, headers=self.headers, json=body)
            return self._handle(response)

    async def _patch(self, url: str, body: dict) -> dict:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.patch(url, headers=self.headers, json=body)
            return self._handle(response)

    async def _delete(self, url: str) -> dict:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.delete(url, headers=self.headers)
            if response.status_code == 204:
                return {'success': True}
            return self._handle(response)

    def _handle(self, response: httpx.Response) -> dict:
        try:
            data = response.json()
        except Exception:
            data = {'raw': response.text}

        if response.status_code >= 400:
            error_msg = self._extract_error(data)
            logger.error(f'Salesforce API error {response.status_code}: {error_msg}')
            raise Exception(f'Salesforce API error ({response.status_code}): {error_msg}')

        return data

    @staticmethod
    def _extract_error(data: Any) -> str:
        if isinstance(data, list) and data:
            return data[0].get('message', str(data))
        if isinstance(data, dict):
            return data.get('message', data.get('errorCode', str(data)))
        return str(data)

    # =========================================================================
    # REST API — SOQL
    # =========================================================================

    async def query(self, soql: str) -> dict:
        """Execute a SOQL query. Returns full response with records list."""
        url = f'{self.base_url}/query'
        return await self._get(url, params={'q': soql})

    async def query_all(self, soql: str) -> list:
        """Execute SOQL and auto-paginate through all results."""
        url = f'{self.base_url}/query'
        result = await self._get(url, params={'q': soql})
        records = result.get('records', [])

        while not result.get('done', True) and result.get('nextRecordsUrl'):
            next_url = f'{self.instance_url}{result["nextRecordsUrl"]}'
            result = await self._get(next_url)
            records.extend(result.get('records', []))

        return records

    # =========================================================================
    # REST API — SObject CRUD
    # =========================================================================

    async def create_record(self, sobject: str, fields: dict) -> dict:
        """Create a new SObject record."""
        url = f'{self.base_url}/sobjects/{sobject}'
        return await self._post(url, fields)

    async def update_record(self, sobject: str, record_id: str, fields: dict) -> dict:
        """Update an existing SObject record."""
        url = f'{self.base_url}/sobjects/{sobject}/{record_id}'
        return await self._patch(url, fields)

    async def delete_record(self, sobject: str, record_id: str) -> dict:
        """Delete a SObject record."""
        url = f'{self.base_url}/sobjects/{sobject}/{record_id}'
        return await self._delete(url)

    async def get_record(self, sobject: str, record_id: str, fields: list[str] = None) -> dict:
        """Get a single SObject record by ID."""
        url = f'{self.base_url}/sobjects/{sobject}/{record_id}'
        params = {'fields': ','.join(fields)} if fields else None
        return await self._get(url, params=params)

    # =========================================================================
    # REST API — SObject Describe
    # =========================================================================

    async def describe_object(self, sobject: str) -> dict:
        """Get full metadata description of an SObject — fields, relationships, etc."""
        url = f'{self.base_url}/sobjects/{sobject}/describe'
        return await self._get(url)

    async def describe_global(self) -> dict:
        """List all available SObjects in the org."""
        url = f'{self.base_url}/sobjects'
        return await self._get(url)

    # =========================================================================
    # REST API — User Management
    # =========================================================================

    async def reset_password(self, user_id: str) -> dict:
        """Trigger a password reset email for a user."""
        url = f'{self.base_url}/sobjects/User/{user_id}/password'
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.delete(url, headers=self.headers)
            if response.status_code in (200, 204):
                return {'success': True, 'message': f'Password reset email sent for user {user_id}'}
            return self._handle(response)

    async def freeze_user(self, user_login_id: str, freeze: bool = True) -> dict:
        """Freeze or unfreeze a user via the UserLogin object."""
        url = f'{self.base_url}/sobjects/UserLogin/{user_login_id}'
        return await self._patch(url, {'IsFrozen': freeze})

    async def get_user_login(self, user_id: str) -> dict:
        """Get UserLogin record for a user (needed for freeze/unfreeze)."""
        soql = f"SELECT Id, IsFrozen, IsPasswordLocked FROM UserLogin WHERE UserId = '{user_id}' LIMIT 1"
        result = await self.query(soql)
        records = result.get('records', [])
        if not records:
            raise Exception(f'UserLogin record not found for user {user_id}')
        return records[0]

    # =========================================================================
    # TOOLING API — Metadata read/write
    # =========================================================================

    async def tooling_query(self, soql: str) -> dict:
        """Execute a SOQL query against the Tooling API."""
        url = f'{self.tooling_url}/query'
        return await self._get(url, params={'q': soql})

    async def tooling_create(self, sobject: str, body: dict) -> dict:
        """Create a Tooling API record (CustomField, ValidationRule, etc.)."""
        url = f'{self.tooling_url}/sobjects/{sobject}'
        return await self._post(url, body)

    async def tooling_update(self, sobject: str, record_id: str, body: dict) -> dict:
        """Update a Tooling API record."""
        url = f'{self.tooling_url}/sobjects/{sobject}/{record_id}'
        return await self._patch(url, body)

    async def tooling_delete(self, sobject: str, record_id: str) -> dict:
        """Delete a Tooling API record."""
        url = f'{self.tooling_url}/sobjects/{sobject}/{record_id}'
        return await self._delete(url)

    async def get_apex_log(self, log_id: str) -> str:
        """Fetch the raw body of an Apex debug log."""
        url = f'{self.tooling_url}/sobjects/ApexLog/{log_id}/Body'
        async with httpx.AsyncClient(timeout=60.0) as client:
            response = await client.get(url, headers=self.headers)
            if response.status_code == 200:
                return response.text
            raise Exception(f'Failed to fetch log body: {response.status_code}')

    async def run_tests(self, class_ids: list[str]) -> dict:
        """
        Run Apex tests asynchronously via Tooling API.
        Returns a test run ID to poll for results.
        """
        url = f'{self.tooling_url}/runTestsAsynchronous'
        body = {'classids': ','.join(class_ids)}
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                url,
                headers=self.headers,
                json=body,
            )
            return self._handle(response)

    async def get_test_results(self, test_run_id: str, poll_interval: int = 3, max_wait: int = 120) -> dict:
        """
        Poll for test run results until complete or timeout.
        Returns full test results including individual method outcomes.
        """
        url = f'{self.tooling_url}/query'
        elapsed = 0

        while elapsed < max_wait:
            result = await self._get(
                url,
                params={
                    'q': f"SELECT Status, NumberOfErrors, MethodsCompleted, MethodsFailed "
                         f"FROM ApexTestRunResult WHERE AsyncApexJobId = '{test_run_id}' LIMIT 1"
                }
            )
            records = result.get('records', [])
            if records and records[0].get('Status') in ('Completed', 'Failed', 'Aborted'):
                # Fetch individual test method results
                methods = await self._get(
                    url,
                    params={
                        'q': f"SELECT MethodName, Outcome, Message, StackTrace "
                             f"FROM ApexTestResult WHERE AsyncApexJobId = '{test_run_id}'"
                    }
                )
                return {
                    'summary': records[0],
                    'methods': methods.get('records', []),
                }

            await __import__('asyncio').sleep(poll_interval)
            elapsed += poll_interval

        raise Exception(f'Test run timed out after {max_wait} seconds.')

    async def execute_anonymous_apex(self, apex_code: str) -> dict:
        """Execute anonymous Apex via Tooling API."""
        url = f'{self.tooling_url}/executeAnonymous'
        import urllib.parse
        encoded = urllib.parse.quote(apex_code)
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(
                f'{url}?anonymousBody={encoded}',
                headers=self.headers,
            )
            return self._handle(response)

    # =========================================================================
    # METADATA API — Deploy/Retrieve
    # =========================================================================

    async def deploy_metadata(self, zip_bytes: bytes, deploy_options: dict = None) -> dict:
        """
        Deploy metadata via Metadata API using REST-based deployment.
        zip_bytes: base64-encoded zip of metadata package.
        Returns an async job ID to poll.
        """
        import base64
        url = f'{self.instance_url}/services/Soap/m/59.0'

        options = deploy_options or {
            'allowMissingFiles': False,
            'autoUpdatePackage': False,
            'checkOnly': False,
            'ignoreWarnings': True,
            'rollbackOnError': True,
            'singlePackage': True,
        }

        zip_b64 = base64.b64encode(zip_bytes).decode('utf-8')

        soap_body = f"""<?xml version="1.0" encoding="utf-8"?>
<soapenv:Envelope
    xmlns:soapenv="http://schemas.xmlsoap.org/soap/envelope/"
    xmlns:met="http://soap.sforce.com/2006/04/metadata">
  <soapenv:Header>
    <met:CallOptions/>
    <met:SessionHeader>
      <met:sessionId>{self.session_id}</met:sessionId>
    </met:SessionHeader>
  </soapenv:Header>
  <soapenv:Body>
    <met:deploy>
      <met:ZipFile>{zip_b64}</met:ZipFile>
      <met:DeployOptions>
        <met:allowMissingFiles>{str(options.get("allowMissingFiles", False)).lower()}</met:allowMissingFiles>
        <met:autoUpdatePackage>{str(options.get("autoUpdatePackage", False)).lower()}</met:autoUpdatePackage>
        <met:checkOnly>{str(options.get("checkOnly", False)).lower()}</met:checkOnly>
        <met:ignoreWarnings>{str(options.get("ignoreWarnings", True)).lower()}</met:ignoreWarnings>
        <met:rollbackOnError>{str(options.get("rollbackOnError", True)).lower()}</met:rollbackOnError>
        <met:singlePackage>{str(options.get("singlePackage", True)).lower()}</met:singlePackage>
      </met:DeployOptions>
    </met:deploy>
  </soapenv:Body>
</soapenv:Envelope>"""

        soap_headers = {
            'Authorization': f'Bearer {self.session_id}',
            'Content-Type': 'text/xml',
            'SOAPAction': 'deploy',
        }

        async with httpx.AsyncClient(timeout=60.0) as client:
            response = await client.post(url, headers=soap_headers, content=soap_body.encode())
            if response.status_code != 200:
                raise Exception(f'Metadata deploy failed: {response.status_code} — {response.text[:500]}')
            return {'raw_response': response.text}

    # =========================================================================
    # BULK API 2.0
    # =========================================================================

    async def bulk_create(self, sobject: str, records: list[dict]) -> dict:
        """
        Create records in bulk using Bulk API 2.0.
        records: list of dicts — each dict is one record's fields.
        """
        return await self._bulk_operation(sobject, 'insert', records)

    async def bulk_update(self, sobject: str, records: list[dict]) -> dict:
        """
        Update records in bulk. Each record dict must include 'Id'.
        """
        return await self._bulk_operation(sobject, 'update', records)

    async def _bulk_operation(self, sobject: str, operation: str, records: list[dict]) -> dict:
        """Internal: create, submit, and poll a Bulk API 2.0 job."""

        # Step 1 — Create job
        job_url = f'{self.base_url}/jobs/ingest'
        job = await self._post(job_url, {
            'object': sobject,
            'operation': operation,
            'contentType': 'CSV',
            'lineEnding': 'LF',
        })
        job_id = job['id']

        # Step 2 — Upload CSV data
        csv_data = self._records_to_csv(records)
        upload_url = f'{job_url}/{job_id}/batches'
        csv_headers = {
            'Authorization': f'Bearer {self.session_id}',
            'Content-Type': 'text/csv',
        }
        async with httpx.AsyncClient(timeout=60.0) as client:
            await client.put(upload_url, headers=csv_headers, content=csv_data.encode())

        # Step 3 — Close job (triggers processing)
        await self._patch(f'{job_url}/{job_id}', {'state': 'UploadComplete'})

        # Step 4 — Poll until complete
        return await self._poll_bulk_job(job_id, job_url)

    async def _poll_bulk_job(self, job_id: str, job_url: str, max_wait: int = 120) -> dict:
        elapsed = 0
        while elapsed < max_wait:
            status = await self._get(f'{job_url}/{job_id}')
            state = status.get('state')

            if state == 'JobComplete':
                return {
                    'success': True,
                    'job_id': job_id,
                    'records_processed': status.get('numberRecordsProcessed', 0),
                    'records_failed': status.get('numberRecordsFailed', 0),
                }
            elif state == 'Failed':
                raise Exception(f'Bulk job {job_id} failed: {status.get("errorMessage", "Unknown error")}')

            await __import__('asyncio').sleep(3)
            elapsed += 3

        raise Exception(f'Bulk job {job_id} timed out after {max_wait} seconds.')

    @staticmethod
    def _records_to_csv(records: list[dict]) -> str:
        if not records:
            return ''
        output = io.StringIO()
        writer = csv.DictWriter(output, fieldnames=records[0].keys())
        writer.writeheader()
        writer.writerows(records)
        return output.getvalue()

    # =========================================================================
    # SETUP AUDIT TRAIL
    # =========================================================================

    async def get_audit_trail(self, filters: dict = None) -> list:
        """
        Query SetupAuditTrail with optional filters.
        filters: dict with optional keys — action, created_by_name, days_ago
        """
        conditions = []
        if filters:
            if filters.get('days_ago'):
                conditions.append(f"CreatedDate = LAST_N_DAYS:{filters['days_ago']}")
            if filters.get('created_by_name'):
                conditions.append(f"CreatedByContext LIKE '%{filters['created_by_name']}%'")
            if filters.get('action'):
                conditions.append(f"Action LIKE '%{filters['action']}%'")

        where = f"WHERE {' AND '.join(conditions)}" if conditions else ''
        soql = f"SELECT Action, Display, Section, CreatedDate, CreatedByContext FROM SetupAuditTrail {where} ORDER BY CreatedDate DESC LIMIT 200"

        return await self.query_all(soql)
