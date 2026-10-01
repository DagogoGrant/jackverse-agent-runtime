import React, { useState } from 'react';
import { useEvents } from '../hooks/useCaseworker';
import { TactileButton } from '../components/ui/TactileButton';
import { ChevronDown, ChevronRight } from 'lucide-react';

function humanizeActivity(type: string): string {
  switch (type) {
    case 'mission_created':
      return 'Mission initiated and registered';
    case 'mission_transitioned':
      return 'Mission lifecycle status transitioned';
    case 'case_created':
      return 'Case created and registered';
    case 'case_transitioned':
      return 'Case status transitioned';
    case 'case_resolved':
      return 'Case resolved with verified outcome';
    case 'action_proposed':
      return 'Action proposed by runtime';
    case 'action_approval_requested':
      return 'Governance authorization requested';
    case 'action_approved':
      return 'Human authorization recorded';
    case 'action_rejected':
      return 'Human governance rejected proposal';
    case 'action_dispatched':
      return 'Action dispatched to execution';
    case 'claim_proposed':
      return 'Factual claim proposed';
    case 'claim_evaluated':
      return 'Claim evaluated against context vault';
    case 'context_fact_set':
    case 'context_fact_updated':
      return 'Context vault fact recorded';
    case 'opportunity_discovered':
      return 'Prospect discovered and catalogued';
    case 'opportunity_transitioned':
      return 'Opportunity lifecycle transitioned';
    default:
      return type.replace(/_/g, ' ');
  }
}

function formatDateGroup(isoStr: string): string {
  const d = new Date(isoStr);
  const now = new Date();
  const isToday =
    d.getDate() === now.getDate() &&
    d.getMonth() === now.getMonth() &&
    d.getFullYear() === now.getFullYear();
  if (isToday) return 'TODAY';

  const yesterday = new Date(now);
  yesterday.setDate(now.getDate() - 1);
  const isYesterday =
    d.getDate() === yesterday.getDate() &&
    d.getMonth() === yesterday.getMonth() &&
    d.getFullYear() === yesterday.getFullYear();
  if (isYesterday) return 'YESTERDAY';

  return d.toLocaleDateString(undefined, {
    month: 'short',
    day: 'numeric',
    year: 'numeric',
  }).toUpperCase();
}

export const ActivityView: React.FC = () => {
  const [cursor, setCursor] = useState<number | undefined>(undefined);
  const [expandedEvents, setExpandedEvents] = useState<Record<string, boolean>>({});
  const { data: eventsData, isLoading } = useEvents(cursor);

  const items = eventsData?.items || [];
  const nextPosition = eventsData?.next_position;

  const toggleExpand = (id: string) => {
    setExpandedEvents((prev) => ({ ...prev, [id]: !prev[id] }));
  };

  // Group events by date heading
  const groupedEvents: { group: string; events: typeof items }[] = [];
  items.forEach((ev) => {
    const groupName = formatDateGroup(ev.occurred_at);
    let g = groupedEvents.find((x) => x.group === groupName);
    if (!g) {
      g = { group: groupName, events: [] };
      groupedEvents.push(g);
    }
    g.events.push(ev);
  });

  return (
    <div className="space-y-12 font-interface text-jv-ink">
      {/* Editorial Header */}
      <div className="border-b border-jv-rule pb-6 flex flex-col md:flex-row md:items-end justify-between gap-4">
        <div className="space-y-2">
          <div className="font-machine text-xs text-jv-muted uppercase">
            06 / CHRONICLE · IMMUTABLE AUDIT LOG
          </div>
          <h1 className="font-display font-normal text-4xl lg:text-5xl text-jv-ink tracking-tight">
            Activity Log
          </h1>
        </div>

        <div className="font-machine text-xs text-jv-muted">
          STREAM: MONOTONIC ROWID CURSOR
        </div>
      </div>

      {isLoading ? (
        <div className="py-24 text-center font-machine text-xs text-jv-muted">
          STREAMING AUDIT EVENTS...
        </div>
      ) : items.length === 0 ? (
        <div className="py-20 border border-jv-rule text-center space-y-4 bg-jv-surface/20">
          <div className="font-machine text-xs text-jv-muted uppercase tracking-widest">
            ZERO RECORDED AUDIT EVENTS
          </div>
          <p className="text-sm text-jv-muted max-w-sm mx-auto">
            No system or operational domain events recorded in this scope.
          </p>
        </div>
      ) : (
        <div className="space-y-10">
          {groupedEvents.map((group) => (
            <div key={group.group} className="space-y-3">
              <div className="font-machine text-xs text-jv-muted tracking-widest uppercase border-b border-jv-rule pb-1 flex items-center justify-between">
                <span>{group.group}</span>
                <span>{group.events.length} RECORDED</span>
              </div>

              <div className="divide-y divide-jv-rule border-b border-jv-rule font-machine text-xs">
                {group.events.map((ev) => {
                  const isExpanded = !!expandedEvents[ev.event_id];
                  return (
                    <div
                      key={ev.event_id}
                      className="py-4 px-3 -mx-3 hover:bg-jv-surface/60 transition-colors flex flex-col gap-2"
                    >
                      <div className="flex flex-col md:flex-row md:items-baseline justify-between gap-2">
                        <div className="space-y-1 max-w-3xl">
                          <div className="flex items-center gap-3">
                            <span className="text-jv-muted">
                              POS #{ev.position !== undefined ? ev.position : '—'}
                            </span>
                            <span className="font-interface font-medium text-jv-ink text-sm">
                              {humanizeActivity(ev.event_type)}
                            </span>
                            <span className="text-jv-muted text-[11px]">
                              [{ev.aggregate_type} / {ev.aggregate_id.slice(0, 8)} v{ev.aggregate_version}]
                            </span>
                          </div>
                        </div>

                        <div className="flex items-center gap-4 text-jv-muted pl-6 md:pl-0 shrink-0">
                          <span className="text-[11px]">
                            {new Date(ev.occurred_at).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}
                          </span>
                          <button
                            type="button"
                            onClick={() => toggleExpand(ev.event_id)}
                            className="inline-flex items-center gap-1 text-[11px] uppercase text-jv-muted hover:text-jv-ink font-machine"
                          >
                            <span>{isExpanded ? 'Hide' : 'Inspect'}</span>
                            {isExpanded ? (
                              <ChevronDown className="w-3.5 h-3.5" />
                            ) : (
                              <ChevronRight className="w-3.5 h-3.5" />
                            )}
                          </button>
                        </div>
                      </div>

                      {isExpanded && (
                        <div className="p-3 bg-jv-surface border border-jv-rule text-[11px] font-machine text-jv-ink break-all mt-1">
                          <div className="text-jv-muted uppercase mb-1">
                            EVENT TYPE: {ev.event_type}
                          </div>
                          <div>PAYLOAD: {JSON.stringify(ev.payload || {}, null, 2)}</div>
                        </div>
                      )}
                    </div>
                  );
                })}
              </div>
            </div>
          ))}

          {/* Cursor Pagination */}
          <div className="flex items-center justify-between pt-4 font-machine text-xs">
            {cursor !== undefined ? (
              <button
                type="button"
                onClick={() => setCursor(undefined)}
                className="text-jv-muted hover:text-jv-ink underline"
              >
                ← Return to Head
              </button>
            ) : (
              <div />
            )}

            {nextPosition ? (
              <TactileButton
                variant="outline"
                size="sm"
                onClick={() => setCursor(nextPosition)}
              >
                Stream Following Position ({nextPosition}) →
              </TactileButton>
            ) : (
              <span className="text-jv-muted uppercase">
                End of Active Stream
              </span>
            )}
          </div>
        </div>
      )}
    </div>
  );
};
