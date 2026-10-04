import React, { useState, useEffect } from 'react';
import {
  FileAudio,
  AlertTriangle,
  ShieldCheck,
  RotateCcw,
  Languages,
  Sparkles,
  RefreshCw,
  X,
} from 'lucide-react';

import Sidebar from './components/Sidebar';
import Topbar from './components/Topbar';
import AudioPlayer from './components/AudioPlayer';
import TranscriptPanel from './components/TranscriptPanel';
import EvaluationPanel from './components/EvaluationPanel';
import OverviewDashboard from './components/OverviewDashboard';
import CounsellorAnalytics from './components/CounsellorAnalytics';
import RubricSettings from './components/RubricSettings';
import UploadModal from './components/UploadModal';
import SystemModal from './components/SystemModal';
import { apiFetch, apiJson } from './api/client';

export default function App() {
  // ── Layout & Navigation ───────────────────────────────────────────────────
  const [activeNav, setActiveNav] = useState('inspector');
  const [sidebarCollapsed, setSidebarCollapsed] = useState(false);
  const [modalType, setModalType] = useState(null);

  // ── Theme ─────────────────────────────────────────────────────────────────
  const [theme, setTheme] = useState(() => localStorage.getItem('pw_qa_theme') || 'light');

  useEffect(() => {
    document.documentElement.setAttribute('data-theme', theme);
    localStorage.setItem('pw_qa_theme', theme);
  }, [theme]);

  const toggleTheme = () => setTheme((p) => (p === 'light' ? 'dark' : 'light'));

  // ── Backend state ─────────────────────────────────────────────────────────
  const [backendOnline, setBackendOnline] = useState(false);

  useEffect(() => {
    apiJson('/health')
      .then((d) => { if (d.status === 'ok') setBackendOnline(true); })
      .catch(() => setBackendOnline(false));
  }, []);

  // ── Call inspection state (API-driven) ───────────────────────────────────
  const [currentCall, setCurrentCall] = useState(null);
  const [segments, setSegments] = useState([]);
  const [transcriptMeta, setTranscriptMeta] = useState({
    version_number: 1,
    transcript_version_id: null,
    mode: 'auto',
    detected_language: null,
    available_versions: [1],
  });
  const [evaluation, setEvaluation] = useState(null);
  const [flaggedMoments, setFlaggedMoments] = useState([]);
  const [callLoading, setCallLoading] = useState(false);

  // Re-transcribe & Analyze state (Section C.4)
  const [showRetranscribeModal, setShowRetranscribeModal] = useState(false);
  const [selectedRetranscribeMode, setSelectedRetranscribeMode] = useState('hi');
  const [isRetranscribing, setIsRetranscribing] = useState(false);
  const [isAnalyzing, setIsAnalyzing] = useState(false);
  const [isRetrying, setIsRetrying] = useState(false);

  // Load the most recent completed call on first mount
  useEffect(() => {
    fetchLatestCall();
  }, []);

  const fetchLatestCall = async () => {
    try {
      const calls = await apiJson('/calls');
      if (calls.length > 0) {
        // Pick the most recently evaluated call (completed or needs_review), falling back to latest
        const evaluated = calls.filter((c) => c.status === 'completed' || c.status === 'needs_review');
        const target = evaluated[evaluated.length - 1] || calls[calls.length - 1] || calls[0];
        await loadCall(target.id);
      }
    } catch {
      // no calls yet — blank state is fine
    }
  };

  const loadCall = async (callId, targetVersion = null) => {
    setCallLoading(true);
    try {
      // Fetch call metadata, transcript, and evaluation in parallel
      const txUrl = `/calls/${callId}/transcript${targetVersion ? `?version=${targetVersion}` : ''}`;
      const [callData, txData, evalData] = await Promise.all([
        apiJson(`/calls/${callId}`),
        apiJson(txUrl).catch(() => null),
        apiJson(`/calls/${callId}/evaluation`).catch(() => null),
      ]);

      setCurrentCall(callData);

      let currentSegments = [];
      if (txData) {
        currentSegments = txData.segments || [];
        setSegments(currentSegments);
        setTranscriptMeta({
          version_number: txData.version_number || 1,
          transcript_version_id: txData.transcript_version_id || null,
          mode: txData.mode || callData.transcription_mode || 'auto',
          detected_language: txData.detected_language || null,
          available_versions: txData.available_versions || [1],
        });
      }

      if (evalData) {
        setEvaluation(evalData);
        // Extract flagged moments from evaluation compliance_flags
        const rawFlags = evalData.compliance_flags || evalData.flags || [];
        const flags = rawFlags.map((f, idx) => {
          const firstEv = (f.evidence && f.evidence[0]) || {};
          const segId = firstEv.segment_id || f.segment_id;
          const seg = currentSegments.find((s) => s.segment_id === segId);
          const startMs = seg ? seg.start_ms : (f.start_ms || 0);
          return {
            id: f.id || idx + 1,
            timestamp_ms: startMs,
            formatted_time: msToMMSS(startMs),
            severity: (f.severity || 'medium').toUpperCase(),
            rule_id: f.rule_id,
            title: f.title || f.rule_id,
            description: f.explanation || f.description || '',
            quote: firstEv.quote || f.quote || '',
            segment_id: segId,
            status: f.status || 'unverified',
            action_required: f.severity === 'critical' ? 'Supervisor review required' : 'Discuss in 1-on-1 coaching',
          };
        });
        setFlaggedMoments(flags);
      } else {
        setEvaluation(null);
        setFlaggedMoments([]);
      }
    } catch {
      // silently ignore network errors
    } finally {
      setCallLoading(false);
    }
  };

  const handleRetranscribe = async () => {
    if (!currentCall) return;
    setIsRetranscribing(true);
    try {
      await apiJson(`/calls/${currentCall.id}/retranscribe`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ mode: selectedRetranscribeMode }),
      });
      setShowRetranscribeModal(false);
      await loadCall(currentCall.id);
    } catch (err) {
      alert(`Re-transcription failed: ${err.message}`);
    } finally {
      setIsRetranscribing(false);
    }
  };

  const handleAnalyzeCall = async () => {
    if (!currentCall) return;
    setIsAnalyzing(true);
    try {
      await apiJson(`/calls/${currentCall.id}/analyze?bypass_cache=true`, {
        method: 'POST',
      });
      await loadCall(currentCall.id);
    } catch (err) {
      alert(`Analysis failed: ${err.message}`);
    } finally {
      setIsAnalyzing(false);
    }
  };

  const handleRetryCall = async () => {
    if (!currentCall) return;
    setIsRetrying(true);
    try {
      await apiJson(`/calls/${currentCall.id}/retry`, {
        method: 'POST',
      });
      await loadCall(currentCall.id);
    } catch (err) {
      alert(`Retry failed: ${err.message}`);
    } finally {
      setIsRetrying(false);
    }
  };

  // ── Audio playback state (controlled) ────────────────────────────────────
  const [currentTimeMs, setCurrentTimeMs] = useState(0);
  const [seekToMs, setSeekToMs] = useState(null);
  const [activeSegmentId, setActiveSegmentId] = useState(null);
  const [highlightedSegmentId, setHighlightedSegmentId] = useState(null);

  // Derive active segment from playback position
  useEffect(() => {
    if (!segments.length) return;
    const current = segments.find(
      (s) => currentTimeMs >= s.start_ms && currentTimeMs <= s.end_ms
    );
    if (current && current.segment_id !== activeSegmentId) {
      setActiveSegmentId(current.segment_id);
    }
  }, [currentTimeMs, segments, activeSegmentId]);

  // ── Cross-panel navigation (evidence / flag clicks) ───────────────────────
  const handleSelectSegment = (seg) => {
    setSeekToMs(seg.start_ms);
    setActiveSegmentId(seg.segment_id);
    setHighlightedSegmentId(seg.segment_id);
    scrollToSegment(seg.segment_id);
  };

  const handleJumpToFlag = (flag) => {
    setSeekToMs(flag.timestamp_ms);
    if (flag.segment_id) {
      setActiveSegmentId(flag.segment_id);
      setHighlightedSegmentId(flag.segment_id);
      scrollToSegment(flag.segment_id);
    }
  };

  const handleJumpToEvidence = (ev) => {
    const seg = segments.find((s) => s.segment_id === ev.segment_id);
    if (seg) {
      setSeekToMs(seg.start_ms);
      setActiveSegmentId(seg.segment_id);
      setHighlightedSegmentId(seg.segment_id);
      scrollToSegment(seg.segment_id);
    }
  };

  const scrollToSegment = (segId) => {
    const el = document.getElementById(`seg-row-${segId}`);
    if (el) el.scrollIntoView({ behavior: 'smooth', block: 'center' });
  };

  // ── Role override (live PUT to backend) ───────────────────────────────────
  const handleRoleOverride = async (segmentId, newRole) => {
    if (!currentCall) return;
    try {
      await apiJson(`/calls/${currentCall.id}/segments/${segmentId}/role`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ role: newRole }),
      });
      setSegments((prev) =>
        prev.map((s) => (s.segment_id === segmentId ? { ...s, role: newRole } : s))
      );
    } catch {
      // silent — UI already updated optimistically
    }
  };

  // ── Helpers ───────────────────────────────────────────────────────────────
  const msToMMSS = (ms) => {
    const s = Math.floor((ms || 0) / 1000);
    const m = Math.floor(s / 60);
    const r = s % 60;
    return `${m < 10 ? '0' : ''}${m}:${r < 10 ? '0' : ''}${r}`;
  };

  return (
    <div className="app-shell">
      {/* ── Left Sidebar ── */}
      <Sidebar
        activeNav={activeNav}
        setActiveNav={setActiveNav}
        collapsed={sidebarCollapsed}
        setCollapsed={setSidebarCollapsed}
        onOpenModal={(type) => setModalType(type)}
      />

      {/* ── Main Viewport ── */}
      <div className="app-main-layout">
        <Topbar
          activeNav={activeNav}
          backendOnline={backendOnline}
          theme={theme}
          onToggleTheme={toggleTheme}
          onSelectCall={(call) => {
            loadCall(call.id);
            setActiveNav('inspector');
          }}
          onOpenModal={(type) => setModalType(type)}
        />

        <div className="page-workspace">
          {/* ── VIEW 1: CALL INSPECTOR ── */}
          {activeNav === 'inspector' && (
            <div style={{ display: 'flex', flexDirection: 'column', gap: '20px' }}>
              {/* Call Header Row */}
              <div
                className="console-panel"
                style={{
                  display: 'flex',
                  alignItems: 'center',
                  justifyContent: 'space-between',
                  flexWrap: 'wrap',
                  gap: '16px',
                  padding: '16px 20px',
                }}
              >
                {currentCall ? (
                  <>
                    {/* Left: file info & version selector */}
                    <div style={{ display: 'flex', alignItems: 'center', gap: '14px' }}>
                      <div style={{
                        width: '42px', height: '42px',
                        borderRadius: 'var(--radius-md)',
                        background: 'var(--primary-subtle)',
                        border: '1px solid var(--primary-border)',
                        display: 'flex', alignItems: 'center', justifyContent: 'center',
                        color: 'var(--primary)',
                      }}>
                        <FileAudio size={22} />
                      </div>
                      <div>
                        <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
                          <span style={{ fontSize: '15px', fontWeight: 600, color: 'var(--text-primary)' }}>
                            {currentCall.original_filename}
                          </span>
                          <span className={`status-badge ${
                            currentCall.status === 'completed' ? 'pass'
                            : currentCall.status === 'needs_review' ? 'fail'
                            : currentCall.status === 'failed' ? 'fail'
                            : currentCall.status === 'analyzing' ? 'ai'
                            : 'neutral'
                          }`}>
                            {currentCall.status === 'uploaded' && 'Uploaded'}
                            {currentCall.status === 'transcribing' && 'Transcribing…'}
                            {currentCall.status === 'transcribed' && 'Transcribed'}
                            {currentCall.status === 'analyzing' && 'Analyzing…'}
                            {currentCall.status === 'completed' && 'Completed'}
                            {currentCall.status === 'needs_review' && 'Needs Review'}
                            {currentCall.status === 'failed' && 'Failed'}
                            {!['uploaded', 'transcribing', 'transcribed', 'analyzing', 'completed', 'needs_review', 'failed'].includes(currentCall.status) && currentCall.status}
                          </span>
                        </div>
                        <div style={{ display: 'flex', alignItems: 'center', gap: '16px', fontSize: '12px', color: 'var(--text-muted)', marginTop: '4px', flexWrap: 'wrap' }}>
                          {currentCall.counsellor_name && (
                            <span>Counsellor: <strong style={{ color: 'var(--text-primary)' }}>{currentCall.counsellor_name}</strong></span>
                          )}
                          {currentCall.duration_seconds && (
                            <span>Duration: <strong style={{ color: 'var(--text-primary)' }}>{msToMMSS(currentCall.duration_seconds * 1000)}</strong></span>
                          )}
                          <span>Mode: <strong style={{ color: 'var(--text-primary)' }}>{transcriptMeta.mode.toUpperCase()}</strong></span>
                          {transcriptMeta.detected_language && (
                            <span>Detected: <strong style={{ color: 'var(--text-primary)' }}>{transcriptMeta.detected_language}</strong></span>
                          )}
                        </div>
                      </div>
                    </div>

                    {/* Right: Version Switcher & Re-transcribe Action */}
                    <div style={{ display: 'flex', alignItems: 'center', gap: '12px', flexWrap: 'wrap' }}>
                      {/* Transcript Version Switcher */}
                      {transcriptMeta.available_versions.length > 1 && (
                        <div style={{ display: 'flex', alignItems: 'center', gap: '6px' }}>
                          <span style={{ fontSize: '12px', color: 'var(--text-muted)' }}>Version:</span>
                          <select
                            value={transcriptMeta.version_number}
                            onChange={(e) => loadCall(currentCall.id, Number(e.target.value))}
                            style={{
                              background: 'var(--bg-surface-elevated)',
                              border: '1px solid var(--border-medium)',
                              borderRadius: 'var(--radius-sm)',
                              color: 'var(--text-primary)',
                              fontSize: '12px',
                              padding: '3px 8px',
                            }}
                          >
                            {transcriptMeta.available_versions.map((v) => (
                              <option key={v} value={v}>v{v}</option>
                            ))}
                          </select>
                        </div>
                      )}

                      <button
                        className="btn btn-secondary btn-sm"
                        onClick={() => setShowRetranscribeModal(true)}
                        title="Re-transcribe with another language configuration"
                      >
                        <Languages size={13} />
                        Re-transcribe as…
                      </button>

                      {/* QA Score Display */}
                      {evaluation?.overall_score !== null && evaluation?.overall_score !== undefined && (
                        <div style={{ textAlign: 'right', marginLeft: '8px' }}>
                          <div style={{ fontSize: '10px', textTransform: 'uppercase', color: 'var(--text-muted)', fontWeight: 600 }}>QA Score</div>
                          <div style={{ fontSize: '20px', fontWeight: 700, color: 'var(--text-primary)', lineHeight: 1.1 }}>
                            {Number(evaluation.overall_score).toFixed(1)} <span style={{ fontSize: '12px', color: 'var(--text-muted)' }}>/ 100</span>
                          </div>
                        </div>
                      )}
                    </div>
                  </>
                ) : (
                  <div style={{ display: 'flex', alignItems: 'center', gap: '12px', color: 'var(--text-muted)', fontSize: '14px' }}>
                    <FileAudio size={20} style={{ color: 'var(--primary)' }} />
                    {callLoading ? 'Loading call…' : 'No call loaded — upload a recording to begin.'}
                  </div>
                )}
              </div>

              {/* ── Stale Evaluation Banner (Section C.4) ── */}
              {evaluation?.is_stale && (
                <div
                  style={{
                    background: 'rgba(245, 158, 11, 0.1)',
                    border: '1px solid var(--warning)',
                    borderRadius: 'var(--radius-md)',
                    padding: '12px 18px',
                    display: 'flex',
                    alignItems: 'center',
                    justifyContent: 'space-between',
                    gap: '12px',
                  }}
                >
                  <div style={{ display: 'flex', alignItems: 'center', gap: '10px' }}>
                    <AlertTriangle size={18} style={{ color: 'var(--warning)', flexShrink: 0 }} />
                    <div>
                      <div style={{ fontSize: '13px', fontWeight: 600, color: 'var(--text-primary)' }}>
                        Stale Evaluation — Out of Date
                      </div>
                      <div style={{ fontSize: '12px', color: 'var(--text-muted)' }}>
                        {evaluation.stale_reason || 'This evaluation is based on an older transcript version. Re-analyze to re-evaluate quotes and criteria scores against the latest transcript.'}
                      </div>
                    </div>
                  </div>
                  <button
                    className="btn btn-primary btn-sm"
                    onClick={handleAnalyzeCall}
                    disabled={isAnalyzing}
                  >
                    <Sparkles size={13} style={{ animation: isAnalyzing ? 'spin 1s linear infinite' : 'none' }} />
                    {isAnalyzing ? 'Analyzing…' : 'Re-analyze Call'}
                  </button>
                </div>
              )}

              {/* ── Failed Pipeline Banner (Section B.4) ── */}
              {currentCall?.status === 'failed' && (
                <div
                  style={{
                    background: 'rgba(239, 68, 68, 0.1)',
                    border: '1px solid var(--danger)',
                    borderRadius: 'var(--radius-md)',
                    padding: '12px 18px',
                    display: 'flex',
                    alignItems: 'center',
                    justifyContent: 'space-between',
                    gap: '12px',
                  }}
                >
                  <div style={{ display: 'flex', alignItems: 'center', gap: '10px' }}>
                    <AlertTriangle size={18} style={{ color: 'var(--danger-light)', flexShrink: 0 }} />
                    <div>
                      <div style={{ fontSize: '13px', fontWeight: 600, color: 'var(--danger-light)' }}>
                        Audio Pipeline Failed
                      </div>
                      <div style={{ fontSize: '12px', color: 'var(--text-muted)' }}>
                        {currentCall.failure_reason || 'An unexpected error occurred during audio processing.'}
                      </div>
                    </div>
                  </div>
                  <button
                    className="btn btn-secondary btn-sm"
                    onClick={handleRetryCall}
                    disabled={isRetrying}
                    style={{ color: 'var(--primary)' }}
                  >
                    <RotateCcw size={13} style={{ animation: isRetrying ? 'spin 1s linear infinite' : 'none' }} />
                    {isRetrying ? 'Retrying…' : 'Retry Pipeline'}
                  </button>
                </div>
              )}

              {/* Audio Player (real <audio>) */}
              <AudioPlayer
                callId={currentCall?.id || null}
                currentTimeMs={currentTimeMs}
                onTimeUpdate={setCurrentTimeMs}
                seekToMs={seekToMs}
                onSeekConsumed={() => setSeekToMs(null)}
                flaggedMoments={flaggedMoments}
                onJumpToFlag={handleJumpToFlag}
              />

              {/* Two-column workspace */}
              <div style={{ display: 'grid', gridTemplateColumns: '1.25fr 1fr', gap: '20px' }}>
                <TranscriptPanel
                  segments={segments}
                  activeSegmentId={activeSegmentId}
                  highlightedSegmentId={highlightedSegmentId}
                  onSelectSegment={handleSelectSegment}
                  onRoleOverride={handleRoleOverride}
                  transcriptMeta={transcriptMeta}
                />
                <EvaluationPanel
                  evaluation={evaluation}
                  segments={segments}
                  onJumpToEvidence={handleJumpToEvidence}
                  flaggedMoments={flaggedMoments}
                />
              </div>
            </div>
          )}

          {/* ── VIEW 2: OVERVIEW DASHBOARD ── */}
          {activeNav === 'dashboard' && (
            <OverviewDashboard
              onSelectCall={(call) => { loadCall(call.id); setActiveNav('inspector'); }}
              onNavigateToUpload={() => setActiveNav('upload')}
            />
          )}

          {/* ── VIEW 3: COUNSELLOR ANALYTICS ── */}
          {activeNav === 'counsellors' && (
            <CounsellorAnalytics
              onInspectCall={() => setActiveNav('inspector')}
            />
          )}

          {/* ── VIEW 4: RUBRIC SETTINGS ── */}
          {activeNav === 'rubric' && (
            <RubricSettings
              onSaveWeights={async (weights) => {
                await apiFetch('/rubric/weights', {
                  method: 'PUT',
                  headers: { 'Content-Type': 'application/json' },
                  body: JSON.stringify(weights),
                });
              }}
            />
          )}

          {/* ── VIEW 5: UPLOAD ── */}
          {activeNav === 'upload' && (
            <UploadModal
              onCancel={() => setActiveNav('inspector')}
              onCompleteUpload={(data) => {
                if (data?.id) {
                  loadCall(data.id);
                }
                setActiveNav('inspector');
              }}
            />
          )}
        </div>
      </div>

      {/* ── Re-transcribe Confirmation Modal (Section C.4) ── */}
      {showRetranscribeModal && (
        <div
          style={{
            position: 'fixed',
            inset: 0,
            background: 'rgba(0, 0, 0, 0.65)',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            zIndex: 1000,
            backdropFilter: 'blur(3px)',
          }}
        >
          <div
            className="console-panel"
            style={{ width: '480px', maxWidth: '90%', padding: '24px' }}
          >
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '16px' }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
                <Languages size={18} style={{ color: 'var(--primary)' }} />
                <span style={{ fontSize: '15px', fontWeight: 600, color: 'var(--text-primary)' }}>
                  Re-transcribe Audio
                </span>
              </div>
              <button
                className="btn btn-ghost btn-xs"
                onClick={() => setShowRetranscribeModal(false)}
                disabled={isRetranscribing}
              >
                <X size={16} />
              </button>
            </div>

            <div
              style={{
                background: 'rgba(245, 158, 11, 0.1)',
                border: '1px solid var(--warning)',
                borderRadius: 'var(--radius-sm)',
                padding: '12px 14px',
                fontSize: '12px',
                color: 'var(--text-primary)',
                marginBottom: '18px',
                lineHeight: 1.45,
              }}
            >
              <strong>Notice:</strong> Re-transcribing creates a new transcript version.
              Existing evidence quotes and QA scores will be marked <strong>out of date</strong> until re-analyzed.
            </div>

            <div style={{ marginBottom: '20px' }}>
              <label style={{ fontSize: '12.5px', fontWeight: 600, color: 'var(--text-primary)', display: 'block', marginBottom: '6px' }}>
                Target Transcription Language
              </label>
              <select
                value={selectedRetranscribeMode}
                onChange={(e) => setSelectedRetranscribeMode(e.target.value)}
                disabled={isRetranscribing}
                style={{
                  width: '100%',
                  background: 'var(--bg-surface-elevated)',
                  border: '1px solid var(--border-medium)',
                  borderRadius: 'var(--radius-md)',
                  padding: '8px 12px',
                  color: 'var(--text-primary)',
                  fontSize: '13px',
                }}
              >
                <option value="auto">Auto — Detect language automatically</option>
                <option value="hi">Hindi — Standard Devanagari script</option>
                <option value="hinglish">Hinglish — Conversational code-mixed</option>
                <option value="en">English — Standard English</option>
              </select>
            </div>

            <div style={{ display: 'flex', justifyContent: 'flex-end', gap: '10px' }}>
              <button
                className="btn btn-secondary btn-sm"
                onClick={() => setShowRetranscribeModal(false)}
                disabled={isRetranscribing}
              >
                Cancel
              </button>
              <button
                className="btn btn-primary btn-sm"
                onClick={handleRetranscribe}
                disabled={isRetranscribing}
              >
                <RefreshCw size={13} style={{ animation: isRetranscribing ? 'spin 1s linear infinite' : 'none' }} />
                {isRetranscribing ? 'Transcribing…' : 'Confirm & Re-transcribe'}
              </button>
            </div>
          </div>
        </div>
      )}

      {/* ── System / Profile Modal ── */}
      <SystemModal type={modalType} onClose={() => setModalType(null)} />
    </div>
  );
}
