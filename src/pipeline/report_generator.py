"""Report Generator module for the AI Radar AWS pipeline.

Uses Amazon Bedrock (Claude Sonnet via global cross-region inference profile)
to generate structured reports for each announcement. Constructs prompts from
config templates, announcement data, and research context. Retries up to 2×
on failure with 1s delay.
"""

import json
import os
import time

import boto3
from botocore.exceptions import ClientError

from src.config import Config
from src.shared.logger import StructuredLogger
from src.shared.models import Report, ResearchContext, RSSItem


# Section markers used to parse the LLM response
_SECTION_MARKERS = [
    "[WHATS_NEW]",
    "[HOW_IT_WORKS]",
    "[WHY_IMPORTANT]",
    "[HOW_DIFFERENT]",
    "[WHEN_TO_PREFER]",
    "[AVAILABILITY]",
]

# Maximum retries for Bedrock API calls
_MAX_RETRIES = 2

# Delay between retries in seconds
_RETRY_DELAY_SECONDS = 1


class ReportGenerationError(Exception):
    """Raised when report generation fails after all retries."""

    pass


class ReportGenerator:
    """Generates structured reports for announcements using Amazon Bedrock.

    Constructs a prompt from the config template, announcement data, and
    research context, then calls Bedrock invoke_model using the application
    inference profile ARN for LLM A (Claude Sonnet) with global cross-region
    inference profile as the model source.

    Retries up to 2× on failure with 1s delay. On persistent failure,
    raises ReportGenerationError for pipeline error handling.
    """

    def __init__(self, config: Config, logger: StructuredLogger) -> None:
        self._config = config
        self._logger = logger
        self._bedrock_client = boto3.client(
            "bedrock-runtime",
            region_name=config.aws_region,
        )

    def generate(self, item: RSSItem, research: ResearchContext) -> Report:
        """Generate a structured report for an announcement.

        Args:
            item: The RSS announcement item to generate a report for.
            research: The research context gathered for this announcement.

        Returns:
            A Report object with all six sections populated.

        Raises:
            ReportGenerationError: If report generation fails after all retries.
        """
        prompt = self._build_prompt(item, research)
        response_text = self._invoke_bedrock(prompt, item.link)
        report = self._parse_response(response_text, item.link)
        return report

    def _build_prompt(self, item: RSSItem, research: ResearchContext) -> str:
        """Construct the prompt from config template + announcement data + research context."""
        research_context_text = self._format_research_context(research)

        prompt = self._config.report_prompt_template.format(
            title=item.title,
            description=item.description,
            pub_date=item.pub_date,
            link=item.link,
            research_context=research_context_text,
        )
        return prompt

    def _format_research_context(self, research: ResearchContext) -> str:
        """Format the research context into a readable string for the prompt."""
        if research.skipped:
            return "Research was skipped due to time constraints. Use only the announcement data above."

        if not research.gathered_content:
            return "No additional research content was gathered. Use only the announcement data above."

        parts: list[str] = []
        for page in research.gathered_content:
            # Limit each page's content to avoid exceeding token limits
            text_preview = page.text[:3000] if len(page.text) > 3000 else page.text
            parts.append(
                f"### Source: {page.title or page.url}\n"
                f"URL: {page.url}\n"
                f"Content:\n{text_preview}\n"
            )

        return "\n".join(parts)

    def _invoke_bedrock(self, prompt: str, announcement_link: str) -> str:
        """Call Bedrock invoke_model with retry logic.

        Uses the application inference profile ARN for LLM A. The inference
        profile ARN is read from the LLM_A_INFERENCE_PROFILE_ARN environment
        variable (set by CDK), falling back to the model ID for direct invocation.

        Retries up to 2× on failure with 1s delay between attempts.

        Raises:
            ReportGenerationError: If all attempts fail.
        """
        # Use inference profile ARN from environment (set by CDK stack)
        # Falls back to model ID for local testing
        model_id = os.environ.get(
            "INFERENCE_PROFILE_A_ARN",
            self._config.llm_a_model_id,
        )

        body = json.dumps({
            "anthropic_version": "bedrock-2023-05-31",
            "max_tokens": self._config.llm_a_max_tokens,
            "temperature": self._config.llm_a_temperature,
            "messages": [
                {
                    "role": "user",
                    "content": prompt,
                }
            ],
        })

        last_error: Exception | None = None

        for attempt in range(1, _MAX_RETRIES + 2):  # 1 initial + 2 retries = 3 attempts
            try:
                response = self._bedrock_client.invoke_model(
                    modelId=model_id,
                    contentType="application/json",
                    accept="application/json",
                    body=body,
                )

                response_body = json.loads(response["body"].read())
                # Extract text from Claude's response format
                content = response_body.get("content", [])
                if content and isinstance(content, list):
                    text_parts = [
                        block.get("text", "")
                        for block in content
                        if block.get("type") == "text"
                    ]
                    return "\n".join(text_parts)

                # Unexpected response format
                raise ReportGenerationError(
                    f"Unexpected Bedrock response format: {response_body}"
                )

            except ClientError as exc:
                last_error = exc
                error_code = exc.response.get("Error", {}).get("Code", "Unknown")
                self._logger.error(
                    "Bedrock API call failed",
                    announcement_link=announcement_link,
                    attempt=attempt,
                    max_attempts=_MAX_RETRIES + 1,
                    error_type=type(exc).__name__,
                    error_code=error_code,
                    error_message=str(exc),
                )

                if attempt <= _MAX_RETRIES:
                    time.sleep(_RETRY_DELAY_SECONDS)
                    continue

            except Exception as exc:
                last_error = exc
                self._logger.error(
                    "Bedrock invocation error",
                    announcement_link=announcement_link,
                    attempt=attempt,
                    max_attempts=_MAX_RETRIES + 1,
                    error_type=type(exc).__name__,
                    error_message=str(exc),
                )

                if attempt <= _MAX_RETRIES:
                    time.sleep(_RETRY_DELAY_SECONDS)
                    continue

        raise ReportGenerationError(
            f"Report generation failed after {_MAX_RETRIES + 1} attempts "
            f"for announcement: {announcement_link}. "
            f"Last error: {last_error}"
        )

    def _parse_response(self, response_text: str, announcement_link: str) -> Report:
        """Parse the LLM response into a structured Report object.

        Extracts content between section markers. If parsing fails or sections
        are missing, raises ReportGenerationError.
        """
        sections = self._extract_sections(response_text)

        # Validate all sections are present and non-empty
        missing_sections = [
            marker for marker, content in sections.items()
            if not content.strip()
        ]

        if missing_sections:
            self._logger.warning(
                "Report response missing sections",
                announcement_link=announcement_link,
                missing_sections=missing_sections,
            )
            raise ReportGenerationError(
                f"Report response missing sections: {missing_sections} "
                f"for announcement: {announcement_link}"
            )

        return Report(
            whats_new=sections["[WHATS_NEW]"].strip(),
            how_it_works=sections["[HOW_IT_WORKS]"].strip(),
            why_important=sections["[WHY_IMPORTANT]"].strip(),
            how_different=sections["[HOW_DIFFERENT]"].strip(),
            when_to_prefer=sections["[WHEN_TO_PREFER]"].strip(),
            availability=sections["[AVAILABILITY]"].strip(),
        )

    def _extract_sections(self, text: str) -> dict[str, str]:
        """Extract content between section markers from the LLM response.

        Returns a dict mapping marker names to their content.
        Missing markers will have empty string values.
        """
        sections: dict[str, str] = {marker: "" for marker in _SECTION_MARKERS}

        for i, marker in enumerate(_SECTION_MARKERS):
            start_idx = text.find(marker)
            if start_idx == -1:
                continue

            # Content starts after the marker
            content_start = start_idx + len(marker)

            # Content ends at the next marker or end of text
            if i + 1 < len(_SECTION_MARKERS):
                next_marker = _SECTION_MARKERS[i + 1]
                end_idx = text.find(next_marker, content_start)
                if end_idx == -1:
                    end_idx = len(text)
            else:
                end_idx = len(text)

            sections[marker] = text[content_start:end_idx]

        return sections
