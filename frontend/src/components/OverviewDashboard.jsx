import React, { useEffect, useState } from 'react';
import {
  TrendingUp,
  ShieldCheck,
  Clock,
  AlertTriangle,
  FileAudio,
  ChevronRight,
  Headphones,
  UploadCloud,
  RefreshCw,
  RotateCcw,
} from 'lucide-react';
import { apiFetch, apiJson } from '../api/client';

export default function OverviewDashboard({
  onSelectCall,
  onNavigateToUpload,
}) {
  const [calls, setCalls] = useState([]);
  const [loading, setLoading] = useState(true);
  const [minutesSavedPerCall, setMinutesSavedPerCall] = useState(null);
  const [isEditingAssumption, setIsEditingAssumption] = useState(false);
  const [assumptionInput, setAssumptionInput] = useState('');
  const [retryingCallId, setRetryingCallId] = useState(null);

  const fetchCalls = async () => {
    setLoading(true);
    try {
      const data = await apiJson('/calls');
      setCalls(data);
    } catch {
      // offline or backend restarting
    } finally {
      setLoading(false);
    }
  };

  const fetchSettings = async () => {
    try {
      const data = await apiJson('/settings');
      setMinutesSavedPerCall(data.minutes_saved_per_call || null);
    } catch {
      // offline
    }
  };

  const saveAssumption = async () => {
    try {
      const val = assumptionInput.trim() === '' ? null : assumptionInput.trim();
      await apiJson('/settings/minutes_saved_per_call', {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ value: val, description: 'Minutes saved per call' }),
      });
      setMinutesSavedPerCall(val);
      setIsEditingAssumption(false);
    } catch {
      // ignore
    }
  };

  const handleRetry = async (e, callId) => {
    e.stopPropagation();
    setRetryingCallId(callId);
    try {
      await apiFetch(`/calls/${callId}/retry`, { method: 'POST' });
      await fetchCalls();
    } catch (err) {
      alert(`Retry failed: ${err.message}`);
    } finally {
      setRetryingCallId(null);
    }
  };

  useEffect(() => {
    fetchCalls();
    fetchSettings();
  }, []);

  const formatDuration = (sec) => {
    if (!sec) return '—';
    const m = Math.floor(sec / 60);
    const s = Math.floor(sec % 60);
    return `${m}m ${s}s`;
  };

  const completedCalls = calls.filter((c) => c.status === 'completed');
  const avgScore = completedCalls.length > 0
    ? (completedCalls.reduce((a, c) => a + (c.overall_score || 0), 0) / completedCalls.length).toFixed(1)
    : '—';
  const evaluatedCalls = calls.filter((c) => c.status === 'completed' || c.status === 'needs_review');
  const compliantCalls = evaluatedCalls.filter((c) => !c.has_critical_flag);
  const complianceRate = evaluatedCalls.length > 0
    ? `${((compliantCalls.length / evaluatedCalls.length) * 100).toFixed(1)}%`
    : '—';

  const hasAssumption = minutesSavedPerCall !== null && minutesSavedPerCall !== undefined && minutesSavedPerCall !== '';
  const parsedMin = hasAssumption ? parseFloat(minutesSavedPerCall) : NaN;
  const timeSavedHours = (!hasAssumption || isNaN(parsedMin))
    ? 'not set'
    : evaluatedCalls.length > 0
    ? `${((evaluatedCalls.length * parsedMin) / 60).toFixed(1)} hrs`
    : '0.0 hrs';

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: '22px' }}>
      {/* ── Key Metrics Grid ── */}
      <div className="grid-cols-4">
        {/* Metric 1 — live */}
        <div className="console-panel">
          <div style={{ fontSize: '12px', color: 'var(--text-muted)', fontWeight: 500 }}>
            Calls in DB
          </div>
          <div style={{ fontSize: '24px', fontWeight: 700, color: 'var(--text-primary)', marginTop: '4px' }}>
            {calls.length}
          </div>
          <div style={{ fontSize: '11px', color: 'var(--text-muted)', marginTop: '4px' }}>
            {completedCalls.length} completed
          </div>
        </div>

        {/* Metric 2 — live avg */}
        <div className="console-panel">
          <div style={{ fontSize: '12px', color: 'var(--text-muted)', fontWeight: 500 }}>
            Average QA Score
          </div>
          <div style={{ fontSize: '24px', fontWeight: 700, color: 'var(--text-primary)', marginTop: '4px' }}>
            {avgScore} <span style={{ fontSize: '13px', color: 'var(--text-muted)' }}>/ 100</span>
          </div>
          <div style={{ fontSize: '11px', color: 'var(--text-muted)', marginTop: '4px' }}>
            Threshold: ≥ 80.0
          </div>
        </div>

        {/* Metric 3 — live compliance rate */}
        <div className="console-panel">
          <div style={{ display: 'flex', alignItems: 'center', gap: '6px', fontSize: '12px', color: 'var(--text-muted)', fontWeight: 500 }}>
            Compliance Rate
          </div>
          <div style={{ fontSize: '24px', fontWeight: 700, color: 'var(--success-light)', marginTop: '4px' }}>
            {complianceRate}
          </div>
          <div style={{ fontSize: '11px', color: 'var(--text-muted)', marginTop: '4px' }}>
            {evaluatedCalls.length > 0 ? `${compliantCalls.length}/${evaluatedCalls.length} passed gate` : 'Upload calls to compute'}
          </div>
        </div>

        {/* Metric 4 — live time saved estimation from settings table assumption */}
        <div className="console-panel">
          <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
            <span style={{ fontSize: '12px', color: 'var(--text-muted)', fontWeight: 500 }}>
              Time Saved (est.)
            </span>
            <button
              className="btn btn-ghost btn-xs"
              style={{ fontSize: '10.5px', padding: '1px 5px', color: 'var(--primary)' }}
              onClick={() => {
                setAssumptionInput(minutesSavedPerCall || '');
                setIsEditingAssumption(!isEditingAssumption);
              }}
              title="Edit manual review assumption in settings"
            >
              {isEditingAssumption ? 'Close' : 'Edit'}
            </button>
          </div>

          <div style={{ fontSize: '24px', fontWeight: 700, color: hasAssumption ? 'var(--primary)' : 'var(--text-muted)', marginTop: '4px' }}>
            {timeSavedHours}
          </div>

          {isEditingAssumption ? (
            <div style={{ display: 'flex', gap: '4px', marginTop: '6px', alignItems: 'center' }}>
              <input
                type="number"
                step="1"
                min="1"
                placeholder="Minutes"
                value={assumptionInput}
                onChange={(e) => setAssumptionInput(e.target.value)}
                style={{
                  width: '65px',
                  padding: '2px 4px',
                  fontSize: '11px',
                  borderRadius: '3px',
                  border: '1px solid var(--border-medium)',
                  background: 'var(--bg-surface-elevated)',
                  color: 'var(--text-primary)',
                }}
              />
              <span style={{ fontSize: '10.5px', color: 'var(--text-muted)' }}>min/call</span>
              <button
                className="btn btn-primary btn-xs"
                style={{ fontSize: '10px', padding: '2px 6px' }}
                onClick={saveAssumption}
              >
                Save
              </button>
            </div>
          ) : (
            <div style={{ fontSize: '11px', color: 'var(--text-muted)', marginTop: '4px' }}>
              Assumption: {hasAssumption ? `${minutesSavedPerCall} min/call` : 'not set'}
            </div>
          )}
        </div>
      </div>

      {/* ── Calls Table ── */}
      <div className="console-panel">
        <div className="console-panel-header">
          <div>
            <span className="console-panel-title">Recent Calls</span>
            <p style={{ fontSize: '12px', color: 'var(--text-muted)', margin: 0 }}>
              Live data from database — {calls.length} total
            </p>
          </div>

          <div style={{ display: 'flex', gap: '8px' }}>
            <button className="btn btn-secondary btn-sm" onClick={fetchCalls} title="Refresh">
              <RefreshCw size={13} />
              Refresh
            </button>
            <button className="btn btn-primary btn-sm" onClick={onNavigateToUpload}>
              <UploadCloud size={14} /> Upload Call
            </button>
          </div>
        </div>

        <div style={{ overflowX: 'auto' }}>
          {loading ? (
            <div style={{ padding: '24px', textAlign: 'center', color: 'var(--text-muted)', fontSize: '13px' }}>
              Loading calls…
            </div>
          ) : calls.length === 0 ? (
            <div style={{ padding: '32px', textAlign: 'center', color: 'var(--text-muted)' }}>
              <FileAudio size={32} style={{ marginBottom: '8px', opacity: 0.4 }} />
              <div style={{ fontSize: '14px', fontWeight: 500 }}>No calls uploaded yet</div>
              <div style={{ fontSize: '12px', marginTop: '4px' }}>
                Upload a recording to get started
              </div>
            </div>
          ) : (
            <table className="console-table">
              <thead>
                <tr>
                  <th>Call File</th>
                  <th>Counsellor</th>
                  <th>Duration</th>
                  <th>QA Score</th>
                  <th>Status</th>
                  <th>Uploaded</th>
                  <th style={{ textAlign: 'right' }}>Action</th>
                </tr>
              </thead>
              <tbody>
                {calls.map((call) => (
                  <tr key={call.id}>
                    <td style={{ color: 'var(--text-primary)', fontWeight: 500 }}>
                      <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
                        <FileAudio size={15} style={{ color: 'var(--primary)' }} />
                        <span>{call.original_filename}</span>
                      </div>
                    </td>
                    <td>{call.counsellor_name || '—'}</td>
                    <td style={{ fontFamily: 'var(--font-mono)', fontSize: '12px' }}>
                      {formatDuration(call.duration_seconds)}
                    </td>
                    <td style={{
                      fontFamily: 'var(--font-mono)', fontWeight: 600,
                      color: call.overall_score != null
                        ? (call.overall_score >= 80 ? 'var(--success)' : 'var(--warning)')
                        : 'var(--text-muted)',
                    }}>
                      {call.overall_score != null ? `${call.overall_score}` : '—'}
                    </td>
                    <td>
                      <div>
                        <span className={`status-badge ${
                          call.status === 'completed' ? 'pass'
                          : call.status === 'needs_review' ? 'fail'
                          : call.status === 'failed' ? 'fail'
                          : call.status === 'analyzing' ? 'ai'
                          : 'neutral'
                        }`}>
                          {call.status === 'uploaded' && 'Uploaded'}
                          {call.status === 'transcribing' && 'Transcribing…'}
                          {call.status === 'transcribed' && 'Transcribed'}
                          {call.status === 'analyzing' && 'Analyzing…'}
                          {call.status === 'completed' && 'Completed'}
                          {call.status === 'needs_review' && 'Needs Review'}
                          {call.status === 'failed' && 'Failed'}
                          {!['uploaded', 'transcribing', 'transcribed', 'analyzing', 'completed', 'needs_review', 'failed'].includes(call.status) && call.status}
                        </span>
                        {call.status === 'failed' && call.failure_reason && (
                          <div
                            style={{
                              fontSize: '11px',
                              color: 'var(--danger-light)',
                              marginTop: '4px',
                              maxWidth: '180px',
                              overflow: 'hidden',
                              textOverflow: 'ellipsis',
                              whiteSpace: 'nowrap',
                            }}
                            title={call.failure_reason}
                          >
                            {call.failure_reason}
                          </div>
                        )}
                      </div>
                    </td>
                    <td style={{ color: 'var(--text-muted)', fontSize: '12px' }}>
                      {call.created_at ? new Date(call.created_at).toLocaleDateString() : '—'}
                    </td>
                    <td style={{ textAlign: 'right' }}>
                      <div style={{ display: 'flex', justifyContent: 'flex-end', gap: '6px' }}>
                        {call.status === 'failed' && (
                          <button
                            className="btn btn-secondary btn-sm"
                            onClick={(e) => handleRetry(e, call.id)}
                            disabled={retryingCallId === call.id}
                            title="Retry pipeline execution"
                            style={{ color: 'var(--primary)' }}
                          >
                            <RotateCcw size={12} style={{ animation: retryingCallId === call.id ? 'spin 1s linear infinite' : 'none' }} />
                            {retryingCallId === call.id ? 'Retrying…' : 'Retry'}
                          </button>
                        )}
                        <button
                          className="btn btn-secondary btn-sm"
                          onClick={() => onSelectCall && onSelectCall(call)}
                          title="Open in Call Inspector"
                        >
                          <Headphones size={13} /> Inspect
                        </button>
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      </div>
    </div>
  );
}


