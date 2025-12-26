#!/usr/bin/env python3
"""
Test Google Docs API write operations.

Usage: python scripts/test_gdocs.py

Writes test notes to the beginning of the doc specified in TEST_DOC_URL.
Use this to verify service account permissions and API connectivity.
"""
import os
from dotenv import load_dotenv
from google.oauth2 import service_account
from googleapiclient.discovery import build

load_dotenv()

# Test data
TEST_NOTES = """
# Call Notes - Test Entry

## Header
Date: 2024-01-15
Call Type: Technical Deep Dive

## Participants
- Temporal: John Doe (SA)
- Customer: Jane Smith (Engineering Manager)

## Call Summary
Customer exploring Temporal for workflow orchestration. Deep dive into durable execution model.

---
"""

def main():
    credentials_path = os.getenv("GOOGLE_APPLICATION_CREDENTIALS")
    doc_url = os.getenv("TEST_DOC_URL")

    if not credentials_path or not doc_url:
        print("ERROR: Set GOOGLE_APPLICATION_CREDENTIALS and TEST_DOC_URL in .env")
        return

    # Extract doc ID from URL
    doc_id = doc_url.split("/d/")[1].split("/")[0]
    print(f"Doc ID: {doc_id}")

    # Authenticate
    credentials = service_account.Credentials.from_service_account_file(
        credentials_path,
        scopes=["https://www.googleapis.com/auth/documents"]
    )
    service = build("docs", "v1", credentials=credentials)

    # Append to end of document
    print("Appending test notes...")
    requests_body = [
        {
            "insertText": {
                "location": {"index": 1},  # 1 = beginning of doc
                "text": TEST_NOTES
            }
        }
    ]

    result = service.documents().batchUpdate(
        documentId=doc_id,
        body={"requests": requests_body}
    ).execute()

    print(f"✅ Success! Added {len(TEST_NOTES)} characters to doc")
    print(f"View: {doc_url}")

if __name__ == "__main__":
    main()
