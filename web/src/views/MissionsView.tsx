import React, { useState } from 'react';
import { useNavigate, Link } from 'react-router-dom';
import { ArrowUpRight } from 'lucide-react';
import { useMissions } from '../hooks/useCaseworker';
import { TextureBadge } from '../components/ui/TextureBadge';
import { TactileButton } from '../components/ui/TactileButton';
import { startViewTransition } from '../utils/transitions';

export const MissionsView: React.FC = () => {
  const navigate = useNavigate();
  const { data: missions, isLoading } = useMissions();
  const [filter, setFilter] = useState<string>('all');

  const filteredMissions = (missions || []).filter((m) => {
    if (filter === 'all') return true;
    return m.status === filter;
  });

  return (
    <div className="space-y-12 font-interface text-jv-ink">
      {/* Editorial Header */}
      <div className="border-b border-jv-rule pb-6 flex flex-col md:flex-row md:items-end justify-between gap-4">
        <div className="space-y-2">
          <div className="font-machine text-[11px] text-jv-muted uppercase tracking-widest">
            02 / FOLIO · MISSIONS
          </div>
          <h1 className="font-display text-4xl sm:text-5xl text-jv-ink tracking-tight">
            Missions
          </h1>
        </div>

        {/* Status Filters */}
        <div className="flex items-center gap-1.5 font-machine text-xs">
          {(['all', 'active', 'paused', 'completed'] as const).map((s) => (
            <button
              key={s}
              type="button"
              onClick={() => setFilter(s)}
              className={`px-3 py-1 uppercase border text-xs tracking-wider transition-colors duration-fast ease-editorial ${
                filter === s
                  ? 'border-jv-ink bg-jv-ink text-jv-bg font-bold'
                  : 'border-jv-rule text-jv-muted hover:text-jv-ink hover:border-jv-rule-strong bg-jv-surface'
              }`}
            >
              {s}
            </button>
          ))}
        </div>
      </div>

      {isLoading ? (
        <div className="py-16 text-center font-machine text-xs text-jv-muted">
          SYNCHRONIZING MISSIONS...
        </div>
      ) : filteredMissions.length === 0 ? (
        <div className="py-24 text-center space-y-4 max-w-md mx-auto">
          <div className="font-display text-2xl text-jv-ink tracking-tight">
            NO MISSIONS YET
          </div>
          <p className="text-sm text-jv-ink-soft leading-relaxed">
            Tell JackVerse what you want to move forward. Your active missions will appear here.
          </p>
          <div className="pt-2">
            <TactileButton
              variant="primary"
              size="md"
              onClick={() => {
                startViewTransition(() => {
                  navigate('/');
                });
              }}
            >
              Start a mission →
            </TactileButton>
          </div>
        </div>
      ) : (
        <div className="divide-y divide-jv-rule border-b border-jv-rule">
          {filteredMissions.map((m, idx) => (
            <Link
              key={m.mission_id}
              to={`/missions/${m.mission_id}`}
              onClick={(e) => {
                e.preventDefault();
                startViewTransition(() => {
                  navigate(`/missions/${m.mission_id}`);
                });
              }}
              className="py-6 px-4 -mx-4 group flex flex-col md:flex-row md:items-baseline justify-between gap-4 hover:bg-jv-surface transition-colors cursor-pointer focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-jv-ink focus-visible:bg-jv-surface"
            >
              <div className="space-y-1.5 max-w-2xl">
                <div className="flex items-baseline gap-4">
                  <span className="font-machine text-xs text-jv-muted">
                    {String(idx + 1).padStart(2, '0')}
                  </span>
                  <span className="font-machine text-xs text-jv-muted">
                    {m.mission_id.slice(0, 8)}
                  </span>
                  <h2 className="font-interface font-medium text-lg sm:text-xl text-jv-ink group-hover:translate-x-1 transition-transform duration-fast ease-editorial">
                    {m.title}
                  </h2>
                </div>
                <p className="font-interface text-sm text-jv-ink-soft pl-14 line-clamp-2">
                  {m.goal}
                </p>
              </div>

              <div className="flex items-center gap-6 pl-14 md:pl-0 shrink-0">
                <TextureBadge status={m.status} />
                <span className="font-machine text-xs text-jv-muted uppercase">
                  {m.kind.replace(/_/g, ' ')}
                </span>
                <ArrowUpRight className="w-5 h-5 text-jv-muted group-hover:text-jv-ink group-hover:translate-x-0.5 group-hover:-translate-y-0.5 transition-transform duration-fast ease-editorial" />
              </div>
            </Link>
          ))}
        </div>
      )}
    </div>
  );
};
