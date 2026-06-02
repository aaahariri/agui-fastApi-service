"""
LLM Orchestrator - Actual LLM-powered dashboard generation using OpenRouter.

This module replaces the heuristic-based orchestrate_dashboard with real LLM calls
using OpenRouter API to intelligently select and generate A2UI components based
on the content analysis.
"""

import os
import json
import re
from typing import AsyncGenerator
import httpx
from dotenv import load_dotenv

from a2ui_generator import (
    A2UIComponent,
    generate_component,
    generate_id,
    reset_id_counter,
    VALID_COMPONENT_TYPES,
    is_valid_external_url,
    # Component generators
    generate_tldr,
    generate_key_takeaways,
    generate_stat_card,
    generate_code_block,
    generate_step_card,
    generate_callout_card,
    generate_video_card,
    generate_repo_card,
    generate_link_card,
    generate_data_table,
    generate_headline_card,
    generate_table_of_contents,
    generate_quote_card,
    generate_comparison_table,
    generate_checklist_item,
    generate_bullet_point,
    generate_section,
    generate_grid,
    generate_expert_tip,
    generate_tag,
    generate_badge,
    # Additional component generators
    generate_trend_indicator,
    generate_metric_row,
    generate_comparison_bar,
    generate_ranked_item,
    generate_pro_con_item,
    generate_accordion,
    generate_executive_summary,
    generate_tool_card,
    generate_book_card,
    generate_timeline_event,
)
from logger import logger
from content_analyzer import parse_markdown, ContentAnalysis, _classify_heuristic
from prompts import (
    format_content_analysis_prompt,
    format_layout_selection_prompt,
    format_component_selection_prompt,
    validate_component_variety,
)

# Load environment variables
load_dotenv()

OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")
OPENROUTER_MODEL = os.getenv("OPENROUTER_MODEL", "anthropic/claude-haiku-4.5")
OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"

# Default semantic zones for each component type
# These are used when the LLM doesn't specify a zone
COMPONENT_DEFAULT_ZONES = {
    # Hero zone - prominent top-level content
    "tldr": "hero",
    "headlineCard": "hero",

    # Metrics zone - statistics and data
    "statCard": "metrics",
    "metricRow": "metrics",

    # Insights zone - key findings and observations
    "keyTakeaways": "insights",
    "calloutCard": "insights",
    "quoteCard": "insights",

    # Content zone - detailed information
    "codeBlock": "content",
    "dataTable": "content",
    "stepCard": "content",
    "bulletList": "content",
    "comparisonTable": "content",
    "vsCard": "content",

    # Resources zone - links and references
    "linkPreview": "resources",
    "profileCard": "resources",
}

# Default width hints for each component type
# These are used when the LLM doesn't specify a width_hint
COMPONENT_DEFAULT_WIDTHS = {
    # Full width components
    "tldr": "full",
    "codeBlock": "full",
    "dataTable": "full",
    "comparisonTable": "full",
    "bulletList": "full",
    "stepCard": "full",
    "metricRow": "full",

    # Half width components
    "keyTakeaways": "half",
    "quoteCard": "half",
    "calloutCard": "half",
    "vsCard": "half",
    "headlineCard": "half",

    # Third width components (3-column grid on desktop)
    "statCard": "third",
    "linkPreview": "third",
    "profileCard": "third",
}


async def call_llm(prompt: str, system_prompt: str = "", max_tokens: int = 4000, temperature: float = 0.7) -> str:
    """
    Call OpenRouter LLM API with the given prompt.

    Args:
        prompt: The user prompt to send
        system_prompt: Optional system prompt
        max_tokens: Maximum tokens in the response
        temperature: Sampling temperature (lower = more precise)

    Returns:
        The LLM response text
    """
    if not OPENROUTER_API_KEY:
        raise ValueError("OPENROUTER_API_KEY not set in environment")

    messages = []
    if system_prompt:
        messages.append({"role": "system", "content": system_prompt})
    messages.append({"role": "user", "content": prompt})

    async with httpx.AsyncClient(timeout=120.0) as client:
        response = await client.post(
            f"{OPENROUTER_BASE_URL}/chat/completions",
            headers={
                "Authorization": f"Bearer {OPENROUTER_API_KEY}",
                "Content-Type": "application/json",
                "HTTP-Referer": "http://localhost:3010",
                "X-Title": "Second Brain Research Dashboard",
            },
            json={
                "model": OPENROUTER_MODEL,
                "messages": messages,
                "temperature": temperature,
                "max_tokens": max_tokens,
            }
        )

        if response.status_code != 200:
            error_text = response.text
            logger.error(f"[LLM] Status {response.status_code}: {error_text}")
            raise Exception(f"LLM API error: {response.status_code} - {error_text}")

        result = response.json()
        return result["choices"][0]["message"]["content"]


def extract_json_from_response(response: str) -> dict:
    """
    Extract JSON from LLM response, handling markdown code blocks and truncated responses.

    Args:
        response: Raw LLM response text

    Returns:
        Parsed JSON dictionary
    """
    # Try to find JSON in code blocks first
    json_match = re.search(r'```(?:json)?\s*([\s\S]*?)```', response)
    if json_match:
        json_str = json_match.group(1).strip()
    else:
        # Try to find raw JSON
        json_match = re.search(r'\{[\s\S]*\}', response)
        if json_match:
            json_str = json_match.group(0)
        else:
            json_str = response

    try:
        return json.loads(json_str)
    except json.JSONDecodeError as e:
        logger.warning(f"[JSON] Parse error: {e}")
        # Try to recover truncated JSON by extracting complete component objects
        recovered = _recover_truncated_components(json_str)
        if recovered:
            logger.info(f"[JSON] Recovered {len(recovered)} components from truncated response")
            return {"components": recovered}
        logger.debug(f"[JSON] Raw response: {response[:500]}...")
        return {}


def _recover_truncated_components(json_str: str) -> list[dict]:
    """
    Recover individual component objects from a truncated JSON response.
    Finds the components array and extracts all complete JSON objects from it.
    """
    # Find the start of the components array
    match = re.search(r'"components"\s*:\s*\[', json_str)
    if not match:
        return []

    array_start = match.end()
    components = []
    pos = array_start

    # Extract complete JSON objects one at a time
    while pos < len(json_str):
        # Skip whitespace and commas
        while pos < len(json_str) and json_str[pos] in ' \t\n\r,':
            pos += 1
        if pos >= len(json_str) or json_str[pos] != '{':
            break

        # Find the matching closing brace
        depth = 0
        start = pos
        in_string = False
        escape_next = False
        for i in range(start, len(json_str)):
            c = json_str[i]
            if escape_next:
                escape_next = False
                continue
            if c == '\\' and in_string:
                escape_next = True
                continue
            if c == '"' and not escape_next:
                in_string = not in_string
                continue
            if in_string:
                continue
            if c == '{':
                depth += 1
            elif c == '}':
                depth -= 1
                if depth == 0:
                    # Found complete object
                    try:
                        obj = json.loads(json_str[start:i+1])
                        components.append(obj)
                    except json.JSONDecodeError:
                        pass
                    pos = i + 1
                    break
        else:
            # Reached end without finding matching brace — truncated
            break

    return components


async def analyze_content_with_llm(markdown_content: str) -> dict:
    """
    Use LLM to analyze markdown content and classify it.

    Args:
        markdown_content: Raw markdown content

    Returns:
        Content analysis dictionary
    """
    system_prompt = """You are an expert content analyst. Analyze documents and return structured JSON.
Always respond with valid JSON only, no additional text."""

    prompt = format_content_analysis_prompt(markdown_content)

    logger.info(f"[LLM] Analyzing content... (prompt length: {len(prompt)} chars)")
    try:
        response = await call_llm(prompt, system_prompt)
        logger.info(f"[LLM] Analysis response received ({len(response)} chars)")
    except Exception as e:
        logger.error(f"[LLM] Content analysis failed: {e}")
        import traceback
        traceback.print_exc()
        response = ""
    result = extract_json_from_response(response)

    # Provide defaults if parsing failed
    if not result:
        parsed = parse_markdown(markdown_content)
        result = {
            "document_type": _classify_heuristic(markdown_content, parsed),
            "title": parsed.get("title", "Untitled"),
            "entities": {"technologies": [], "tools": [], "languages": [], "concepts": []},
            "confidence": 0.5,
            "reasoning": "Fallback to heuristic analysis"
        }

    logger.info(f"[LLM] Content analyzed: {result.get('document_type', 'unknown')}")
    return result


async def select_layout_with_llm(content_analysis: dict) -> dict:
    """
    Use LLM to select optimal layout based on content analysis.

    Args:
        content_analysis: Content analysis results

    Returns:
        Layout selection dictionary
    """
    system_prompt = """You are an expert UI/UX designer. Select optimal layouts and return structured JSON.
Always respond with valid JSON only, no additional text."""

    prompt = format_layout_selection_prompt(content_analysis)

    logger.info("[LLM] Selecting layout...")
    response = await call_llm(prompt, system_prompt)
    result = extract_json_from_response(response)

    # Provide defaults if parsing failed
    if not result or "layout_type" not in result:
        result = {
            "layout_type": "summary_layout",
            "confidence": 0.5,
            "reasoning": "Default layout selection",
            "alternative_layouts": ["list_layout", "news_layout"],
            "component_suggestions": ["TLDR", "KeyTakeaways", "StatCard", "CalloutCard"]
        }

    logger.info(f"[LLM] Layout selected: {result.get('layout_type', 'unknown')}")
    return result


async def select_components_with_llm(
    content_analysis: dict,
    layout_decision: dict,
    markdown_content: str
) -> list[dict]:
    """
    Use LLM to select and configure A2UI components.

    Args:
        content_analysis: Content analysis results
        layout_decision: Layout selection results
        markdown_content: Original markdown for context

    Returns:
        List of component specifications
    """
    system_prompt = """You are an expert A2UI component architect. Generate diverse dashboard components.
CRITICAL: You MUST return valid JSON with a "components" array containing component specifications.
Each component needs: component_type, priority, zone, and props with actual data from the document.
RULES:
1. Cover ALL sections of the document - do NOT stop at the first few sections.
2. Use at least 6 different component types. No single type should exceed 40% of components.
3. Never place 3+ consecutive components of the same type.
4. Match component types to content: quotes->QuoteCard, news stories->HeadlineCard, stats->StatCard, events->TimelineEvent.
5. For documents with 5+ sections, generate 15-25 components."""

    prompt = format_component_selection_prompt(content_analysis, layout_decision)

    # Add full document content for the LLM to use (up to 30k chars)
    content_limit = 30000
    if len(markdown_content) > content_limit:
        doc_content = markdown_content[:content_limit] + "\n\n[... content truncated ...]"
    else:
        doc_content = markdown_content

    prompt += f"""

## Actual Document Content (use this to populate component props)

{doc_content}

CRITICAL: You must generate components covering ALL sections and topics above, not just the first few.
Extract REAL data from the document to populate component props.
Return JSON with "components" array."""

    logger.info(f"[LLM] Selecting components... (prompt length: {len(prompt)} chars)")
    try:
        response = await call_llm(prompt, system_prompt, max_tokens=16000, temperature=0.4)
        logger.info(f"[LLM] Response received ({len(response)} chars)")
    except Exception as e:
        logger.error(f"[LLM] Component selection failed: {e}")
        import traceback
        traceback.print_exc()
        raise

    result = extract_json_from_response(response)

    components = result.get("components", [])

    if not components:
        logger.error(f"[LLM] No components parsed. Response first 1000 chars:\n{response[:1000]}")
        raise ValueError(f"LLM returned no components. Parsed keys: {list(result.keys())}. Response length: {len(response)}. First 200 chars: {response[:200]}")

    # Validate variety
    variety = validate_component_variety(components)
    logger.info(f"[LLM] Components selected: {len(components)}, unique types: {variety['unique_types_count']}")
    if not variety['valid']:
        for violation in variety.get('violations', []):
            logger.warning(f"[VARIETY] {violation}")

    return components


def apply_layout_and_zone(component: A2UIComponent, spec: dict) -> A2UIComponent:
    """
    Apply layout width hint and semantic zone to a component.

    Uses the width_hint and zone from the spec if provided, otherwise falls back
    to the defaults for the component type.

    Args:
        component: The built A2UIComponent
        spec: Original component spec that may contain width_hint and zone

    Returns:
        Component with layout and zone fields set
    """
    # Get component type (strip legacy a2ui. prefix if present)
    component_type = component.type.replace("a2ui.", "")

    # Check for explicit width_hint in spec
    props = spec.get("props", {})
    explicit_width = props.get("width_hint") or spec.get("width_hint")

    # Use explicit width or fall back to default
    width = explicit_width or COMPONENT_DEFAULT_WIDTHS.get(component_type, "full")

    # Apply the layout
    component.layout = {"width": width}

    # Check for explicit zone in spec
    explicit_zone = spec.get("zone")

    # Case-insensitive lookup for zone defaults
    # Build a lowercase mapping for robust lookups
    zone_lookup_key = component_type
    default_zone = COMPONENT_DEFAULT_ZONES.get(zone_lookup_key)

    # If not found, try case-insensitive lookup
    if default_zone is None:
        lower_key = component_type.lower()
        for key, value in COMPONENT_DEFAULT_ZONES.items():
            if key.lower() == lower_key:
                default_zone = value
                logger.debug(f"[ZONE] Case-insensitive match: '{component_type}' → '{key}' → zone='{value}'")
                break

    # Final fallback to "content"
    if default_zone is None:
        default_zone = "content"
        logger.debug(f"[ZONE] No default zone for '{component_type}', using 'content'")

    # Use explicit zone or fall back to default
    zone = explicit_zone or default_zone

    # Debug logging for zone assignment
    logger.debug(f"[ZONE] {component_type}: explicit_zone={explicit_zone!r}, default={default_zone}, final={zone}")

    # Apply the zone
    component.zone = zone

    return component


def expand_component_specs(specs: list[dict]) -> list[dict]:
    """
    Expand component specs that contain batched items into individual specs.

    This handles ProConItem specs that have multiple items in an 'items' array,
    converting them into individual specs for each item.

    Args:
        specs: List of component specifications

    Returns:
        Expanded list with batched items converted to individual specs
    """
    expanded = []

    for spec in specs:
        component_type = spec.get("component_type", "")
        props = spec.get("props", {})

        # Handle ProConItem with multiple items
        if component_type == "ProConItem":
            items = props.get("items", [])
            item_type = props.get("type", "").lower()

            if items and isinstance(items, list) and len(items) > 1:
                is_pro = item_type in ("pro", "pros")
                is_con = item_type in ("con", "cons")

                if is_pro or is_con:
                    # Expand each item into its own ProConItem spec
                    for item in items:
                        expanded.append({
                            "component_type": "ProConItem",
                            "priority": spec.get("priority", "medium"),
                            "props": {
                                "type": "pro" if is_pro else "con",
                                "label": item,
                            }
                        })
                    continue

        # Handle bulletList with multiple items
        if component_type == "bulletList":
            items = props.get("items", [])
            if items and isinstance(items, list) and len(items) > 1:
                for item in items:
                    expanded.append({
                        "component_type": "bulletList",
                        "priority": spec.get("priority", "medium"),
                        "width_hint": spec.get("width_hint"),
                        "zone": spec.get("zone"),
                        "props": {
                            "text": item if isinstance(item, str) else str(item),
                        }
                    })
                continue

        # Keep spec as-is for all other cases
        expanded.append(spec)

    return expanded


def build_a2ui_component(spec: dict, content_analysis: dict) -> A2UIComponent | None:
    """
    Build an A2UIComponent from a specification dictionary.

    Args:
        spec: Component specification from LLM
        content_analysis: Content analysis for fallback data

    Returns:
        A2UIComponent instance or None if invalid
    """
    component_type = spec.get("component_type", "")
    props = spec.get("props", {})

    # Normalize component type - strip whitespace
    component_type = component_type.strip()

    # Case-insensitive type mapping for robust LLM output handling
    # Maps lowercase versions and legacy a2ui.* names to canonical camelCase types
    COMPONENT_TYPE_CANONICAL = {
        # Direct camelCase (expected from LLM)
        "tldr": "tldr",
        "keytakeaways": "keyTakeaways",
        "statcard": "statCard",
        "metricrow": "metricRow",
        "datatable": "dataTable",
        "headlinecard": "headlineCard",
        "calloutcard": "calloutCard",
        "quotecard": "quoteCard",
        "bulletlist": "bulletList",
        "codeblock": "codeBlock",
        "stepcard": "stepCard",
        "comparisontable": "comparisonTable",
        "vscard": "vsCard",
        "linkpreview": "linkPreview",
        "profilecard": "profileCard",
        # Legacy a2ui.* PascalCase names (backwards compat)
        "bulletpoint": "bulletList",
        "linkcard": "linkPreview",
    }

    # Try to normalize type (handle various casing from LLM)
    original_type = component_type
    lookup = component_type.lower().replace("a2ui.", "")
    if lookup in COMPONENT_TYPE_CANONICAL:
        component_type = COMPONENT_TYPE_CANONICAL[lookup]
        if original_type != component_type:
            logger.debug(f"[BUILD] Normalized component type: '{original_type}' → '{component_type}'")
    elif component_type and component_type not in COMPONENT_TYPE_CANONICAL.values():
        logger.warning(f"[BUILD] Unknown component type: '{component_type}'")

    try:
        # Map component types to generator functions
        if component_type == "tldr":
            # Try multiple keys the LLM might use for tldr text
            content = (
                props.get("content")
                or props.get("text")
                or props.get("body")
                or props.get("summary")
                or spec.get("data_source")
                or content_analysis.get("title", "")
            )
            if not content or not content.strip():
                logger.warning("[SKIP] tldr: no content found in props or fallbacks")
                return None
            content = content.strip()
            if len(content) > 300:
                content = content[:297] + "..."
            return generate_tldr(content=content, max_length=props.get("max_length", 200))

        elif component_type == "keyTakeaways":
            items = props.get("items")
            if not items or not isinstance(items, list) or not any(isinstance(i, str) and i.strip() for i in items):
                logger.warning("[SKIP] keyTakeaways: no valid items provided by LLM")
                return None
            return generate_key_takeaways(items=[i for i in items if isinstance(i, str) and i.strip()][:5])

        elif component_type == "statCard":
            change_val = props.get("trendValue", props.get("change_value", props.get("change")))
            change_float = None
            if change_val is not None:
                try:
                    change_str = str(change_val).replace(",", "").replace("%", "").replace("+", "")
                    change_float = float(change_str) if change_str else None
                except (ValueError, TypeError):
                    change_float = None

            trend = props.get("trend", "neutral")
            change_type_map = {"up": "positive", "down": "negative", "positive": "positive", "negative": "negative"}
            change_type = change_type_map.get(trend, "neutral")

            title = props.get("label") or props.get("title")
            value = props.get("value")
            if not title or not value:
                logger.warning(f"[SKIP] statCard: missing required title={title!r} or value={value!r}")
                return None
            return generate_stat_card(
                title=title,
                value=str(value),
                unit=props.get("unit"),
                change=change_float,
                change_type=change_type,
                highlight=props.get("highlight", False)
            )

        elif component_type == "metricRow":
            metrics_data = props.get("metrics", [])
            if metrics_data and isinstance(metrics_data, list):
                metrics = []
                for m in metrics_data:
                    if isinstance(m, dict) and m.get("label") and m.get("value") is not None:
                        metrics.append({
                            "label": m["label"],
                            "value": m["value"],
                            "unit": m.get("unit", "")
                        })
                if not metrics:
                    logger.warning("[SKIP] metricRow: no valid metrics with label+value in array")
                    return None
                return generate_metric_row(
                    label=props.get("title") or props.get("label", ""),
                    metrics=metrics
                )
            else:
                label = props.get("label") or props.get("title")
                value = props.get("value")
                if not label or value is None:
                    logger.warning(f"[SKIP] metricRow: missing required label={label!r} or value={value!r}")
                    return None
                return generate_metric_row(
                    label=label,
                    value=value,
                    unit=props.get("unit", "")
                )

        elif component_type == "dataTable":
            headers = props.get("headers")
            rows = props.get("rows")
            if not headers or not rows:
                logger.warning("[SKIP] dataTable: missing required headers or rows")
                return None
            return generate_data_table(headers=headers, rows=rows)

        elif component_type == "headlineCard":
            title = props.get("headline") or props.get("title")
            if not title:
                logger.warning("[SKIP] headlineCard: missing required headline/title")
                return None
            return generate_headline_card(
                title=title,
                summary=props.get("subheadline") or props.get("subtitle") or props.get("summary", ""),
                source=props.get("source", ""),
                published_at=props.get("timestamp") or props.get("published_at") or props.get("publishedAt", ""),
                sentiment=props.get("sentiment", "neutral"),
                image_url=props.get("image_url") or props.get("imageUrl")
            )

        elif component_type == "calloutCard":
            content = props.get("content")
            if not content or not str(content).strip():
                logger.warning("[SKIP] calloutCard: missing required content")
                return None
            return generate_callout_card(
                type=props.get("type", "info"),
                title=props.get("title", ""),
                content=content
            )

        elif component_type == "quoteCard":
            text = props.get("quote") or props.get("text")
            if not text or not str(text).strip():
                logger.warning("[SKIP] quoteCard: missing required quote text")
                return None
            return generate_quote_card(
                text=text,
                author=props.get("author", ""),
                source=props.get("source")
            )

        elif component_type == "bulletList":
            # After expansion, each spec has "text". Handle single-item "items" as fallback.
            text = props.get("text")
            if not text:
                items = props.get("items", [])
                text = items[0] if items and isinstance(items, list) and items else None
            if not text or not str(text).strip():
                logger.warning("[SKIP] bulletList: no text content provided by LLM")
                return None
            return generate_bullet_point(
                text=text if isinstance(text, str) else str(text)
            )

        elif component_type == "codeBlock":
            code = props.get("code")
            if not code or not code.strip():
                logger.warning("[SKIP] codeBlock: no code content provided by LLM")
                return None
            return generate_code_block(
                code=code,
                language=props.get("language", "text")
            )

        elif component_type == "stepCard":
            title = props.get("title")
            description = props.get("description")
            if not title or not description:
                logger.warning(f"[SKIP] stepCard: missing required title={title!r} or description={description!r}")
                return None
            return generate_step_card(
                step_number=props.get("step_number", props.get("number", 1)),
                title=title,
                description=description
            )

        elif component_type == "comparisonTable":
            items = props.get("items", [])
            features = props.get("features", [])
            if not items or not features:
                return None
            return generate_comparison_table(items=items, features=features)

        elif component_type == "vsCard":
            item_a = props.get("itemA") or props.get("item_a")
            item_b = props.get("itemB") or props.get("item_b")
            if not item_a or not item_b:
                logger.warning(f"[SKIP] vsCard: missing required itemA={item_a!r} or itemB={item_b!r}")
                return None
            return generate_component("vsCard", {
                "itemA": item_a,
                "itemB": item_b,
                "winner": props.get("winner"),
                "criteria": props.get("criteria", []),
            })

        elif component_type == "linkPreview":
            url = props.get("url", "")
            if not is_valid_external_url(url):
                logger.warning(f"[SKIP] linkPreview: invalid URL: {url!r}")
                return None
            return generate_link_card(
                url=url,
                title=props.get("title", "")
            )

        elif component_type == "profileCard":
            name = props.get("name")
            if not name:
                logger.warning("[SKIP] profileCard: missing required name")
                return None
            return generate_component("profileCard", {
                "name": name,
                "title": props.get("title") or props.get("role", ""),
                "bio": props.get("bio") or props.get("description", ""),
                "imageUrl": props.get("imageUrl") or props.get("avatar"),
                "links": props.get("links", [])
            })

        else:
            # Unknown component type — skip rather than emit hardcoded fallback
            logger.warning(f"[SKIP] Unknown component type '{component_type}' — no fallback emitted")
            return None

    except Exception as e:
        logger.error(f"[BUILD] Failed to build {component_type}: {e}")
        return None


async def orchestrate_dashboard_with_llm(markdown_content: str) -> AsyncGenerator[A2UIComponent, None]:
    """
    Main orchestration function that uses LLM to generate dashboard components.

    This is the async generator version that yields components one at a time
    for streaming via SSE.

    Args:
        markdown_content: Raw markdown content to transform

    Yields:
        A2UIComponent instances
    """
    # Reset ID counter for fresh component IDs
    reset_id_counter()

    logger.info("=" * 60)
    logger.info("[ORCHESTRATOR] Starting LLM-powered dashboard generation")
    logger.info("=" * 60)

    # Step 1: Parse markdown structure (fast, no LLM)
    parsed = parse_markdown(markdown_content)
    logger.info(f"[PARSE] Title: {parsed.get('title', 'Untitled')}")
    logger.info(f"[PARSE] Sections: {len(parsed.get('sections', []))}")
    logger.info(f"[PARSE] Code blocks: {len(parsed.get('code_blocks', []))}")

    # Step 2: Analyze content with LLM
    content_analysis = await analyze_content_with_llm(markdown_content)

    # Merge parsed data with LLM analysis
    full_analysis = {
        **content_analysis,
        "sections": parsed.get("sections", []),
        "code_blocks": parsed.get("code_blocks", []),
        "tables": parsed.get("tables", []),
        "links": parsed.get("all_links", []),
        "youtube_links": parsed.get("youtube_links", []),
        "github_links": parsed.get("github_links", []),
    }

    # Step 3: Select layout with LLM
    layout_decision = await select_layout_with_llm(full_analysis)

    # Step 4: Select components with LLM
    component_specs = await select_components_with_llm(
        full_analysis,
        layout_decision,
        markdown_content
    )

    # Step 5: Expand batched specs (e.g., ProConItem with multiple items)
    expanded_specs = expand_component_specs(component_specs)

    # Step 6: Build and yield A2UI components
    logger.info(f"[BUILD] Building {len(expanded_specs)} components (expanded from {len(component_specs)} specs)...")

    components_built = 0
    component_types_used = set()

    for spec in expanded_specs:
        component = build_a2ui_component(spec, full_analysis)
        if component:
            # Apply layout width hints and semantic zone
            component = apply_layout_and_zone(component, spec)

            components_built += 1
            component_types_used.add(component.type)
            logger.info(f"[YIELD] Component {components_built}: {component.type} (id={component.id}, width={component.layout.get('width', 'full')}, zone={component.zone})")
            yield component

    logger.info(f"[COMPLETE] Generated {components_built} components with {len(component_types_used)} unique types")
    logger.info("=" * 60)


async def orchestrate_dashboard_with_llm_list(markdown_content: str) -> list[A2UIComponent]:
    """
    Synchronous list version that collects all components.

    Args:
        markdown_content: Raw markdown content

    Returns:
        List of A2UIComponent instances
    """
    components = []
    async for component in orchestrate_dashboard_with_llm(markdown_content):
        components.append(component)
    return components
