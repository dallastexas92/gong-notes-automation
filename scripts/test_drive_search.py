#!/usr/bin/env python3
"""
Test Google Drive folder/document search functionality.

Usage: python scripts/test_drive_search.py

Lists all accessible folders and tests search patterns to verify:
- Service account has access to Shared Drives
- Folder search with `corpora='allDrives'` works correctly
- Document listing within folders works

Useful for debugging Drive permissions and search logic.
"""

import os
from dotenv import load_dotenv
from google.oauth2 import service_account
from googleapiclient.discovery import build

load_dotenv()

credentials = service_account.Credentials.from_service_account_file(
    os.getenv("GOOGLE_APPLICATION_CREDENTIALS"),
    scopes=["https://www.googleapis.com/auth/drive"]
)
service = build("drive", "v3", credentials=credentials)

# First, list ALL folders the service account can see
print("=== ALL FOLDERS ACCESSIBLE TO SERVICE ACCOUNT ===\n")
all_folders_query = "mimeType='application/vnd.google-apps.folder'"
all_results = service.files().list(
    q=all_folders_query,
    fields="files(id, name)",
    pageSize=50,
    corpora='allDrives',
    supportsAllDrives=True,
    includeItemsFromAllDrives=True
).execute()

all_folders = all_results.get("files", [])
if all_folders:
    print(f"Found {len(all_folders)} total folders:")
    for folder in all_folders:
        print(f"  - {folder['name']} (ID: {folder['id']})")
else:
    print("No folders accessible to this service account")

print("\n" + "="*60 + "\n")

# Test different company names to verify we can find their docs
search_patterns = [
    "heron",         # Test Heron Data (partial match)
    "Herondata",     # Test Heron Data (full extracted name)
    "Redis",         # Test Redis
    "Viz",           # Test Viz
]

print("Testing Google Drive folder search patterns:\n")

for pattern in search_patterns:
    query = f"name contains '{pattern}' and mimeType='application/vnd.google-apps.folder'"
    print(f"Pattern: '{pattern}'")
    print(f"Query: {query}")

    results = service.files().list(
        q=query,
        fields="files(id, name)",
        pageSize=10,
        corpora='allDrives',
        supportsAllDrives=True,
        includeItemsFromAllDrives=True
    ).execute()

    folders = results.get("files", [])

    if folders:
        print(f"✓ Found {len(folders)} folder(s):")
        for folder in folders:
            print(f"  - {folder['name']} (ID: {folder['id']})")

            # Search for docs inside this folder
            doc_query = f"'{folder['id']}' in parents and mimeType='application/vnd.google-apps.document'"
            doc_results = service.files().list(
                q=doc_query,
                fields="files(id, name)",
                pageSize=5,
                corpora='allDrives',
                supportsAllDrives=True,
                includeItemsFromAllDrives=True
            ).execute()
            docs = doc_results.get("files", [])

            if docs:
                print(f"    Documents in folder:")
                for doc in docs:
                    print(f"      - {doc['name']}")
    else:
        print("✗ No folders found")

    print()
