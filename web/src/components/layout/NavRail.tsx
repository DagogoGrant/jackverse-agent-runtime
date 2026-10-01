import React from 'react';
import { NavLink } from 'react-router-dom';
import { useApprovals } from '../../hooks/useCaseworker';

interface NavItem {
  id: string;
  number: string;
  label: string;
  path: string;
  badge?: number;
}

interface NavRailProps {
  className?: string;
  onNavigate?: () => void;
}

export const NavRail: React.FC<NavRailProps> = ({ className = '', onNavigate }) => {
  const { data: approvals } = useApprovals();
  const pendingCount = approvals?.filter((a) => a.status === 'pending').length || 0;

  const navItems: NavItem[] = [
    { id: 'home', number: '01', label: 'Home', path: '/' },
    { id: 'missions', number: '02', label: 'Missions', path: '/missions' },
    { id: 'opportunities', number: '03', label: 'Opportunities', path: '/opportunities' },
    { id: 'approvals', number: '04', label: 'Needs you', path: '/approvals', badge: pendingCount },
    { id: 'context', number: '05', label: 'My Context', path: '/context' },
    { id: 'activity', number: '06', label: 'Activity', path: '/activity' },
  ];

  return (
    <aside className={`border-grey-700 bg-canvas select-none font-interface flex flex-col justify-between p-6 ${className}`}>
      <div className="space-y-10">
        {/* Brand identity */}
        <div className="space-y-1">
          <div className="font-machine text-xs tracking-widest uppercase text-grey-500">
            JACKVERSE
          </div>
          <div className="font-display text-2xl text-pure tracking-tight">
            Caseworker
          </div>
        </div>

        {/* Numbered Navigation */}
        <nav className="space-y-2">
          {navItems.map((item) => (
            <NavLink
              key={item.id}
              to={item.path}
              end={item.path === '/'}
              onClick={onNavigate}
              className={({ isActive }) =>
                `group flex items-center justify-between py-2 px-2 text-sm tracking-wide transition-all duration-150 ${
                  isActive
                    ? 'bg-paper text-canvas font-semibold'
                    : 'text-grey-300 hover:text-pure hover:translate-x-1'
                }`
              }
            >
              {({ isActive }) => (
                <>
                  <div className="flex items-center gap-3">
                    <span className="font-machine text-xs opacity-60">
                      {isActive ? '■' : item.number}
                    </span>
                    <span className="uppercase text-xs tracking-wider">
                      {item.label}
                    </span>
                  </div>
                  {item.badge !== undefined && item.badge > 0 && (
                    <span
                      className={`font-machine text-[10px] px-1.5 py-0.2 border ${
                        isActive
                          ? 'border-canvas bg-canvas text-paper'
                          : 'border-paper text-paper'
                      }`}
                    >
                      {item.badge}
                    </span>
                  )}
                </>
              )}
            </NavLink>
          ))}
        </nav>
      </div>

      {/* System footprint */}
      <div className="pt-6 border-t border-grey-700/60 font-machine text-[11px] text-grey-500 space-y-1">
        <div>SYS // RUNTIME: v0.4.0</div>
        <div>MODEL // DETERMINISTIC</div>
      </div>
    </aside>
  );
};
