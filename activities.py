import os
import base64
import json
import requests
from anthropic import Anthropic
from temporalio import activity
from dataclasses import dataclass
from google.oauth2 import service_account
from googleapiclient.discovery import build


@dataclass
class GongTranscript:
    call_id: str
    title: str
    call_date: str
    account_name: str
    participants: list[dict]
    transcript_text: str


# === GONG API ===

@activity.defn
async def fetch_gong_transcript(call_id: str) -> GongTranscript:
    """Fetch transcript from Gong API."""
    api_key = os.getenv("GONG_API_KEY")
    api_secret = os.getenv("GONG_API_SECRET")

    if not api_key or not api_secret:
        raise ValueError("GONG_API_KEY and GONG_API_SECRET must be set")

    headers = {"Content-Type": "application/json"}
    auth = (api_key, api_secret)

    # Fetch call metadata with parties info using extensive endpoint
    activity.logger.info(f"\n{'='*60}\n[WORKFLOW START] Processing call {call_id}\n{'='*60}")
    activity.logger.info(f"Fetching call metadata for {call_id}")
    meta_response = requests.post(
        "https://api.gong.io/v2/calls/extensive",
        auth=auth,
        headers=headers,
        json={
            "filter": {"callIds": [call_id]},
            "contentSelector": {"exposedFields": {"parties": True}}
        }
    )
    meta_response.raise_for_status()
    call_data = meta_response.json()["calls"][0]

    # Then fetch transcript
    activity.logger.info(f"Fetching transcript for {call_id}")
    transcript_response = requests.post(
        "https://api.gong.io/v2/calls/transcript",
        auth=auth,
        headers=headers,
        json={"filter": {"callIds": [call_id]}}
    )
    transcript_response.raise_for_status()
    call_transcript = transcript_response.json()["callTranscripts"][0]

    # Parse the transcript correctly
    transcript_lines = []
    for entry in call_transcript["transcript"]:
        speaker_id = entry["speakerId"]
        topic = entry.get("topic", "")
        
        # Combine all sentences for this speaker segment
        sentences = entry.get("sentences", [])
        text = " ".join([s["text"] for s in sentences])
        
        # Format: Speaker ID (Topic): Text
        if topic:
            transcript_lines.append(f"Speaker {speaker_id} ({topic}): {text}")
        else:
            transcript_lines.append(f"Speaker {speaker_id}: {text}")

    transcript_text = "\n".join(transcript_lines)

    # Extract account name from customer email domain
    account_name = ""
    metadata = call_data.get("metaData", {})
    parties = call_data.get("parties", [])

    for party in parties:
        email = party.get("emailAddress", "")
        if email and not email.endswith("@temporal.io"):
            domain = email.split("@")[1]

            # Strip common TLDs
            for tld in [".io", ".com", ".net", ".org", ".co"]:
                if domain.endswith(tld):
                    domain = domain[:-len(tld)]
                    break

            # Replace hyphens with spaces and capitalize words
            account_name = domain.replace("-", " ").title()
            break

    return GongTranscript(
        call_id=call_id,
        title=metadata.get("title", ""),
        call_date=metadata.get("scheduled", ""),
        account_name=account_name,
        participants=parties,
        transcript_text=transcript_text
    )


# === CLAUDE API ===

STRUCTURING_PROMPT = """You are creating structured call notes for Temporal AEs and SAs.

You will receive:
1. Current account snapshot (if exists)
2. New call transcript

Output THREE sections separated by "---SUMMARY---" and "---SPLIT---":

**SECTION 1: Updated Account Snapshot**
Format:
```
=== ACCOUNT SNAPSHOT ===
Primary Use Case: [one-line summary]
Current Solution: [what they use today]
Why Temporal: [their main reasons]
Why Now: [urgency/timing]

Key Stakeholders:
- Name (Role) - [involvement]

Business Impact: [what breaks without Temporal]
Timing/Priority: [timeline, urgency]
Workload Sizing: [Low/Med/High + details]
Risks: [blockers, concerns]

Additional Use Cases: [if any]
=== END SNAPSHOT ===
```

Update this section based on new information from the call. Preserve existing details not contradicted by new call.

---SUMMARY---

**SECTION 2: Call Summary** (quick scan - inserted at top of doc)
Format as 3-5 concise bullet points for quick review:
- Primary topic or decision from this call
- Key outcomes or action items
- Critical blockers (if any)
- Notable technical details
- Urgent follow-ups

Guidelines:
- Keep each bullet to one line
- Focus on "need to know" before next interaction
- Use markdown for **emphasis** on critical items

---SPLIT---

**SECTION 3: Detailed Call Notes** (comprehensive - inserted under date block)
Format as conversational bullets:

**Participants**
[Names with phonetic spellings, roles]

**Use Case/Context**
[What they're building, why Temporal]

**Current State**
[Where they are today, scale, adoption status]

**Technical Details**
- SDK/language
- Architecture notes
- Specific challenges discussed

**Why Temporal / Why Now**
[Reasoning, alternatives considered]

**Next Steps**
[Person - action - timing]

**Open Items**
[Unresolved questions]

Guidelines:
- Be conversational and scannable
- Include direct quotes when useful
- Add phonetic spellings: "Lukasz (lukash)"
- Focus on substance over formatting
- Use markdown: **bold** for emphasis, ## for headings, - for bullets"""


@activity.defn
async def structure_with_claude(transcript: GongTranscript, existing_snapshot: str) -> dict:
    """Structure transcript into snapshot + summary + detailed call notes."""
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        raise ValueError("ANTHROPIC_API_KEY must be set")

    client = Anthropic(api_key=api_key)
    activity.logger.info("Sending transcript to Claude for structuring")

    prompt_content = f"""{STRUCTURING_PROMPT}

EXISTING SNAPSHOT:
{existing_snapshot if existing_snapshot else "No existing snapshot - this is the first call"}

NEW CALL:
Title: {transcript.title}
Date: {transcript.call_date}

Transcript:
{transcript.transcript_text}"""

    message = client.messages.create(
        model="claude-sonnet-4-5-j50929",
        max_tokens=4096,
        messages=[{"role": "user", "content": prompt_content}]
    )

    output = message.content[0].text

    # Split output into 3 sections: snapshot, summary, detailed notes
    if "---SUMMARY---" in output and "---SPLIT---" in output:
        # Parse 3 sections (new format)
        parts_1 = output.split("---SUMMARY---")
        snapshot = parts_1[0].strip()

        parts_2 = parts_1[1].split("---SPLIT---")
        summary = parts_2[0].strip()
        call_notes = parts_2[1].strip()

        activity.logger.info("Successfully parsed 3 sections (snapshot, summary, detailed notes)")
    elif "---SPLIT---" in output:
        # Fallback for 2-section format (backward compatibility)
        parts = output.split("---SPLIT---")
        snapshot = parts[0].strip()
        call_notes = parts[1].strip()
        # Generate simple summary from first 500 chars of call notes
        summary = call_notes[:500] + "..." if len(call_notes) > 500 else call_notes
        activity.logger.warning("Falling back to 2-section format, generated summary from call_notes")
    else:
        # Old fallback if no split markers
        snapshot = output[:output.find("=== END SNAPSHOT ===")+len("=== END SNAPSHOT ===")] if "=== END SNAPSHOT ===" in output else ""
        call_notes = output[output.find("=== END SNAPSHOT ===")+len("=== END SNAPSHOT ==="):] if "=== END SNAPSHOT ===" in output else output
        summary = ""
        activity.logger.warning("No split markers found, using old fallback logic")

    activity.logger.info(f"Successfully structured notes - snapshot: {len(snapshot)} chars, summary: {len(summary)} chars, notes: {len(call_notes)} chars")
    return {"snapshot": snapshot, "summary": summary, "call_notes": call_notes}


# === GOOGLE DRIVE ===

@activity.defn
async def find_google_doc(account_name: str) -> str:
    """Search Google Drive for doc by account name. Returns doc URL or empty string."""
    credentials_path = os.getenv("GOOGLE_APPLICATION_CREDENTIALS")
    if not credentials_path:
        raise ValueError("GOOGLE_APPLICATION_CREDENTIALS must be set")

    credentials = service_account.Credentials.from_service_account_file(
        credentials_path,
        scopes=["https://www.googleapis.com/auth/drive"]
    )
    service = build("drive", "v3", credentials=credentials)

    # Try multiple search patterns with decreasing specificity
    # This handles various folder naming patterns:
    # - "Companyname" → "Company Name"
    # - "Acmecorp" → "Acme Corp"
    patterns = [
        account_name,  # Full extracted name
        account_name.lower()[:8],  # First 8 chars
        account_name.lower()[:6],  # First 6 chars
        account_name.lower()[:4],  # First 4 chars
    ]

    activity.logger.info(f"Searching Drive for account: {account_name}")

    folder_id = None
    folder_name = None
    folders_found = []

    for pattern in patterns:
        activity.logger.info(f"  Trying pattern: '{pattern}'")
        query = f"name contains '{pattern}' and mimeType='application/vnd.google-apps.folder'"
        results = service.files().list(
            q=query,
            fields="files(id, name)",
            corpora='allDrives',
            supportsAllDrives=True,
            includeItemsFromAllDrives=True
        ).execute()
        folders = results.get("files", [])

        if folders:
            # If we get exactly 1 match, use it
            if len(folders) == 1:
                folder_id = folders[0]["id"]
                folder_name = folders[0]["name"]
                activity.logger.info(f"✓ Found folder: {folder_name} (ID: {folder_id})")
                break
            # If multiple matches, store them but continue to try more specific patterns
            else:
                if not folders_found:  # Only store first set of multiple matches
                    folders_found = folders
                activity.logger.info(f"Found {len(folders)} folders with pattern '{pattern}': {[f['name'] for f in folders]}")

    # If we found multiple folders but no single match, return empty for user confirmation
    if not folder_id and folders_found:
        activity.logger.warning(f"Multiple folders found, need user confirmation: {[f['name'] for f in folders_found]}")
        return ""

    if not folder_id:
        activity.logger.warning(f"No folder found for: {account_name}")
        return ""

    # Get all docs in folder
    all_docs_query = f"'{folder_id}' in parents and mimeType='application/vnd.google-apps.document'"
    all_results = service.files().list(
        q=all_docs_query,
        fields="files(id, name)",
        pageSize=10,
        corpora='allDrives',
        supportsAllDrives=True,
        includeItemsFromAllDrives=True
    ).execute()
    docs = all_results.get("files", [])

    if not docs:
        activity.logger.warning(f"No docs found in folder: {folder_name}")
        return ""

    activity.logger.info(f"Found {len(docs)} docs in folder: {[d['name'] for d in docs]}")

    # Priority search: prefer docs with "notes" or "use case" in name
    for doc in docs:
        name_lower = doc["name"].lower()
        if "notes" in name_lower or "use case" in name_lower:
            activity.logger.info(f"Selected doc (priority match): {doc['name']}")
            return f"https://docs.google.com/document/d/{doc['id']}/edit"

    # If only 1 doc, use it
    if len(docs) == 1:
        activity.logger.info(f"Selected doc (only one available): {docs[0]['name']}")
        return f"https://docs.google.com/document/d/{docs[0]['id']}/edit"

    # Multiple docs but no clear match - return empty and let workflow signal for user input
    activity.logger.warning(f"Multiple docs found, none with 'notes' or 'use case'. Available: {[d['name'] for d in docs]}")
    return ""


@activity.defn
async def llm_find_google_doc(call_id: str, parties: list[dict]) -> str:
    """
    LLM-powered doc finder: Uses customer email from Gong to search Drive,
    then asks Claude to identify the meeting notes doc.
    Returns doc URL or empty string if not found.

    Args:
        call_id: Gong call ID (for logging)
        parties: Participant data from fetch_gong_transcript (avoids redundant API call)
    """
    # Get API keys
    anthropic_api_key = os.getenv("ANTHROPIC_API_KEY")
    credentials_path = os.getenv("GOOGLE_APPLICATION_CREDENTIALS")

    if not all([anthropic_api_key, credentials_path]):
        raise ValueError("Missing required API credentials")

    # Step 1: Extract customer participants from provided parties data
    activity.logger.info(f"Finding doc for call {call_id} using provided participant data")
    customer_participants = []

    for party in parties:
        email = party.get("emailAddress", "")
        name = party.get("name", "")
        if email and not email.endswith("@temporal.io"):
            customer_participants.append({"email": email, "name": name})

    if not customer_participants:
        activity.logger.warning("No customer participants found")
        return ""

    activity.logger.info(f"Found {len(customer_participants)} customer participant(s)")

    # Step 2: Search Google Drive
    credentials = service_account.Credentials.from_service_account_file(
        credentials_path,
        scopes=["https://www.googleapis.com/auth/drive"]
    )
    service = build("drive", "v3", credentials=credentials)

    # First try: Search by company folder
    domain = customer_participants[0]["email"].split("@")[1]
    company_prefix = domain.split(".")[0]

    activity.logger.info(f"Searching for folders matching '{company_prefix}'")
    folder_query = f"name contains '{company_prefix}' and mimeType='application/vnd.google-apps.folder'"
    folder_results = service.files().list(
        q=folder_query,
        fields="files(id, name)",
        pageSize=20,
        corpora='allDrives',
        supportsAllDrives=True,
        includeItemsFromAllDrives=True
    ).execute()

    folders = folder_results.get("files", [])
    folder_docs_found = []

    if folders:
        activity.logger.info(f"Found {len(folders)} folder(s)")
        for folder in folders:
            # Get docs in this folder
            docs_in_folder_query = f"'{folder['id']}' in parents and mimeType='application/vnd.google-apps.document'"
            docs_in_folder_results = service.files().list(
                q=docs_in_folder_query,
                fields="files(id, name)",
                pageSize=10,
                corpora='allDrives',
                supportsAllDrives=True,
                includeItemsFromAllDrives=True
            ).execute()
            docs_in_folder = docs_in_folder_results.get("files", [])
            if docs_in_folder:
                for doc in docs_in_folder:
                    folder_docs_found.append({"id": doc["id"], "name": doc["name"], "folder": folder["name"]})

    # Second try: Search for docs containing customer email addresses
    docs_by_email = []
    matched_email = ""
    matched_name = ""

    for participant in customer_participants:
        email = participant["email"]
        name = participant["name"]

        activity.logger.info(f"Searching for docs containing {email}")
        query = f"fullText contains '{email}' and mimeType='application/vnd.google-apps.document'"
        results = service.files().list(
            q=query,
            fields="files(id, name)",
            pageSize=20,
            corpora='allDrives',
            supportsAllDrives=True,
            includeItemsFromAllDrives=True
        ).execute()

        docs = results.get("files", [])
        if docs:
            activity.logger.info(f"Found {len(docs)} doc(s) containing {email}")
            docs_by_email = [{"id": d["id"], "name": d["name"]} for d in docs]
            matched_email = email
            matched_name = name
            break

    # Determine which docs to use
    if docs_by_email:
        docs_to_check = docs_by_email
        match_type = "email"
    elif folder_docs_found:
        docs_to_check = folder_docs_found
        match_type = "folder"
        matched_email = customer_participants[0]["email"]
        matched_name = customer_participants[0]["name"]
    else:
        activity.logger.warning("No docs found by email or folder search")
        return ""

    activity.logger.info(f"Found {len(docs_to_check)} docs via {match_type} search")

    # Step 3: Ask Claude to identify the meeting notes doc
    client = Anthropic(api_key=anthropic_api_key)

    prompt = f"""You are helping find the correct Google Doc for meeting notes.

We searched Google Drive for docs containing this customer's email and found these results:

Customer: {matched_name} <{matched_email}>

Documents found:
{json.dumps(docs_to_check, indent=2)}

Task: Identify which document is used to write MEETING NOTES after each call.

Important context:
- "Use Case" docs often ALSO contain meeting notes sections
- If there's BOTH a dedicated "Notes" doc AND a "Use Case" doc, prefer the Notes doc
- If there's ONLY a "Use Case" doc, it likely contains the meeting notes - select it
- Exclude docs that are clearly not for notes (e.g., "Sales Deck", "Proposal", "Contract")

Return ONLY valid JSON in one of these formats:

Single match (high confidence):
{{"doc_id": "abc123", "doc_name": "Meeting Notes", "confidence": "high", "reasoning": "..."}}

Multiple matches (need user choice):
{{"options": [{{"doc_id": "...", "doc_name": "..."}}, ...], "needs_user_choice": true, "reasoning": "..."}}

No valid doc found:
{{"error": "No meeting notes doc found", "reasoning": "..."}}
"""

    activity.logger.info("Asking Claude to identify meeting notes doc")
    message = client.messages.create(
        model="claude-sonnet-4-5-20250929",
        max_tokens=1024,
        messages=[{"role": "user", "content": prompt}]
    )

    output = message.content[0].text
    activity.logger.info(f"Claude response received: {output[:100]}...")

    # Parse JSON response
    try:
        # Extract JSON from markdown code blocks if present
        if "```json" in output:
            json_str = output.split("```json")[1].split("```")[0].strip()
        elif "```" in output:
            json_str = output.split("```")[1].split("```")[0].strip()
        else:
            json_str = output.strip()

        result = json.loads(json_str)
    except json.JSONDecodeError as e:
        activity.logger.error(f"Failed to parse Claude response as JSON: {e}")
        return ""

    # Handle result
    if "error" in result:
        activity.logger.warning(f"Claude found no valid doc: {result.get('reasoning')}")
        return ""

    elif result.get("needs_user_choice"):
        activity.logger.warning(f"Multiple docs found, need user choice: {result.get('reasoning')}")
        # Return empty to trigger workflow signal for user input
        return ""

    else:
        doc_id = result.get("doc_id")
        doc_name = result.get("doc_name", "Unknown")
        confidence = result.get("confidence", "unknown")
        reasoning = result.get("reasoning", "N/A")

        activity.logger.info(f"Found meeting notes doc: {doc_name} (confidence: {confidence})")
        activity.logger.info(f"Reasoning: {reasoning}")

        return f"https://docs.google.com/document/d/{doc_id}/edit"


# === GOOGLE DOCS ===

SNAPSHOT_MARKER_START = "=== ACCOUNT SNAPSHOT ==="
SNAPSHOT_MARKER_END = "=== END SNAPSHOT ==="

@activity.defn
async def read_google_doc(doc_url: str) -> str:
    """Read existing doc content (snapshot section only to stay under 2MB)."""
    credentials_path = os.getenv("GOOGLE_APPLICATION_CREDENTIALS")
    if not credentials_path:
        raise ValueError("GOOGLE_APPLICATION_CREDENTIALS must be set")

    doc_id = doc_url.split("/d/")[1].split("/")[0]
    credentials = service_account.Credentials.from_service_account_file(
        credentials_path,
        scopes=["https://www.googleapis.com/auth/documents"]
    )
    service = build("docs", "v1", credentials=credentials)

    activity.logger.info(f"Reading doc {doc_id}")
    doc = service.documents().get(documentId=doc_id).execute()

    # Extract text content
    full_text = ""
    for element in doc.get("body", {}).get("content", []):
        if "paragraph" in element:
            for text_run in element["paragraph"].get("elements", []):
                if "textRun" in text_run:
                    full_text += text_run["textRun"]["content"]

    # Extract only snapshot section if it exists
    if SNAPSHOT_MARKER_START in full_text and SNAPSHOT_MARKER_END in full_text:
        start = full_text.find(SNAPSHOT_MARKER_START)
        end = full_text.find(SNAPSHOT_MARKER_END) + len(SNAPSHOT_MARKER_END)
        return full_text[start:end]

    return ""  # No existing snapshot


@activity.defn
async def append_to_google_doc(snapshot: str, summary: str, call_notes: str, doc_url: str, call_date: str) -> bool:
    """Append formatted notes to Google Doc (summary at top + detailed notes under date block)."""
    credentials_path = os.getenv("GOOGLE_APPLICATION_CREDENTIALS")
    if not credentials_path:
        raise ValueError("GOOGLE_APPLICATION_CREDENTIALS must be set")

    # Extract doc ID from URL
    doc_id = doc_url.split("/d/")[1].split("/")[0]

    # Authenticate
    credentials = service_account.Credentials.from_service_account_file(
        credentials_path,
        scopes=["https://www.googleapis.com/auth/documents"]
    )
    service = build("docs", "v1", credentials=credentials)

    activity.logger.info(f"Updating doc {doc_id}")

    # Read current doc to find snapshot location
    doc = service.documents().get(documentId=doc_id).execute()
    full_text = ""
    for element in doc.get("body", {}).get("content", []):
        if "paragraph" in element:
            for text_run in element["paragraph"].get("elements", []):
                if "textRun" in text_run:
                    full_text += text_run["textRun"]["content"]

    requests_body = []
    from datetime import datetime

    # STEP 1: Find and insert call notes FIRST (before any indices shift)
    # Parse call date (format: "1518863400" timestamp or ISO string)
    try:
        if call_date.isdigit():
            call_datetime = datetime.fromtimestamp(int(call_date))
        else:
            call_datetime = datetime.fromisoformat(call_date.replace("Z", "+00:00"))
        call_date_str = call_datetime.strftime("%Y-%m-%d")
        activity.logger.info(f"Looking for meeting block with date: {call_date_str}")
    except Exception as e:
        raise Exception(f"Failed to parse call date '{call_date}': {e}")

    content_elements = doc.get("body", {}).get("content", [])
    found_matching_heading = False
    insert_index = None

    for element in content_elements:
        if "paragraph" not in element:
            continue

        para = element["paragraph"]

        # Look for HEADING_2 with matching date
        if not found_matching_heading and para.get("paragraphStyle", {}).get("namedStyleType") == "HEADING_2":
            for el in para.get("elements", []):
                if "dateElement" in el:
                    # Timestamp is nested in dateElementProperties
                    timestamp = el["dateElement"].get("dateElementProperties", {}).get("timestamp", "")
                    if timestamp:
                        block_date = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
                        block_date_str = block_date.strftime("%Y-%m-%d")
                        activity.logger.info(f"Comparing call date {call_date_str} with block date {block_date_str}")
                        if call_date_str == block_date_str:
                            found_matching_heading = True
                            activity.logger.info(f"✓ Found matching HEADING_2 for date {call_date_str}")
                            break

        # After finding the heading, look for "Attendees:" paragraph (always present)
        elif found_matching_heading:
            for el in para.get("elements", []):
                if "textRun" in el:
                    content = el["textRun"].get("content", "")
                    # Check if this paragraph contains "Attendees:"
                    if "Attendees:" in content or "attendees:" in content.lower():
                        insert_index = element.get("endIndex")
                        activity.logger.info(f"Found 'Attendees' paragraph at index {insert_index}")
                        break
            if insert_index:
                break

    # If no matching block found, raise exception to trigger workflow signal
    if not insert_index:
        raise Exception(f"No matching meeting notes block found for date {call_date_str}. Please create the meeting notes building block in Google Doc for this date, then send confirm_block_created signal.")

    # Insert call notes at the found location
    requests_body.append({
        "insertText": {
            "location": {"index": insert_index},
            "text": f"\n{call_notes}\n\n"
        }
    })
    activity.logger.info(f"Added call notes insert request at index {insert_index}")

    # STEP 2: Now handle snapshot (insert or replace)
    # This happens AFTER notes insertion, so indices won't affect the notes location
    if SNAPSHOT_MARKER_START in full_text and SNAPSHOT_MARKER_END in full_text:
        # Find and delete old snapshot
        start_idx = full_text.find(SNAPSHOT_MARKER_START)
        end_idx = full_text.find(SNAPSHOT_MARKER_END) + len(SNAPSHOT_MARKER_END)
        requests_body.append({
            "deleteContentRange": {
                "range": {"startIndex": start_idx + 1, "endIndex": end_idx + 1}
            }
        })
        # Insert new snapshot at same location
        requests_body.append({
            "insertText": {
                "location": {"index": start_idx + 1},
                "text": snapshot
            }
        })
        activity.logger.info(f"Added snapshot replace request at index {start_idx + 1}")
    else:
        # Insert snapshot at beginning
        requests_body.append({
            "insertText": {
                "location": {"index": 1},
                "text": f"{snapshot}\n\n"
            }
        })
        activity.logger.info("Added snapshot insert request at index 1")

    service.documents().batchUpdate(
        documentId=doc_id,
        body={"requests": requests_body}
    ).execute()

    activity.logger.info("Successfully updated doc")
    return True


# === SLACK ===

@activity.defn
async def post_to_slack(call_id: str, doc_url: str) -> bool:
    """Post confirmation to Slack."""
    token = os.getenv("SLACK_BOT_TOKEN")
    if not token:
        raise ValueError("SLACK_BOT_TOKEN must be set")

    channel = os.getenv("SLACK_CHANNEL", "#gong-notes")

    activity.logger.info(f"Posting to Slack channel {channel}")

    response = requests.post(
        "https://slack.com/api/chat.postMessage",
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json"
        },
        json={
            "channel": channel,
            "text": f"✅ Processed call `{call_id}` - Notes added to <{doc_url}|Google Doc>"
        }
    )

    response.raise_for_status()
    result = response.json()

    if not result.get("ok"):
        raise Exception(f"Slack API error: {result.get('error')}")

    activity.logger.info("Successfully posted to Slack")
    return True
