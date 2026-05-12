# AI Radar AWS — Mermaid Visual Summary Style Guide

## Problem

The current graph prompt produces inconsistent diagrams:
- Some are colorful, others monochrome
- Complexity varies wildly (3 nodes vs 15 nodes)
- No consistent meaning for colors, shapes, or line types
- The "announced feature" node isn't always visually distinct
- No legend or visual language that users can learn across diagrams

## Design Principles

1. **Consistency** — Every diagram follows the same visual language
2. **Scannability** — A user should understand the diagram in 5 seconds
3. **Semantic encoding** — Colors, shapes, and lines carry meaning
4. **Focus** — The announced feature is always the visual anchor

---

## Proposed Visual Language

### Node Shapes (what type of thing it is)

| Shape | Mermaid Syntax | Meaning |
|-------|---------------|---------|
| Rounded rectangle | `A(Label)` | AWS Service |
| Stadium/pill | `A([Label])` | Feature or capability |
| Hexagon | `A{{Label}}` | The announced feature (always exactly one) |
| Rectangle | `A[Label]` | Data store or resource |
| Circle | `A((Label))` | User/actor/external system |

### Colors (what category it belongs to)

| Color | Hex | Meaning |
|-------|-----|---------|
| **Orange** (fill) | `#ff9900` | The announced feature (always one node) |
| **Blue** (fill) | `#e3f2fd` | Compute/AI services (Bedrock, SageMaker, Lambda) |
| **Green** (fill) | `#e8f5e9` | Storage/data services (S3, DynamoDB, OpenSearch) |
| **Purple** (fill) | `#f3e5f5` | Developer tools (SDKs, APIs, IDEs) |
| **Gray** (fill) | `#f5f5f5` | External systems or users |

### Line Types (what kind of relationship)

| Line | Mermaid Syntax | Meaning |
|------|---------------|---------|
| Solid arrow | `A --> B` | Data flows to / invokes |
| Dashed arrow | `A -.-> B` | Optional or async relationship |
| Thick arrow | `A ==> B` | Primary/critical path |
| Dotted line (no arrow) | `A -.- B` | Logical grouping / association |

### Arrow Labels (what happens)

Short verb phrases on arrows to describe the interaction:
- `"invokes"`, `"reads from"`, `"writes to"`, `"triggers"`, `"returns"`
- Keep to 1-2 words maximum

### Layout

- Always use `graph TD` (top-down) for consistency
- The announced feature node is always at the top or center
- Related services fan out below
- 6-10 nodes is the sweet spot (never more than 12)

---

## Example: Standardized Diagram

```mermaid
graph TD
    A{{Amazon Bedrock AgentCore Payments}}:::announced
    B(Amazon Bedrock):::compute
    C(AWS Lambda):::compute
    D[Amazon DynamoDB]:::storage
    E([Payment Processing]):::feature
    F((Merchant)):::external

    A ==> B
    A --> C
    A --> E
    C --> D
    E -.-> F
    B -->|"invokes"| A

    classDef announced fill:#ff9900,stroke:#ec7211,color:#fff,font-weight:bold
    classDef compute fill:#e3f2fd,stroke:#1565c0,color:#1565c0
    classDef storage fill:#e8f5e9,stroke:#2e7d32,color:#2e7d32
    classDef feature fill:#fff3e0,stroke:#e65100,color:#e65100
    classDef external fill:#f5f5f5,stroke:#616161,color:#616161
```

---

## Implementation: Updated Prompt Template

The prompt should include:
1. The visual language rules (shapes, colors, lines)
2. The `classDef` declarations to include at the bottom
3. A concrete example showing the expected format
4. Explicit constraints (6-10 nodes, always TD, always one hexagon)

### Key additions to the prompt:

```
## Visual Language Rules (MUST follow exactly)

### Node shapes:
- The announced feature: hexagon syntax {{Label}} with class "announced"
- AWS services: rounded rectangle (Label) with class "compute" or "storage"
- Features/capabilities: stadium ([Label]) with class "feature"
- External systems/users: circle ((Label)) with class "external"

### Line types:
- Solid arrow (-->) for data flow / invocation
- Dashed arrow (-.->) for optional/async relationships
- Thick arrow (==>) for the primary integration path

### Required classDef block (include at the end of every diagram):
classDef announced fill:#ff9900,stroke:#ec7211,color:#fff,font-weight:bold
classDef compute fill:#e3f2fd,stroke:#1565c0,color:#1565c0
classDef storage fill:#e8f5e9,stroke:#2e7d32,color:#2e7d32
classDef feature fill:#fff3e0,stroke:#e65100,color:#e65100
classDef external fill:#f5f5f5,stroke:#616161,color:#616161

### Constraints:
- Always use graph TD (top-down layout)
- Exactly ONE node uses the hexagon shape (the announced feature)
- 6-10 nodes total (never more than 12)
- Every node must have a :::className applied
- Arrow labels are optional but encouraged (1-2 words max)
```

---

## Benefits

1. **Users learn the visual language** — after seeing 3-4 diagrams, they instantly know: orange hexagon = the new thing, blue = AI services, green = data, dashed = optional
2. **Consistent aesthetics** — all diagrams look like they belong to the same publication
3. **Predictable complexity** — always 6-10 nodes, never overwhelming
4. **Meaningful encoding** — shapes and colors carry information, not just decoration

---

## Migration

- New announcements will use the updated prompt automatically
- Existing diagrams can be regenerated with `scripts/generate_missing_graphs.py` (after clearing the `mermaid_graph` column)
- No website builder changes needed — Mermaid.js renders classDef styles natively

---

## Mermaid.js Compatibility Notes

- `classDef` is supported in Mermaid 9.0+
- `:::className` syntax for applying classes is supported in Mermaid 9.3+
- Our CDN loads Mermaid 10, so all features are available
- The `{{Label}}` hexagon syntax requires proper escaping in the LLM output


---

## Validation & Retry Loop (Plan B)

### Problem

LLMs occasionally produce syntactically invalid Mermaid code:
- Unbalanced brackets/parentheses
- Invalid characters in node IDs
- Malformed arrow syntax
- Missing closing quotes in labels
- Invalid `classDef` declarations

When this happens, Mermaid.js fails to render and shows an error box on the page.

### Solution: Server-side validation with LLM retry

After the LLM generates the Mermaid code, validate it before storing. If invalid, retry with error feedback.

```
┌─────────────┐     ┌──────────────┐     ┌─────────────┐
│ LLM generates│────▶│ Validate     │────▶│ Store in CSV│
│ Mermaid code │     │ (regex-based)│     │             │
└─────────────┘     └──────┬───────┘     └─────────────┘
                           │ INVALID
                           ▼
                    ┌──────────────┐
                    │ Retry with   │──── max 2 retries
                    │ error message│
                    └──────┬───────┘
                           │ STILL INVALID
                           ▼
                    ┌──────────────┐
                    │ Store None   │
                    │ (no diagram) │
                    └──────────────┘
```

### Validation Checks (regex-based, no external dependencies)

1. **Structure check**: starts with `graph TD` or `graph LR`
2. **Balanced brackets**: count of `(`, `)`, `[`, `]`, `{`, `}` must be even
3. **Valid node IDs**: all node references match `[A-Za-z][A-Za-z0-9_]*`
4. **Arrow syntax**: lines with connections use valid patterns (`-->`, `-.->`, `==>`, `-.-`)
5. **No empty lines between node definitions** (causes Mermaid parse errors)
6. **classDef syntax**: `classDef <name> <properties>` format
7. **No unescaped special characters** in labels (quotes, `<`, `>`)

### Retry Prompt

When validation fails, call the LLM again with:

```
The Mermaid diagram you generated has syntax errors:
- [specific error description]

Here is your original output:
```mermaid
[the invalid code]
```

Please fix the syntax errors and return ONLY the corrected Mermaid diagram.
Keep the same content and structure, just fix the syntax.
```

### Implementation Details

- **Location**: `src/pipeline/graph_generator.py` — add a `_validate_mermaid()` method
- **Max retries**: 2 correction attempts (total 3 LLM calls max)
- **Cost impact**: negligible (~$0.02 per retry, retries happen <10% of the time)
- **Fallback**: if still invalid after retries, return `None` (announcement proceeds without diagram)
- **Logging**: log validation failures with the specific error for monitoring

### Example Validation Function

```python
def _validate_mermaid(self, code: str) -> tuple[bool, str]:
    """Validate Mermaid diagram syntax.
    
    Returns (is_valid, error_message).
    """
    lines = code.strip().split('\n')
    
    # Check starts with graph declaration
    if not lines[0].strip().startswith(('graph TD', 'graph LR', 'graph TB')):
        return False, "Must start with 'graph TD' or 'graph LR'"
    
    # Check balanced brackets
    full_text = '\n'.join(lines)
    for open_char, close_char in [('(', ')'), ('[', ']'), ('{', '}')]:
        if full_text.count(open_char) != full_text.count(close_char):
            return False, f"Unbalanced {open_char}{close_char} brackets"
    
    # Check for common issues
    for i, line in enumerate(lines[1:], 2):
        stripped = line.strip()
        if not stripped or stripped.startswith('%%') or stripped.startswith('classDef'):
            continue
        # Check for invalid arrow syntax (common LLM mistake)
        if '-->' in stripped or '-.->'' in stripped or '==>' in stripped:
            continue  # Valid arrow line
        if ':::' in stripped:
            continue  # Class assignment
        if stripped.startswith('style ') or stripped.startswith('linkStyle'):
            continue  # Style declarations
        # Node definition or subgraph — should have valid ID
        # ... additional checks
    
    return True, ""
```

### Client-side Fallback (complementary)

Even with server-side validation, add a CSS fallback for edge cases:

```css
.mermaid[data-processed="true"] .error {
  display: none;
}

.mermaid-fallback {
  display: none;
  padding: 1rem;
  background: var(--aws-light);
  border-radius: 4px;
  color: var(--aws-text-secondary);
  font-size: 0.85rem;
  text-align: center;
}

/* Show fallback when mermaid fails */
.mermaid:empty + .mermaid-fallback {
  display: block;
}
```

With HTML:
```html
<div class="mermaid">{{MERMAID_CODE}}</div>
<div class="mermaid-fallback">Visual summary unavailable for this announcement.</div>
```
