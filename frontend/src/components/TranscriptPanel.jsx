import React, { useState } from 'react';
import {
  Search,
  Play,
  MessageSquare,
  AlertTriangle,
  HelpCircle,
  ShieldAlert,
  ChevronDown,
} from 'lucide-react';

export default function TranscriptPanel({
  segments = [],
  activeSegmentId,
  highlightedSegmentId,
  onSelectSegment,
  onRoleOverride,
}) {
  const [filterType, setFilterType] = useState('all');
  const [searchQuery, setSearchQuery] = useState('');
  const [annotatingSegId, setAnnotatingSegId] = useState(null);
  const [notes, setNotes] = useState({});
  const [noteInput, setNoteInput] = useState('');

  // Format milliseconds into MM:SS
  const formatTime = (ms) => {
    const totalSec = Math.floor((ms || 0) / 1000);
    const m = Math.floor(totalSec / 60);
    const s = totalSec % 60;
    return `${m < 10 ? '0' : ''}${m}:${s < 10 ? '0' : ''}${s}`;
  };

  // Filter segments
  const filteredSegments = segments.filter((seg) => {
    // Role or feature filters
    if (filterType === 'counsellor' && seg.role !== 'counsellor') return false;
    if (filterType === 'student' && seg.role !== 'student') return false;
    if (filterType === 'flags' && !seg.is_flagged) return false;
    if (filterType === 'questions' && !seg.is_question) return false;
    if (filterType === 'objections' && !seg.is_objection) return false;

    // Search query
    if (searchQuery.trim() !== '') {
      return seg.text.toLowerCase().includes(searchQuery.toLowerCase());
    }

    return true;
  });

  const handleSaveNote = (segId) => {
    if (noteInput.trim()) {
      setNotes((prev) => ({ ...prev, [segId]: noteInput.trim() }));
      setNoteInput('');
      setAnnotatingSegId(null);
    }
  };

  return (
    <div className="transcript-panel">
      {/* ── Header Bar ── */}
      <div className="transcript-header-bar">
        <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
          <span style={{ fontSize: '14px', fontWeight: 600, color: 'var(--text-primary)' }}>
            Transcript
          </span>
          <span className="status-badge neutral">
            {segments.length} segments
          </span>
        </div>

        {/* Filter Buttons */}
        <div className="transcript-filters-row">
          {[
            { id: 'all', label: 'All' },
            { id: 'counsellor', label: 'Counsellor' },
            { id: 'student', label: 'Student' },
            { id: 'flags', label: 'Flags' },
            { id: 'questions', label: 'Questions' },
            { id: 'objections', label: 'Objections' },
          ].map((f) => (
            <button
              key={f.id}
              className={`btn btn-sm ${filterType === f.id ? 'btn-primary' : 'btn-ghost'}`}
              style={{ fontSize: '11.5px', padding: '3px 8px' }}
              onClick={() => setFilterType(f.id)}
            >
              {f.label}
            </button>
          ))}
        </div>
      </div>

      {/* ── Search Input ── */}
      <div className="transcript-search-wrap">
        <Search size={14} style={{ color: 'var(--text-muted)' }} />
        <input
          type="text"
          placeholder="Search conversation keywords in Hindi / English..."
          value={searchQuery}
          onChange={(e) => setSearchQuery(e.target.value)}
          style={{
            flex: 1,
            background: 'transparent',
            border: 'none',
            outline: 'none',
            fontSize: '12.5px',
            color: 'var(--text-primary)',
          }}
        />
        {searchQuery && (
          <button
            className="btn btn-ghost btn-sm"
            onClick={() => setSearchQuery('')}
            style={{ fontSize: '11px', padding: '2px 6px' }}
          >
            Clear
          </button>
        )}
      </div>

      {/* ── Scrollable Segment Rows ── */}
      <div className="transcript-scroll-area">
        {filteredSegments.length === 0 ? (
          <div style={{ padding: '32px 16px', textAlign: 'center', color: 'var(--text-muted)' }}>
            No transcript segments match your active filter.
          </div>
        ) : (
          filteredSegments.map((seg) => {
            const isActive = activeSegmentId === seg.segment_id;
            const isHighlighted = highlightedSegmentId === seg.segment_id;
            const isCounsellor = seg.role === 'counsellor';
            const segNote = notes[seg.segment_id];

            let rowClass = 'transcript-row';
            if (isActive) rowClass += ' active';
            if (isHighlighted) rowClass += ' highlighted';

            return (
              <div
                key={seg.segment_id}
                id={`seg-row-${seg.segment_id}`}
                className={rowClass}
                onClick={() => onSelectSegment && onSelectSegment(seg)}
              >
                {/* Speaker Avatar */}
                <div
                  className={`speaker-avatar-wrap ${
                    isCounsellor ? 'speaker-avatar-counsellor' : 'speaker-avatar-student'
                  }`}
                  title={seg.role}
                >
                  {isCounsellor ? 'C' : 'S'}
                </div>

                {/* Metadata Column */}
                <div className="transcript-meta-col">
                  <span className="transcript-time-label">
                    {formatTime(seg.start_ms)} – {formatTime(seg.end_ms)}
                  </span>
                  <select
                    value={seg.role}
                    onChange={(e) => {
                      e.stopPropagation();
                      if (onRoleOverride) onRoleOverride(seg.segment_id, e.target.value);
                    }}
                    onClick={(e) => e.stopPropagation()}
                    style={{
                      background: isCounsellor ? 'var(--primary-subtle)' : 'var(--bg-surface-elevated)',
                      color: isCounsellor ? 'var(--primary)' : 'var(--text-secondary)',
                      border: isCounsellor ? '1px solid var(--primary-border)' : '1px solid var(--border-subtle)',
                      borderRadius: '4px',
                      fontSize: '10.5px',
                      fontWeight: 600,
                      padding: '2px 4px',
                      marginTop: '3px',
                      cursor: 'pointer',
                      outline: 'none',
                    }}
                    title="Change speaker role"
                  >
                    <option value="counsellor">Counsellor</option>
                    <option value="student">Student</option>
                    <option value="parent">Parent</option>
                  </select>
                </div>

                {/* Text Content */}
                <div className="transcript-text-content">
                  <p style={{ margin: 0 }}>
                    {searchQuery.trim() ? (
                      highlightText(seg.text, searchQuery)
                    ) : (
                      seg.text
                    )}
                  </p>

                  {/* Flag indicator pill if flagged */}
                  {seg.is_flagged && (
                    <div
                      style={{
                        display: 'inline-flex',
                        alignItems: 'center',
                        gap: '4px',
                        marginTop: '6px',
                        padding: '2px 6px',
                        borderRadius: '3px',
                        fontSize: '11px',
                        background: 'var(--warning-subtle)',
                        color: 'var(--warning-text)',
                        border: '1px solid var(--warning-border)',
                      }}
                    >
                      <AlertTriangle size={11} />
                      <span>Flag {seg.flag_rule || 'Observation'}{seg.flag_explanation ? `: ${seg.flag_explanation}` : ''}</span>
                    </div>
                  )}

                  {/* User Note if added */}
                  {segNote && (
                    <div
                      style={{
                        marginTop: '6px',
                        padding: '4px 8px',
                        borderRadius: '4px',
                        background: 'var(--bg-surface-elevated)',
                        borderLeft: '2px solid var(--primary)',
                        fontSize: '11.5px',
                        color: 'var(--text-secondary)',
                      }}
                    >
                      <strong style={{ color: 'var(--primary-text)' }}>QA Note:</strong> {segNote}
                    </div>
                  )}

                  {/* Annotation Input form if active */}
                  {annotatingSegId === seg.segment_id && (
                    <div
                      style={{ marginTop: '8px', display: 'flex', gap: '6px' }}
                      onClick={(e) => e.stopPropagation()}
                    >
                      <input
                        type="text"
                        placeholder="Add QA supervisor note..."
                        value={noteInput}
                        onChange={(e) => setNoteInput(e.target.value)}
                        autoFocus
                        style={{
                          flex: 1,
                          background: 'var(--bg-surface-elevated)',
                          border: '1px solid var(--border-medium)',
                          borderRadius: '4px',
                          padding: '4px 8px',
                          color: 'var(--text-primary)',
                          fontSize: '12px',
                        }}
                      />
                      <button
                        className="btn btn-primary btn-sm"
                        onClick={() => handleSaveNote(seg.segment_id)}
                      >
                        Save
                      </button>
                      <button
                        className="btn btn-ghost btn-sm"
                        onClick={() => setAnnotatingSegId(null)}
                      >
                        Cancel
                      </button>
                    </div>
                  )}
                </div>

                {/* Hover Actions Bar */}
                <div className="transcript-hover-actions" onClick={(e) => e.stopPropagation()}>
                  <button
                    className="btn btn-secondary btn-sm"
                    style={{ padding: '3px 8px', fontSize: '11px' }}
                    onClick={() => onSelectSegment && onSelectSegment(seg)}
                    title="Play audio from this timestamp"
                  >
                    <Play size={11} /> Play
                  </button>

                  <button
                    className="btn btn-ghost btn-sm"
                    style={{ padding: '3px 6px' }}
                    onClick={() => {
                      setAnnotatingSegId(seg.segment_id);
                      setNoteInput(notes[seg.segment_id] || '');
                    }}
                    title="Add manager note"
                  >
                    <MessageSquare size={12} />
                  </button>
                </div>
              </div>
            );
          })
        )}
      </div>
    </div>
  );
}

// Simple highlighter helper
function highlightText(text, query) {
  if (!query) return text;
  const parts = text.split(new RegExp(`(${escapeRegex(query)})`, 'gi'));
  return parts.map((part, i) =>
    part.toLowerCase() === query.toLowerCase() ? (
      <mark
        key={i}
        style={{
          background: 'var(--primary-subtle)',
          color: 'var(--primary-text)',
          fontWeight: 600,
          padding: '0 3px',
          borderRadius: '2px',
        }}
      >
        {part}
      </mark>
    ) : (
      part
    )
  );
}

function escapeRegex(string) {
  return string.replace(/[-/\\^$*+?.()|[\]{}]/g, '\\$&');
}
