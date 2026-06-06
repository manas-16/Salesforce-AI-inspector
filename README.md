# Salesforce (AI)nspector

> An AI-powered Chrome extension that embeds directly inside your Salesforce org — letting you manage users, query data, debug logs, investigate audit trails, run RCA, and generate test data through natural language.

No separate dashboard. No copy-pasting. Just open the sidebar and talk to your org.

---

## Table of Contents

- [Prerequisites](#prerequisites)
- [Backend Setup](#backend-setup)
- [Chrome Extension Setup](#chrome-extension-setup)
- [Salesforce Connected App Setup](#salesforce-connected-app-setup)
- [Extension Configuration](#extension-configuration)
- [Capabilities](#capabilities)
- [Example Usage](#example-usage)
- [Limitations](#limitations)
- [Architecture](#architecture)

---

## Prerequisites

Before you start, make sure you have:

- **Python 3.11+** — [Download](https://www.python.org/downloads/)
- **Google Chrome** browser
- **Salesforce org** — Developer Edition or Sandbox (not Production)
- **API key** for at least one supported LLM:
  - [Anthropic (Claude)](https://console.anthropic.com/account/keys)
  - [OpenAI (GPT-4o)](https://platform.openai.com/api-keys)
  - [Google (Gemini)](https://aistudio.google.com/app/apikey)

---

## Backend Setup

The backend is a FastAPI server that runs locally on your machine. It handles LLM orchestration and all Salesforce API calls.

**Step 1 — Clone or download the project:**

```bash
git clone https://github.com/yourusername/salesforce-ai-inspector.git
cd salesforce-ai-inspector
```

**Step 2 — Navigate to the backend folder:**

```bash
cd backend
```

**Step 3 — Create a virtual environment:**

```bash
python -m venv venv
```

**Step 4 — Activate the virtual environment:**

On Windows:
```bash
venv\Scripts\activate
```

On Mac/Linux:
```bash
source venv/bin/activate
```

**Step 5 — Install dependencies:**

```bash
pip install -r requirements.txt
```

**Step 6 — Start the server:**

```bash
python main.py
```

You should see:
```
INFO:     Uvicorn running on http://127.0.0.1:8000
INFO:     Application startup complete.
```

Keep this terminal open while using the extension. The backend must be running for the extension to work.

---

## Chrome Extension Setup

**Step 1 — Open Chrome Extensions:**

Navigate to `chrome://extensions` in your browser.

**Step 2 — Enable Developer Mode:**

Toggle **Developer mode** on — top right corner of the extensions page.

**Step 3 — Load the extension:**

Click **Load unpacked** → select the `extension/` folder from the project → click **Select Folder**.

You should see **Salesforce (AI)nspector** appear in the list.

**Step 4 — Pin the extension:**

Click the puzzle icon in the Chrome toolbar → click the pin icon next to **Salesforce (AI)nspector** so it stays visible.

---

## Salesforce Connected App Setup

The extension uses OAuth to get a proper REST API session token. You need to create a Connected App in your org once.

**Step 1 — Create the Connected App:**

In Salesforce: `Setup → App Manager → New Connected App`

Fill in:
- **Connected App Name:** `SF AIinspector`
- **API Name:** `SF_AIinspector`
- **Contact Email:** your email
- **Enable OAuth Settings:** ✓ checked
- **Callback URL:** `http://localhost:8000/oauth/callback`
- **Selected OAuth Scopes:**
  - `Access and manage your data (api)`
  - `Perform requests at any time (refresh_token, offline_access)`

Click **Save**. Wait 5–10 minutes for the app to activate.

**Step 2 — Get your credentials:**

Go to `Setup → App Manager → find your app → View → Manage Consumer Details`

Note down:
- **Consumer Key**
- **Consumer Secret**

**Step 3 — Get your Security Token:**

`Profile icon (top right) → Settings → My Personal Information → Reset My Security Token`

Check your email for the token. You'll need this unless your IP is in the org's trusted IP ranges.

---

## Extension Configuration

Open the extension settings by clicking the extension icon in your Chrome toolbar.

**Step 1 — Connect to Salesforce:**

Fill in the **Salesforce Connection** section:
- **Consumer Key** — from your Connected App
- **Consumer Secret** — from your Connected App
- **Username** — your Salesforce login email
- **Password** — your Salesforce password
- **Security Token** — from the reset email (leave blank if your IP is trusted)
- **Sandbox / Developer Edition checkbox** — check this for Developer Edition orgs; leave unchecked for standard sandboxes

Click **Connect to Salesforce**. You should see a green **Connected** badge with your org's hostname.

**Step 2 — Set your LLM API Key:**

Select your preferred provider from the dropdown:
- **Anthropic (Claude)** — recommended, best instruction following
- **OpenAI (GPT-4o)**
- **Google (Gemini)**

Paste your API key and click **Save Key**.

**Step 3 — Verify backend connection:**

The Backend URL defaults to `http://localhost:8000`. Click **Test Connection** — you should see ✓ Connected.

**Step 4 — Open the sidebar:**

Navigate to any Salesforce page. You should see a small **✦ AI** button on the right edge of the screen. Click it to open the chat panel.

---

## Capabilities

Salesforce (AI)nspector supports the following operations. All write operations are blocked on production orgs — the extension enforces sandbox-only writes automatically.

### Admin Automation

| Capability | Description |
| :--- | :--- |
| Create user | Creates a new user with profile, timezone, and locale |
| Update user | Updates any field on an existing user record |
| Activate / Deactivate user | Enables or disables login access |
| Freeze / Unfreeze user | Temporarily blocks login without deactivation |
| Reset password | Triggers a password reset email |
| Bulk reset passwords | Resets passwords for a list of users or from a CSV upload |
| Assign profile | Assigns a different profile to a user |
| Assign / Revoke permission set | Adds or removes permission set assignments |
| Assign role | Assigns a user role |
| Create custom field | Creates a field on any object via Tooling API |
| Bulk create fields from CSV | Creates multiple fields from an uploaded CSV file |
| Create custom object | Creates a new custom object |
| Add picklist values | Adds new values to an existing picklist field |
| Create validation rule | Creates a validation rule with formula and error message |
| Create / Update / Delete records | CRUD operations on any SObject |
| Bulk create / update records | Bulk operations via Salesforce Bulk API 2.0 |

### Data Analysis & Validation

| Capability | Description |
| :--- | :--- |
| SOQL queries | Run any SOQL query, results rendered as tables |
| Cross-reference analysis | Upload a file and cross-reference against live org data |
| Data validation | Find records that violate business rules or have missing fields |
| Object schema inspection | View all fields, types, picklist values, and relationships |

### Audit & Compliance

| Capability | Description |
| :--- | :--- |
| Setup audit trail | Query what changed in org setup with filters by user, section, and date |
| Login history | Review login events, failed attempts, IP addresses |
| Field history | See who changed a specific field on a specific record |
| Permission changes | Focused audit of profile, permission set, and role changes |
| User activity summary | Full activity report for a user — logins, setup changes, record activity |

### Debugging & RCA

| Capability | Description |
| :--- | :--- |
| Apex debug log analysis | Fetch and analyse debug logs for a user — surfaces exceptions, DML, SOQL, validation failures |
| Multi-log pattern analysis | Identify recurring errors across multiple logs |
| Flow execution errors | Query FlowExecutionErrorEvent and identify failing elements |
| Flow metadata inspection | Read active flow structure — decisions, assignments, record lookups |
| Full org RCA | Recursive search across triggers, flows, validation rules, and Apex classes |
| Execution order analysis | Map the full automation execution order for any object and DML operation — with conflict detection |
| Field reference search | Find every trigger, class, flow, and validation rule that references a specific field |

### Test Data & Testing

| Capability | Description |
| :--- | :--- |
| Schema-aware test data | Creates valid test records based on live org schema — respects mandatory fields and picklist values |
| Relational test data chains | Creates full chains: Account → Contact → Opportunity → Order |
| Pre-built test scenarios | Lead conversion, order management, case management, account hierarchy |
| Apex test class generation | Generates a targeted test class from a user story and uploaded Apex classes |
| Deploy and run test class | Deploys generated test class, runs it, returns per-method pass/fail results |
| Org test coverage report | Shows org-wide coverage and flags classes with zero or low coverage |

### Knowledge Assistance

Answers Salesforce questions without making any API calls:
- SOQL syntax help
- Apex coding guidance
- Governor limits
- Security model explanations
- Deployment best practices
- Error message explanations
- Data model design advice

---

## Example Usage

### Query records

```
Show me the last 10 cases with High priority that are still open
```

```
How many users are active in this org? Break it down by profile
```

### User management

```
Create a user John Smith, email john.smith@company.com,
Standard User profile, India timezone
```

```
Reset passwords for all users in this CSV
[attach CSV with Username column]
```

```
What permissions does manaskhare07@empathetic-impala-aoeqac.com have?
```

### Metadata operations

```
Create these fields on the Account object
[attach CSV with Label, Type, Length columns]
```

```
Add picklist values "Enterprise", "SMB", "Startup" to the CustomerTier__c field on Account
```

```
Create a validation rule on Opportunity that blocks saving if
Amount is empty when Stage is Closed Won
```

### Audit investigation

```
What changes were made to profiles and permission sets in the last 30 days?
```

```
Show me all failed login attempts this week
```

```
What did manaskhare07@empathetic-impala-aoeqac.com change in Setup this month?
```

### Debugging

```
Find the latest debug log for manaskhare07@empathetic-impala-aoeqac.com
and tell me what went wrong
```

```
Why is the Order flow failing? Check the last 7 days of flow errors
```

### RCA

```
What automation runs on the Case object?
```

```
Find everything that references the SLAViolation__c field on Case
```

```
Map the execution order for Case on update and flag any conflicts
```

### Test data

```
Create 10 test Cases with varied Status and Priority values for testing
```

```
Create a full order management test data chain
```

### Test class generation

```
Write and run a test class for this handler
[attach ApexClass.cls]

User story: When a Case is created with Priority = High,
the SLA Violation field should be set to Yes automatically
```

---

## Limitations

| Feature | Status | Reason |
| :--- | :--- | :--- |
| Flow creation / modification | Limited | Flow metadata XML complexity — simple flows work, branching logic is unreliable |
| FlexiPage creation | Limited | FlexiPage XML structure is too complex for reliable LLM generation |
| Production org writes | Blocked by design | Safety guardrail — all write operations require a sandbox |
| Multi-org support | Not supported | Single org per session only |
| Full page DOM reading | Partial | Salesforce Lightning Shadow DOM limits deep page inspection — URL and visible text only |

The API layer supports all of these operations fully. Limitations are LLM capability constraints that improve as models get more capable — no architectural changes needed.

---

## Architecture

```
Chrome Extension (sidebar UI)
        |
        | OAuth token + user message
        ↓
FastAPI Backend (localhost:8000)
        |
        | LangChain Agent
        | 64 tools across 12 domains
        ↓
Salesforce Org
  ├── REST API       (records, users, SOQL)
  ├── Tooling API    (Apex, metadata, debug logs)
  ├── Metadata API   (deployments)
  └── Bulk API 2.0   (large data operations)
```

**Tech stack:**

| Layer | Technology |
| :--- | :--- |
| Extension UI | Vanilla JS + HTML/CSS |
| Backend | Python FastAPI |
| AI Orchestration | LangChain |
| LLM | Anthropic Claude / OpenAI GPT-4o / Google Gemini |
| Salesforce APIs | REST + Tooling + Metadata + Bulk API 2.0 |
| Streaming | Server-Sent Events (SSE) |

---

## Notes

- The backend must be running locally while using the extension
- OAuth tokens expire — if you see authentication errors, reconnect via Settings
- All write operations are sandbox-only by design — the extension queries `IsSandbox` on the Organisation object to enforce this
- Your API key is stored in `chrome.storage.local` and never persisted on the backend
- This is a POC build — not intended for production org use