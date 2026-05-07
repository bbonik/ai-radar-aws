"""Lambda 1 entry point for the AI Radar AWS Report Generation Pipeline.

This handler is invoked by EventBridge on a daily schedule. It initializes
the PipelineOrchestrator with a unique correlation ID (run_id) and executes
the full pipeline: fetch → deduplicate → filter → classify → research →
report → graph → store → invoke website builder.
"""

import uuid

from src.config import Config
from src.shared.logger import StructuredLogger
from src.pipeline.orchestrator import PipelineOrchestrator


def handler(event: dict, context) -> dict:
    """Lambda 1 handler — Report Generation Pipeline.

    Args:
        event: The EventBridge event payload (not used for processing).
        context: The Lambda execution context (provides remaining time info).

    Returns:
        A dict with statusCode and summary of the pipeline run.
    """
    # Generate a unique correlation ID for this pipeline run
    run_id = str(uuid.uuid4())

    # Initialize configuration and structured logger
    config = Config()
    logger = StructuredLogger(lambda_name="report-pipeline", run_id=run_id)

    logger.info("Lambda 1 handler invoked", event_source=event.get("source", "unknown"))

    try:
        # Create and run the pipeline orchestrator
        orchestrator = PipelineOrchestrator(config=config, context=context, logger=logger)
        summary = orchestrator.run()

        return {
            "statusCode": 200,
            "body": {
                "run_id": summary.run_id,
                "total_fetched": summary.total_fetched,
                "total_relevant": summary.total_relevant,
                "total_processed_ok": summary.total_processed_ok,
                "total_failed": summary.total_failed,
                "website_builder_invoked": summary.website_builder_invoked,
            },
        }

    except Exception as exc:
        logger.error(
            "Pipeline run failed with unhandled exception",
            error_type=type(exc).__name__,
            error_message=str(exc),
        )
        return {
            "statusCode": 500,
            "body": {
                "run_id": run_id,
                "error": str(exc),
            },
        }
