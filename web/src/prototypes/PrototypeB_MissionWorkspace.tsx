import React from 'react';
import { ArrowUpRight } from 'lucide-react';
import { TextureBadge } from '../components/ui/TextureBadge';
import { TactileButton } from '../components/ui/TactileButton';

export const PrototypeB_MissionWorkspace: React.FC = () => {
  const mockCases = [
    {
      id: 'c-101',
      title: 'Manex / Agent Platform Engineer',
      type: 'Job Application',
      status: 'investigating',
      deadline: '14 OCT 2026',
      actionsCount: 2,
    },
    {
      id: 'c-102',
      title: 'Exxeta / Forward-Deployed AI Engineer',
      type: 'Job Application',
      status: 'awaiting_approval',
      deadline: '20 OCT 2026',
      actionsCount: 1,
    },
    {
      id: 'c-103',
      title: 'Bavarian AI Lab / Systems Fellow',
      type: 'Grant Submission',
      status: 'waiting_external',
      deadline: '01 NOV 2026',
      actionsCount: 0,
    },
  ];

  const mockActivity = [
    { time: '22:14:02', text: 'Opportunity shortlisted: Exxeta Forward-Deployed AI' },
    { time: '21:08:45', text: 'Claim proposed: "4 years production agent experience"' },
    { time: '18:41:10', text: 'Mission initialized under protocol v1' },
  ];

  return (
    <div className="space-y-12 font-interface text-paper">
      {/* Dossier Header */}
      <div className="border-b border-grey-700 pb-8 space-y-4">
        <div className="flex items-center justify-between font-machine text-xs text-grey-500">
          <div>DOSSIER // JV-M001</div>
          <TextureBadge status="active" />
        </div>

        <h1 className="font-display text-4xl lg:text-5xl text-pure tracking-tight leading-tight">
          Get an Agentic AI Role in Germany
        </h1>

        <div className="flex flex-wrap items-center gap-6 font-machine text-xs text-grey-300 pt-2">
          <span>KIND: OPPORTUNITY_PURSUIT</span>
          <span className="text-grey-700">|</span>
          <span>SINCE: 30 SEP 2026</span>
          <span className="text-grey-700">|</span>
          <span>CASES: 03 ACTIVE</span>
        </div>
      </div>

      {/* Two-Column Asymmetric Dossier Body */}
      <div className="grid grid-cols-1 lg:grid-cols-12 gap-12">
        {/* Left column: Active Cases List (8 cols) */}
        <div className="lg:col-span-8 space-y-8">
          <div className="flex items-baseline justify-between border-b border-grey-700 pb-2">
            <h2 className="font-display text-2xl text-pure tracking-tight">
              Active Cases Dossier
            </h2>
            <TactileButton variant="outline" size="sm">
              + New Case
            </TactileButton>
          </div>

          <div className="divide-y divide-grey-700 border-y border-grey-700">
            {mockCases.map((c) => (
              <div
                key={c.id}
                className="py-5 group flex items-baseline justify-between gap-4 px-3 -mx-3 hover:bg-ink/60 transition-colors cursor-pointer"
              >
                <div className="space-y-1.5">
                  <div className="flex items-center gap-3">
                    <span className="font-machine text-xs text-grey-500">{c.id}</span>
                    <span className="font-interface font-medium text-base text-pure group-hover:translate-x-1 transition-transform">
                      {c.title}
                    </span>
                  </div>
                  <div className="font-machine text-xs text-grey-500 pl-11">
                    {c.type} · Deadline {c.deadline}
                  </div>
                </div>

                <div className="flex items-center gap-4">
                  <TextureBadge status={c.status} />
                  <ArrowUpRight className="w-4 h-4 text-grey-500 group-hover:text-pure transition-colors" />
                </div>
              </div>
            ))}
          </div>

          {/* Goals & Success Criteria */}
          <div className="border border-grey-700 p-6 space-y-4 bg-ink/30">
            <div className="font-machine text-xs tracking-widest uppercase text-grey-500">
              OPERATIONAL PARAMETERS
            </div>
            <p className="text-sm text-grey-300 leading-relaxed">
              Targeting full-time or high-impact contractor roles in Germany requiring
              autonomous agent frameworks (LangGraph, AutoGen, custom ReAct runtimes),
              Model Context Protocol (MCP) integrations, and production orchestration.
            </p>
          </div>
        </div>

        {/* Right column: Recent Activity Stream (4 cols) */}
        <div className="lg:col-span-4 space-y-6">
          <div className="border-b border-grey-700 pb-2">
            <h2 className="font-display text-xl text-pure tracking-tight">
              Recent Stream
            </h2>
          </div>

          <div className="space-y-4 font-machine text-xs">
            {mockActivity.map((a, idx) => (
              <div key={idx} className="pb-3 border-b border-grey-700/50 space-y-1">
                <div className="text-grey-500">{a.time}</div>
                <div className="text-grey-300 leading-normal">{a.text}</div>
              </div>
            ))}
          </div>
        </div>
      </div>
    </div>
  );
};
