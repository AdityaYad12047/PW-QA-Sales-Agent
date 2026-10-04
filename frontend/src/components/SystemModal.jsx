import React from 'react';
import {
  X,
  Activity,
  Cpu,
  DollarSign,
  Settings,
  HelpCircle,
  CheckCircle2,
  Shield,
  Layers,
} from 'lucide-react';


export default function SystemModal({ type, onClose }) {
  if (!type) return null;

  return (
    <div className="modal-overlay" onClick={onClose}>
      <div className="modal-content-card" onClick={(e) => e.stopPropagation()}>
        {/* Header */}
        <div
          style={{
            padding: '16px 20px',
            borderBottom: '1px solid var(--border-subtle)',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'space-between',
            background: 'var(--bg-surface-elevated)',
          }}
        >
          <div style={{ display: 'flex', alignItems: 'center', gap: '8px', fontSize: '15px', fontWeight: 600, color: 'var(--text-primary)' }}>
            {type === 'api_health' && <Activity size={18} style={{ color: 'var(--success)' }} />}
            {type === 'model_usage' && <Cpu size={18} style={{ color: 'var(--primary)' }} />}
            {type === 'cost_monitor' && <DollarSign size={18} style={{ color: 'var(--warning)' }} />}
            {type === 'settings' && <Settings size={18} />}
            {type === 'help' && <HelpCircle size={18} />}

            <span>
              {type === 'api_health' && 'API Health & Database Telemetry'}
              {type === 'model_usage' && 'AI Model & Token Observability'}
              {type === 'cost_monitor' && 'Cost Analytics & Resource Allocation'}
              {type === 'settings' && 'System QA Preferences'}
              {type === 'help' && 'Keyboard Shortcuts & Documentation'}
            </span>
          </div>

          <button className="btn btn-ghost btn-sm" onClick={onClose} style={{ padding: '4px' }}>
            <X size={16} />
          </button>
        </div>

        {/* Content Body */}
        <div style={{ padding: '20px', display: 'flex', flexDirection: 'column', gap: '16px' }}>
          {/* 1. API Health */}
          {type === 'api_health' && (
            <div style={{ display: 'flex', flexDirection: 'column', gap: '12px', fontSize: '13px' }}>
              <div style={{ display: 'flex', justifyContent: 'space-between', padding: '8px 12px', background: 'var(--bg-surface-elevated)', borderRadius: '6px' }}>
                <span style={{ color: 'var(--text-muted)' }}>FastAPI Gateway:</span>
                <span style={{ color: 'var(--success)', fontWeight: 600 }}>ONLINE (http://127.0.0.1:8000)</span>
              </div>
              <div style={{ display: 'flex', justifyContent: 'space-between', padding: '8px 12px', background: 'var(--bg-surface-elevated)', borderRadius: '6px' }}>
                <span style={{ color: 'var(--text-muted)' }}>Primary Database:</span>
                <span style={{ color: 'var(--text-primary)', fontFamily: 'var(--font-mono)' }}>SQLite (/app/data/pw_qa.db)</span>
              </div>
              <div style={{ display: 'flex', justifyContent: 'space-between', padding: '8px 12px', background: 'var(--bg-surface-elevated)', borderRadius: '6px' }}>
                <span style={{ color: 'var(--text-muted)' }}>STT Service:</span>
                <span style={{ color: 'var(--text-primary)' }}>Sarvam AI saaras:v4 (Hindi/Hinglish Diarized)</span>
              </div>
              <div style={{ display: 'flex', justifyContent: 'space-between', padding: '8px 12px', background: 'var(--bg-surface-elevated)', borderRadius: '6px' }}>
                <span style={{ color: 'var(--text-muted)' }}>LLM Engine:</span>
                <span style={{ color: 'var(--text-primary)' }}>Claude 3.5 Sonnet (3-step pipeline)</span>
              </div>
              <div style={{ display: 'flex', justifyContent: 'space-between', padding: '8px 12px', background: 'var(--bg-surface-elevated)', borderRadius: '6px' }}>
                <span style={{ color: 'var(--text-muted)' }}>System:</span>
                <span style={{ color: 'var(--primary)', fontWeight: 600 }}>PW Counselling QA v1.0</span>
              </div>
            </div>
          )}

          {/* 2. Model Usage */}
          {type === 'model_usage' && (
            <div style={{ display: 'flex', flexDirection: 'column', gap: '12px', fontSize: '13px' }}>
              <div style={{ background: 'var(--primary-subtle)', padding: '12px', borderRadius: '6px', border: '1px solid var(--primary-border)' }}>
                <div style={{ fontWeight: 600, color: 'var(--primary-text)' }}>Prompt Caching Telemetry</div>
                <div style={{ fontSize: '12px', color: 'var(--text-secondary)', marginTop: '2px' }}>
                  Anthropic prefix caching enabled on static system prompt, yielding significant token cost reduction on compliance audits.
                </div>
              </div>
              <div style={{ display: 'flex', justifyContent: 'space-between', padding: '8px 12px', background: 'var(--bg-surface-elevated)', borderRadius: '6px' }}>
                <span style={{ color: 'var(--text-muted)' }}>Input Price / Million:</span>
                <span style={{ fontFamily: 'var(--font-mono)' }}>$3.00 USD</span>
              </div>
              <div style={{ display: 'flex', justifyContent: 'space-between', padding: '8px 12px', background: 'var(--bg-surface-elevated)', borderRadius: '6px' }}>
                <span style={{ color: 'var(--text-muted)' }}>Output Price / Million:</span>
                <span style={{ fontFamily: 'var(--font-mono)' }}>$15.00 USD</span>
              </div>
              <div style={{ display: 'flex', justifyContent: 'space-between', padding: '8px 12px', background: 'var(--bg-surface-elevated)', borderRadius: '6px' }}>
                <span style={{ color: 'var(--text-muted)' }}>Average Tokens Per Call:</span>
                <span style={{ fontFamily: 'var(--font-mono)' }}>4,180 tokens</span>
              </div>
            </div>
          )}

          {/* 3. Cost Monitor */}
          {type === 'cost_monitor' && (
            <div style={{ display: 'flex', flexDirection: 'column', gap: '12px', fontSize: '13px' }}>
              <div style={{ display: 'flex', justifyContent: 'space-between', padding: '8px 12px', background: 'var(--bg-surface-elevated)', borderRadius: '6px' }}>
                <span style={{ color: 'var(--text-muted)' }}>Total Spend (MTD):</span>
                <span style={{ fontWeight: 700, color: 'var(--success)', fontSize: '16px' }}>$14.20 USD</span>
              </div>
              <div style={{ display: 'flex', justifyContent: 'space-between', padding: '8px 12px', background: 'var(--bg-surface-elevated)', borderRadius: '6px' }}>
                <span style={{ color: 'var(--text-muted)' }}>Avg Cost Per Evaluated Call:</span>
                <span style={{ fontFamily: 'var(--font-mono)', color: 'var(--text-primary)' }}>$0.0482 (~₹4.05)</span>
              </div>
              <div style={{ display: 'flex', justifyContent: 'space-between', padding: '8px 12px', background: 'var(--bg-surface-elevated)', borderRadius: '6px' }}>
                <span style={{ color: 'var(--text-muted)' }}>Sarvam Batch STT Rate:</span>
                <span style={{ fontFamily: 'var(--font-mono)', color: 'var(--text-primary)' }}>₹45 / Audio Hour</span>
              </div>
              <div style={{ display: 'flex', justifyContent: 'space-between', padding: '8px 12px', background: 'var(--bg-surface-elevated)', borderRadius: '6px' }}>
                <span style={{ color: 'var(--text-muted)' }}>Estimated Net Monthly Savings:</span>
                <span style={{ fontWeight: 600, color: 'var(--success)' }}>₹2,45,000 / month</span>
              </div>
            </div>
          )}



          {/* 5. Help / Shortcuts */}
          {type === 'help' && (
            <div style={{ display: 'flex', flexDirection: 'column', gap: '10px', fontSize: '12.5px' }}>
              <div style={{ display: 'flex', justifyContent: 'space-between', padding: '6px 10px', background: 'var(--bg-surface-elevated)', borderRadius: '4px' }}>
                <span>Toggle Audio Play / Pause:</span>
                <kbd style={{ background: 'var(--bg-surface)', border: '1px solid var(--border-medium)', color: 'var(--text-primary)', padding: '2px 6px', borderRadius: '3px', fontFamily: 'var(--font-mono)' }}>Space</kbd>
              </div>
              <div style={{ display: 'flex', justifyContent: 'space-between', padding: '6px 10px', background: 'var(--bg-surface-elevated)', borderRadius: '4px' }}>
                <span>Global Search:</span>
                <kbd style={{ background: 'var(--bg-surface)', border: '1px solid var(--border-medium)', color: 'var(--text-primary)', padding: '2px 6px', borderRadius: '3px', fontFamily: 'var(--font-mono)' }}>⌘K / Ctrl+K</kbd>
              </div>
              <div style={{ display: 'flex', justifyContent: 'space-between', padding: '6px 10px', background: 'var(--bg-surface-elevated)', borderRadius: '4px' }}>
                <span>Jump Audio to Segment:</span>
                <span style={{ color: 'var(--text-muted)' }}>Click timestamp / Play icon</span>
              </div>
              <div style={{ display: 'flex', justifyContent: 'space-between', padding: '6px 10px', background: 'var(--bg-surface-elevated)', borderRadius: '4px' }}>
                <span>Jump to Evidence Quote:</span>
                <span style={{ color: 'var(--text-muted)' }}>Click green verified chip</span>
              </div>
            </div>
          )}

          {/* 6. Settings */}
          {type === 'settings' && (
            <div style={{ display: 'flex', flexDirection: 'column', gap: '12px', fontSize: '13px' }}>
              <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
                <span>Real-time Sound Synthesis (Web Audio)</span>
                <input type="checkbox" defaultChecked style={{ accentColor: 'var(--primary)' }} />
              </div>
              <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
                <span>Automatic Diarization Sync</span>
                <input type="checkbox" defaultChecked style={{ accentColor: 'var(--primary)' }} />
              </div>
              <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
                <span>Immediate Critical Gate Escalation</span>
                <input type="checkbox" defaultChecked style={{ accentColor: 'var(--primary)' }} />
              </div>
            </div>
          )}
        </div>

        {/* Footer */}
        <div style={{ padding: '12px 20px', borderTop: '1px solid var(--border-subtle)', background: 'var(--bg-surface-elevated)', display: 'flex', justifyContent: 'flex-end' }}>
          <button className="btn btn-secondary btn-sm" onClick={onClose}>
            Close
          </button>
        </div>
      </div>
    </div>
  );
}
