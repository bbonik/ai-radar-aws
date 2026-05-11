# AI Radar AWS — Announcement Taxonomy Analysis

## Problem Statement

The current tagging system assigns a single `aws_service` label per announcement (e.g., "Amazon Bedrock", "Sagemaker", "Other"). This is too coarse:

- "Other" is a catch-all that loses all meaning
- An announcement about "AgentCore Payments" is tagged only as "Amazon Bedrock" — missing the concepts of *agentic AI*, *payments*, *e-commerce*
- "4 new Qwen models on SageMaker JumpStart" is tagged "Sagemaker" — missing *new model*, *open source*, *multimodal*
- Users can't filter by what they actually care about (e.g., "show me everything about agents" or "what new models dropped this week")

## Proposed Multi-Tag Taxonomy

Each announcement gets **multiple tags** from orthogonal dimensions. A user can filter by any combination.

---

## Dimension 1: AWS Service

The primary AWS service(s) involved. An announcement can reference multiple services.

| Tag | Description |
|-----|-------------|
| `bedrock` | Amazon Bedrock (model invocation, guardrails, knowledge bases) |
| `bedrock-agentcore` | Amazon Bedrock AgentCore (agent runtime, identity, memory, tools) |
| `sagemaker` | Amazon SageMaker (training, endpoints, pipelines) |
| `sagemaker-ai` | Amazon SageMaker AI (inference, model customization) |
| `sagemaker-jumpstart` | SageMaker JumpStart (model hub, pre-trained models) |
| `sagemaker-hyperpod` | SageMaker HyperPod (distributed training clusters) |
| `sagemaker-unified-studio` | SageMaker Unified Studio (IDE, collaboration) |
| `quicksight` | Amazon QuickSight / Amazon Quick (BI, dashboards) |
| `kiro` | Kiro (AI-powered IDE) |
| `q-developer` | Amazon Q Developer (coding assistant) |
| `q-business` | Amazon Q Business (enterprise assistant) |
| `comprehend` | Amazon Comprehend (NLP) |
| `rekognition` | Amazon Rekognition (computer vision) |
| `textract` | Amazon Textract (document processing) |
| `transcribe` | Amazon Transcribe (speech-to-text) |
| `polly` | Amazon Polly (text-to-speech) |
| `lex` | Amazon Lex (conversational AI) |
| `personalize` | Amazon Personalize (recommendations) |
| `kendra` | Amazon Kendra (intelligent search) |
| `neuron` | AWS Neuron SDK / Trainium / Inferentia |
| `lambda` | AWS Lambda (when AI-relevant) |
| `cloudwatch` | CloudWatch (when AI-relevant) |
| `elasticache` | ElastiCache (when AI-relevant, e.g., vector caching) |
| `opensearch` | OpenSearch (when AI-relevant, e.g., vector search) |
| `other-aws` | Other AWS service not in the list above |

---

## Dimension 2: Announcement Type

What kind of news is this?

| Tag | Description |
|-----|-------------|
| `new-model` | A new foundation model or fine-tuned model is available |
| `new-feature` | A new capability added to an existing service |
| `new-service` | An entirely new service or product launch |
| `region-expansion` | Service now available in new region(s) |
| `ga-launch` | Feature moves from preview/beta to general availability |
| `preview-launch` | New feature available in preview/beta |
| `integration` | New integration between services or with third parties |
| `performance` | Performance improvement, optimization, or scaling enhancement |
| `pricing` | Pricing change, new tier, or cost optimization |
| `security` | Security feature, compliance certification, or access control |
| `deprecation` | Service or feature being deprecated or sunset |

---

## Dimension 3: AI/ML Concept

The underlying AI/ML concepts and paradigms involved.

| Tag | Description |
|-----|-------------|
| `agentic-ai` | Autonomous agents, multi-agent systems, tool use |
| `genai` | Generative AI (text, image, code generation) |
| `llm` | Large Language Models specifically |
| `rag` | Retrieval-Augmented Generation |
| `fine-tuning` | Model customization, fine-tuning, RLHF |
| `inference` | Model inference, serving, endpoints |
| `training` | Model training, distributed training |
| `embedding` | Embeddings, vector representations |
| `multimodal` | Multi-modal models (text + image + audio) |
| `nlp` | Natural Language Processing |
| `computer-vision` | Image/video analysis |
| `speech` | Speech recognition or synthesis |
| `mlops` | ML operations, pipelines, monitoring |
| `responsible-ai` | Guardrails, safety, bias detection, explainability |
| `coding-assistant` | AI-powered code generation and development tools |
| `conversational-ai` | Chatbots, dialog systems, Q&A |
| `recommendation` | Recommendation systems, personalization |
| `search` | Intelligent search, semantic search, vector search |
| `document-ai` | Document processing, OCR, extraction |
| `data-analytics` | AI-powered analytics, BI, insights |

---

## Dimension 4: Use Case / Industry

Who benefits from this announcement?

| Tag | Description |
|-----|-------------|
| `enterprise` | Enterprise-grade features (SSO, compliance, governance) |
| `developer-tools` | Tools for developers (SDKs, APIs, IDE integrations) |
| `devops` | Infrastructure, deployment, monitoring for AI workloads |
| `e-commerce` | Payments, transactions, retail applications |
| `healthcare` | Healthcare and life sciences applications |
| `financial` | Financial services applications |
| `government` | GovCloud, FedRAMP, public sector |
| `observability` | Monitoring, logging, tracing for AI systems |
| `migration` | Migration tools, compatibility layers |
| `open-source` | Open-source models, tools, or contributions |
| `cost-optimization` | Cost reduction, efficient resource usage |
| `multi-region` | Multi-region deployment, cross-region capabilities |

---

## Dimension 5: Model Provider (when applicable)

| Tag | Description |
|-----|-------------|
| `anthropic` | Anthropic (Claude models) |
| `openai` | OpenAI (GPT models) |
| `meta` | Meta (Llama models) |
| `google` | Google (Gemma, Gemini models) |
| `nvidia` | NVIDIA (Nemotron, NIM) |
| `mistral` | Mistral AI |
| `cohere` | Cohere |
| `stability` | Stability AI |
| `amazon` | Amazon (Nova, Titan models) |
| `alibaba` | Alibaba (Qwen models) |
| `community` | Community/open-source model providers |

---

## Example Taggings

Here's how existing announcements would be tagged under this taxonomy:

### "Agents that transact: Amazon Bedrock AgentCore now includes Payments (preview)"
- **Service**: `bedrock-agentcore`
- **Type**: `new-feature`, `preview-launch`
- **Concept**: `agentic-ai`
- **Use Case**: `e-commerce`, `enterprise`

### "4 new Qwen models for multimodal reasoning, agentic coding, and multilingual applications are now available in Amazon SageMaker JumpStart"
- **Service**: `sagemaker-jumpstart`
- **Type**: `new-model`
- **Concept**: `multimodal`, `coding-assistant`, `agentic-ai`, `llm`
- **Use Case**: `developer-tools`, `open-source`
- **Provider**: `alibaba`

### "Amazon Bedrock AgentCore is now available in AWS GovCloud (US-West)"
- **Service**: `bedrock-agentcore`
- **Type**: `region-expansion`
- **Concept**: `agentic-ai`
- **Use Case**: `government`

### "The AWS MCP Server is now generally available"
- **Service**: `bedrock-agentcore`
- **Type**: `ga-launch`
- **Concept**: `agentic-ai`, `developer-tools`
- **Use Case**: `developer-tools`

### "Amazon SageMaker AI Now Supports Capacity-Aware Inference with Automatic Instance Fallback"
- **Service**: `sagemaker-ai`
- **Type**: `new-feature`
- **Concept**: `inference`, `mlops`
- **Use Case**: `cost-optimization`, `devops`

### "OpenAI GPT OSS and NVIDIA Nemotron Models Available on Amazon Bedrock in AWS GovCloud (US)"
- **Service**: `bedrock`
- **Type**: `new-model`, `region-expansion`
- **Concept**: `llm`, `genai`
- **Use Case**: `government`, `open-source`
- **Provider**: `openai`, `nvidia`

### "Amazon WorkSpaces now lets AI agents operate desktop applications (Preview)"
- **Service**: `other-aws`
- **Type**: `new-feature`, `preview-launch`
- **Concept**: `agentic-ai`
- **Use Case**: `enterprise`

---

## Implementation Approach

### Option A: LLM-based tagging (recommended)

Add a tagging step to the pipeline after the relevance filter. Use the same Bedrock Sonnet model to classify each announcement into the taxonomy. The prompt would provide the taxonomy definitions and ask for JSON output.

**Pros**: Handles nuance, new services, and edge cases gracefully. No maintenance of regex patterns.
**Cons**: Additional Bedrock API call per announcement (cost + latency).

### Option B: Hybrid (regex + LLM)

Use regex for Dimension 1 (service detection — straightforward keyword matching) and LLM for Dimensions 2-5 (require understanding context).

**Pros**: Faster for service detection, LLM only for conceptual tagging.
**Cons**: More complex code, regex still misses edge cases.

### Option C: Pure regex/rules

Extend the current pattern-matching approach to all dimensions.

**Pros**: Fast, no API cost.
**Cons**: Brittle, misses context, requires constant maintenance as new services/concepts emerge.

---

## Recommendation

**Option A (LLM-based)** is the best fit for this project because:
1. We already call Bedrock for report generation — one more call per announcement is marginal
2. The taxonomy requires understanding context (e.g., "Payments" → `e-commerce`)
3. New services and concepts appear constantly — LLM adapts without code changes
4. The tag set is small enough that the LLM can reliably select from it

### Suggested pipeline position

```
RSS → Dedup → Filter → Classify Importance → Tag (NEW) → Research → Report → Graph → Store
```

The tagging step runs after importance classification (so we have the service name) but before research (so tags can inform the research focus).

---

## Data Model Changes

```python
@dataclass
class AnnouncementTags:
    services: list[str]        # Dimension 1
    types: list[str]           # Dimension 2
    concepts: list[str]        # Dimension 3
    use_cases: list[str]       # Dimension 4
    providers: list[str]       # Dimension 5 (may be empty)
```

The CSV would store tags as pipe-separated values within each dimension column, or as a single JSON blob column.

---

## Website Impact

- Replace the single "Service" filter dropdown with a multi-select tag filter
- Add tag chips on announcement cards for quick visual scanning
- Enable filtering by any combination of tags across dimensions
- Add a "Tag Cloud" or faceted navigation sidebar

---

## Open Questions

1. Should we retroactively tag existing announcements? (Yes — re-run pipeline with tagging enabled)
2. Should tags influence importance scoring? (Possibly — `new-service` and `ga-launch` could boost score)
3. Maximum tags per announcement? (Suggest: no hard limit, but LLM prompt should aim for 3-8 total)
4. Should users be able to "subscribe" to specific tags for notifications? (Future feature)
