import React, { useState } from 'react';
import { useEvents } from '../hooks/useCaseworker';
import { TactileButton } from '../components/ui/TactileButton';

export const ActivityView: React.FC = () => {
  const [cursor, setCursor] = useState<number | undefined>(undefined);
  const { data: eventsData, isLoading } = useEvents(cursor);

  const items = eventsData?.items || [];
  const nextPosition = eventsData?.next_position;

  return (
    <div className="space-y-12 font-interface text-paper">
      {/* Header */}
      <div className="border-b border-grey-700 pb-6 flex flex-col md:flex-row md:items-end justify-between gap-4">
        <div className="space-y-2">
          <div className="font-machine text-xs text-grey-500 uppercase">
            AUDIT STREAM // IMMUTABLE EVENT LEDGER
          </div>
          <h1 className="font-display text-4xl text-pure tracking-tight">
            Activity Log
          </h1>
        </div>

        <div className="font-machine text-xs text-grey-500">
          STREAM: MONOTONIC ROWID CURSOR
        </div>
      </div>

      {isLoading ? (
        <div className="py-24 text-center font-machine text-xs text-grey-500">
          STREAMING AUDIT EVENTS...
        </div>
      ) : items.length === 0 ? (
        <div className="py-20 border border-grey-700 text-center space-y-4 bg-ink/20">
          <div className="font-machine text-xs text-grey-500 uppercase tracking-widest">
            ZERO RECORDED AUDIT EVENTS
          </div>
          <p className="text-sm text-grey-300 max-w-sm mx-auto">
            No system or operational domain events recorded in this scope.
          </p>
        </div>
      ) : (
        <div className="space-y-6">
          <div className="divide-y divide-grey-700 border-y border-grey-700 font-machine text-xs">
            {items.map((ev) => (
              <div
                key={ev.event_id}
                className="py-4 px-3 -mx-3 hover:bg-ink/50 transition-colors flex flex-col md:flex-row md:items-baseline justify-between gap-3"
              >
                <div className="space-y-1 max-w-2xl">
                  <div className="flex items-center gap-3">
                    <span className="text-grey-500">
                      POS #{ev.position !== undefined ? ev.position : '—'}
                    </span>
                    <span className="text-pure font-bold">{ev.event_type}</span>
                    <span className="text-grey-500">
                      [{ev.aggregate_type} / {ev.aggregate_id.slice(0, 8)} v{ev.aggregate_version}]
                    </span>
                  </div>
                  <div className="text-grey-300 break-all pl-12 text-[11px]">
                    PAYLOAD: {JSON.stringify(ev.payload || {})}
                  </div>
                </div>

                <div className="text-grey-500 pl-12 md:pl-0 shrink-0">
                  {new Date(ev.timestamp).toLocaleString()}
                </div>
              </div>
            ))}
          </div>

          {/* Cursor Pagination */}
          <div className="flex items-center justify-between pt-4">
            {cursor !== undefined ? (
              <button
                type="button"
                onClick={() => setCursor(undefined)}
                className="font-machine text-xs text-grey-500 hover:text-pure underline"
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
              <span className="font-machine text-xs text-grey-500 uppercase">
                End of Active Stream
              </span>
            )}
          </div>
        </div>
      )}
    </div>
  );
};
