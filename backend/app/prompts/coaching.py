"""
backend/app/prompts/coaching.py
Prompts and Pydantic schemas for LLM Call 3: Coaching & Action Plan.
Generates strengths, improvement opportunities, and next call directive.
"""
from __future__ import annotations

import json
from typing import Any, List, Optional
from pydantic import BaseModel, Field


class CoachingOutput(BaseModel):
    strengths: List[str] = Field(
        description="2-3 specific strengths demonstrated by the counsellor during the call, grounded in what was said"
    )
    improvements: List[str] = Field(
        description="2-3 specific, actionable coaching improvements for the counsellor based on missed rubric criteria or compliance issues"
    )
    next_call_focus: str = Field(
        description="A concise directive (1-2 sentences) summarizing the highest priority focus area for the counsellor in their next call"
    )


def build_coaching_system_prompt() -> str:
    return (
        "You are an expert sales performance coach for Physics Wallah (PW) educational counselling.\n"
        "Your task is to review the call transcript, rubric scores, and compliance audit results to generate "
        "practical, motivating, and actionable coaching guidance for the counsellor.\n\n"
        "GUIDELINES:\n"
        "1. GROUNDED IN EVIDENCE: Highlight what the counsellor actually said or missed in the call.\n"
        "2. CONSTRUCTIVE & ACTIONABLE: Give concrete phrasing or techniques the counsellor can use next time.\n"
        "3. STRENGTHS: Provide 2-3 genuine strengths with clear examples from the call.\n"
        "4. IMPROVEMENTS: Provide 2-3 prioritized coaching opportunities based on rubric gaps or compliance flags.\n"
        "5. NEXT CALL FOCUS: A crisp, 1-2 sentence directive prioritizing the single most impactful adjustment.\n"
        "6. OUTPUT FORMAT: Output strictly valid JSON matching the requested schema. No surrounding text outside JSON."
    )


def build_coaching_user_prompt(
    formatted_transcript: str,
    overall_score: float,
    criteria_summary: List[dict[str, Any]],
    compliance_summary: List[dict[str, Any]],
) -> str:
    crit_text = "\n".join(
        f"- {c.get('criterion_id')}: score {c.get('score')}/4 (confidence: {c.get('confidence')}) — {c.get('rationale')}"
        for c in criteria_summary
    )
    
    flag_text = "None"
    if compliance_summary:
        flag_text = "\n".join(
            f"- [{f.get('severity', '').upper()}] {f.get('rule_id')}: {f.get('explanation')}"
            for f in compliance_summary
        )

    return f"""Generate an actionable coaching and action plan for the counsellor based on this counselling call.

[EVALUATION SUMMARY]
Overall Score: {overall_score:.1f}/100

Criteria Scores:
{crit_text}

Compliance Flags:
{flag_text}

[CALL TRANSCRIPT]
{formatted_transcript}

Provide a JSON object adhering to this schema:
{{
  "strengths": [
    "<strength 1 with specific observation>",
    "<strength 2 with specific observation>"
  ],
  "improvements": [
    "<coaching opportunity 1 with actionable advice>",
    "<coaching opportunity 2 with actionable advice>"
  ],
  "next_call_focus": "<1-2 sentence directive for next counselling call>"
}}
"""
