export interface EventLike {
  event_type?: string;
  payload?: Record<string, unknown> | null;
  aggregate_type?: string;
  aggregate_id?: string;
  occurred_at?: string;
}

function humanizeStatus(status: unknown): string {
  if (typeof status !== 'string') return '';
  return status
    .replace(/[._]/g, ' ')
    .replace(/\b\w/g, (c) => c.toUpperCase());
}

/**
 * Transforms technical or dotted JackVerse domain events into dignified,
 * human-centered editorial phrasing without claiming external execution
 * unless backend state explicitly records it.
 */
export function humanizeEvent(eventOrType: EventLike | string): string {
  const event: EventLike =
    typeof eventOrType === 'string' ? { event_type: eventOrType } : eventOrType || {};

  const type = event.event_type || '';
  const payload = (event.payload || {}) as Record<string, unknown>;

  switch (type) {
    case 'mission.created':
    case 'mission_created':
      return 'Mission created';

    case 'mission.status_changed':
    case 'mission_transitioned': {
      const newStatus = payload.new_status;
      if (newStatus) {
        return `Mission moved to ${humanizeStatus(newStatus)}`;
      }
      return 'Mission status updated';
    }

    case 'mission.archived':
    case 'mission_archived':
      return 'Mission archived';

    case 'mission.restored':
    case 'mission_restored':
      return 'Mission restored from archive';

    case 'case.created':
    case 'case_created':
      return 'Case created';

    case 'case.status_changed':
    case 'case_transitioned': {
      const newStatus = payload.new_status;
      if (newStatus) {
        return `Case moved to ${humanizeStatus(newStatus)}`;
      }
      return 'Case status updated';
    }

    case 'case.resolved':
    case 'case_resolved':
      return 'Case resolved with verified outcome';

    case 'approval.requested':
    case 'action_approval_requested':
      return 'Your approval was requested';

    case 'approval.decided': {
      const decision = payload.decision;
      if (decision === 'approved') return 'You approved an action';
      if (decision === 'rejected') return 'Action rejected';
      return 'Governance decision recorded';
    }

    case 'approval.approved':
    case 'action_approved':
      return 'You approved an action';

    case 'approval.rejected':
    case 'action_rejected':
      return 'Action rejected';

    case 'action.proposed':
    case 'action_proposed':
      return 'Action proposed by runtime';

    case 'action.status_changed': {
      const newStatus = payload.new_status;
      if (newStatus) {
        return `Action moved to ${humanizeStatus(newStatus)}`;
      }
      return 'Action status updated';
    }

    case 'action_dispatched':
    case 'action.dispatched':
      return 'Action dispatched to execution';

    case 'claim.proposed':
    case 'claim_proposed':
      return 'Claim asserted';

    case 'claim.supported':
      return 'Claim verified as supported';

    case 'claim.unsupported':
      return 'Claim marked unsupported';

    case 'claim.rejected':
      return 'Claim rejected';

    case 'claim.evaluated':
    case 'claim_evaluated':
      return 'Claim evaluated';

    case 'context.fact_superseded':
    case 'context_fact_updated':
    case 'context.fact_updated':
      return 'Profile information updated';

    case 'context.fact_recorded':
    case 'context_fact.created':
    case 'context_fact_set':
    case 'context.fact_created':
      return 'Profile information recorded';

    case 'context.fact_verified':
      return 'Profile information verified';

    case 'context.fact_rejected':
      return 'Profile fact rejected';

    case 'context.access_granted':
      return 'Vault access authorized';

    case 'context.access_denied':
      return 'Vault access denied';

    case 'opportunity.discovered':
    case 'opportunity_discovered':
      return 'Prospect discovered and catalogued';

    case 'opportunity.status_changed':
    case 'opportunity_transitioned': {
      const newStatus = payload.new_status;
      if (newStatus) {
        return `Opportunity moved to ${humanizeStatus(newStatus)}`;
      }
      return 'Opportunity status updated';
    }

    default: {
      if (!type) return 'System event';
      // Gracefully format unknown dotted or underscored event types
      return type
        .replace(/[._]/g, ' ')
        .trim()
        .replace(/\b\w/g, (c) => c.toUpperCase());
    }
  }
}

/**
 * Format an ISO timestamp string into a concise editorial 24h clock: HH:MM
 */
export function formatEventTime(isoStr?: string): string {
  if (!isoStr) return '--:--';
  try {
    const d = new Date(isoStr);
    if (isNaN(d.getTime())) return '--:--';
    return d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', hour12: false });
  } catch {
    return '--:--';
  }
}
