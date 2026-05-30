# ─── Salesforce (AI)nspector — agent/tools/file_parser_tool.py ───────────────
# LangChain tool for parsing uploaded files.
# Handles CSV, JSON, and plain text (Apex classes, XML).
# Extracts structured content so other tools can consume it.

import csv
import io
import json
import logging
from langchain_core.tools import tool

logger = logging.getLogger(__name__)


def make_file_parser_tools():
    """
    Factory — returns file parser tools.
    File parsing has no session dependency.
    """

    # ─────────────────────────────────────────────────────────────────────────

    @tool
    def parse_uploaded_file(
        file_name: str,
        file_content: str,
        file_type: str,
    ) -> str:
        """
        Parse the content of an uploaded file and return structured data.

        Supports:
        - CSV files (.csv) — returns rows as list of dicts
        - JSON files (.json) — returns parsed JSON
        - Apex class files (.cls) — returns raw source with line count
        - XML files (.xml) — returns raw content for metadata operations
        - Plain text (.txt) — returns content with line count

        Use this as the first step when the user uploads a file —
        parse it, then pass the structured content to the appropriate tool.

        Args:
            file_name: Original filename including extension.
            file_content: Raw file content as a string.
            file_type: File extension. Examples: '.csv', '.json', '.cls', '.xml'

        Returns:
            JSON with parsed content and metadata about the file.
        """
        logger.info(f'Parsing file: {file_name} ({file_type})')

        ext = file_type.lower().lstrip('.')

        try:
            if ext == 'csv':
                return _parse_csv(file_name, file_content)
            elif ext == 'json':
                return _parse_json(file_name, file_content)
            elif ext in ('cls', 'trigger'):
                return _parse_apex(file_name, file_content)
            elif ext == 'xml':
                return _parse_xml(file_name, file_content)
            else:
                return _parse_text(file_name, file_content)

        except Exception as e:
            logger.error(f'File parse failed: {e}')
            return json.dumps({'error': f'Failed to parse {file_name}: {str(e)}'})

    # ─────────────────────────────────────────────────────────────────────────

    @tool
    def extract_usernames_from_content(content: str) -> str:
        """
        Extract Salesforce usernames from any text content —
        CSV rows, plain text lists, or comma-separated values.

        Salesforce usernames are in email format and typically contain
        org-specific suffixes like .sandbox or .dev.

        Use this when a user pastes or uploads a list of usernames
        in any format before passing them to user management tools.

        Args:
            content: Raw text containing usernames in any format.

        Returns:
            JSON with list of extracted usernames.
        """
        import re
        logger.info('Extracting usernames from content')

        # Match email-format strings (Salesforce usernames are email format)
        email_pattern = re.compile(
            r'\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b'
        )
        usernames = list(set(email_pattern.findall(content)))
        usernames.sort()

        return json.dumps({
            'count':     len(usernames),
            'usernames': usernames,
        }, indent=2)

    # ─────────────────────────────────────────────────────────────────────────

    return [
        parse_uploaded_file,
        extract_usernames_from_content,
    ]


# ─── PARSERS ──────────────────────────────────────────────────────────────────

def _parse_csv(file_name: str, content: str) -> str:
    reader  = csv.DictReader(io.StringIO(content.strip()))
    rows    = list(reader)
    columns = list(rows[0].keys()) if rows else []

    # Clean whitespace from keys and values
    clean_rows = [
        {k.strip(): v.strip() for k, v in row.items() if k}
        for row in rows
    ]

    return json.dumps({
        'file_name':   file_name,
        'type':        'csv',
        'row_count':   len(clean_rows),
        'columns':     columns,
        'rows':        clean_rows,
        'next_step':   (
            'CSV parsed successfully. '
            'Pass "rows" to the appropriate tool — e.g. bulk_create_from_csv, '
            'create_custom_fields_from_csv, or bulk_reset_passwords.'
        ),
    }, indent=2)


def _parse_json(file_name: str, content: str) -> str:
    data = json.loads(content)
    return json.dumps({
        'file_name': file_name,
        'type':      'json',
        'content':   data,
    }, indent=2)


def _parse_apex(file_name: str, content: str) -> str:
    lines = content.splitlines()
    return json.dumps({
        'file_name':  file_name,
        'type':       'apex',
        'line_count': len(lines),
        'body':       content,
        'next_step':  (
            'Apex source parsed. Pass "body" to get_apex_class_body context, '
            'generate_test_class_prompt, or rca analysis tools.'
        ),
    }, indent=2)


def _parse_xml(file_name: str, content: str) -> str:
    return json.dumps({
        'file_name':  file_name,
        'type':       'xml',
        'line_count': len(content.splitlines()),
        'content':    content,
    }, indent=2)


def _parse_text(file_name: str, content: str) -> str:
    lines = content.splitlines()
    return json.dumps({
        'file_name':  file_name,
        'type':       'text',
        'line_count': len(lines),
        'content':    content,
    }, indent=2)