"""Graph Generator module for the AI Radar AWS pipeline.

Uses Amazon Bedrock (Claude Opus via global cross-region inference profile)
to generate Mermaid diagrams for 3-star and above announcements. Skips
generation for 1-star and 2-star announcements. Retries up to 2× on failure
with 1s delay; returns None on persistent failure.
"""

import json
import os
import re
import time

import boto3
from botocore.exceptions import ClientError

from src.config import Config
from src.shared.logger import StructuredLogger
from src.shared.models import Report, ResearchContext, RSSItem


# Maximum retries for Bedrock API calls
_MAX_RETRIES = 2

# Delay between retries in seconds
_RETRY_DELAY_SECONDS = 1


class GraphGenerator:
    """Generates Mermaid diagrams for announcements using Amazon Bedrock.

    Constructs a prompt from the config template, announcement data, and
    report context, then calls Bedrock invoke_model using the application
    inference profile ARN for LLM B (Claude Opus) with global cross-region
    inference profile as the model source.

    Skips generation for importance_level < 2 (returns None without LLM call).
    Retries up to 2× on failure with 1s delay. Returns None on persistent failure.
    """

    def __init__(self, config: Config, logger: StructuredLogger) -> None:
        self._config = config
        self._logger = logger
        self._bedrock_client = boto3.client(
            "bedrock-runtime",
            region_name=config.aws_region,
        )

    def generate(
        self, item: RSSItem, report: Report, importance_level: int,
        research_context: ResearchContext | None = None,
    ) -> str | None:
        """Generate a Mermaid diagram for an announcement.

        Args:
            item: The RSS announcement item.
            report: The generated report for this announcement.
            importance_level: The importance level (1, 2, or 3).
            research_context: The research context gathered for this announcement,
                including content from blogposts and documentation links.

        Returns:
            A Mermaid diagram string, or None if skipped or generation failed.
        """
        # Skip graph generation for 1-star announcements only
        if importance_level < 2:
            self._logger.info(
                "Skipping graph generation for low-importance announcement",
                announcement_link=item.link,
                importance_level=importance_level,
            )
            return None

        prompt = self._build_prompt(item, report, research_context)
        response_text = self._invoke_bedrock(prompt, item.link)

        if response_text is None:
            return None

        mermaid_code = self._extract_mermaid(response_text, item.link)

        if mermaid_code is None:
            return None

        # Validate the generated diagram
        is_valid, error_msg = self._validate_mermaid(mermaid_code)
        if not is_valid:
            self._logger.warning(
                "Generated Mermaid diagram has syntax errors, attempting fix",
                announcement_link=item.link,
                error=error_msg,
            )
            # Retry with fix (max 1 correction attempt)
            fixed_code = self._retry_with_fix(mermaid_code, error_msg, item.link)
            if fixed_code:
                is_valid2, error_msg2 = self._validate_mermaid(fixed_code)
                if is_valid2:
                    self._logger.info("Mermaid diagram fixed successfully", announcement_link=item.link)
                    return fixed_code
                else:
                    self._logger.warning("Fix attempt still invalid", announcement_link=item.link, error=error_msg2)

            # Return None if still invalid (graceful degradation)
            self._logger.error("Mermaid diagram validation failed after retry", announcement_link=item.link)
            return None

        return mermaid_code

    def _build_prompt(self, item: RSSItem, report: Report, research_context: ResearchContext | None = None) -> str:
        """Construct the prompt from config template + announcement + report + research context."""
        report_summary = (
            f"What's New: {report.whats_new}\n"
            f"How It Works: {report.how_it_works}\n"
            f"Why Important: {report.why_important}"
        )

        # Extract AWS service name from the title (best-effort)
        aws_service = self._extract_service_name(item.title)

        # Build research context string from gathered page content
        research_text = self._format_research_context(research_context)

        prompt = self._config.graph_prompt_template.format(
            title=item.title,
            description=item.description,
            aws_service=aws_service,
            report_summary=report_summary,
            research_context=research_text,
        )
        return prompt

    def _format_research_context(self, research_context: ResearchContext | None) -> str:
        """Format the research context into a string for the prompt.

        Includes content from blogposts, documentation, and other linked pages
        that were fetched during the research stage.
        """
        if research_context is None or research_context.skipped:
            return "No additional research context available."

        if not research_context.gathered_content:
            return "No additional research context available."

        sections: list[str] = []
        for page in research_context.gathered_content:
            # Truncate very long page content to keep prompt manageable
            text = page.text[:3000] if len(page.text) > 3000 else page.text
            sections.append(
                f"### {page.title}\nSource: {page.url}\n{text}"
            )

        return "\n\n".join(sections)

    def _extract_service_name(self, title: str) -> str:
        """Extract the AWS service name from the announcement title.

        Uses common patterns like 'Amazon X' or 'AWS Y' at the start of titles.
        Falls back to the full title if no pattern matches.
        """
        # Match "Amazon <Service>" or "AWS <Service>" patterns
        match = re.match(r"(Amazon|AWS)\s+[\w\s]+", title)
        if match:
            # Take up to the first verb-like word or punctuation
            service_part = match.group(0)
            # Trim to first few words to get just the service name
            words = service_part.split()
            if len(words) > 4:
                return " ".join(words[:4])
            return service_part
        return title.split(" - ")[0].split(" now ")[0].split(" announces ")[0].strip()

    def _invoke_bedrock(self, prompt: str, announcement_link: str) -> str | None:
        """Call Bedrock invoke_model with retry logic.

        Uses the application inference profile ARN for LLM B. The inference
        profile ARN is read from the LLM_B_INFERENCE_PROFILE_ARN environment
        variable (set by CDK), falling back to the model ID for direct invocation.

        Retries up to 2× on failure with 1s delay between attempts.
        Returns None on persistent failure.
        """
        # Use inference profile ARN from environment (set by CDK stack)
        # Falls back to model ID for local testing
        model_id = os.environ.get(
            "INFERENCE_PROFILE_B_ARN",
            self._config.llm_b_model_id,
        )

        body = json.dumps({
            "anthropic_version": "bedrock-2023-05-31",
            "max_tokens": self._config.llm_b_max_tokens,
            "temperature": self._config.llm_b_temperature,
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
                self._logger.error(
                    "Unexpected Bedrock response format for graph generation",
                    announcement_link=announcement_link,
                    response_body=str(response_body),
                )
                return None

            except ClientError as exc:
                last_error = exc
                error_code = exc.response.get("Error", {}).get("Code", "Unknown")
                self._logger.error(
                    "Bedrock API call failed for graph generation",
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
                    "Bedrock invocation error for graph generation",
                    announcement_link=announcement_link,
                    attempt=attempt,
                    max_attempts=_MAX_RETRIES + 1,
                    error_type=type(exc).__name__,
                    error_message=str(exc),
                )

                if attempt <= _MAX_RETRIES:
                    time.sleep(_RETRY_DELAY_SECONDS)
                    continue

        # Persistent failure — return None (graceful degradation)
        self._logger.error(
            "Graph generation failed after all retries",
            announcement_link=announcement_link,
            total_attempts=_MAX_RETRIES + 1,
            last_error=str(last_error),
        )
        return None

    def _validate_mermaid(self, code: str) -> tuple[bool, str]:
        """Validate Mermaid diagram syntax.

        Returns (is_valid, error_message).
        """
        if not code or not code.strip():
            return False, "Empty diagram"

        lines = code.strip().split('\n')

        # Check starts with graph declaration
        first_line = lines[0].strip()
        if not first_line.startswith(('graph TD', 'graph LR', 'graph TB', 'flowchart')):
            return False, f"Must start with 'graph TD' or 'graph LR', got: '{first_line}'"

        # Check balanced brackets
        full_text = code
        for open_char, close_char, name in [('(', ')', 'parentheses'), ('[', ']', 'square brackets'), ('{', '}', 'curly braces')]:
            if full_text.count(open_char) != full_text.count(close_char):
                return False, f"Unbalanced {name}: {full_text.count(open_char)} '{open_char}' vs {full_text.count(close_char)} '{close_char}'"

        # Check for unmatched quotes in labels
        quote_count = full_text.count('"')
        if quote_count % 2 != 0:
            return False, f"Unmatched quotes: {quote_count} double-quote characters (must be even)"

        # Check minimum content (at least a few nodes and connections)
        arrow_count = full_text.count('-->') + full_text.count('-.->') + full_text.count('==>')
        if arrow_count < 2:
            return False, f"Too few connections: found {arrow_count} arrows (need at least 2)"

        return True, ""

    def _retry_with_fix(self, original_code: str, error_message: str, announcement_link: str) -> str | None:
        """Ask the LLM to fix a syntactically invalid Mermaid diagram."""
        fix_prompt = (
            f"The Mermaid diagram you generated has syntax errors:\n"
            f"- {error_message}\n\n"
            f"Here is your original output:\n```mermaid\n{original_code}\n```\n\n"
            f"Please fix the syntax errors and return ONLY the corrected Mermaid diagram "
            f"inside ```mermaid ... ``` markers. Keep the same content and structure, just fix the syntax."
        )

        response_text = self._invoke_bedrock(fix_prompt, announcement_link)
        if response_text is None:
            return None

        return self._extract_mermaid(response_text, announcement_link)

    def _extract_mermaid(self, response_text: str, announcement_link: str) -> str | None:
        """Extract the Mermaid diagram code from the LLM response.

        Looks for content between ```mermaid and ``` markers.
        Returns the diagram code or None if not found.
        """
        # Try to extract mermaid code block
        pattern = r"```mermaid\s*\n(.*?)```"
        match = re.search(pattern, response_text, re.DOTALL)

        if match:
            mermaid_code = match.group(1).strip()
            if mermaid_code:
                return mermaid_code

        # Fallback: check if the response itself looks like a mermaid diagram
        # (starts with graph/flowchart/sequenceDiagram etc.)
        stripped = response_text.strip()
        if stripped.startswith(("graph ", "graph\n", "flowchart ", "sequenceDiagram")):
            return stripped

        self._logger.warning(
            "Could not extract Mermaid diagram from LLM response",
            announcement_link=announcement_link,
            response_preview=response_text[:200],
        )
        return None
