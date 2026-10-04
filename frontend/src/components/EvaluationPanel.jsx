import React, { useState } from 'react';
import {
  ChevronDown,
  ChevronRight,
  CheckCircle2,
  AlertTriangle,
  Sparkles,
  ShieldCheck,
  ShieldAlert,
  ArrowRight,
  Info,
  Clock,
  Play,
} from 'lucide-react';

function formatMs(ms) {
  if (ms === undefined || ms === null) return '00:00';
  const totalSec = Math.floor(ms / 1000);
  const mins = Math.floor(totalSec / 60);
  const secs = totalSec % 60;
  return `${mins.toString().padStart(2, '0')}:${secs.toString().padStart(2, '0')}`;
}

export default function EvaluationPanel({
  evaluation,
  segments = [],
  onJumpToEvidence,
  flaggedMoments = [],
  onAnalyze,
  isAnalyzing = false,
}) {
  const [expandedCriteria, setExpandedCriteria] = useState({
    discovery: true,
    course_fit: false,
    pitch_quality: false,
    objection_handling: false,
    closing_next_steps: false,
    compliance: false,
  });

  const [activeTab, setActiveTab] = useState('rubric'); // 'rubric' | 'compliance' | 'flags' | 'coaching'

  const toggleCriterion = (id) => {
    setExpandedCriteria((prev) => ({ ...prev, [id]: !prev[id] }));
  };

  // Build lookup map for segments to get accurate start timestamps
  const segmentMap = React.useMemo(() => {
    const map = {};
    for (const s of segments) {
      map[s.segment_id] = s;
    }
    return map;
  }, [segments]);

  // ── No evaluation loaded yet ─────────────────────────────────────────────
  if (!evaluation) {
    return (
      <div
        className="evaluation-panel"
        style={{
          display: 'flex',
          flexDirection: 'column',
          alignItems: 'center',
          justifyContent: 'center',
          minHeight: '340px',
          gap: '12px',
          padding: '24px',
        }}
      >
        <Sparkles size={36} style={{ color: 'var(--primary)', opacity: isAnalyzing ? 1 : 0.5, animation: isAnalyzing ? 'spin 2s linear infinite' : 'none' }} />
        <div style={{ fontSize: '15px', fontWeight: 600, color: 'var(--text-primary)' }}>
          {isAnalyzing ? 'Evaluating Call…' : 'No Evaluation Loaded'}
        </div>
        <div style={{ fontSize: '12px', color: 'var(--text-muted)', textAlign: 'center', maxWidth: '320px', lineHeight: 1.5 }}>
          {isAnalyzing
            ? 'Running AI rubric evaluation, criteria scoring, compliance checks, and coaching recommendations.'
            : 'Transcription is complete. Click below to score this call against the rubric and view AI evaluation.'}
        </div>
        {onAnalyze && (
          <button
            className="btn btn-primary btn-sm"
            style={{ marginTop: '8px', gap: '6px' }}
            onClick={onAnalyze}
            disabled={isAnalyzing}
          >
            <Sparkles size={13} style={{ animation: isAnalyzing ? 'spin 1s linear infinite' : 'none' }} />
            {isAnalyzing ? 'Evaluating…' : 'Run Evaluation'}
          </button>
        )}
      </div>
    );
  }

  const criteria = evaluation.criteria || [];
  const complianceFlags = evaluation.compliance_flags || evaluation.flags || [];

  // Compute average AI confidence
  const confidences = criteria.map((c) => c.confidence).filter((v) => typeof v === 'number');
  const avgConfidence = confidences.length
    ? Math.round((confidences.reduce((a, b) => a + b, 0) / confidences.length) * 100)
    : 92;

  // Derive dynamic explainability summary
  // ── Build Why <Score>? breakdown strictly from stored data ──────────────
  const formattedScore = evaluation.overall_score !== null && evaluation.overall_score !== undefined
    ? Number(evaluation.overall_score).toFixed(1)
    : '—';

  // Read rubric thresholds from evaluation (one source of truth from rubric config)
  const minReviewScore = evaluation.min_overall_score_for_review ?? 50.0;
  const lowConfThresh = evaluation.low_confidence_display_threshold ?? 0.70;
  const minGateConf = evaluation.min_confidence_for_gate ?? 0.70;

  // 1. Gate Status (from evaluation.has_critical_flag and evaluation.status)
  const isGatedCritical = !!evaluation.has_critical_flag;
  const gateReason = isGatedCritical
    ? `Gated to Needs Review due to active critical compliance violation (confidence >= ${Math.round(minGateConf * 100)}%).`
    : (evaluation.overall_score !== null && evaluation.overall_score < minReviewScore)
    ? `Score below passing threshold (< ${minReviewScore.toFixed(1)}/100); routed to supervisor review.`
    : 'Call passed review gate without critical compliance violations.';

  // 2. High scoring criteria (score >= 3)
  const highScores = criteria.filter((c) => !c.not_applicable && typeof c.score === 'number' && c.score >= 3);

  // 3. Score deductions (score <= 2)
  const deductions = criteria.filter((c) => !c.not_applicable && typeof c.score === 'number' && c.score <= 2);

  // 4. Verified compliance flags
  const verifiedFlags = complianceFlags.filter((f) => f.status === 'verified');

  // 5. Low confidence items (using rubric configured low_confidence_display_threshold)
  const lowConfidenceCriteria = criteria.filter((c) => typeof c.confidence === 'number' && c.confidence < lowConfThresh);
  const lowConfidenceFlags = complianceFlags.filter((f) => typeof f.confidence === 'number' && f.confidence < lowConfThresh);

  return (
    <div className="evaluation-panel" style={{ display: 'flex', flexDirection: 'column', gap: '16px' }}>
      {/* ── Panel Header ── */}
      <div
        style={{
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'space-between',
          paddingBottom: '12px',
          borderBottom: '1px solid var(--border-subtle)',
        }}
      >
        <div>
          <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
            <span style={{ fontSize: '16px', fontWeight: 700, color: 'var(--text-primary)' }}>
              AI QA Evaluation
            </span>
            <span className="status-badge neutral">
              Rubric v{evaluation.rubric_version || 1}
            </span>
          </div>
          <div style={{ fontSize: '11.5px', color: 'var(--text-muted)', marginTop: '3px' }}>
            Deterministic Python Scoring · Verbatim Evidence Grounding
          </div>
        </div>

        {/* Overall Score & Status */}
        <div style={{ textAlign: 'right' }}>
          <div style={{ fontSize: '22px', fontWeight: 800, color: 'var(--text-primary)', letterSpacing: '-0.5px' }}>
            {evaluation.overall_score !== null && evaluation.overall_score !== undefined
              ? Number(evaluation.overall_score).toFixed(1)
              : '—'}{' '}
            <span style={{ fontSize: '13px', color: 'var(--text-muted)', fontWeight: 500 }}>/ 100</span>
          </div>
          <span
            className={`status-badge ${
              evaluation.status === 'completed'
                ? 'pass'
                : evaluation.status === 'needs_review' || evaluation.has_critical_flag
                ? 'danger'
                : 'warning'
            }`}
            style={{ fontSize: '10px', marginTop: '2px', display: 'inline-block' }}
          >
            {(evaluation.status || 'evaluated').toUpperCase()}
          </span>
        </div>
      </div>

      {/* ── Dynamic Quality Rating Bar ── */}
      <div style={{ display: 'flex', flexDirection: 'column', gap: '8px' }}>
        <div className="segmented-score-bar" style={{ display: 'flex', height: '8px', borderRadius: '4px', overflow: 'hidden', background: 'rgba(255,255,255,0.06)' }}>
          {criteria.map((c) => {
            const maxScore = c.max_score || 4;
            const rawScore = c.score ?? 0;
            const pct = c.not_applicable ? 1 : rawScore / maxScore;
            const isGood = pct >= 0.75;
            const isMid = pct >= 0.5;

            return (
              <div
                key={c.criterion_id}
                style={{
                  width: `${c.weight || 16.66}%`,
                  height: '100%',
                  background: c.not_applicable
                    ? 'rgba(148, 163, 184, 0.3)'
                    : isGood
                    ? 'var(--success)'
                    : isMid
                    ? 'var(--warning)'
                    : 'var(--danger)',
                  borderRight: '1px solid var(--bg-surface)',
                  transition: 'width 0.3s ease',
                }}
                title={
                  c.name
                    ? `${c.name}: ${c.not_applicable ? 'N/A' : (c.max_score != null ? `${Math.round(pct * 100)}%` : '[Missing max_score]')} (Weight: ${c.weight}%)`
                    : `[Schema Error: Missing criterion name for ${c.criterion_id}]`
                }
              />
            );
          })}
        </div>

        {/* Quality rating pills below the bar */}
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(130px, 1fr))', gap: '6px', fontSize: '11px' }}>
          {criteria.map((c) => {
            const hasMax = typeof c.max_score === 'number' && c.max_score > 0;
            const rawScore = c.score ?? 0;
            const pct = c.not_applicable ? null : hasMax ? Math.round((rawScore / c.max_score) * 100) : null;

            return (
              <div
                key={c.criterion_id}
                style={{
                  background: 'var(--bg-surface-elevated)',
                  border: '1px solid var(--border-subtle)',
                  borderRadius: 'var(--radius-sm)',
                  padding: '4px 8px',
                  display: 'flex',
                  alignItems: 'center',
                  justifyContent: 'space-between',
                }}
              >
                <span
                  style={{
                    color: !c.name ? 'var(--danger)' : 'var(--text-secondary)',
                    whiteSpace: 'nowrap',
                    overflow: 'hidden',
                    textOverflow: 'ellipsis',
                    maxWidth: '85px',
                  }}
                  title={c.name || `[Missing title: ${c.criterion_id}]`}
                >
                  {c.name ? c.name : <span style={{ color: 'var(--danger)', fontWeight: 600 }}>[Missing title]</span>}
                </span>
                <span
                  style={{
                    fontWeight: 700,
                    fontFamily: 'var(--font-mono)',
                    color: c.not_applicable
                      ? 'var(--text-muted)'
                      : !hasMax
                      ? 'var(--danger)'
                      : pct >= 75
                      ? 'var(--success-light)'
                      : pct >= 50
                      ? 'var(--warning)'
                      : 'var(--danger-light)',
                  }}
                >
                  {c.not_applicable ? 'N/A' : !hasMax ? 'ERR' : `${pct}%`}
                </span>
              </div>
            );
          })}
        </div>
      </div>

      {/* ── Sub-tabs ── */}
      <div style={{ display: 'flex', gap: '6px', borderBottom: '1px solid var(--border-subtle)', paddingBottom: '8px' }}>
        {[
          { id: 'rubric', label: 'Rubric Criteria' },
          { id: 'compliance', label: 'Compliance Audit' },
          { id: 'flags', label: `Flagged Moments (${flaggedMoments.length})` },
          { id: 'coaching', label: 'Coaching Plan' },
        ].map((tab) => (
          <button
            key={tab.id}
            className={`btn btn-sm ${activeTab === tab.id ? 'btn-primary' : 'btn-ghost'}`}
            style={{ fontSize: '12px', padding: '5px 10px', borderRadius: 'var(--radius-sm)' }}
            onClick={() => setActiveTab(tab.id)}
          >
            {tab.label}
          </button>
        ))}
      </div>

      {/* ── TAB 1: RUBRIC CRITERIA ── */}
      {activeTab === 'rubric' && (
        <div style={{ display: 'flex', flexDirection: 'column', gap: '12px' }}>
          {/* Why <score>? Explainability Card */}
          <div className="why-score-card">
            <div style={{ display: 'flex', alignItems: 'center', gap: '6px', marginBottom: '10px' }}>
              <Sparkles size={15} style={{ color: 'var(--primary)' }} />
              <strong style={{ fontSize: '13px', color: 'var(--text-primary)' }}>
                Why {formattedScore}?
              </strong>
              <span className="status-badge ai" style={{ marginLeft: 'auto', fontSize: '10.5px' }}>
                {avgConfidence}% AI Confidence
              </span>
            </div>

            <div style={{ display: 'flex', flexDirection: 'column', gap: '8px', fontSize: '12px', lineHeight: 1.5 }}>
              {/* Sentence 1: Gate Status */}
              <div style={{ display: 'flex', gap: '8px', alignItems: 'baseline' }}>
                <span className="status-badge neutral" style={{ fontSize: '10px', flexShrink: 0, padding: '2px 6px' }}>
                  Input: Gate Status
                </span>
                <span style={{ color: 'var(--text-secondary)' }}>
                  {gateReason}
                </span>
              </div>

              {/* Sentence 2: Strengths */}
              {highScores.length > 0 && (
                <div style={{ display: 'flex', gap: '8px', alignItems: 'baseline' }}>
                  <span className="status-badge pass" style={{ fontSize: '10px', flexShrink: 0, padding: '2px 6px' }}>
                    Input: Criterion Scores
                  </span>
                  <span style={{ color: 'var(--text-secondary)' }}>
                    Positive score contribution from{' '}
                    {highScores.map((c, i) => (
                      <span key={c.criterion_id}>
                        {i > 0 && ', '}
                        <strong>{c.name || c.criterion_id}</strong> ({c.score}/{c.max_score || 4}
                        {c.rationale ? `: "${c.rationale.slice(0, 90)}${c.rationale.length > 90 ? '...' : ''}"` : ''})
                      </span>
                    ))}.
                  </span>
                </div>
              )}

              {/* Sentence 3: Deductions */}
              {deductions.length > 0 ? (
                <div style={{ display: 'flex', gap: '8px', alignItems: 'baseline' }}>
                  <span className="status-badge warning" style={{ fontSize: '10px', flexShrink: 0, padding: '2px 6px' }}>
                    Input: Deductions & Rationales
                  </span>
                  <span style={{ color: 'var(--text-secondary)' }}>
                    Deductions occurred in{' '}
                    {deductions.map((c, i) => (
                      <span key={c.criterion_id}>
                        {i > 0 && ', '}
                        <strong>{c.name || c.criterion_id}</strong> ({c.score}/{c.max_score || 4}
                        {c.rationale ? `: "${c.rationale.slice(0, 90)}${c.rationale.length > 90 ? '...' : ''}"` : ''})
                      </span>
                    ))}.
                  </span>
                </div>
              ) : (
                <div style={{ display: 'flex', gap: '8px', alignItems: 'baseline' }}>
                  <span className="status-badge pass" style={{ fontSize: '10px', flexShrink: 0, padding: '2px 6px' }}>
                    Input: Deductions
                  </span>
                  <span style={{ color: 'var(--text-secondary)' }}>
                    No rubric criteria scored at or below 2.
                  </span>
                </div>
              )}

              {/* Sentence 4: Verified Compliance */}
              <div style={{ display: 'flex', gap: '8px', alignItems: 'baseline' }}>
                <span className="status-badge neutral" style={{ fontSize: '10px', flexShrink: 0, padding: '2px 6px' }}>
                  Input: Verified Flags
                </span>
                <span style={{ color: 'var(--text-secondary)' }}>
                  {verifiedFlags.length > 0
                    ? `${verifiedFlags.length} verified compliance violation(s): ${verifiedFlags.map((f) => `[${f.severity.toUpperCase()}] ${f.rule_id}`).join(', ')}.`
                    : 'Zero verified compliance violations.'}
                </span>
              </div>

              {/* Sentence 5: Low Confidence Warnings */}
              {(lowConfidenceCriteria.length > 0 || lowConfidenceFlags.length > 0) && (
                <div style={{ display: 'flex', gap: '8px', alignItems: 'baseline' }}>
                  <span className="status-badge danger" style={{ fontSize: '10px', flexShrink: 0, padding: '2px 6px' }}>
                    Input: Low Confidence List
                  </span>
                  <span style={{ color: 'var(--text-secondary)' }}>
                    Low confidence (&lt; {Math.round(lowConfThresh * 100)}%) flagged on:{' '}
                    {[
                      ...lowConfidenceCriteria.map((c) => `${c.name || c.criterion_id} (${Math.round((c.confidence || 0) * 100)}%)`),
                      ...lowConfidenceFlags.map((f) => `Flag ${f.rule_id} (${Math.round((f.confidence || 0) * 100)}%)`),
                    ].join(', ')}.
                  </span>
                </div>
              )}
            </div>
          </div>

          {/* Collapsible Criteria List */}
          {criteria.map((crit) => {
            const isExpanded = !!expandedCriteria[crit.criterion_id];
            const hasMax = typeof crit.max_score === 'number' && crit.max_score > 0;
            const scoreVal = crit.score ?? 0;
            const title = crit.name;

            let badgeClass = 'pass';
            let badgeLabel = 'GOOD';
            if (crit.not_applicable) {
              badgeClass = 'neutral';
              badgeLabel = 'N/A';
            } else if (!hasMax) {
              badgeClass = 'danger';
              badgeLabel = 'ERR';
            } else if (scoreVal === 4) {
              badgeClass = 'pass';
              badgeLabel = 'EXCELLENT';
            } else if (scoreVal === 3) {
              badgeClass = 'pass';
              badgeLabel = 'GOOD';
            } else if (scoreVal === 2) {
              badgeClass = 'warning';
              badgeLabel = 'ADEQUATE';
            } else {
              badgeClass = 'danger';
              badgeLabel = 'DEFICIENT';
            }

            return (
              <div key={crit.criterion_id} className="collapsible-criterion">
                {/* Header */}
                <div
                  className="criterion-header"
                  onClick={() => toggleCriterion(crit.criterion_id)}
                  style={{ cursor: 'pointer', userSelect: 'none' }}
                >
                  <div style={{ display: 'flex', alignItems: 'center', gap: '8px', flex: 1, minWidth: 0 }}>
                    {isExpanded ? <ChevronDown size={15} /> : <ChevronRight size={15} />}
                    <span style={{ fontSize: '13px', fontWeight: 600, color: 'var(--text-primary)' }}>
                      {title ? (
                        title
                      ) : (
                        <span style={{ color: 'var(--danger)', fontWeight: 700 }}>
                          [Error: Missing criterion name in rubric for {crit.criterion_id}]
                        </span>
                      )}
                    </span>
                    <span style={{ fontSize: '11px', color: 'var(--text-muted)' }}>
                      ({crit.weight}%)
                    </span>
                  </div>

                  <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
                    <span
                      style={{
                        fontFamily: 'var(--font-mono)',
                        fontSize: '13px',
                        fontWeight: 700,
                        color:
                          crit.not_applicable
                            ? 'var(--text-muted)'
                            : !hasMax
                            ? 'var(--danger)'
                            : scoreVal >= 3
                            ? 'var(--success)'
                            : scoreVal >= 2
                            ? 'var(--warning)'
                            : 'var(--danger)',
                      }}
                    >
                      {crit.not_applicable ? 'N/A' : hasMax ? `${scoreVal} / ${crit.max_score}` : `${scoreVal} / [Missing max_score]`}
                    </span>
                    <span className={`status-badge ${badgeClass}`} style={{ fontSize: '10px' }}>
                      {badgeLabel}
                    </span>
                  </div>
                </div>

                {/* Expanded Details */}
                {isExpanded && (
                  <div className="criterion-body" style={{ display: 'flex', flexDirection: 'column', gap: '10px' }}>
                    {/* Why rationale */}
                    <div style={{ fontSize: '12.5px', color: 'var(--text-secondary)', lineHeight: 1.5 }}>
                      <strong style={{ color: 'var(--text-primary)' }}>Why: </strong>
                      {crit.rationale || crit.why || 'No specific rationale provided.'}
                    </div>

                    {/* Verified Evidence Jump Chips */}
                    {crit.evidence && crit.evidence.length > 0 && (
                      <div style={{ display: 'flex', flexDirection: 'column', gap: '6px' }}>
                        <span style={{ fontSize: '11px', fontWeight: 600, color: 'var(--text-muted)' }}>
                          Verified Evidence Quotes ({crit.evidence.length}):
                        </span>
                        {crit.evidence.map((ev, i) => {
                          const seg = segmentMap[ev.segment_id];
                          const startMs = ev.start_ms !== undefined && ev.start_ms !== null ? ev.start_ms : (seg ? seg.start_ms : 0);
                          const timeStr = formatMs(startMs);

                          return (
                            <div
                              key={i}
                              className="evidence-jump-chip"
                              onClick={() =>
                                onJumpToEvidence &&
                                onJumpToEvidence({
                                  timestamp_ms: startMs,
                                  segment_id: ev.segment_id,
                                  quote: ev.quote,
                                })
                              }
                              title="Click to jump audio and transcript to this quote"
                              style={{ display: 'flex', alignItems: 'center', gap: '8px', cursor: 'pointer' }}
                            >
                              <span style={{ fontFamily: 'var(--font-mono)', fontWeight: 600, color: 'var(--primary)' }}>
                                {timeStr}
                              </span>
                              <span style={{ fontSize: '11px', color: 'var(--text-muted)' }}>
                                [{ev.segment_id}]
                              </span>
                              <span
                                style={{
                                  fontStyle: 'italic',
                                  flex: 1,
                                  whiteSpace: 'nowrap',
                                  overflow: 'hidden',
                                  textOverflow: 'ellipsis',
                                  fontSize: '12px',
                                }}
                              >
                                "{ev.quote}"
                              </span>
                              <span style={{ fontSize: '10.5px', color: 'var(--primary)', display: 'flex', alignItems: 'center', gap: '2px' }}>
                                Jump <Play size={10} />
                              </span>
                            </div>
                          );
                        })}
                      </div>
                    )}

                    {/* Weight & Model Confidence */}
                    <div
                      style={{
                        display: 'flex',
                        justifyContent: 'space-between',
                        fontSize: '11px',
                        color: 'var(--text-muted)',
                        borderTop: '1px solid rgba(148, 163, 184, 0.08)',
                        paddingTop: '8px',
                      }}
                    >
                      <span>Configured Rubric Weight: {crit.weight}%</span>
                      {crit.confidence !== undefined && (
                        <span>Model Confidence: {Math.round(crit.confidence * 100)}%</span>
                      )}
                    </div>
                  </div>
                )}
              </div>
            );
          })}
        </div>
      )}

      {/* ── TAB 2: COMPLIANCE AUDIT ── */}
      {activeTab === 'compliance' && (
        <div style={{ display: 'flex', flexDirection: 'column', gap: '14px' }}>
          <div className="why-score-card">
            <div style={{ display: 'flex', alignItems: 'center', gap: '8px', marginBottom: '8px' }}>
              {evaluation.has_critical_flag ? (
                <ShieldAlert size={18} style={{ color: 'var(--danger)' }} />
              ) : (
                <ShieldCheck size={18} style={{ color: 'var(--success-light)' }} />
              )}
              <strong style={{ fontSize: '13px', color: 'var(--text-primary)' }}>
                Compliance & Regulatory Status
              </strong>
              <span
                className={`status-badge ${
                  evaluation.has_critical_flag
                    ? 'danger'
                    : complianceFlags.length > 0
                    ? 'warning'
                    : 'pass'
                }`}
                style={{ marginLeft: 'auto' }}
              >
                {evaluation.has_critical_flag
                  ? 'CRITICAL GATE TRIGGERED'
                  : complianceFlags.length > 0
                  ? 'OBSERVATIONS FOUND'
                  : 'CLEARED - COMPLIANT'}
              </span>
            </div>
            <p style={{ fontSize: '12px', color: 'var(--text-secondary)', margin: 0 }}>
              {complianceFlags.length === 0
                ? 'No compliance violations detected. The counsellor adhered to ethical selling standards and made no prohibited guarantees.'
                : `${complianceFlags.length} potential compliance observation(s) audited by the policy verification engine.`}
            </p>
          </div>

          {/* Compliance Items Sections */}
          {(() => {
            const verifiedList = complianceFlags.filter((f) => f.status === 'verified');
            const unverifiedList = complianceFlags.filter((f) => f.status !== 'verified');

            return (
              <div style={{ display: 'flex', flexDirection: 'column', gap: '16px' }}>
                {/* Section 1: Confirmed Violations */}
                <div style={{ display: 'flex', flexDirection: 'column', gap: '8px' }}>
                  <div style={{ display: 'flex', alignItems: 'center', gap: '6px' }}>
                    <ShieldAlert size={14} style={{ color: verifiedList.length > 0 ? 'var(--danger)' : 'var(--text-muted)' }} />
                    <span style={{ fontSize: '12px', fontWeight: 700, color: 'var(--text-primary)' }}>
                      Confirmed Policy Violations ({verifiedList.length})
                    </span>
                  </div>

                  {verifiedList.length === 0 ? (
                    <div
                      style={{
                        background: 'var(--bg-surface-elevated)',
                        border: '1px solid var(--border-subtle)',
                        borderRadius: 'var(--radius-md)',
                        padding: '12px 16px',
                        color: 'var(--text-secondary)',
                        fontSize: '12px',
                        display: 'flex',
                        alignItems: 'center',
                        gap: '8px',
                      }}
                    >
                      <CheckCircle2 size={16} style={{ color: 'var(--success)', flexShrink: 0 }} />
                      <span>Zero confirmed compliance violations detected.</span>
                    </div>
                  ) : (
                    verifiedList.map((flag, idx) => (
                      <div
                        key={idx}
                        style={{
                          background: 'var(--bg-surface-elevated)',
                          border: '1px solid var(--border-subtle)',
                          borderLeft: `3px solid ${flag.severity === 'critical' ? 'var(--danger)' : 'var(--warning)'}`,
                          borderRadius: 'var(--radius-md)',
                          padding: '12px',
                          display: 'flex',
                          flexDirection: 'column',
                          gap: '6px',
                        }}
                      >
                        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
                          <div style={{ display: 'flex', alignItems: 'center', gap: '6px' }}>
                            <span style={{ fontSize: '12px', fontWeight: 700, color: 'var(--text-primary)' }}>
                              {flag.rule_id}
                            </span>
                            <span className={`status-badge ${flag.severity === 'critical' ? 'danger' : 'warning'}`}>
                              {(flag.severity || 'major').toUpperCase()}
                            </span>
                            <span className="status-badge pass">VERIFIED VIOLATION</span>
                          </div>
                          <span style={{ fontSize: '10.5px', color: 'var(--text-muted)' }}>
                            {Math.round((flag.confidence || 0) * 100)}% Confidence
                          </span>
                        </div>
                        <div style={{ fontSize: '12px', color: 'var(--text-secondary)', lineHeight: 1.4 }}>
                          {flag.explanation || flag.description}
                        </div>

                        {/* Evidence Chips */}
                        {flag.evidence && flag.evidence.length > 0 && (
                          <div style={{ display: 'flex', flexDirection: 'column', gap: '4px', marginTop: '4px' }}>
                            {flag.evidence.map((ev, i) => {
                              const seg = segmentMap[ev.segment_id];
                              const startMs = ev.start_ms !== undefined && ev.start_ms !== null ? ev.start_ms : (seg ? seg.start_ms : 0);
                              return (
                                <div
                                  key={i}
                                  className="evidence-jump-chip"
                                  onClick={() =>
                                    onJumpToEvidence &&
                                    onJumpToEvidence({
                                      timestamp_ms: startMs,
                                      segment_id: ev.segment_id,
                                      quote: ev.quote,
                                    })
                                  }
                                  style={{ cursor: 'pointer', display: 'flex', alignItems: 'center', gap: '6px' }}
                                >
                                  <span style={{ fontFamily: 'var(--font-mono)', fontWeight: 600, color: 'var(--primary)' }}>
                                    {formatMs(startMs)}
                                  </span>
                                  <span style={{ fontStyle: 'italic', fontSize: '11.5px', flex: 1 }}>
                                    "{ev.quote}"
                                  </span>
                                  <span style={{ fontSize: '10px', color: 'var(--primary)', display: 'flex', alignItems: 'center', gap: '2px' }}>
                                    Jump <Play size={10} />
                                  </span>
                                </div>
                              );
                            })}
                          </div>
                        )}
                      </div>
                    ))
                  )}
                </div>

                {/* Section 2: Needs Manual Check */}
                <div style={{ display: 'flex', flexDirection: 'column', gap: '8px' }}>
                  <div style={{ display: 'flex', alignItems: 'center', gap: '6px' }}>
                    <Info size={14} style={{ color: 'var(--warning)' }} />
                    <span style={{ fontSize: '12px', fontWeight: 700, color: 'var(--text-primary)' }}>
                      Needs Manual Check ({unverifiedList.length})
                    </span>
                    <span style={{ fontSize: '11px', color: 'var(--text-muted)' }}>
                      — Unverified observations pending human review
                    </span>
                  </div>

                  {unverifiedList.length === 0 ? (
                    <div
                      style={{
                        background: 'var(--bg-surface-elevated)',
                        border: '1px solid var(--border-subtle)',
                        borderRadius: 'var(--radius-md)',
                        padding: '12px 16px',
                        color: 'var(--text-secondary)',
                        fontSize: '12px',
                      }}
                    >
                      No unverified observations. All model flags have verified verbatim evidence.
                    </div>
                  ) : (
                    unverifiedList.map((flag, idx) => (
                      <div
                        key={idx}
                        style={{
                          background: 'var(--bg-surface-card)',
                          border: '1px dashed var(--border-medium)',
                          borderLeft: '3px solid var(--text-muted)',
                          borderRadius: 'var(--radius-md)',
                          padding: '12px',
                          display: 'flex',
                          flexDirection: 'column',
                          gap: '6px',
                        }}
                      >
                        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
                          <div style={{ display: 'flex', alignItems: 'center', gap: '6px' }}>
                            <span style={{ fontSize: '12px', fontWeight: 700, color: 'var(--text-primary)' }}>
                              {flag.rule_id}
                            </span>
                            <span className="status-badge neutral">
                              {(flag.severity || 'observation').toUpperCase()}
                            </span>
                            <span className="status-badge warning">NEEDS MANUAL CHECK</span>
                          </div>
                          <span style={{ fontSize: '10.5px', color: 'var(--text-muted)' }}>
                            {Math.round((flag.confidence || 0) * 100)}% Confidence
                          </span>
                        </div>
                        <div style={{ fontSize: '12px', color: 'var(--text-secondary)', lineHeight: 1.4 }}>
                          {flag.explanation || flag.description}
                        </div>
                        <div style={{ fontSize: '11px', color: 'var(--text-muted)', fontStyle: 'italic' }}>
                          Evidence quote could not be confirmed verbatim against transcript. Flag held as unverified observation.
                        </div>

                        {/* Evidence Chips */}
                        {flag.evidence && flag.evidence.length > 0 && (
                          <div style={{ display: 'flex', flexDirection: 'column', gap: '4px', marginTop: '4px' }}>
                            {flag.evidence.map((ev, i) => {
                              const seg = segmentMap[ev.segment_id];
                              const startMs = ev.start_ms !== undefined && ev.start_ms !== null ? ev.start_ms : (seg ? seg.start_ms : 0);
                              return (
                                <div
                                  key={i}
                                  className="evidence-jump-chip"
                                  onClick={() =>
                                    onJumpToEvidence &&
                                    onJumpToEvidence({
                                      timestamp_ms: startMs,
                                      segment_id: ev.segment_id,
                                      quote: ev.quote,
                                    })
                                  }
                                  style={{ cursor: 'pointer', display: 'flex', alignItems: 'center', gap: '6px' }}
                                >
                                  <span style={{ fontFamily: 'var(--font-mono)', fontWeight: 600, color: 'var(--primary)' }}>
                                    {formatMs(startMs)}
                                  </span>
                                  <span style={{ fontStyle: 'italic', fontSize: '11.5px', flex: 1 }}>
                                    "{ev.quote}"
                                  </span>
                                  <span style={{ fontSize: '10px', color: 'var(--primary)', display: 'flex', alignItems: 'center', gap: '2px' }}>
                                    Jump <Play size={10} />
                                  </span>
                                </div>
                              );
                            })}
                          </div>
                        )}
                      </div>
                    ))
                  )}
                </div>
              </div>
            );
          })()}
        </div>
      )}

      {/* ── TAB 3: FLAGGED MOMENTS ── */}
      {activeTab === 'flags' && (
        <div style={{ display: 'flex', flexDirection: 'column', gap: '12px' }}>
          {flaggedMoments.length === 0 ? (
            <div
              style={{
                background: 'var(--bg-surface-elevated)',
                border: '1px solid var(--border-subtle)',
                borderRadius: 'var(--radius-md)',
                padding: '24px',
                textAlign: 'center',
                color: 'var(--text-muted)',
                fontSize: '13px',
              }}
            >
              <CheckCircle2 size={28} style={{ color: 'var(--success)', marginBottom: '8px' }} />
              <div>No moments flagged for review in this call.</div>
            </div>
          ) : (
            flaggedMoments.map((flag) => (
              <div
                key={flag.id}
                style={{
                  background: 'var(--bg-surface-elevated)',
                  border: '1px solid var(--border-subtle)',
                  borderLeft:
                    flag.severity === 'HIGH' || flag.severity === 'CRITICAL'
                      ? '3px solid var(--danger)'
                      : '3px solid var(--warning)',
                  borderRadius: 'var(--radius-md)',
                  padding: '14px',
                  display: 'flex',
                  flexDirection: 'column',
                  gap: '8px',
                }}
              >
                <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
                  <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
                    <span style={{ fontFamily: 'var(--font-mono)', fontSize: '12px', fontWeight: 600, color: 'var(--text-primary)' }}>
                      {flag.formatted_time}
                    </span>
                    <span className={`status-badge ${flag.severity === 'HIGH' || flag.severity === 'CRITICAL' ? 'danger' : 'warning'}`}>
                      {flag.severity}
                    </span>
                    <span style={{ fontSize: '12px', fontWeight: 600, color: 'var(--text-primary)' }}>
                      {flag.rule_id}
                    </span>
                  </div>

                  <button
                    className="btn btn-secondary btn-sm"
                    style={{ fontSize: '11px', padding: '3px 8px' }}
                    onClick={() =>
                      onJumpToEvidence &&
                      onJumpToEvidence({
                        timestamp_ms: flag.timestamp_ms,
                        quote: flag.quote,
                        segment_id: flag.segment_id,
                      })
                    }
                    title="Jump audio and transcript to flagged moment"
                  >
                    Jump to Evidence →
                  </button>
                </div>

                <p style={{ fontSize: '12px', color: 'var(--text-secondary)', margin: 0, lineHeight: 1.4 }}>
                  {flag.description}
                </p>

                {flag.quote && (
                  <div
                    style={{
                      background: 'rgba(0, 0, 0, 0.25)',
                      padding: '6px 10px',
                      borderRadius: '4px',
                      fontSize: '12px',
                      color: 'var(--text-primary)',
                      fontStyle: 'italic',
                    }}
                  >
                    "{flag.quote}"
                  </div>
                )}

                <div style={{ fontSize: '11px', color: 'var(--text-muted)' }}>
                  <strong>Action: </strong> {flag.action_required || 'Review with counsellor'}
                </div>
              </div>
            ))
          )}
        </div>
      )}

      {/* ── TAB 4: COACHING & ACTION PLAN ── */}
      {activeTab === 'coaching' && (
        <div style={{ display: 'flex', flexDirection: 'column', gap: '14px' }}>
          {evaluation.coaching ? (
            <>
              {/* Strengths */}
              <div
                style={{
                  background: 'var(--bg-surface-elevated)',
                  border: '1px solid var(--border-subtle)',
                  borderRadius: 'var(--radius-md)',
                  padding: '14px',
                }}
              >
                <div style={{ display: 'flex', alignItems: 'center', gap: '6px', color: 'var(--success-light)', fontWeight: 600, fontSize: '13px', marginBottom: '8px' }}>
                  <CheckCircle2 size={16} /> Demonstrated Strengths
                </div>
                <ul style={{ paddingLeft: '18px', fontSize: '12px', color: 'var(--text-secondary)', display: 'flex', flexDirection: 'column', gap: '6px', margin: 0 }}>
                  {(evaluation.coaching.strengths || []).map((s, idx) => (
                    <li key={idx} style={{ lineHeight: 1.4 }}>{s}</li>
                  ))}
                </ul>
              </div>

              {/* Coaching Opportunities */}
              <div
                style={{
                  background: 'var(--bg-surface-elevated)',
                  border: '1px solid var(--border-subtle)',
                  borderRadius: 'var(--radius-md)',
                  padding: '14px',
                }}
              >
                <div style={{ display: 'flex', alignItems: 'center', gap: '6px', color: 'var(--warning)', fontWeight: 600, fontSize: '13px', marginBottom: '8px' }}>
                  <AlertTriangle size={16} /> Coaching Opportunities
                </div>
                <ul style={{ paddingLeft: '18px', fontSize: '12px', color: 'var(--text-secondary)', display: 'flex', flexDirection: 'column', gap: '6px', margin: 0 }}>
                  {(evaluation.coaching.improvements || []).map((imp, idx) => (
                    <li key={idx} style={{ lineHeight: 1.4 }}>{imp}</li>
                  ))}
                </ul>
              </div>

              {/* Next Call Focus Directive */}
              <div
                style={{
                  background: 'var(--primary-subtle)',
                  border: '1px solid var(--primary-border)',
                  borderRadius: 'var(--radius-md)',
                  padding: '14px',
                  fontSize: '12.5px',
                  color: 'var(--text-secondary)',
                }}
              >
                <strong style={{ color: 'var(--text-primary)', display: 'block', marginBottom: '6px', fontSize: '13px' }}>
                  Next Call Coaching Directive:
                </strong>
                {evaluation.coaching.next_call_focus}
              </div>
            </>
          ) : (
            <div
              style={{
                background: 'var(--bg-surface-elevated)',
                border: '1px solid var(--border-subtle)',
                borderRadius: 'var(--radius-md)',
                padding: '24px',
                textAlign: 'center',
                color: 'var(--text-muted)',
                fontSize: '12.5px',
              }}
            >
              <Info size={24} style={{ color: 'var(--primary)', marginBottom: '8px' }} />
              <div>No coaching plan generated for this call yet.</div>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
