import React from 'react';
import {
  BarChart3,
  Headphones,
  UploadCloud,
  Sliders,
  Users,
  Activity,
  Cpu,
  DollarSign,
  ChevronLeft,
  ChevronRight,
  Settings,
  HelpCircle,
} from 'lucide-react';

export default function Sidebar({
  activeNav,
  setActiveNav,
  collapsed,
  setCollapsed,
  onOpenModal,
}) {
  const mainNavItems = [
    { id: 'inspector', label: 'Call Inspector', icon: Headphones },
    { id: 'dashboard', label: 'Overview', icon: BarChart3 },
    { id: 'upload', label: 'Upload Call', icon: UploadCloud },
    { id: 'rubric', label: 'Rubric Settings', icon: Sliders },
    { id: 'counsellors', label: 'Counsellors', icon: Users },
  ];



  const systemItems = [
    { id: 'api_health', label: 'API Health', icon: Activity, status: 'online' },
    { id: 'model_usage', label: 'Model Usage', icon: Cpu, detail: 'Claude 3.5' },
    { id: 'cost_monitor', label: 'Cost Monitor', icon: DollarSign, detail: '$14.20' },
  ];

  return (
    <aside className={`app-sidebar ${collapsed ? 'collapsed' : ''}`}>
      {/* Sidebar Header */}
      <div className="sidebar-header">
        {collapsed ? (
          <div
            className="collapsed-header-wrap"
            onClick={() => setCollapsed(false)}
            title="Expand sidebar (Click to open)"
          >
            <div className="pw-badge-mark">PW</div>
            <button
              className="collapsed-floating-toggle"
              onClick={(e) => {
                e.stopPropagation();
                setCollapsed(false);
              }}
              title="Expand sidebar"
            >
              <ChevronRight size={13} />
            </button>
          </div>
        ) : (
          <>
            <div
              className="brand-wrapper"
              onClick={() => setActiveNav('dashboard')}
              title="PW Counselling QA Platform"
            >
              <div className="pw-badge-mark">PW</div>
              <div className="brand-text">
                <span className="brand-name">PW Counselling QA</span>
                <span className="brand-sub">AI Operations Console</span>
              </div>
            </div>

            <button
              className="sidebar-toggle-btn"
              onClick={() => setCollapsed(true)}
              title="Collapse sidebar"
            >
              <ChevronLeft size={16} />
            </button>
          </>
        )}
      </div>

      {/* Nav Content */}
      <div className="sidebar-nav-container">
        {/* Main Navigation */}
        <div>
          {!collapsed && <div className="sidebar-section-title">Navigation</div>}
          <ul className="sidebar-nav-list">
            {mainNavItems.map((item) => {
              const Icon = item.icon;
              const isActive = activeNav === item.id;
              return (
                <li key={item.id}>
                  <div
                    className={`sidebar-nav-item ${isActive ? 'active' : ''}`}
                    onClick={() => setActiveNav(item.id)}
                    title={collapsed ? item.label : undefined}
                  >
                    <Icon size={17} />
                    {!collapsed && <span>{item.label}</span>}
                  </div>
                </li>
              );
            })}
          </ul>
        </div>



        {/* System Monitoring */}
        <div>
          {!collapsed && <div className="sidebar-section-title">System</div>}
          <ul className="sidebar-nav-list">
            {systemItems.map((item) => {
              const Icon = item.icon;
              return (
                <li key={item.id}>
                  <div
                    className="sidebar-nav-item"
                    onClick={() => onOpenModal && onOpenModal(item.id)}
                    title={collapsed ? `${item.label}: ${item.detail || item.status}` : undefined}
                  >
                    <Icon size={16} style={{ color: item.status === 'online' ? '#10B981' : 'inherit' }} />
                    {!collapsed && (
                      <>
                        <span>{item.label}</span>
                        {item.status === 'online' ? (
                          <span style={{ marginLeft: 'auto', display: 'flex', alignItems: 'center', gap: '4px', fontSize: '11px', color: '#10B981' }}>
                            <span className="status-dot" /> Live
                          </span>
                        ) : (
                          <span className="sidebar-count-badge">{item.detail}</span>
                        )}
                      </>
                    )}
                  </div>
                </li>
              );
            })}
          </ul>
        </div>
      </div>

      {/* Sidebar Footer — neutral label */}
      <div className="sidebar-footer">
        <div style={{ display: 'flex', alignItems: 'center', gap: '8px', flex: 1, minWidth: 0 }}>
          {!collapsed && (
            <span style={{ fontSize: '11.5px', color: 'var(--text-secondary)', fontWeight: 600, whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>
              Prototype by Aditya Yadav
            </span>
          )}
        </div>
        {!collapsed && (
          <div style={{ display: 'flex', gap: '4px' }}>
            <button
              className="sidebar-toggle-btn"
              onClick={() => onOpenModal && onOpenModal('settings')}
              title="Settings"
            >
              <Settings size={15} />
            </button>
            <button
              className="sidebar-toggle-btn"
              onClick={() => onOpenModal && onOpenModal('help')}
              title="Help & Shortcuts"
            >
              <HelpCircle size={15} />
            </button>
          </div>
        )}
      </div>
    </aside>
  );
}
