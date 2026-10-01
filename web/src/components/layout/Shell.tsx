import React, { useState, useEffect } from 'react';
import { NavRail } from './NavRail';
import { isDevAuthEnabled, getDevUser, setDevUser } from '../../api/client';
import { AgentGlyph } from '../ui/AgentGlyph';
import { ThemeSwitch } from '../ui/ThemeSwitch';
import { AmbientCanvas } from '../ui/AmbientCanvas';
import { useDevUserSync, useApprovals } from '../../hooks/useCaseworker';

interface ShellProps {
  children: React.ReactNode;
}

export const Shell: React.FC<ShellProps> = ({ children }) => {
  useDevUserSync();
  const { data: approvals } = useApprovals();
  const pendingApprovalsCount = approvals?.filter((a) => a.status === 'pending').length || 0;

  const [currentUser, setCurrentUserState] = useState(getDevUser());
  const [mobileMenuOpen, setMobileMenuOpen] = useState(false);
  const [timeStr, setTimeStr] = useState('');
  const devAuth = isDevAuthEnabled();

  useEffect(() => {
    const updateTime = () => {
      const now = new Date();
      setTimeStr(
        now.toLocaleTimeString('en-US', {
          hour12: false,
          hour: '2-digit',
          minute: '2-digit',
          second: '2-digit',
        })
      );
    };
    updateTime();
    const interval = setInterval(updateTime, 1000);
    return () => clearInterval(interval);
  }, []);

  const handleSwitchUser = (user: string) => {
    setDevUser(user);
    setCurrentUserState(user);
  };

  return (
    <div className="flex min-h-screen bg-jv-bg text-jv-ink relative">
      {/* Restrained Ambient Visual Signature */}
      <AmbientCanvas hasPendingApprovals={pendingApprovalsCount > 0} />

      {/* Contextual Editorial Navigation Spine - Desktop */}
      <NavRail className="hidden md:flex shrink-0 min-h-screen z-20" />

      {/* Main content area */}
      <div className="flex-1 flex flex-col min-w-0 z-10">
        {/* Top Editorial Status Bar */}
        <header className="h-14 border-b border-jv-rule px-4 md:px-8 flex items-center justify-between font-machine text-xs text-jv-muted bg-jv-bg/90 backdrop-blur-sm sticky top-0 z-20">
          <div className="flex items-center gap-3 md:gap-6">
            {/* Mobile Menu Trigger */}
            <button
              type="button"
              onClick={() => setMobileMenuOpen(!mobileMenuOpen)}
              className="md:hidden flex items-center gap-1.5 px-2 py-1 border border-jv-rule hover:border-jv-ink text-jv-ink text-[11px] uppercase font-machine tracking-wider bg-jv-surface active:scale-tactile"
              aria-label="Toggle Navigation Menu"
            >
              <span>{mobileMenuOpen ? '✕ CLOSE' : '☰ 01-06'}</span>
            </button>

            <span className="text-jv-ink font-medium tracking-wide">
              JACKVERSE<span className="hidden sm:inline"> / LIVING EDITORIAL</span>
            </span>
            <span className="hidden sm:inline text-jv-rule-strong">|</span>
            <span className="hidden sm:inline text-jv-muted">
              PASSIVE · LOCAL RUNTIME
            </span>
          </div>

          <div className="flex items-center gap-2 sm:gap-3 md:gap-5">
            {/* Authored Theme Switcher */}
            <ThemeSwitch />

            {/* Diagnostic Dev Switcher - ONLY when VITE_JACKVERSE_DEV_AUTH is true */}
            {devAuth && (
              <div className="flex items-center gap-0.5 sm:gap-1 px-1.5 sm:px-2 py-0.5 border border-jv-rule bg-jv-surface text-[10px] sm:text-[11px]">
                <span className="text-jv-muted uppercase font-machine hidden sm:inline mr-1">
                  DEV:
                </span>
                {(['alice', 'bob', 'charlie'] as const).map((u) => (
                  <button
                    key={u}
                    onClick={() => handleSwitchUser(u)}
                    className={`px-1 sm:px-1.5 uppercase font-machine transition-colors ${
                      currentUser === u
                        ? 'bg-jv-ink text-jv-bg font-bold'
                        : 'text-jv-muted hover:text-jv-ink'
                    }`}
                  >
                    {u}
                  </button>
                ))}
              </div>
            )}

            {/* System activity glyph and clock */}
            <div className="flex items-center gap-2 text-jv-muted">
              <AgentGlyph size="sm" ariaLabel="System activity indicator" />
              <span className="font-machine text-[11px]">{timeStr || '00:00:00'}</span>
            </div>
          </div>
        </header>

        {/* Mobile Navigation Drawer Overlay */}
        {mobileMenuOpen && (
          <div className="md:hidden fixed inset-0 top-14 z-50 bg-jv-bg border-b border-jv-rule p-6 flex flex-col justify-between overflow-y-auto">
            <NavRail
              className="w-full flex-1"
              isMobileDrawer
              onNavigate={() => setMobileMenuOpen(false)}
            />
          </div>
        )}

        {/* Scrollable Editorial Main Stage */}
        <main className="flex-1 p-4 sm:p-6 md:p-8 lg:p-12 max-w-7xl w-full mx-auto overflow-y-auto">
          {children}
        </main>
      </div>
    </div>
  );
};
