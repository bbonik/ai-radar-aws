"""
AI Radar AWS - Centralized Configuration Module.

All tunable parameters for the pipeline are defined here.
Changes take effect on next Lambda execution without redeployment.

No sensitive values (API keys, credentials) are stored in this file.
All credentials come from IAM roles at runtime.
"""

from dataclasses import dataclass, field


@dataclass
class Config:
    """Central configuration for the AI Radar AWS pipeline.

    Contains all tunable parameters including AWS region, schedule settings,
    LLM model configurations, importance scoring weights, prompt templates,
    and operational timeouts.
    """

    # AWS Region
    aws_region: str = "us-east-1"

    # Schedule (daily execution time in UTC)
    schedule_hour: int = 22
    schedule_minute: int = 0

    # LLM A - Report Generator (Claude Sonnet)
    llm_a_model_id: str = "global.anthropic.claude-sonnet-4-6"
    llm_a_temperature: float = 0.3
    llm_a_max_tokens: int = 4096
    llm_a_inference_profile_name: str = "ai-radar-report-generator"

    # LLM B - Graph Generator (Claude Opus)
    llm_b_model_id: str = "global.anthropic.claude-opus-4-6-v1"
    llm_b_temperature: float = 0.2
    llm_b_max_tokens: int = 2048
    llm_b_inference_profile_name: str = "ai-radar-graph-generator"

    # LLM C - Tagger (Claude Haiku 4.5)
    llm_c_model_id: str = "global.anthropic.claude-haiku-4-5-20251001-v1:0"
    llm_c_temperature: float = 0.1
    llm_c_max_tokens: int = 1024
    llm_c_inference_profile_name: str = "ai-radar-tagger"

    # Importance Scoring
    service_points_high: int = 3      # Bedrock, AgentCore, SageMaker AI, QuickSight
    service_points_medium: int = 2    # SageMaker, Unified Studio, Kiro
    service_points_base: int = 1      # All other relevant services
    blogpost_points: int = 2          # Points for having blogpost links
    word_count_scale: float = 0.005   # Points per word (e.g., 400 words = 2 points)
    threshold_2_star: float = 3.0     # Score >= this -> 2-star
    threshold_3_star: float = 5.0     # Score >= this -> 3-star

    # Tag-based scoring bonuses (applied when tags are available)
    tag_bonus_new_model: float = 2.0      # Bonus for "new-model" type tag
    tag_bonus_new_service: float = 1.5    # Bonus for "new-service" type tag
    tag_bonus_ga_launch: float = 1.0      # Bonus for "ga-launch" type tag

    # Prompt Templates
    report_prompt_template: str = field(default="""\
You are an expert AWS AI/ML analyst. Given the following AWS announcement and \
any additional research context, produce a structured report with exactly seven sections.

## Announcement
Title: {title}
Description: {description}
Publication Date: {pub_date}
Link: {link}

## Research Context
{research_context}

## Instructions
Produce a report with the following seven sections. Use clear, concise language \
suitable for a technical audience. Each section should be a well-formed paragraph.

1. **What's New**: A concise summary of what was announced (2-3 sentences of flowing prose).
2. **How It Works**: A technical explanation using bullet points (each starting with '- '). Each bullet should be a complete thought covering one aspect of how it works.
3. **Why It's Important**: The significance and practical implications using bullet points. Each bullet covers one reason or implication.
4. **How It's Different**: Comparison points using bullet points. Each bullet highlights one difference or advantage.
5. **When to Prefer It**: Guidance using bullet points. Each bullet describes a scenario or use case.
6. **Availability**: Status and regions using bullet points. Include GA/Preview status, supported regions, pricing model, and any limitations as separate bullets.
7. **Card Summary**: A single sentence (max 150 characters) that captures the essence of this announcement. This will be displayed as a preview on the news feed alongside the title. Do NOT repeat the title. Focus on the "so what" — why should someone click to read more?

## Output Format
Return your response using exactly these section headers:

[WHATS_NEW]
<content>

[HOW_IT_WORKS]
<content>

[WHY_IMPORTANT]
<content>

[HOW_DIFFERENT]
<content>

[WHEN_TO_PREFER]
<content>

[AVAILABILITY]
<content>

[CARD_SUMMARY]
<one sentence, max 150 characters>
""")

    graph_prompt_template: str = field(default="""\
You are an expert AWS solutions architect. Given the following AWS AI/ML announcement, \
its report, and additional research context from linked documentation and blogposts, \
produce a Mermaid diagram showing how this service or feature fits \
into the broader AWS AI/ML/GenAI ecosystem.

## Announcement
Title: {title}
Description: {description}
Service: {aws_service}

## Report Summary
{report_summary}

## Research Context (from linked blogposts and documentation)
{research_context}

## Instructions
Create a Mermaid diagram (using graph TD or graph LR syntax) that shows:
- The announced service/feature as the central node, highlighted with orange color always
- Related AWS services it integrates with
- Data flow or interaction patterns
- Where it fits in a typical AI/ML workflow

Use the research context to identify specific integrations, data flows, and \
architectural patterns mentioned in the documentation. This will help produce \
a more accurate and detailed diagram.

Keep the diagram focused and readable (5-12 nodes maximum). 
Use clear, short labels. 
Utilize color coding (similar attributes/features/services share the same color), arrows, diffierent types of lines etc., to signify relationships in the diagram.

## Output Format
Return ONLY the Mermaid diagram code, starting with ```mermaid and ending with ```.

```mermaid
graph TD
    ...
```
""")

    tagger_prompt_template: str = field(default="""\
You are an expert AWS AI/ML taxonomy classifier. Given an AWS announcement, \
assign tags from the following multi-dimensional taxonomy. Only use tags from \
the provided lists below.

## Announcement
Title: {title}
Description: {description}

## Taxonomy

### Dimension 1: AWS Service (services)
Valid tags: bedrock, bedrock-agentcore, sagemaker, sagemaker-ai, sagemaker-jumpstart, \
sagemaker-hyperpod, sagemaker-unified-studio, quicksight, kiro, q-developer, q-business, \
comprehend, rekognition, textract, transcribe, polly, lex, personalize, kendra, neuron, \
lambda, cloudwatch, elasticache, opensearch, other-aws

### Dimension 2: Announcement Type (types)
Valid tags: new-model, new-feature, new-service, region-expansion, ga-launch, \
preview-launch, integration, performance, pricing, security, deprecation

### Dimension 3: AI/ML Concept (concepts)
Valid tags: agentic-ai, genai, llm, rag, fine-tuning, inference, training, embedding, \
multimodal, nlp, computer-vision, speech, mlops, responsible-ai, coding-assistant, \
conversational-ai, recommendation, search, document-ai, data-analytics

### Dimension 4: Use Case / Industry (use_cases)
Valid tags: enterprise, developer-tools, devops, e-commerce, healthcare, financial, \
government, observability, migration, open-source, cost-optimization, multi-region

### Dimension 5: Model Provider (providers)
Valid tags: anthropic, openai, meta, google, nvidia, mistral, cohere, stability, \
amazon, alibaba, community

## Instructions
- Assign 1-3 tags per dimension (fewer is better; only assign what clearly applies)
- For providers dimension: only assign if a specific model provider is mentioned
- If no tags clearly apply for a dimension, return an empty list for that dimension
- Return ONLY valid JSON with no additional text

## Output Format
Return a JSON object with exactly these keys:
```json
{{
  "services": [...],
  "types": [...],
  "concepts": [...],
  "use_cases": [...],
  "providers": [...]
}}
```
""")

    # Research
    research_timeout_per_announcement: int = 300  # 5 minutes in seconds

    # RSS
    rss_url: str = "https://aws.amazon.com/about-aws/whats-new/recent/feed/"
    rss_fetch_timeout: int = 30
    rss_max_retries: int = 3

    # Lambda 2
    website_builder_function_name: str = "ai-radar-website-builder"
    website_builder_timeout: int = 600  # 10 minutes in seconds
