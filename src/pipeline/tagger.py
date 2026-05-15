"""Tagger module for the AI Radar AWS pipeline.

Uses Amazon Bedrock (Claude Haiku 4.5 via global cross-region inference profile)
to assign multi-dimensional taxonomy tags to each announcement. Runs after
importance classification and before research.

Non-fatal: if tagging fails, the announcement proceeds with empty tags.
"""

import json
import os
import time

import boto3
from botocore.exceptions import ClientError

from src.config import Config
from src.shared.logger import StructuredLogger
from src.shared.models import AnnouncementTags, RSSItem


# Maximum retries for Bedrock API calls
_MAX_RETRIES = 2

# Delay between retries in seconds
_RETRY_DELAY_SECONDS = 1


class Tagger:
    """Assigns multi-dimensional taxonomy tags to announcements using Bedrock.

    Constructs a prompt from the config template and announcement data, then
    calls Bedrock invoke_model using the application inference profile ARN for
    LLM C (Claude Haiku 4.5).

    Retries up to 2× on failure with 1s delay. On persistent failure, returns
    empty AnnouncementTags (non-fatal — announcement still proceeds).
    """

    def __init__(self, config: Config, logger: StructuredLogger) -> None:
        self._config = config
        self._logger = logger
        self._bedrock_client = boto3.client(
            "bedrock-runtime",
            region_name=config.aws_region,
        )

    def tag(self, item: RSSItem) -> AnnouncementTags:
        """Assign taxonomy tags to an announcement.

        Args:
            item: The RSS announcement item to tag.

        Returns:
            An AnnouncementTags object with tags from all dimensions.
            Returns empty AnnouncementTags on failure (non-fatal).
        """
        try:
            prompt = self._build_prompt(item)
            response_text = self._invoke_bedrock(prompt, item.link)
            tags = self._parse_response(response_text, item.link)
            tags = self._apply_post_processing_rules(item, tags)
            self._logger.info(
                "Tagging complete",
                announcement_link=item.link,
                tag_count=len(tags.all_tags()),
                services=tags.services,
                types=tags.types,
                concepts=tags.concepts,
                use_cases=tags.use_cases,
                providers=tags.providers,
            )
            return tags
        except Exception as exc:
            self._logger.warning(
                "Tagging failed, proceeding with empty tags",
                announcement_link=item.link,
                error_type=type(exc).__name__,
                error_message=str(exc),
            )
            return AnnouncementTags()

    def _build_prompt(self, item: RSSItem) -> str:
        """Construct the prompt from config template + announcement data."""
        return self._config.tagger_prompt_template.format(
            title=item.title,
            description=item.description,
        )

    @staticmethod
    def _apply_post_processing_rules(item: RSSItem, tags: AnnouncementTags) -> AnnouncementTags:
        """Apply deterministic post-processing rules to fix known LLM tagging gaps.

        Rules:
        1. Validate service tags against the title (services must be named in title)
        2. Force aws-transform when title mentions it
        """
        title_lower = item.title.lower()
        text_lower = (item.title + " " + item.description).lower()

        # Rule 1: Validate service tags — only keep services whose name appears in the title
        # Map tag names to title keywords they should match
        service_title_keywords = {
            "bedrock": "bedrock",
            "bedrock-agentcore": "agentcore",
            "sagemaker": "sagemaker",
            "sagemaker-ai": "sagemaker ai",
            "sagemaker-jumpstart": "jumpstart",
            "sagemaker-hyperpod": "hyperpod",
            "sagemaker-unified-studio": "unified studio",
            "quicksight": "quicksight",
            "quick": "quick",
            "quick-suite": "quick suite",
            "kiro": "kiro",
            "q-developer": "q developer",
            "q-business": "q business",
            "aws-transform": "transform",
            "comprehend": "comprehend",
            "rekognition": "rekognition",
            "textract": "textract",
            "transcribe": "transcribe",
            "polly": "polly",
            "lex": "lex",
            "personalize": "personalize",
            "kendra": "kendra",
            "neuron": "neuron",
            "lambda": "lambda",
            "cloudwatch": "cloudwatch",
            "elasticache": "elasticache",
            "opensearch": "opensearch",
        }

        validated_services = []
        for tag in tags.services:
            if tag == "other-aws":
                validated_services.append(tag)
                continue
            keyword = service_title_keywords.get(tag, tag)
            if keyword in title_lower:
                validated_services.append(tag)

        # If all services were filtered out, keep "other-aws"
        if not validated_services and tags.services:
            validated_services = ["other-aws"]

        tags.services = validated_services

        # Rule 2: Force aws-transform when title mentions it
        if "transform" in title_lower and "aws-transform" not in tags.services:
            tags.services.append("aws-transform")

        # Rule 3: Deduplicate parent/child services — if the more specific
        # service is present, remove the generic parent
        if "bedrock-agentcore" in tags.services and "bedrock" in tags.services:
            tags.services.remove("bedrock")
        if "sagemaker-ai" in tags.services and "sagemaker" in tags.services:
            tags.services.remove("sagemaker")

        return tags

    def _invoke_bedrock(self, prompt: str, announcement_link: str) -> str:
        """Call Bedrock invoke_model with retry logic.

        Uses the application inference profile ARN for LLM C. The inference
        profile ARN is read from the INFERENCE_PROFILE_C_ARN environment
        variable (set by CDK), falling back to the model ID for local testing.

        Retries up to 2× on failure with 1s delay between attempts.

        Raises:
            RuntimeError: If all attempts fail.
        """
        # Use inference profile ARN from environment (set by CDK stack)
        # Falls back to model ID for local testing
        model_id = os.environ.get(
            "INFERENCE_PROFILE_C_ARN",
            self._config.llm_c_model_id,
        )

        body = json.dumps({
            "anthropic_version": "bedrock-2023-05-31",
            "max_tokens": self._config.llm_c_max_tokens,
            "temperature": self._config.llm_c_temperature,
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

                raise RuntimeError(
                    f"Unexpected Bedrock response format: {response_body}"
                )

            except ClientError as exc:
                last_error = exc
                error_code = exc.response.get("Error", {}).get("Code", "Unknown")
                self._logger.warning(
                    "Tagger Bedrock API call failed",
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
                self._logger.warning(
                    "Tagger Bedrock invocation error",
                    announcement_link=announcement_link,
                    attempt=attempt,
                    max_attempts=_MAX_RETRIES + 1,
                    error_type=type(exc).__name__,
                    error_message=str(exc),
                )

                if attempt <= _MAX_RETRIES:
                    time.sleep(_RETRY_DELAY_SECONDS)
                    continue

        raise RuntimeError(
            f"Tagging failed after {_MAX_RETRIES + 1} attempts "
            f"for announcement: {announcement_link}. "
            f"Last error: {last_error}"
        )

    def _parse_response(self, response_text: str, announcement_link: str) -> AnnouncementTags:
        """Parse the LLM JSON response into an AnnouncementTags object.

        Robust extraction: handles markdown code blocks, prose around JSON,
        trailing commas, and other common LLM output quirks.
        Validates that tags are from the allowed taxonomy.
        """
        json_text = self._extract_json(response_text)

        try:
            data = json.loads(json_text)
        except json.JSONDecodeError:
            # Try fixing common issues: trailing commas, single quotes
            fixed = json_text.replace("'", '"')
            # Remove trailing commas before } or ]
            import re
            fixed = re.sub(r",\s*([}\]])", r"\1", fixed)
            try:
                data = json.loads(fixed)
            except json.JSONDecodeError:
                self._logger.warning(
                    "Could not parse tagger JSON response",
                    announcement_link=announcement_link,
                    response_preview=response_text[:200],
                )
                return AnnouncementTags()

        if not isinstance(data, dict):
            return AnnouncementTags()

        return AnnouncementTags(
            services=self._validate_tags(data.get("services", []), _VALID_SERVICES),
            types=self._validate_tags(data.get("types", []), _VALID_TYPES),
            concepts=self._validate_tags(data.get("concepts", []), _VALID_CONCEPTS),
            use_cases=self._validate_tags(data.get("use_cases", []), _VALID_USE_CASES),
            providers=self._validate_tags(data.get("providers", []), _VALID_PROVIDERS),
            geo_availability=self._validate_geo(data.get("geo_availability", "")),
        )

    @staticmethod
    def _extract_json(response_text: str) -> str:
        """Extract JSON from LLM response, handling various output formats.

        Handles:
        - Pure JSON
        - JSON wrapped in ```json ... ``` code blocks
        - JSON embedded in prose (finds first { to last })
        """
        text = response_text.strip()

        # Case 1: Markdown code block
        if "```" in text:
            start = text.find("```")
            # Skip the opening fence line (```json or ```)
            start = text.find("\n", start) + 1
            end = text.find("```", start)
            if end != -1:
                return text[start:end].strip()

        # Case 2: Starts with { — likely pure JSON (possibly with trailing text)
        if text.startswith("{"):
            # Find the matching closing brace
            depth = 0
            for i, ch in enumerate(text):
                if ch == "{":
                    depth += 1
                elif ch == "}":
                    depth -= 1
                    if depth == 0:
                        return text[: i + 1]
            return text  # Unbalanced — try anyway

        # Case 3: JSON embedded in prose — find first { to matching }
        brace_start = text.find("{")
        if brace_start != -1:
            depth = 0
            for i in range(brace_start, len(text)):
                if text[i] == "{":
                    depth += 1
                elif text[i] == "}":
                    depth -= 1
                    if depth == 0:
                        return text[brace_start: i + 1]

        return text  # Last resort — return as-is

    @staticmethod
    def _validate_tags(tags: list, valid_set: set[str]) -> list[str]:
        """Filter tags to only include valid taxonomy values."""
        if not isinstance(tags, list):
            return []
        return [tag for tag in tags if isinstance(tag, str) and tag in valid_set]

    @staticmethod
    def _validate_geo(value) -> str:
        """Validate geo_availability value."""
        if not isinstance(value, str):
            return ""
        if value in _VALID_GEO_AVAILABILITY:
            return value
        return ""


# Valid taxonomy tag sets for validation
_VALID_SERVICES = {
    "bedrock", "bedrock-agentcore", "sagemaker", "sagemaker-ai",
    "sagemaker-jumpstart", "sagemaker-hyperpod", "sagemaker-unified-studio",
    "quicksight", "quick", "quick-suite", "kiro", "q-developer", "q-business",
    "aws-transform", "comprehend", "rekognition", "textract", "transcribe", "polly", "lex",
    "personalize", "kendra", "neuron", "lambda", "cloudwatch", "elasticache",
    "opensearch", "other-aws",
}

_VALID_TYPES = {
    "new-model", "new-feature", "new-service", "region-expansion",
    "ga-launch", "preview-launch", "integration", "performance",
    "pricing", "security", "deprecation",
}

_VALID_CONCEPTS = {
    "agentic-ai", "genai", "llm", "rag", "fine-tuning", "inference",
    "training", "embedding", "multimodal", "nlp", "computer-vision",
    "speech", "mlops", "responsible-ai", "coding-assistant",
    "conversational-ai", "recommendation", "search", "document-ai",
    "data-analytics",
}

_VALID_USE_CASES = {
    "enterprise", "developer-tools", "devops", "e-commerce", "healthcare",
    "financial", "government", "observability", "migration", "open-source",
    "cost-optimization", "multi-region",
}

_VALID_PROVIDERS = {
    "anthropic", "openai", "meta", "google", "nvidia", "mistral",
    "cohere", "stability", "amazon", "alibaba", "community",
}

_VALID_GEO_AVAILABILITY = {
    "apj", "emea", "americas", "global", "unknown",
}
