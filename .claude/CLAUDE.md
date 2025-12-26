# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

# Automated Structured Call Notes System

## What This Does
Temporal workflow that fetches Gong call transcripts, structures them with Claude API, and updates Google Docs with:
1. **Account snapshot** at top (updated each call)
2. **Call-specific notes** appended chronologically

Solves: Commercial SAs lacking quick account context before follow-up calls.

## Tech Stack
- Python 3.12+ with UV for package management
- Temporal Python SDK connecting to Temporal Cloud
- APIs: Gong, Anthropic (Claude Sonnet 4.5), Google Drive/Docs, Slack
- macOS development environment

## API Documentation
Detailed API references are maintained in skills files:
- [Gong API Reference](.claude/skills/gong-api.md) - Call metadata, transcripts, account extraction
- [Google APIs Reference](.claude/skills/google-apis.md) - Drive search, Docs structure, meeting notes blocks

## Common Commands

### Setup
```bash
# Install dependencies
uv sync

# Configure .env (copy from .env.example and fill in values)
cp .env.example .env
```

### Running the System
```bash
# Terminal 1: Start worker (must run first)
python3 worker.py

# Terminal 2: Trigger workflow
python3 trigger.py <gong-call-id>

# Example
python3 trigger.py 123456789
```

### Testing & Debugging
```bash
# Test Google Docs write operations
python3 scripts/test_gdocs.py

# Test Google Drive folder/document search
python3 scripts/test_drive_search.py

# Test date matching logic for meeting notes blocks
python3 scripts/test_date_matching.py

# Generate Google API access token for curl testing
python3 scripts/get-token.py
```

## Architecture Overview

### Workflow Execution Pattern
The system follows a standard Temporal workflow pattern with separate worker and trigger processes:

1. **Worker Process** ([worker.py](worker.py)) - Long-running process that:
   - Connects to Temporal Cloud
   - Registers workflow ([workflow.py](workflow.py)) and activities ([activities.py](activities.py))
   - Polls task queue `gong-notes-queue` for work

2. **Trigger Process** ([trigger.py](trigger.py)) - One-off execution that:
   - Starts a workflow instance with call_id and doc_url
   - Waits for completion and prints result

3. **Workflow** ([workflow.py](workflow.py)) - Orchestrates 4 activities:
   - `fetch_gong_transcript` → `read_google_doc` → `structure_with_claude` → `append_to_google_doc`
   - Each activity has retry policy (3 attempts, exponential backoff)
   - Each activity has timeout (1-2 minutes)

4. **Activities** ([activities.py](activities.py)) - All 5 activities in single file:
   - `fetch_gong_transcript`: Gong API call
   - `read_google_doc`: Extract snapshot section only (stays under 2MB Temporal limit)
   - `structure_with_claude`: Send transcript + snapshot to Claude, get back updated snapshot + call notes
   - `append_to_google_doc`: Replace snapshot, append notes under matching HEADING_2
   - `post_to_slack`: Send completion notification (not currently used in workflow)

### Data Flow
```
Gong API → GongTranscript dataclass → Claude API → dict{snapshot, call_notes} → Google Docs API
```

### Critical Constraints
- **2MB Temporal limit**: `read_google_doc` extracts ONLY the snapshot section, not full doc
- **Activity statelessness**: No instance variables, all state passed via parameters
- **Idempotency**: Activities safe to retry (Google Docs operations use replace/append)

## Google Docs Integration Details

### Snapshot Management
- Wrapped in `=== ACCOUNT SNAPSHOT ===` / `=== END SNAPSHOT ===` markers
- Always at top of document
- Replaced in-place on each workflow run (delete old range, insert new at same location)

### Meeting Notes Block Detection
The system looks for Google Docs HEADING_2 paragraphs that contain:
- A `dateElement` with timestamp (auto-added by `@meeting notes` building block)
- Timestamp nested at: `dateElement.dateElementProperties.timestamp`

Structure:
```json
{
  "paragraph": {
    "elements": [
      {"dateElement": {"dateElementProperties": {"timestamp": "2025-12-23T12:00:00Z"}}},
      {"textRun": {"content": " | "}},
      {"richLink": {"richLinkProperties": {"title": "Call Title"}}}
    ],
    "paragraphStyle": {"namedStyleType": "HEADING_2"}
  }
}
```

### Note Insertion Logic
1. Parse call date from Gong transcript (timestamp or ISO format)
2. Search for HEADING_2 with `dateElement.dateElementProperties.timestamp` matching call date (YYYY-MM-DD)
3. Find "Attendees:" paragraph immediately following the matched HEADING_2
4. Insert notes at `element.endIndex` of "Attendees:" paragraph (right after participants)
5. Batch update order: Insert notes FIRST (using original indices), THEN insert/replace snapshot at top (prevents index shifting)

## API Integration Patterns

### Temporal Cloud Connection
```python
client = await Client.connect(
    os.getenv("TEMPORAL_ADDRESS"),
    namespace=os.getenv("TEMPORAL_NAMESPACE"),
    api_key=os.getenv("TEMPORAL_API_KEY"),
    tls=True,  # Required for API key auth
)
```

### Gong API
See [.claude/skills/gong-api.md](.claude/skills/gong-api.md) for complete endpoint documentation.

Key points:
- Uses POST with JSON body filter, not GET with query params
- Basic auth via `requests.post(..., auth=(api_key, api_secret))`
- Main endpoints: `/v2/calls/extensive` (metadata), `/v2/calls/transcript` (full transcript)
- Account extraction from participant email domains with TLD stripping

### Claude API
- Model: `claude-sonnet-4-5-20250929`
- Prompt in [activities.py:77-138](activities.py#L77-L138) (`STRUCTURING_PROMPT`)
- Expected output: Two sections separated by `---SPLIT---`
- Fallback parsing if Claude doesn't include split marker

### Google Drive/Docs API
See [.claude/skills/google-apis.md](.claude/skills/google-apis.md) for complete API documentation.

Key points:
- Service account auth with required scopes (drive, documents)
- Drive search requires `corpora='allDrives'` for Shared Drives
- Multi-pattern folder search handles domain variations (e.g., "herondata.io" → "Heron Data")
- Docs index positioning: `1` = start of doc, `endIndex-1` = end of doc
- Batch update order matters to avoid index shifting

## Environment Variables

Required in `.env`:
```bash
# Temporal Cloud
TEMPORAL_NAMESPACE=your-namespace.account-id
TEMPORAL_ADDRESS=your-namespace.account-id.tmprl.cloud:7233
TEMPORAL_API_KEY=your-temporal-api-key

# Gong API (Basic Auth)
GONG_API_KEY=your-gong-api-key
GONG_API_SECRET=your-gong-api-secret

# Anthropic Claude API
ANTHROPIC_API_KEY=your-anthropic-api-key

# Google Docs (Service Account JSON path)
GOOGLE_APPLICATION_CREDENTIALS=path/to/service-account.json

# Slack (optional - not used in current workflow)
SLACK_BOT_TOKEN=xoxb-your-slack-bot-token

# Test Configuration
TEST_DOC_URL=https://docs.google.com/document/d/YOUR_DOC_ID/edit
```

## Current Implementation Status

### Working ✅
- Gong API: Fetch transcripts via POST to `/v2/calls/transcript` with account name extraction
- Google Drive API: Multi-pattern folder search with `corpora='allDrives'` support
- Claude API: Two-part output (snapshot + call notes) via Sonnet 4.5
- Google Docs: Read/write with service account auth, date-based meeting block matching
- Note insertion: Correctly finds "Attendees:" paragraph and inserts without index shifting
- Temporal workflow: 4-step process (fetch → read → structure → write) with signals for error recovery
- Worker + trigger scripts functional

### Known Issues & TODO
1. ~~**Doc discovery**: Currently uses hardcoded `TEST_DOC_URL`, should search Drive by account name~~ ✅ **FIXED** - Google Drive search working with multi-pattern fallback
2. ~~**Date matching**: Matches call title but not date - can insert notes under wrong meeting block~~ ✅ **FIXED** - Date matching compares `dateElement.dateElementProperties.timestamp` with call date
3. ~~**Index shifting bug**: Notes inserted at wrong location~~ ✅ **FIXED** - Reordered batch update requests to insert notes first, then snapshot
4. **Error recovery**: Workflow signals implemented for missing doc/block, but trigger.py only handles doc URL signal (not confirm_block_created signal)
5. **No call type logic**: Single prompt for all call types (Discovery, Technical, Check-in, etc.)
6. **Slack activity**: Defined but not called in workflow

## File Structure
```
gong-notes-automation/
├── activities.py              # All Temporal activities (Gong, Claude, GDocs, Slack)
├── workflow.py               # Main ProcessCallNotesWorkflow orchestration
├── worker.py                 # Temporal worker process (polls task queue)
├── trigger.py                # Manual workflow trigger CLI
├── scripts/                  # Testing and debugging utilities
│   ├── test_gdocs.py        # Test Google Docs write operations
│   ├── test_drive_search.py # Test Drive folder/doc search
│   ├── test_date_matching.py # Test meeting notes block date matching
│   └── get-token.py         # Generate service account access token
├── .claude/
│   ├── CLAUDE.md            # This file - project documentation
│   └── skills/
│       ├── gong-api.md      # Gong API reference and patterns
│       └── google-apis.md   # Google Drive/Docs API reference
├── pyproject.toml           # UV dependencies
├── .env                     # API keys (git-ignored)
└── .env.example             # Environment variable template
```

## Development Notes
- Always use `python3` not `python` on macOS
- Service account JSON must have scopes: `documents`, `drive`
- Workflow code changes require worker restart (not hot-reloaded)
- HEADING_2 paragraphs = meeting notes blocks created by `@meeting notes` building block
- Claude prompt expects specific format with `---SPLIT---` separator
- See skills files for detailed API usage patterns

## Future Enhancements (Post-MVP)
- Gong webhook listener for automatic processing
- Google Drive API: auto-create docs per account
- SFDC integration: validate stages, link opportunities
- Account intelligence database for doc URL mapping
- Claude-powered account Q&A interface
- Batch processing for multiple calls
- Prompt iteration based on SA feedback
- Call type-specific prompts (Discovery vs Technical vs Check-in)
- Temporal signals for error recovery (pause workflow, wait for user input, resume)
