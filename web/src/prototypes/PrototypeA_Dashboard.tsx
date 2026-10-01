import React, { useState } from 'react';
import { ArrowUpRight } from 'lucide-react';
import { TactileButton } from '../components/ui/TactileButton';
import { TextureBadge } from '../components/ui/TextureBadge';

export const PrototypeA_Dashboard: React.FC = () => {
  const [promptText, setPromptText] = useState('Find me an Agentic AI role in Germany');
  const [showCreateForm, setShowCreateForm] = useState(false);
  const [title, setTitle] = useState('Agentic AI Engineering Role in Germany');
  const [kind, setKind] = useState('opportunity_pursuit');

  const mockMissions = [
    {
      index: '01',
      id: 'm-1',
      title: 'GET AN AGENTIC AI ROLE',
      meta: 'Germany · English-speaking · Agent systems',
      status: 'active',
      opportunitiesCount: 12,
      actionsNeeded: 3,
    },
    {
      index: '02',
      id: 'm-2',
      title: 'FIND HOUSING IN MUNICH',
      meta: 'Max €1200 warm · Central / U-Bahn proximity',
      status: 'active',
      opportunitiesCount: 4,
      actionsNeeded: 0,
    },
    {
      index: '03',
      id: 'm-3',
      title: 'AI RESEARCH FELLOWSHIP FUNDING',
      meta: 'Post-graduate · DAAD / EU Grants',
      status: 'paused',
      opportunitiesCount: 1,
      actionsNeeded: 0,
    },
  ];

  return (
    <div className="space-y-16 font-interface text-paper">
      {/* Dramatic Editorial Home Prompt */}
      <section className="space-y-6 pt-4">
        <div className="font-machine text-xs tracking-widest text-grey-500 uppercase">
          OPERATIONAL INGRESS // INTENT DISPATCH
        </div>

        <div className="space-y-4">
          <label
            htmlFor="home-prompt"
            className="block font-display text-4xl lg:text-6xl text-pure tracking-tight leading-tight select-none"
          >
            What do you want JackVerse to move forward?
          </label>

          <div className="relative border-b-2 border-paper pb-2 pt-2">
            <input
              id="home-prompt"
              type="text"
              value={promptText}
              onChange={(e) => setPromptText(e.target.value)}
              onFocus={() => setShowCreateForm(true)}
              placeholder="State a clear operational mission goal..."
              className="w-full bg-transparent font-interface text-xl lg:text-2xl text-pure placeholder-grey-700 outline-none pr-12"
            />
            <button
              type="button"
              onClick={() => setShowCreateForm(!showCreateForm)}
              className="absolute right-0 bottom-3 text-paper hover:text-pure transition-transform active:scale-95"
              aria-label="Toggle mission creation"
            >
              <ArrowUpRight className="w-8 h-8 stroke-[1.5]" />
            </button>
          </div>
        </div>

        {/* Inline Creation Emergence (No generic modal!) */}
        {showCreateForm && (
          <div className="border border-grey-700 bg-ink p-6 space-y-6 mt-4 transition-all">
            <div className="flex items-center justify-between border-b border-grey-700 pb-3">
              <span className="font-machine text-xs tracking-widest uppercase text-grey-300">
                MISSION // INLINE SPECIFICATION
              </span>
              <button
                type="button"
                onClick={() => setShowCreateForm(false)}
                className="font-machine text-xs text-grey-500 hover:text-pure"
              >
                DISMISS [ESC]
              </button>
            </div>

            <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
              <div className="space-y-2">
                <label className="font-machine text-xs uppercase text-grey-500">
                  Mission Title
                </label>
                <input
                  type="text"
                  value={title}
                  onChange={(e) => setTitle(e.target.value)}
                  className="w-full bg-canvas border border-grey-700 px-3 py-2 text-sm text-pure outline-none focus:border-paper"
                />
              </div>

              <div className="space-y-2">
                <label className="font-machine text-xs uppercase text-grey-500">
                  Classification Kind
                </label>
                <select
                  value={kind}
                  onChange={(e) => setKind(e.target.value)}
                  className="w-full bg-canvas border border-grey-700 px-3 py-2 text-sm text-pure outline-none focus:border-paper font-machine"
                >
                  <option value="opportunity_pursuit">Opportunity Pursuit</option>
                  <option value="capability_expansion">Capability Expansion</option>
                  <option value="dispute_resolution">Dispute Resolution</option>
                  <option value="ongoing_monitoring">Ongoing Monitoring</option>
                </select>
              </div>
            </div>

            <div className="flex items-center justify-end gap-4 pt-2">
              <button
                type="button"
                onClick={() => setShowCreateForm(false)}
                className="font-machine text-xs uppercase tracking-wider text-grey-500 hover:text-paper"
              >
                Cancel
              </button>
              <TactileButton variant="primary" size="md">
                Initialize Mission →
              </TactileButton>
            </div>
          </div>
        )}
      </section>

      {/* Editorial Index of Active Missions (Anti-card row layout) */}
      <section className="space-y-6">
        <div className="flex items-baseline justify-between border-b border-grey-700 pb-2">
          <h2 className="font-display text-2xl text-pure tracking-tight">
            Active Missions Index
          </h2>
          <span className="font-machine text-xs text-grey-500">
            03 TRACKED OBJECTS
          </span>
        </div>

        <div className="divide-y divide-grey-700 border-y border-grey-700">
          {mockMissions.map((m) => (
            <div
              key={m.id}
              className="group py-6 flex flex-col md:flex-row md:items-baseline justify-between gap-4 transition-colors hover:bg-ink/60 px-4 -mx-4 cursor-pointer"
            >
              <div className="space-y-2 max-w-2xl">
                <div className="flex items-center gap-4">
                  <span className="font-machine text-xs text-grey-500">
                    {m.index}
                  </span>
                  <h3 className="font-interface font-semibold text-lg lg:text-xl text-pure tracking-wide group-hover:translate-x-1 transition-transform">
                    {m.title}
                  </h3>
                </div>
                <p className="font-interface text-sm text-grey-300 pl-8">
                  {m.meta}
                </p>
              </div>

              <div className="flex items-center gap-6 pl-8 md:pl-0">
                <TextureBadge status={m.status} />

                <div className="font-machine text-xs text-grey-300 text-right space-y-1">
                  <div>{m.opportunitiesCount} opportunities</div>
                  {m.actionsNeeded > 0 ? (
                    <div className="text-pure font-bold underline decoration-1 underline-offset-4">
                      {m.actionsNeeded} actions need you
                    </div>
                  ) : (
                    <div className="text-grey-500">Autonomous loop</div>
                  )}
                </div>

                <ArrowUpRight className="w-5 h-5 text-grey-500 group-hover:text-pure group-hover:translate-x-0.5 group-hover:-translate-y-0.5 transition-all" />
              </div>
            </div>
          ))}
        </div>
      </section>
    </div>
  );
};
