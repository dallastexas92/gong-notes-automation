from datetime import timedelta
from temporalio import workflow
from temporalio.common import RetryPolicy

with workflow.unsafe.imports_passed_through():
    from activities import (
        fetch_gong_transcript,
        llm_find_google_doc,
        read_google_doc,
        structure_with_claude,
        append_to_google_doc,
        post_to_slack,
    )


@workflow.defn
class ProcessCallNotesWorkflow:
    def __init__(self):
        self.doc_url: str = ""
        self.block_confirmed: bool = False

    @workflow.signal
    async def provide_doc_url(self, url: str):
        """Signal to provide doc URL when auto-discovery fails."""
        self.doc_url = url

    @workflow.signal
    async def confirm_block_created(self):
        """Signal to confirm user created the date block."""
        self.block_confirmed = True

    @workflow.run
    async def run(self, call_id: str) -> str:
        """
        Main workflow: Gong → Claude → Google Docs → Slack
        """
        retry_policy = RetryPolicy(
            maximum_attempts=3,
            initial_interval=timedelta(seconds=1),
            maximum_interval=timedelta(seconds=10),
        )

        # Step 1: Fetch transcript from Gong
        transcript = await workflow.execute_activity(
            fetch_gong_transcript,
            call_id,
            start_to_close_timeout=timedelta(minutes=2),
            retry_policy=retry_policy,
        )

        # Step 2: Find Google Doc using LLM-powered search (reuses parties from transcript)
        found_url = await workflow.execute_activity(
            llm_find_google_doc,
            args=[call_id, transcript.participants],
            start_to_close_timeout=timedelta(minutes=2),
            retry_policy=retry_policy,
        )

        if not found_url:
            workflow.logger.info(f"Doc not found for {transcript.account_name}, waiting for user signal...")
            await workflow.wait_condition(lambda: self.doc_url != "")
            found_url = self.doc_url

        # Return early for testing - just validate doc finding
        workflow.logger.info(f"✓ Found doc: {found_url}")
        workflow.logger.info(f"\n{'='*60}\n[WORKFLOW COMPLETE] ✅ Success!\n{'='*60}\n")
        return f"Successfully found doc for call {call_id}: {found_url}"

        # # Step 3: Read existing doc snapshot
        # existing_snapshot = await workflow.execute_activity(
        #     read_google_doc,
        #     found_url,
        #     start_to_close_timeout=timedelta(minutes=1),
        #     retry_policy=retry_policy,
        # )

        # # Step 4: Structure with Claude
        # structured_output = await workflow.execute_activity(
        #     structure_with_claude,
        #     args=[transcript, existing_snapshot],
        #     start_to_close_timeout=timedelta(minutes=2),
        #     retry_policy=retry_policy,
        # )

        # # Step 5: Try to update Google Doc
        # try:
        #     await workflow.execute_activity(
        #         append_to_google_doc,
        #         args=[structured_output["snapshot"], structured_output["call_notes"], found_url, transcript.call_date],
        #         start_to_close_timeout=timedelta(minutes=2),
        #         retry_policy=RetryPolicy(maximum_attempts=1),  # Don't retry if date block missing
        #     )
        # except Exception as e:
        #     if "no matching" in str(e).lower():
        #         workflow.logger.info(f"Date block not found for {transcript.call_date}, waiting for user signal...")
        #         await workflow.wait_condition(lambda: self.block_confirmed)
        #         # Retry after confirmation
        #         await workflow.execute_activity(
        #             append_to_google_doc,
        #             args=[structured_output["snapshot"], structured_output["call_notes"], found_url, transcript.call_date],
        #             start_to_close_timeout=timedelta(minutes=2),
        #             retry_policy=retry_policy,
        #         )
        #     else:
        #         raise

        # return f"Successfully processed call {call_id} - snapshot updated and notes appended"
