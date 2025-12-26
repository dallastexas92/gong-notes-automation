import asyncio
import logging
import os
from dotenv import load_dotenv
from temporalio.client import Client
from temporalio.worker import Worker
from workflow import ProcessCallNotesWorkflow
from activities import (
    fetch_gong_transcript,
    find_google_doc,
    read_google_doc,
    structure_with_claude,
    append_to_google_doc,
    post_to_slack,
)

# Configure logging to see activity logs in terminal
logging.basicConfig(level=logging.INFO)


async def main():
    load_dotenv()

    # Connect to Temporal Cloud
    client = await Client.connect(
        os.getenv("TEMPORAL_ADDRESS"),
        namespace=os.getenv("TEMPORAL_NAMESPACE"),
        api_key=os.getenv("TEMPORAL_API_KEY"),
        tls=True,
    )

    # Create worker
    worker = Worker(
        client,
        task_queue="gong-notes-queue",
        workflows=[ProcessCallNotesWorkflow],
        activities=[
            fetch_gong_transcript,
            find_google_doc,
            read_google_doc,
            structure_with_claude,
            append_to_google_doc,
            post_to_slack,
        ],
    )

    print("🚀 Worker started. Waiting for workflows...")
    await worker.run()


if __name__ == "__main__":
    asyncio.run(main())
