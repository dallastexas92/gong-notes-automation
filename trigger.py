import asyncio
import os
import sys
from dotenv import load_dotenv
from temporalio.client import Client
from workflow import ProcessCallNotesWorkflow


async def main():
    if len(sys.argv) < 2:
        print("Usage: python trigger.py <call_id>")
        sys.exit(1)

    call_id = sys.argv[1]
    load_dotenv()

    # Connect to Temporal Cloud
    client = await Client.connect(
        os.getenv("TEMPORAL_ADDRESS"),
        namespace=os.getenv("TEMPORAL_NAMESPACE"),
        api_key=os.getenv("TEMPORAL_API_KEY"),
        tls=True,
    )

    # Start workflow
    print(f"🚀 Starting workflow for call: {call_id}")
    handle = await client.start_workflow(
        ProcessCallNotesWorkflow.run,
        args=[call_id],
        id=f"process-call-{call_id}",
        task_queue="gong-notes-queue",
    )

    print(f"📋 Workflow started: {handle.id}")
    print(f"🔗 Workflow URL: https://cloud.temporal.io/namespaces/{os.getenv('TEMPORAL_NAMESPACE')}/workflows/{handle.id}")
    print("\n⏳ Processing...")

    # Wait with timeout to detect if workflow is stuck
    try:
        result = await asyncio.wait_for(handle.result(), timeout=30)
        print(f"✅ {result}")
    except asyncio.TimeoutError:
        # Workflow likely waiting for doc URL
        print("\n⚠️  Could not find Google Doc")
        doc_url = input("Enter Google Doc URL: ").strip()

        if doc_url:
            await handle.signal("provide_doc_url", doc_url)
            print("✓ Continuing workflow...")
            result = await handle.result()
            print(f"✅ {result}")
        else:
            print("❌ No URL provided, workflow will remain blocked")


if __name__ == "__main__":
    asyncio.run(main())
