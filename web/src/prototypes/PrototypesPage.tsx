import React, { useState } from 'react';
import { PrototypeA_Dashboard } from './PrototypeA_Dashboard';
import { PrototypeB_MissionWorkspace } from './PrototypeB_MissionWorkspace';
import { PrototypeC_NeedsYou } from './PrototypeC_NeedsYou';

export const PrototypesPage: React.FC<{ initialTab?: 'a' | 'b' | 'c' }> = ({ initialTab = 'a' }) => {
  const [tab, setTab] = useState<'a' | 'b' | 'c'>(initialTab);

  return (
    <div className="space-y-8 font-interface">
      {/* Prototype Switcher Bar */}
      <div className="flex items-center justify-between border-b border-grey-700 pb-4">
        <div className="font-machine text-xs text-grey-500 uppercase">
          VISUAL PROTOTYPE AUDIT & CRITIQUE HARNESS
        </div>
        <div className="flex items-center gap-2 font-machine text-xs">
          <button
            onClick={() => setTab('a')}
            className={`px-3 py-1 border ${
              tab === 'a'
                ? 'border-paper bg-paper text-canvas font-bold'
                : 'border-grey-700 text-grey-300 hover:text-pure'
            }`}
          >
            01 DASHBOARD
          </button>
          <button
            onClick={() => setTab('b')}
            className={`px-3 py-1 border ${
              tab === 'b'
                ? 'border-paper bg-paper text-canvas font-bold'
                : 'border-grey-700 text-grey-300 hover:text-pure'
            }`}
          >
            02 MISSION DOSSIER
          </button>
          <button
            onClick={() => setTab('c')}
            className={`px-3 py-1 border ${
              tab === 'c'
                ? 'border-paper bg-paper text-canvas font-bold'
                : 'border-grey-700 text-grey-300 hover:text-pure'
            }`}
          >
            03 NEEDS YOU
          </button>
        </div>
      </div>

      {/* Render Active Prototype */}
      <div>
        {tab === 'a' && <PrototypeA_Dashboard />}
        {tab === 'b' && <PrototypeB_MissionWorkspace />}
        {tab === 'c' && <PrototypeC_NeedsYou />}
      </div>
    </div>
  );
};
