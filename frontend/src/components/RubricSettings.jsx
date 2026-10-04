import React, { useState, useEffect } from 'react';
import {
  Sliders,
  RotateCcw,
  CheckCircle2,
  AlertTriangle,
  Shield,
  Layers,
} from 'lucide-react';
import { apiJson } from '../api/client';

const DEFAULT_WEIGHTS = {
  discovery: 25.0,
  course_pitch: 25.0,
  objection_handling: 20.0,
  compliance: 15.0,
  closing: 15.0,
};

export default function RubricSettings({ onSaveWeights }) {
  const [weights, setWeights] = useState(DEFAULT_WEIGHTS);
  const [minConfidence, setMinConfidence] = useState(85);
  const [requireEvidence, setRequireEvidence] = useState(true);
  const [autoEscalate, setAutoEscalate] = useState(true);
  const [saveStatus, setSaveStatus] = useState(null);
  const [rubricLoaded, setRubricLoaded] = useState(false);

  // Fetch current rubric weights from backend on mount
  useEffect(() => {
    apiJson('/rubric')
      .then((data) => {
        if (data.weights) setWeights(data.weights);
        if (data.min_confidence_threshold) setMinConfidence(data.min_confidence_threshold);
        setRubricLoaded(true);
      })
      .catch(() => setRubricLoaded(true)); // fall back to defaults
  }, []);

  const totalWeights = Object.values(weights).reduce((a, b) => a + b, 0);
  const isBalanced = Math.abs(totalWeights - 100) < 0.05;

  const handleWeightChange = (key, val) => {
    setWeights((prev) => ({ ...prev, [key]: Number(val) }));
  };

  const handleSave = async () => {
    if (!isBalanced) {
      alert('Total weights must sum to exactly 100%');
      return;
    }
    setSaveStatus('saving');
    try {
      if (onSaveWeights) {
        await onSaveWeights(weights);
      }
      setSaveStatus('saved');
      setTimeout(() => setSaveStatus(null), 3000);
    } catch {
      setSaveStatus('error');
    }
  };

  return (
    <div style={{ maxWidth: '860px', margin: '0 auto', display: 'flex', flexDirection: 'column', gap: '22px' }}>
      {/* ── Main Rubric Config Card ── */}
      <div className="console-panel">
        <div className="console-panel-header">
          <div>
            <span className="console-panel-title">Scoring Rubric Definition</span>
            <p style={{ fontSize: '12px', color: 'var(--text-muted)', margin: 0 }}>
              Deterministic scoring formula applied to LLM criterion 0–4 scores. Zero LLM calls incurred on weight adjustments.
            </p>
          </div>

          <div
            style={{
              padding: '4px 10px',
              borderRadius: '4px',
              background: isBalanced ? 'var(--success-subtle)' : 'var(--danger-subtle)',
              color: isBalanced ? 'var(--success-light)' : 'var(--danger-light)',
              border: isBalanced ? '1px solid var(--success-border)' : '1px solid var(--danger-border)',
              fontFamily: 'var(--font-mono)',
              fontSize: '12px',
              fontWeight: 600,
            }}
          >
            Total: {totalWeights.toFixed(1)}% / 100%
          </div>
        </div>

        {/* Criteria Sliders */}
        <div style={{ display: 'flex', flexDirection: 'column', gap: '14px', marginTop: '16px' }}>
          {[
            { key: 'discovery', label: 'Student Needs Discovery', desc: '10th Board %, academic goals, previous coaching history' },
            { key: 'course_pitch', label: 'PW Curriculum & Feature Pitch', desc: 'Arjuna/Yakeen batches, 24/7 Doubt Engine, DPP video solutions' },
            { key: 'objection_handling', label: 'Parent & Student Objection Handling', desc: 'Offline vs online skepticism, price validation, study habit guidance' },
            { key: 'compliance', label: 'Compliance & Ethical Standards', desc: 'Prohibited guarantees, competitor remarks, transparent refund disclosure' },
            { key: 'closing', label: 'Clear Next Steps & Closing', desc: 'WhatsApp batch registration link, orientation lecture, scheduled callback' },
          ].map((item) => (
            <div
              key={item.key}
              style={{
                background: 'var(--bg-surface-elevated)',
                border: '1px solid var(--border-subtle)',
                borderRadius: 'var(--radius-md)',
                padding: '14px 18px',
              }}
            >
              <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: '8px' }}>
                <div>
                  <div style={{ fontSize: '13px', fontWeight: 600, color: 'var(--text-primary)' }}>
                    {item.label}
                  </div>
                  <div style={{ fontSize: '11.5px', color: 'var(--text-muted)' }}>
                    {item.desc}
                  </div>
                </div>
                <div style={{ fontFamily: 'var(--font-mono)', fontSize: '14px', fontWeight: 600, color: 'var(--primary)' }}>
                  {weights[item.key]}%
                </div>
              </div>

              <input
                type="range"
                min="5"
                max="50"
                step="1"
                value={weights[item.key]}
                onChange={(e) => handleWeightChange(item.key, e.target.value)}
                style={{ width: '100%', accentColor: 'var(--primary)', cursor: 'pointer' }}
              />
            </div>
          ))}
        </div>
      </div>

      {/* ── AI Evaluation Rules ── */}
      <div className="console-panel">
        <div className="console-panel-header">
          <span className="console-panel-title">AI Evaluation & Gate Rules</span>
        </div>

        <div style={{ display: 'flex', flexDirection: 'column', gap: '16px' }}>
          {/* Rule 1 */}
          <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
            <div>
              <div style={{ fontSize: '13px', fontWeight: 500, color: 'var(--text-primary)' }}>
                Evidence Grounding Required
              </div>
              <div style={{ fontSize: '11.5px', color: 'var(--text-muted)' }}>
                Drop LLM scores to 0 if exact transcript quote substring is not verified by Python regex
              </div>
            </div>
            <input
              type="checkbox"
              checked={requireEvidence}
              onChange={(e) => setRequireEvidence(e.target.checked)}
              style={{ width: '18px', height: '18px', accentColor: 'var(--primary)', cursor: 'pointer' }}
            />
          </div>

          {/* Rule 2 */}
          <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
            <div>
              <div style={{ fontSize: '13px', fontWeight: 500, color: 'var(--text-primary)' }}>
                Auto-Escalate Critical Violations
              </div>
              <div style={{ fontSize: '11.5px', color: 'var(--text-muted)' }}>
                Force call status to 'NEEDS REVIEW' if any verified critical compliance flag is triggered
              </div>
            </div>
            <input
              type="checkbox"
              checked={autoEscalate}
              onChange={(e) => setAutoEscalate(e.target.checked)}
              style={{ width: '18px', height: '18px', accentColor: 'var(--primary)', cursor: 'pointer' }}
            />
          </div>

          {/* Rule 3 */}
          <div>
            <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: '6px' }}>
              <span style={{ fontSize: '13px', fontWeight: 500, color: 'var(--text-primary)' }}>
                Minimum Confidence Threshold
              </span>
              <span style={{ fontFamily: 'var(--font-mono)', fontSize: '12px', color: 'var(--primary)', fontWeight: 600 }}>
                {minConfidence}%
              </span>
            </div>
            <input
              type="range"
              min="70"
              max="95"
              step="1"
              value={minConfidence}
              onChange={(e) => setMinConfidence(Number(e.target.value))}
              style={{ width: '100%', accentColor: 'var(--primary)', cursor: 'pointer' }}
            />
          </div>
        </div>
      </div>

      {/* ── Actions Row ── */}
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
        <button
          className="btn btn-secondary"
          onClick={() => setWeights(DEFAULT_WEIGHTS)}
        >
          <RotateCcw size={14} /> Reset Defaults
        </button>

        <div style={{ display: 'flex', alignItems: 'center', gap: '10px' }}>
          {saveStatus === 'saved' && (
            <span style={{ fontSize: '12px', color: 'var(--success-light)', fontWeight: 500 }}>
              ✓ Saved to backend!
            </span>
          )}
          <button
            className="btn btn-primary"
            onClick={handleSave}
            disabled={!isBalanced}
          >
            Save & Deploy Rubric
          </button>
        </div>
      </div>
    </div>
  );
}
