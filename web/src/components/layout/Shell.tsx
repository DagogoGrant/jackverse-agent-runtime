import React, { useState, useEffect } from 'react';
import { NavRail } from './NavRail';
import { isDevAuthEnabled, getDevUser, setDevUser } from '../../api/client';
import { AgentGlyph } from '../ui/AgentGlyph';
import { useDevUserSync } from '../../hooks/useCaseworker';

interface ShellProps {
  children: React.ReactNode;
}

export const Shell: React.FC<ShellProps> = ({ children }) => {
  useDevUserSync();
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
    <div className="flex min-h-screen bg-canvas text-paper">
      {/* Numbered left rail - Desktop */}
      <NavRail className="hidden md:flex w-56 shrink-0 border-r min-h-screen" />

      {/* Main content area */}
      <div className="flex-1 flex flex-col min-w-0">
        {/* Top metadata status bar */}
        <header className="h-12 border-b border-grey-700 px-4 md:px-8 flex items-center justify-between font-machine text-xs text-grey-500 bg-canvas">
          <div className="flex items-center gap-3 md:gap-6">
            {/* Mobile Menu Trigger */}
            <button
              type="button"
              onClick={() => setMobileMenuOpen(!mobileMenuOpen)}
              className="md:hidden flex items-center gap-1.5 px-2 py-0.5 border border-grey-700 hover:border-paper text-paper text-[11px] uppercase font-machine tracking-wider"
              aria-label="Toggle Navigation Menu"
            >
              <span>{mobileMenuOpen ? '✕ CLOSE' : '☰ 01-06'}</span>
            </button>

            <span className="text-grey-300">JACKVERSE / OPERATING PROTOCOL</span>
            <span className="hidden sm:inline text-grey-700">|</span>
            <span className="hidden sm:inline">LOC: PASSIVE</span>
          </div>

          <div className="flex items-center gap-3 md:gap-6">
            {/* Diagnostic Dev Switcher - ONLY when VITE_JACKVERSE_DEV_AUTH is true */}
            {devAuth && (
              <div className="flex items-center gap-2 px-2 py-0.5 border border-grey-700 bg-ink text-[11px]">
                <span className="text-grey-500 font-bold uppercase hidden sm:inline">DEV IDENTITY:</span>
                {(['alice', 'bob', 'charlie'] as const).map((u) => (
                  <button
                    key={u}
                    onClick={() => handleSwitchUser(u)}
                    className={`px-1.5 uppercase transition-colors ${
                      currentUser === u
                        ? 'bg-paper text-canvas font-bold'
                        : 'text-grey-500 hover:text-paper'
                    }`}
                  >
                    {u}
                  </button>
                ))}
              </div>
            )}

            <div className="flex items-center gap-2">
              <AgentGlyph size="sm" />
              <span>{timeStr || '00:00:00'}</span>
            </div>
          </div>
        </header>

        {/* Mobile Navigation Drawer Overlay */}
        {mobileMenuOpen && (
          <div className="md:hidden fixed inset-0 top-12 z-50 bg-canvas border-b border-grey-700 p-6 flex flex-col justify-between overflow-y-auto">
            <NavRail
              className="w-full flex-1"
              onNavigate={() => setMobileMenuOpen(false)}
            />
          </div>
        )}

        {/* Scrollable Canvas */}
        <main className="flex-1 p-4 sm:p-6 md:p-8 lg:p-12 max-w-7xl w-full mx-auto overflow-y-auto">
          {children}
        </main>
      </div>
    </div>
  );
};
