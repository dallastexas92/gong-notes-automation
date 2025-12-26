# Gong Notes Automation

Temporal workflow that automatically structures Gong call transcripts with Claude AI and updates Google Docs with organized notes for SA teams.

## What It Does

1. Fetches call transcript from Gong
2. Extracts account info from participant emails
3. Uses Claude to generate:
   - Account snapshot (updated each call)
   - Structured call notes
4. Updates Google Doc:
   - Replaces snapshot at top
   - Appends notes to matching meeting block by date

## Quick Start

```bash
# Install dependencies
uv sync

# Configure environment
cp .env.example .env
# Edit .env with your API keys

# Terminal 1: Start worker
python3 worker.py

# Terminal 2: Trigger workflow
python3 trigger.py <gong-call-id>
```

## Requirements

- Python 3.12+
- [UV](https://docs.astral.sh/uv/) package manager
- API keys for: Temporal Cloud, Gong, Anthropic, Google Cloud
- Google service account with Drive + Docs access

## Environment Setup

Copy `.env.example` to `.env` and configure:

- **Temporal Cloud**: namespace, address, API key
- **Gong**: API key + secret
- **Anthropic**: API key for Claude
- **Google**: Service account JSON path
- **Test**: Google Doc URL for testing

## Google Docs Setup

1. Create meeting notes blocks using `@meeting notes` building block
2. Ensure service account has edit access to your shared drive
3. Workflow will:
   - Search Drive for folder matching account name
   - Find doc in folder
   - Match meeting block by call date
   - Insert notes after "Attendees:" section

## Testing

```bash
# Test Google Docs writes
python3 scripts/test_gdocs.py

# Test Drive folder search
python3 scripts/test_drive_search.py

# Test date matching logic
python3 scripts/test_date_matching.py
```

## Architecture

- **Temporal Workflow**: Orchestrates 4 activities with retries
- **Activities**: Gong API, Claude API, Google Drive/Docs, Slack
- **Worker**: Long-running process polling `gong-notes-queue`
- **Trigger**: CLI to start workflows manually

See [.claude/CLAUDE.md](.claude/CLAUDE.md) for detailed documentation.

## Project Structure

```
├── activities.py              # All Temporal activities
├── workflow.py               # Main workflow orchestration
├── worker.py                 # Temporal worker process
├── trigger.py                # Manual workflow trigger
├── scripts/                  # Testing utilities
├── .claude/
│   ├── CLAUDE.md            # Detailed documentation
│   └── skills/              # API reference docs
└── pyproject.toml           # Dependencies
```

## Known Limitations

- Single prompt for all call types (no Discovery vs Technical differentiation)
- Manual trigger only (no webhook listener yet)
- Requires pre-created meeting notes blocks in Google Doc

## Future Enhancements

- Gong webhook integration for automatic processing
- Call type-specific prompts
- Auto-create Google Docs per account
- Salesforce integration
- Batch processing for multiple calls

## License

MIT
