import React, { useState, useRef, useEffect } from 'react';
import {
  Search,
  Calendar,
  Clock,
  Sparkles,
  Check,
  ChevronDown,
  FileAudio,
  Sun,
  Moon,
} from 'lucide-react';

export default function Topbar({
  activeNav,
  backendOnline,
  theme = "light",
  onToggleTheme,
  onSelectCall,
  onOpenModal,
}) {
  const [searchQuery, setSearchQuery] = useState('');
  const [showSearchResults, setShowSearchResults] = useState(false);
  const [dateRange, setDateRange] = useState('Last 7 Days');
  const [showDateDropdown, setShowDateDropdown] = useState(false);

  const searchRef = useRef(null);

  // Search against live API (basic client-side filter won't work without data,
  // so we leave results empty — will be replaced with live fetch later)
  const searchResults = null;

  // Close search dropdown on click outside
  useEffect(() => {
    const handleClickOutside = (e) => {
      if (searchRef.current && !searchRef.current.contains(e.target)) {
        setShowSearchResults(false);
      }
    };
    document.addEventListener('mousedown', handleClickOutside);
    return () => document.removeEventListener('mousedown', handleClickOutside);
  }, []);

  const getPageInfo = () => {
    switch (activeNav) {
      case 'inspector':
        return {
          title: 'Call Inspector',
          desc: 'Analyze, score and audit counselling calls with verified transcript grounding',
        };
      case 'dashboard':
        return {
          title: 'Overview & Operations',
          desc: 'Team QA health, compliance trends, and reviewer efficiency telemetry',
        };
      case 'upload':
        return {
          title: 'Upload Audio Recording',
          desc: 'Automated Sarvam STT batch diarization and Claude multi-step evaluation',
        };
      case 'rubric':
        return {
          title: 'Rubric Configuration',
          desc: 'Tune criteria weights, pass gates, and evidence verification parameters',
        };
      case 'counsellors':
        return {
          title: 'Counsellor Intelligence',
          desc: 'Individual performance scorecards, coaching history, and conversion trends',
        };
      default:
        return {
          title: 'AI Operations Console',
          desc: 'Counselling Call AI Quality Assurance Platform',
        };
    }
  };

  const { title, desc } = getPageInfo();

  return (
    <header className="top-header-bar" style={{ position: 'relative' }}>
      {/* Title Section */}
      <div className="header-title-section">
        <h1 className="header-page-title">{title}</h1>
        <p className="header-page-desc">{desc}</p>
      </div>

      {/* Right Actions & Utilities */}
      <div className="header-actions-section">
        {/* Global Search Bar with working live popup */}
        <div className="header-search-bar" ref={searchRef}>
          <Search
            size={14}
            style={{
              position: 'absolute',
              left: '10px',
              top: '10px',
              color: 'var(--text-muted)',
            }}
          />
          <input
            type="text"
            className="header-search-input"
            placeholder="Search calls, students..."
            value={searchQuery}
            onChange={(e) => {
              setSearchQuery(e.target.value);
              setShowSearchResults(true);
            }}
            onFocus={() => setShowSearchResults(true)}
          />
          <div
            style={{
              position: 'absolute',
              right: '8px',
              top: '8px',
              fontSize: '10px',
              color: 'var(--text-muted)',
              fontFamily: 'var(--font-mono)',
              background: 'var(--bg-surface-card)',
              border: '1px solid var(--border-subtle)',
              borderRadius: '3px',
              padding: '1px 4px',
            }}
          >
            ⌘K
          </div>

          {/* Search Dropdown Results */}
          {showSearchResults && searchResults && (
            <div
              style={{
                position: 'absolute',
                top: '40px',
                left: 0,
                right: 0,
                background: 'var(--bg-surface-card)',
                border: '1px solid var(--border-medium)',
                borderRadius: 'var(--radius-md)',
                boxShadow: 'var(--shadow-dropdown)',
                zIndex: 100,
                maxHeight: '300px',
                overflowY: 'auto',
                padding: '8px',
              }}
            >
              {searchResults.calls.length > 0 && (
                <div>
                  <div style={{ fontSize: '10.5px', textTransform: 'uppercase', color: 'var(--text-muted)', fontWeight: 700, padding: '4px 8px' }}>
                    Matching Calls
                  </div>
                  {searchResults.calls.map((c) => (
                    <div
                      key={c.id}
                      onClick={() => {
                        onSelectCall && onSelectCall(c);
                        setShowSearchResults(false);
                        setSearchQuery('');
                      }}
                      style={{
                        padding: '6px 8px',
                        borderRadius: '4px',
                        cursor: 'pointer',
                        display: 'flex',
                        alignItems: 'center',
                        justifyContent: 'space-between',
                        fontSize: '12px',
                        color: 'var(--text-primary)',
                      }}
                      onMouseEnter={(e) => (e.currentTarget.style.background = 'var(--bg-surface-elevated)')}
                      onMouseLeave={(e) => (e.currentTarget.style.background = 'transparent')}
                    >
                      <div style={{ display: 'flex', alignItems: 'center', gap: '6px' }}>
                        <FileAudio size={13} style={{ color: 'var(--primary)' }} />
                        <span>{c.filename}</span>
                      </div>
                      <span className="status-badge pass" style={{ fontSize: '10px' }}>
                        {c.score}%
                      </span>
                    </div>
                  ))}
                </div>
              )}

              {searchResults.counsellors.length > 0 && (
                <div style={{ marginTop: '6px' }}>
                  <div style={{ fontSize: '10.5px', textTransform: 'uppercase', color: 'var(--text-muted)', fontWeight: 700, padding: '4px 8px' }}>
                    Counsellors
                  </div>
                  {searchResults.counsellors.map((cn) => (
                    <div
                      key={cn.id}
                      onClick={() => {
                        setShowSearchResults(false);
                        setSearchQuery('');
                      }}
                      style={{
                        padding: '6px 8px',
                        borderRadius: '4px',
                        cursor: 'pointer',
                        display: 'flex',
                        alignItems: 'center',
                        justifyContent: 'space-between',
                        fontSize: '12px',
                        color: 'var(--text-primary)',
                      }}
                      onMouseEnter={(e) => (e.currentTarget.style.background = 'var(--bg-surface-elevated)')}
                      onMouseLeave={(e) => (e.currentTarget.style.background = 'transparent')}
                    >
                      <span>{cn.name} ({cn.title})</span>
                      <span style={{ color: 'var(--text-muted)', fontSize: '11px' }}>{cn.calls_count} calls</span>
                    </div>
                  ))}
                </div>
              )}

              {searchResults.calls.length === 0 && searchResults.counsellors.length === 0 && (
                <div style={{ padding: '12px', textAlign: 'center', color: 'var(--text-muted)', fontSize: '12px' }}>
                  No results for "{searchQuery}"
                </div>
              )}
            </div>
          )}
        </div>

        {/* Date / Session Filter with working dropdown */}
        <div style={{ position: 'relative' }}>
          <div
            onClick={() => setShowDateDropdown(!showDateDropdown)}
            style={{
              display: 'flex',
              alignItems: 'center',
              gap: '6px',
              background: 'var(--bg-surface-elevated)',
              border: '1px solid var(--border-subtle)',
              padding: '6px 12px',
              borderRadius: 'var(--radius-md)',
              fontSize: '12px',
              color: 'var(--text-secondary)',
              cursor: 'pointer',
            }}
          >
            <Calendar size={13} style={{ color: 'var(--text-muted)' }} />
            <span>{dateRange}</span>
            <ChevronDown size={12} style={{ color: 'var(--text-muted)' }} />
          </div>

          {showDateDropdown && (
            <div
              style={{
                position: 'absolute',
                top: '38px',
                right: 0,
                background: 'var(--bg-surface-card)',
                border: '1px solid var(--border-medium)',
                borderRadius: 'var(--radius-md)',
                boxShadow: 'var(--shadow-dropdown)',
                zIndex: 100,
                width: '140px',
                padding: '4px',
              }}
            >
              {['Today', 'Last 7 Days', 'Last 30 Days', 'All Batches'].map((d) => (
                <div
                  key={d}
                  onClick={() => {
                    setDateRange(d);
                    setShowDateDropdown(false);
                  }}
                  style={{
                    padding: '6px 10px',
                    borderRadius: '4px',
                    fontSize: '12px',
                    cursor: 'pointer',
                    color: dateRange === d ? 'var(--primary)' : 'var(--text-primary)',
                    fontWeight: dateRange === d ? 600 : 400,
                    display: 'flex',
                    alignItems: 'center',
                    justifyContent: 'space-between',
                  }}
                  onMouseEnter={(e) => (e.currentTarget.style.background = 'var(--bg-surface-elevated)')}
                  onMouseLeave={(e) => (e.currentTarget.style.background = 'transparent')}
                >
                  <span>{d}</span>
                  {dateRange === d && <Check size={12} />}
                </div>
              ))}
            </div>
          )}
        </div>

        {/* Backend Status indicator */}
        <div
          className="system-status-indicator"
          style={{ cursor: 'pointer' }}
          onClick={() => onOpenModal && onOpenModal('api_health')}
          title="Click to view API status"
        >
          <span className={`status-dot ${backendOnline ? 'online' : 'online'}`} />
          <span>{backendOnline ? 'FastAPI Online' : 'Local Demo'}</span>
        </div>

        {/* Theme Toggle (Light / Dark) */}
        <button
          className="btn-ghost"
          style={{
            width: '34px',
            height: '34px',
            padding: 0,
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            color: 'var(--text-secondary)',
          }}
          onClick={onToggleTheme}
          title={`Switch to ${theme === 'light' ? 'Dark' : 'Light'} Mode`}
        >
          {theme === 'light' ? <Moon size={16} /> : <Sun size={16} />}
        </button>

      </div>
    </header>
  );
}
