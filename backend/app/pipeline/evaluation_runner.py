"""
backend/app/pipeline/evaluation_runner.py
End-to-end evaluation runner:
  1. Formats transcript for LLM
  2. Executes LLM Call 1 (Rubric Evaluation) + verifies evidence with 1 repair retry
  3. Executes LLM Call 2 (Compliance Audit) + verifies flags with 1 repair retry
  4. Runs deterministic scoring + gate check (needs_review)
  5. Records all metrics into DB (Evaluation, Criteria, Evidence, Flags, LlmRun)
"""
from __future__ import annotations

import logging
import time
from typing import Optional

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.api.rubric import get_or_seed_active_rubric
from app.config.rubric_loader import CriterionConfig, RubricConfig
from app.models.orm import (
    Call,
    Coaching,
    ComplianceFlag,
    Evaluation,
    EvaluationCriterion,
    EvaluationEvidence,
    LlmRun,
    TranscriptSegment,
    TranscriptVersion,
)
from app.pipeline.scoring import calculate_overall_score
from app.pipeline.segments import NormalisedSegment, format_segment_for_prompt
from app.pipeline.verify import verify_compliance_flags, verify_rubric_evaluations
from app.prompts.compliance import (
    ComplianceOutput,
    build_compliance_system_prompt,
    build_compliance_user_prompt,
    extract_valid_rule_ids,
    load_policy_text,
)
from app.prompts.coaching import (
    CoachingOutput,
    build_coaching_system_prompt,
    build_coaching_user_prompt,
)
from app.prompts.evaluator import (
    EvaluatorOutput,
    build_evaluator_system_prompt,
    build_evaluator_user_prompt,
)
from app.services.llm import BaseLLMClient, ClaudeClient, get_llm_client

logger = logging.getLogger("pw_qa.pipeline.evaluation")


def run_evaluation_pipeline(
    call_id: int,
    db: Session,
    client: Optional[BaseLLMClient] = None,
    bypass_cache: bool = False,
    transcript_version_id: Optional[int] = None,
) -> Evaluation:
    """
    Run full QA analysis on a transcribed call.
    Updates DB with complete scores, flags, evidence, and timing metrics.
    """
    call = db.get(Call, call_id)
    if not call:
        raise ValueError(f"Call {call_id} not found")

    if call.status in ("uploaded", "transcribing"):
        raise ValueError(f"Call {call_id} is not ready for analysis (status: {call.status})")

    call.status = "analyzing"
    db.commit()

    t_start = time.perf_counter()
    if client is None:
        client = get_llm_client()

    try:
        # Determine target transcript version if not provided
        if transcript_version_id is None:
            latest_tv = (
                db.query(TranscriptVersion)
                .filter(TranscriptVersion.call_id == call_id)
                .order_by(TranscriptVersion.version_number.desc())
                .first()
            )
            if latest_tv:
                transcript_version_id = latest_tv.id

        # 1. Fetch transcript segments for the selected version
        seg_query = db.query(TranscriptSegment).filter(TranscriptSegment.call_id == call_id)
        if transcript_version_id is not None:
            # If segments have transcript_version_id, filter by it
            has_ver_segments = (
                db.query(TranscriptSegment)
                .filter(
                    TranscriptSegment.call_id == call_id,
                    TranscriptSegment.transcript_version_id == transcript_version_id,
                )
                .count()
            )
            if has_ver_segments > 0:
                seg_query = seg_query.filter(TranscriptSegment.transcript_version_id == transcript_version_id)

        segments = seg_query.order_by(TranscriptSegment.start_ms).all()
        if not segments:
            call.status = "failed"
            call.failure_reason = "No transcript segments found for analysis."
            db.commit()
            raise ValueError(call.failure_reason)

        # Convert to prompt lines
        prompt_lines = [
            format_segment_for_prompt(
                NormalisedSegment(
                    segment_id=s.segment_id,
                    start_ms=s.start_ms,
                    end_ms=s.end_ms,
                    speaker_label=s.speaker_label,
                    role=s.role,
                    text=s.text,
                )
            )
            for s in segments
        ]
        formatted_transcript = "\n".join(prompt_lines)

        # 2. Fetch active rubric
        active_rubric = get_or_seed_active_rubric(db)
        rubric_cfg = RubricConfig(
            version=active_rubric.version,
            name=f"Rubric v{active_rubric.version}",
            criteria=[CriterionConfig.model_validate(c) for c in active_rubric.criteria_json],
        )

        # 3. Load synthetic policy
        policy_text = load_policy_text()
        valid_rules = extract_valid_rule_ids(policy_text)

        # ── LLM Call 1: Rubric Evaluator ──────────────────────────────────────
        sys_eval = build_evaluator_system_prompt()
        user_eval = build_evaluator_user_prompt(formatted_transcript, rubric_cfg)

        eval_model, llm_run_1 = client.generate_structured(
            system_prompt=sys_eval,
            user_prompt=user_eval,
            schema_cls=EvaluatorOutput,
            rubric_version=active_rubric.version,
            bypass_cache=bypass_cache,
        )

        # Save LLM run 1
        db.add(
            LlmRun(
                call_id=call.id,
                call_type="evaluator",
                model=client.model,
                input_tokens=llm_run_1.input_tokens,
                output_tokens=llm_run_1.output_tokens,
                estimated_cost_usd=llm_run_1.estimated_cost_usd,
                latency_ms=llm_run_1.latency_ms,
                status="succeeded" if llm_run_1.success else "failed",
                error_detail=llm_run_1.error_detail,
                from_cache=llm_run_1.from_cache,
                attempt_number=llm_run_1.attempts,
                price_version=llm_run_1.price_version,
            )
        )
        db.flush()

        # Evidence verification for Call 1
        verified_criteria, log_1, retry_errs_1 = verify_rubric_evaluations(
            eval_model.evaluations, segments
        )

        # Retry once if verification failed
        if retry_errs_1 and not llm_run_1.from_cache:
            logger.info("Retrying Evaluator with verification errors...")
            retry_user_eval = build_evaluator_user_prompt(
                formatted_transcript, rubric_cfg, retry_errors=retry_errs_1
            )
            try:
                eval_model, retry_run_1 = client.generate_structured(
                    system_prompt=sys_eval,
                    user_prompt=retry_user_eval,
                    schema_cls=EvaluatorOutput,
                    rubric_version=active_rubric.version,
                    bypass_cache=True,
                )
                db.add(
                    LlmRun(
                        call_id=call.id,
                        call_type="evaluator_retry",
                        model=client.model,
                        input_tokens=retry_run_1.input_tokens,
                        output_tokens=retry_run_1.output_tokens,
                        estimated_cost_usd=retry_run_1.estimated_cost_usd,
                        latency_ms=retry_run_1.latency_ms,
                        status="succeeded" if retry_run_1.success else "failed",
                        error_detail=retry_run_1.error_detail,
                        from_cache=False,
                        attempt_number=2,
                        price_version=retry_run_1.price_version,
                    )
                )
                db.flush()
                verified_criteria, log_1, _ = verify_rubric_evaluations(
                    eval_model.evaluations, segments
                )
            except Exception as e:
                logger.warning("Evaluator retry failed; keeping first attempt: %s", e)

        # ── LLM Call 2: Compliance Auditor ───────────────────────────────────
        sys_comp = build_compliance_system_prompt()
        user_comp = build_compliance_user_prompt(formatted_transcript, policy_text)

        comp_model, llm_run_2 = client.generate_structured(
            system_prompt=sys_comp,
            user_prompt=user_comp,
            schema_cls=ComplianceOutput,
            rubric_version=active_rubric.version,
            bypass_cache=bypass_cache,
        )

        # Save LLM run 2
        db.add(
            LlmRun(
                call_id=call.id,
                call_type="compliance",
                model=client.model,
                input_tokens=llm_run_2.input_tokens,
                output_tokens=llm_run_2.output_tokens,
                estimated_cost_usd=llm_run_2.estimated_cost_usd,
                latency_ms=llm_run_2.latency_ms,
                status="succeeded" if llm_run_2.success else "failed",
                error_detail=llm_run_2.error_detail,
                from_cache=llm_run_2.from_cache,
                attempt_number=llm_run_2.attempts,
                price_version=llm_run_2.price_version,
            )
        )
        db.flush()

        # Evidence verification for Call 2
        flags_to_check = [] if comp_model.no_flags else comp_model.flags
        verified_flags, log_2, retry_errs_2 = verify_compliance_flags(
            flags_to_check, segments, valid_rules
        )

        # Retry once if verification failed
        if retry_errs_2 and not llm_run_2.from_cache:
            logger.info("Retrying Compliance Auditor with verification errors...")
            retry_user_comp = build_compliance_user_prompt(
                formatted_transcript, policy_text, retry_errors=retry_errs_2
            )
            try:
                comp_model, retry_run_2 = client.generate_structured(
                    system_prompt=sys_comp,
                    user_prompt=retry_user_comp,
                    schema_cls=ComplianceOutput,
                    rubric_version=active_rubric.version,
                    bypass_cache=True,
                )
                db.add(
                    LlmRun(
                        call_id=call.id,
                        call_type="compliance_retry",
                        model=client.model,
                        input_tokens=retry_run_2.input_tokens,
                        output_tokens=retry_run_2.output_tokens,
                        estimated_cost_usd=retry_run_2.estimated_cost_usd,
                        latency_ms=retry_run_2.latency_ms,
                        status="succeeded" if retry_run_2.success else "failed",
                        error_detail=retry_run_2.error_detail,
                        from_cache=False,
                        attempt_number=2,
                        price_version=retry_run_2.price_version,
                    )
                )
                db.flush()
                flags_to_check = [] if comp_model.no_flags else comp_model.flags
                verified_flags, log_2, _ = verify_compliance_flags(
                    flags_to_check, segments, valid_rules
                )
            except Exception as e:
                logger.warning("Compliance retry failed; keeping first attempt: %s", e)

        # ── Deterministic Scoring (Python) ───────────────────────────────────
        score_res = calculate_overall_score(
            criteria=verified_criteria,
            flags=verified_flags,
            rubric=rubric_cfg,
            weights_override=active_rubric.weights_json,
        )

        # ── Persist Evaluation to DB ─────────────────────────────────────────
        # Delete existing evaluation and its child records for this call if re-analyzing
        existing_eval = (
            db.query(Evaluation).filter(Evaluation.call_id == call_id).first()
        )
        if existing_eval:
            db.query(Coaching).filter(Coaching.evaluation_id == existing_eval.id).delete()
            db.query(EvaluationEvidence).filter(EvaluationEvidence.evaluation_id == existing_eval.id).delete()
            db.query(EvaluationCriterion).filter(EvaluationCriterion.evaluation_id == existing_eval.id).delete()
            db.query(ComplianceFlag).filter(ComplianceFlag.evaluation_id == existing_eval.id).delete()
            db.delete(existing_eval)
            db.flush()

        eval_record = Evaluation(
            call_id=call.id,
            rubric_version_id=active_rubric.id,
            transcript_version_id=transcript_version_id,
            is_stale=False,
            stale_reason=None,
            overall_score=score_res.overall_score,
            has_critical_flag=score_res.has_critical_flag,
        )
        db.add(eval_record)
        db.flush()

        # Persist criteria scores
        for cr in score_res.criteria_results:
            crit_row = EvaluationCriterion(
                evaluation_id=eval_record.id,
                criterion_id=cr.criterion_id,
                score=cr.raw_score,
                confidence=cr.confidence,
                rationale=cr.rationale,
                not_applicable=cr.not_applicable,
                weight_used=cr.configured_weight,
            )
            db.add(crit_row)

        # Persist criteria evidence items
        for vc in verified_criteria:
            for ev in vc.evidence:
                db.add(
                    EvaluationEvidence(
                        evaluation_id=eval_record.id,
                        criterion_id=vc.criterion_id,
                        flag_id=None,
                        segment_id=ev.segment_id,
                        quote=ev.quote,
                        note=ev.note,
                        verification_status=ev.status,
                        verification_detail=ev.detail,
                    )
                )

        # Persist compliance flags and their evidence
        for vf in verified_flags:
            flag_row = ComplianceFlag(
                evaluation_id=eval_record.id,
                rule_id=vf.rule_id,
                severity=vf.severity,
                confidence=vf.confidence,
                explanation=vf.explanation,
                status=vf.status,
            )
            db.add(flag_row)
            db.flush()

            for ev in vf.evidence:
                db.add(
                    EvaluationEvidence(
                        evaluation_id=eval_record.id,
                        criterion_id="compliance",
                        flag_id=flag_row.id,
                        segment_id=ev.segment_id,
                        quote=ev.quote,
                        note=ev.note,
                        verification_status=ev.status,
                        verification_detail=ev.detail,
                    )
                )

        # ── LLM Call 3: Coaching & Action Plan ───────────────────────────────
        sys_coach = build_coaching_system_prompt()
        crit_summary = [
            {
                "criterion_id": cr.criterion_id,
                "score": cr.raw_score,
                "confidence": cr.confidence,
                "rationale": cr.rationale,
            }
            for cr in score_res.criteria_results
        ]
        flag_summary = [
            {
                "rule_id": vf.rule_id,
                "severity": vf.severity,
                "explanation": vf.explanation,
            }
            for vf in verified_flags
            if vf.is_verified
        ]
        user_coach = build_coaching_user_prompt(
            formatted_transcript, score_res.overall_score, crit_summary, flag_summary
        )

        coach_model = None
        try:
            coach_model, llm_run_3 = client.generate_structured(
                system_prompt=sys_coach,
                user_prompt=user_coach,
                schema_cls=CoachingOutput,
                rubric_version=active_rubric.version,
                bypass_cache=bypass_cache,
            )
            db.add(
                LlmRun(
                    call_id=call.id,
                    call_type="coaching",
                    model=client.model,
                    input_tokens=llm_run_3.input_tokens,
                    output_tokens=llm_run_3.output_tokens,
                    estimated_cost_usd=llm_run_3.estimated_cost_usd,
                    latency_ms=llm_run_3.latency_ms,
                    status="succeeded" if llm_run_3.success else "failed",
                    error_detail=llm_run_3.error_detail,
                    from_cache=llm_run_3.from_cache,
                    attempt_number=llm_run_3.attempts,
                    price_version=llm_run_3.price_version,
                )
            )
            db.flush()
        except Exception as e:
            logger.warning("Coaching generation failed: %s", e)

        # Persist coaching record
        if coach_model:
            coaching_row = Coaching(
                evaluation_id=eval_record.id,
                content_json=coach_model.model_dump(),
            )
            db.add(coaching_row)
            eval_record.coaching = coaching_row

        # Finalize call status and timing
        call.status = score_res.status
        call.t_llm_s = round(time.perf_counter() - t_start, 2)
        db.commit()
        setattr(eval_record, "_raw_evaluator", eval_model.model_dump())
        setattr(eval_record, "_raw_compliance", comp_model.model_dump())
        if coach_model:
            setattr(eval_record, "_raw_coaching", coach_model.model_dump())
        return eval_record

    except Exception as exc:
        logger.exception("Analysis pipeline failed for call %d", call_id)
        call.status = "failed"
        call.failure_reason = str(exc)
        db.commit()
        raise
