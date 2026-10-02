import React from 'react';
import { NavLink, useLocation, useNavigate } from 'react-router-dom';
import { useApprovals, useCase } from '../../hooks/useCaseworker';
import { NumberRoll } from '../ui/NumberRoll';
import { ArrowLeft } from 'lucide-react';
import { startViewTransition } from '../../utils/transitions';

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
  isMobileDrawer?: boolean;
}

export const NavRail: React.FC<NavRailProps> = ({
  className = '',
  onNavigate,
  isMobileDrawer = false,
}) => {
  const location = useLocation();
  const navigate = useNavigate();
  const { data: approvals } = useApprovals();
  const pendingCount = approvals?.filter((a) => a.status === 'pending').length || 0;

  const pathname = location.pathname;
  const isMissionDetail = pathname.startsWith('/missions/') && pathname !== '/missions';
  const isCaseDetail = pathname.startsWith('/cases/');
  const currentCaseId = isCaseDetail ? pathname.split('/cases/')[1]?.split('/')[0] : undefined;
  const { data: caseData } = useCase(currentCaseId);
  const parentMissionId = caseData?.caseItem?.mission_id;

  const navItems: NavItem[] = [
    { id: 'home', number: '01', label: 'Home', path: '/' },
    { id: 'missions', number: '02', label: 'Missions', path: '/missions' },
    { id: 'opportunities', number: '03', label: 'Opportunities', path: '/opportunities' },
    { id: 'approvals', number: '04', label: 'Needs you', path: '/approvals', badge: pendingCount },
    { id: 'context', number: '05', label: 'My Context', path: '/context' },
    { id: 'activity', number: '06', label: 'Activity', path: '/activity' },
  ];

  return (
    <aside
      className={`border-r border-jv-rule bg-jv-bg select-none font-interface flex flex-col justify-between p-4 md:p-5 transition-[width] duration-base ease-editorial overflow-hidden ${
        isMobileDrawer
          ? 'w-full'
          : 'w-16 hover:w-60 focus-within:w-60 group/spine'
      } ${className}`}
    >
      <div className="space-y-8">
        {/* Brand Folio */}
        <div className="space-y-1">
          <div className="font-machine text-[11px] tracking-widest text-jv-muted flex items-center gap-1.5">
            <span>JV</span>
            <span
              className={`transition-opacity duration-fast ease-editorial whitespace-nowrap ${
                isMobileDrawer ? 'inline' : 'hidden group-hover/spine:inline group-focus-within/spine:inline'
              }`}
            >
              / Caseworker
            </span>
          </div>

          <div
            className={`font-display text-lg text-jv-ink tracking-tight transition-opacity duration-fast ease-editorial whitespace-nowrap ${
              isMobileDrawer ? 'block' : 'hidden group-hover/spine:block group-focus-within/spine:block'
            }`}
          >
            Editorial System
          </div>
        </div>

        {/* Contextual Folio Navigation if inside a Mission or Case */}
        {(isMissionDetail || isCaseDetail) && (
          <div className="border-y border-jv-rule py-3 space-y-1 font-interface text-xs">
            {isMissionDetail && (
              <button
                type="button"
                onClick={() => {
                  startViewTransition(() => {
                    navigate('/missions');
                  });
                  onNavigate?.();
                }}
                className="flex items-center gap-2 text-jv-muted hover:text-jv-ink transition-colors w-full text-left"
                title="Return to Missions Index"
                aria-label="Return to Missions Index"
              >
                <ArrowLeft className="w-3.5 h-3.5 shrink-0" />
                <span
                  className={`text-xs whitespace-nowrap ${
                    isMobileDrawer
                      ? 'inline'
                      : 'hidden group-hover/spine:inline group-focus-within/spine:inline'
                  }`}
                >
                  Missions Index
                </span>
              </button>
            )}
            {isCaseDetail && (
              <button
                type="button"
                onClick={() => {
                  startViewTransition(() => {
                    navigate(parentMissionId ? `/missions/${parentMissionId}` : '/missions');
                  });
                  onNavigate?.();
                }}
                className="flex items-center gap-2 text-jv-muted hover:text-jv-ink transition-colors w-full text-left"
                title={parentMissionId ? 'Return to Parent Mission' : 'Return to Missions Index'}
                aria-label={parentMissionId ? 'Return to Parent Mission' : 'Return to Missions Index'}
              >
                <ArrowLeft className="w-3.5 h-3.5 shrink-0" />
                <span
                  className={`text-xs whitespace-nowrap ${
                    isMobileDrawer
                      ? 'inline'
                      : 'hidden group-hover/spine:inline group-focus-within/spine:inline'
                  }`}
                >
                  {parentMissionId ? 'Parent Mission' : 'Missions Index'}
                </span>
              </button>
            )}
          </div>
        )}

        {/* Book Spine Numbered Navigation */}
        <nav className="space-y-1.5" aria-label="Main Navigation">
          {navItems.map((item) => (
            <NavLink
              key={item.id}
              to={item.path}
              end={item.path === '/'}
              onClick={onNavigate}
              title={`${item.number} ${item.label}`}
              aria-label={`${item.number} ${item.label}`}
              className={({ isActive }) =>
                `group flex items-center justify-between py-2 px-2 text-sm tracking-wide transition-colors duration-fast ease-editorial outline-none focus-visible:ring-1 focus-visible:ring-jv-ink ${
                  isActive
                    ? 'bg-jv-ink text-jv-bg font-semibold'
                    : 'text-jv-muted hover:text-jv-ink hover:bg-jv-surface'
                }`
              }
            >
              {({ isActive }) => (
                <>
                  <div className="flex items-center gap-3 min-w-0">
                    <span
                      className={`font-machine text-xs shrink-0 ${
                        isActive ? 'opacity-90' : 'opacity-60'
                      }`}
                    >
                      {isActive ? '■' : item.number}
                    </span>
                    <span
                      className={`text-sm font-medium truncate whitespace-nowrap transition-opacity duration-fast ease-editorial ${
                        isMobileDrawer
                          ? 'inline'
                          : 'hidden group-hover/spine:inline group-focus-within/spine:inline'
                      }`}
                    >
                      {item.label}
                    </span>
                  </div>

                  {item.badge !== undefined && item.badge > 0 && (
                    <span
                      className={`font-machine text-[10px] px-1.5 py-0.2 border shrink-0 transition-opacity duration-fast ease-editorial ${
                        isActive
                          ? 'border-jv-bg bg-jv-bg text-jv-ink'
                          : 'border-jv-ink bg-jv-ink text-jv-bg'
                      } ${
                        isMobileDrawer
                          ? 'inline'
                          : 'hidden group-hover/spine:inline group-focus-within/spine:inline'
                      }`}
                    >
                      <NumberRoll value={item.badge} />
                    </span>
                  )}
                </>
              )}
            </NavLink>
          ))}
        </nav>
      </div>

      {/* System Footprint / Living Editorial Folio */}
      <div className="pt-6 border-t border-jv-rule font-machine text-[10px] text-jv-muted space-y-0.5 whitespace-nowrap">
        <div
          className={`${
            isMobileDrawer ? 'block' : 'hidden group-hover/spine:block group-focus-within/spine:block'
          }`}
        >
          v2.0.2
        </div>
        <div>01–06 Index</div>
      </div>
    </aside>
  );
};
