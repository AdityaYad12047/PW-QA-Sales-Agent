import React, { useState, useEffect } from 'react';
import {
  Users,
  ChevronRight,
  X,
  PhoneCall,
  Calendar,
  Mail,
} from 'lucide-react';
import { apiJson } from '../api/client';

export default function CounsellorAnalytics({ onInspectCall }) {
  const [counsellors, setCounsellors] = useState([]);
  const [selectedCounsellor, setSelectedCounsellor] = useState(null);
  const [drawerOpen, setDrawerOpen] = useState(false);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    apiJson('/counsellors')
      .then((data) => {
        setCounsellors(data);
        if (data.length > 0) setSelectedCounsellor(data[0]);
      })
      .catch(() => {})
      .finally(() => setLoading(false));
  }, []);

  const openDrawer = (c) => {
    setSelectedCounsellor(c);
    setDrawerOpen(true);
  };

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: '22px' }}>
      {/* ── Table Console Panel ── */}
      <div className="console-panel">
        <div className="console-panel-header">
          <div>
            <span className="console-panel-title">Counsellors Directory</span>
            <p style={{ fontSize: '12px', color: 'var(--text-muted)', margin: 0 }}>
              Counsellor profiles and call audit volume from the database
            </p>
          </div>
        </div>

        <div style={{ overflowX: 'auto' }}>
          {loading ? (
            <div style={{ padding: '24px', textAlign: 'center', color: 'var(--text-muted)' }}>Loading counsellors…</div>
          ) : counsellors.length === 0 ? (
            <div style={{ padding: '24px', textAlign: 'center', color: 'var(--text-muted)' }}>No counsellors recorded yet. Add counsellors during call upload.</div>
          ) : (
            <table className="console-table">
              <thead>
                <tr>
                  <th>Counsellor</th>
                  <th>Email</th>
                  <th>Registered</th>
                  <th>Calls Audited</th>
                  <th style={{ textAlign: 'right' }}>Action</th>
                </tr>
              </thead>
              <tbody>
                {counsellors.map((c) => (
                  <tr
                    key={c.id}
                    onClick={() => openDrawer(c)}
                    style={{ cursor: 'pointer' }}
                  >
                    <td style={{ color: 'var(--text-primary)', fontWeight: 600 }}>
                      <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
                        <div
                          style={{
                            width: '26px',
                            height: '26px',
                            borderRadius: '50%',
                            background: 'var(--primary-subtle)',
                            color: 'var(--primary)',
                            display: 'flex',
                            alignItems: 'center',
                            justifyContent: 'center',
                            fontSize: '11px',
                            fontWeight: 700,
                          }}
                        >
                          {c.name ? c.name.charAt(0).toUpperCase() : 'C'}
                        </div>
                        {c.name}
                      </div>
                    </td>
                    <td style={{ color: 'var(--text-muted)' }}>{c.email || '—'}</td>
                    <td style={{ fontFamily: 'var(--font-mono)', fontSize: '12px' }}>
                      {c.created_at ? new Date(c.created_at).toLocaleDateString() : '—'}
                    </td>
                    <td style={{ fontFamily: 'var(--font-mono)', fontWeight: 600 }}>
                      {c.calls_count || 0}
                    </td>
                    <td style={{ textAlign: 'right' }}>
                      <button
                        className="btn btn-secondary btn-sm"
                        onClick={(e) => {
                          e.stopPropagation();
                          openDrawer(c);
                        }}
                      >
                        Details <ChevronRight size={13} />
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      </div>

      {/* ── Drilldown Details Panel ── */}
      {drawerOpen && selectedCounsellor && (
        <div className="console-panel" style={{ borderLeft: '3px solid var(--primary)' }}>
          <div className="console-panel-header">
            <div style={{ display: 'flex', alignItems: 'center', gap: '10px' }}>
              <div
                style={{
                  width: '36px',
                  height: '36px',
                  borderRadius: '50%',
                  background: 'var(--primary-subtle)',
                  color: '#818CF8',
                  display: 'flex',
                  alignItems: 'center',
                  justifyContent: 'center',
                  fontWeight: 700,
                }}
              >
                {selectedCounsellor.name ? selectedCounsellor.name.charAt(0).toUpperCase() : 'C'}
              </div>
              <div>
                <span className="console-panel-title">{selectedCounsellor.name}</span>
                <span style={{ fontSize: '11px', color: 'var(--text-muted)' }}>
                  ID #{selectedCounsellor.id} {selectedCounsellor.email ? `· ${selectedCounsellor.email}` : ''}
                </span>
              </div>
            </div>

            <button className="btn btn-ghost btn-sm" onClick={() => setDrawerOpen(false)}>
              <X size={15} /> Close
            </button>
          </div>

          <div className="grid-cols-2" style={{ marginTop: '14px', gap: '16px' }}>
            <div
              style={{
                background: 'var(--bg-surface-elevated)',
                padding: '16px',
                borderRadius: 'var(--radius-md)',
                border: '1px solid var(--border-subtle)',
                display: 'flex',
                flexDirection: 'column',
                gap: '8px',
              }}
            >
              <div style={{ fontSize: '11px', color: 'var(--text-muted)', textTransform: 'uppercase', fontWeight: 600 }}>
                Audit Summary
              </div>
              <div style={{ display: 'flex', alignItems: 'center', gap: '8px', fontSize: '14px' }}>
                <PhoneCall size={15} color="var(--primary)" />
                <span>Total Calls Audited: <strong>{selectedCounsellor.calls_count || 0}</strong></span>
              </div>
              <div style={{ display: 'flex', alignItems: 'center', gap: '8px', fontSize: '13px', color: 'var(--text-secondary)' }}>
                <Calendar size={14} />
                <span>Registered: {selectedCounsellor.created_at ? new Date(selectedCounsellor.created_at).toLocaleDateString() : '—'}</span>
              </div>
              {selectedCounsellor.email && (
                <div style={{ display: 'flex', alignItems: 'center', gap: '8px', fontSize: '13px', color: 'var(--text-secondary)' }}>
                  <Mail size={14} />
                  <span>{selectedCounsellor.email}</span>
                </div>
              )}
            </div>

            <div
              style={{
                background: 'var(--primary-subtle)',
                border: '1px solid var(--primary-border)',
                borderRadius: 'var(--radius-md)',
                padding: '16px',
                display: 'flex',
                flexDirection: 'column',
                justifyContent: 'space-between',
                gap: '12px',
              }}
            >
              <div>
                <div style={{ fontSize: '13px', fontWeight: 600, color: '#A5B4FC' }}>
                  Filter Audits by Counsellor
                </div>
                <p style={{ fontSize: '12px', color: '#CBD5E1', margin: '6px 0 0 0', lineHeight: 1.4 }}>
                  Inspect all calls, compliance status, and coaching opportunities logged for {selectedCounsellor.name}.
                </p>
              </div>
              <div style={{ display: 'flex', justifyContent: 'flex-end' }}>
                <button
                  className="btn btn-primary btn-sm"
                  onClick={() => onInspectCall && onInspectCall({ counsellor: selectedCounsellor.name })}
                >
                  View Calls in Overview →
                </button>
              </div>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

