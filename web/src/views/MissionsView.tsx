import React, { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { ArrowUpRight } from 'lucide-react';
import { useMissions } from '../hooks/useCaseworker';
import { TextureBadge } from '../components/ui/TextureBadge';
import { TactileButton } from '../components/ui/TactileButton';

export const MissionsView: React.FC = () => {
  const navigate = useNavigate();
  const { data: missions, isLoading } = useMissions();
  const [filter, setFilter] = useState<string>('all');

  const filteredMissions = (missions || []).filter((m) => {
    if (filter === 'all') return true;
    return m.status === filter;
  });

  return (
    <div className="space-y-12 font-interface text-paper">
      {/* Header */}
      <div className="border-b border-grey-700 pb-6 flex flex-col md:flex-row md:items-end justify-between gap-4">
        <div className="space-y-2">
          <div className="font-machine text-xs text-grey-500 uppercase">
            REGISTRY // DOSSIER INDEX
          </div>
          <h1 className="font-display text-4xl text-pure tracking-tight">
            Operational Missions
          </h1>
        </div>

        {/* Status Filters */}
        <div className="flex items-center gap-2 font-machine text-xs">
          {(['all', 'active', 'paused', 'completed'] as const).map((s) => (
            <button
              key={s}
              onClick={() => setFilter(s)}
              className={`px-3 py-1 uppercase border transition-colors ${
                filter === s
                  ? 'border-paper bg-paper text-canvas font-bold'
                  : 'border-grey-700 text-grey-300 hover:text-pure'
              }`}
            >
              {s}
            </button>
          ))}
        </div>
      </div>

      {isLoading ? (
        <div className="py-16 text-center font-machine text-xs text-grey-500">
          SYNCHRONIZING REPOSITORY DOSSIERS...
        </div>
      ) : filteredMissions.length === 0 ? (
        <div className="py-20 border border-grey-700 text-center space-y-4 bg-ink/20">
          <div className="font-machine text-xs text-grey-500 uppercase tracking-widest">
            ZERO MATCHING MISSIONS
          </div>
          <p className="text-sm text-grey-300 max-w-sm mx-auto">
            No missions found matching the current criteria.
          </p>
          <div className="pt-2">
            <TactileButton variant="primary" size="md" onClick={() => navigate('/')}>
              Initialize Mission on Home →
            </TactileButton>
          </div>
        </div>
      ) : (
        <div className="divide-y divide-grey-700 border-y border-grey-700">
          {filteredMissions.map((m, idx) => (
            <div
              key={m.mission_id}
              onClick={() => navigate(`/missions/${m.mission_id}`)}
              className="py-6 px-4 -mx-4 group flex flex-col md:flex-row md:items-baseline justify-between gap-4 hover:bg-ink/60 transition-colors cursor-pointer"
            >
              <div className="space-y-2 max-w-2xl">
                <div className="flex items-center gap-3">
                  <span className="font-machine text-xs text-grey-500">
                    {String(idx + 1).padStart(2, '0')}
                  </span>
                  <span className="font-machine text-xs text-grey-500">
                    {m.mission_id.slice(0, 8)}
                  </span>
                  <h2 className="font-interface font-semibold text-lg text-pure group-hover:translate-x-1 transition-transform">
                    {m.title}
                  </h2>
                </div>
                <p className="font-interface text-sm text-grey-300 pl-16 line-clamp-2">
                  {m.goal}
                </p>
              </div>

              <div className="flex items-center gap-6 pl-16 md:pl-0">
                <TextureBadge status={m.status} />
                <span className="font-machine text-xs text-grey-500 uppercase">
                  {m.kind}
                </span>
                <ArrowUpRight className="w-5 h-5 text-grey-500 group-hover:text-pure group-hover:translate-x-0.5 group-hover:-translate-y-0.5 transition-all" />
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
};
