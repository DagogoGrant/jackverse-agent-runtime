import React, { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { ArrowUpRight } from 'lucide-react';
import { useOpportunities } from '../hooks/useCaseworker';
import { TextureBadge } from '../components/ui/TextureBadge';

export const OpportunitiesView: React.FC = () => {
  const navigate = useNavigate();
  const { data: opportunities, isLoading } = useOpportunities();
  const [filter, setFilter] = useState<string>('all');

  const filtered = (opportunities || []).filter((o) => {
    if (filter === 'all') return true;
    return o.status === filter;
  });

  return (
    <div className="space-y-12 font-interface text-paper">
      {/* Header */}
      <div className="border-b border-grey-700 pb-6 flex flex-col md:flex-row md:items-end justify-between gap-4">
        <div className="space-y-2">
          <div className="font-machine text-xs text-grey-500 uppercase">
            PROSPECTS // DISCOVERY STREAM
          </div>
          <h1 className="font-display text-4xl text-pure tracking-tight">
            Opportunity Inbox
          </h1>
        </div>

        {/* Status Filters */}
        <div className="flex items-center gap-2 font-machine text-xs">
          {(['all', 'discovered', 'evaluating', 'shortlisted', 'archived'] as const).map((s) => (
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
          SYNCHRONIZING OPPORTUNITY FEED...
        </div>
      ) : filtered.length === 0 ? (
        <div className="py-20 border border-grey-700 text-center space-y-4 bg-ink/20">
          <div className="font-machine text-xs text-grey-500 uppercase tracking-widest">
            ZERO DISCOVERED OPPORTUNITIES
          </div>
          <p className="text-sm text-grey-300 max-w-sm mx-auto">
            No opportunities registered in this view. Active discovery monitors will populate
            this inbox in subsequent phases.
          </p>
        </div>
      ) : (
        <div className="divide-y divide-grey-700 border-y border-grey-700">
          {filtered.map((opp, idx) => (
            <div
              key={opp.opportunity_id}
              onClick={() => navigate(`/opportunities/${opp.opportunity_id}`)}
              className="py-6 px-4 -mx-4 group flex flex-col md:flex-row md:items-baseline justify-between gap-4 hover:bg-ink/60 transition-colors cursor-pointer"
            >
              <div className="space-y-2 max-w-2xl">
                <div className="flex items-center gap-3">
                  <span className="font-machine text-xs text-grey-500">
                    {String(idx + 1).padStart(2, '0')}
                  </span>
                  <span className="font-machine text-xs uppercase text-grey-500">
                    {opp.organization}
                  </span>
                  <h2 className="font-interface font-semibold text-lg text-pure group-hover:translate-x-1 transition-transform">
                    {opp.title}
                  </h2>
                </div>
                <p className="font-interface text-sm text-grey-300 pl-16 line-clamp-1">
                  {opp.description}
                </p>
              </div>

              <div className="flex items-center gap-6 pl-16 md:pl-0">
                <TextureBadge status={opp.status} />
                <span className="font-machine text-xs text-grey-500 uppercase">
                  {opp.opportunity_type}
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
