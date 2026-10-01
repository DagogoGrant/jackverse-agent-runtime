import React, { useState } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import { ArrowUpRight, AlertTriangle } from 'lucide-react';
import {
  useMission,
  useMissionCases,
  useCreateCase,
  useTransitionMission,
  useEvents,
} from '../hooks/useCaseworker';
import { TextureBadge } from '../components/ui/TextureBadge';
import { TactileButton } from '../components/ui/TactileButton';
import { startViewTransition } from '../utils/transitions';

function humanizeEventType(eventType: string): string {
  const map: Record<string, string> = {
    'mission.created': 'Mission created',
    'mission.status_changed': 'Mission status changed',
    'case.created': 'Case created',
    'case.status_changed': 'Case status changed',
    'claim.proposed': 'Claim asserted',
    'claim.evaluated': 'Claim evaluated',
    'action.created': 'Action planned',
    'action.status_changed': 'Action status changed',
    'approval.requested': 'Approval requested',
    'approval.approved': 'You approved an action',
    'approval.rejected': 'Action rejected',
    'context.fact_recorded': 'Profile fact recorded',
    'context.fact_verified': 'Profile fact verified',
    'context.fact_superseded': 'Profile fact updated',
  };
  return map[eventType] || eventType.replace(/[._]/g, ' ').replace(/\b\w/g, (c) => c.toUpperCase());
}

export const MissionDetailView: React.FC = () => {
  const { missionId } = useParams<{ missionId: string }>();
  const navigate = useNavigate();

  const { data: missionData, isLoading: missionLoading, error: missionError, refetch: refetchMission } = useMission(missionId);
  const { data: cases, isLoading: casesLoading } = useMissionCases(missionId);
  const { data: eventsData } = useEvents();

  const createCase = useCreateCase();
  const transitionMission = useTransitionMission();

  const [showCaseForm, setShowCaseForm] = useState(false);
  const [caseTitle, setCaseTitle] = useState('');
  const [caseGoal, setCaseGoal] = useState('');
  const [caseType, setCaseType] = useState('job_application');
  const [concurrencyNotice, setConcurrencyNotice] = useState<string | null>(null);

  if (missionLoading) {
    return (
      <div className="py-24 text-center font-machine text-xs text-jv-muted">
        LOADING MISSION DOSSIER...
      </div>
    );
  }

  if (missionError || !missionData?.mission) {
    return (
      <div className="py-24 text-center space-y-4 font-interface text-jv-ink">
        <div className="font-machine text-xs text-jv-muted uppercase">HTTP 404 // NOT FOUND</div>
        <h1 className="font-display text-4xl text-jv-ink">Mission Dossier Not Found</h1>
        <p className="text-sm text-jv-ink-soft">
          This mission does not exist or belongs to another user scope.
        </p>
        <TactileButton
          variant="primary"
          size="md"
          onClick={() => {
            startViewTransition(() => {
              navigate('/missions');
            });
          }}
        >
          Back to Missions →
        </TactileButton>
      </div>
    );
  }

  const { mission, etag } = missionData;

  const handleTransition = async (newStatus: string) => {
    if (!etag) return;
    setConcurrencyNotice(null);
    try {
      await transitionMission.mutateAsync({
        missionId: mission.mission_id,
        newStatus,
        etag,
      });
    } catch (err: any) {
      if (err.name === 'PreconditionFailedError' || err.status === 412) {
        setConcurrencyNotice("This changed elsewhere. We've loaded the latest version.");
        refetchMission();
      } else {
        setConcurrencyNotice(err.message || 'Transition failed');
      }
    }
  };

  const handleCreateCase = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!caseTitle.trim() || !caseGoal.trim() || !missionId) return;

    try {
      await createCase.mutateAsync({
        missionId,
        title: caseTitle.trim(),
        goal: caseGoal.trim(),
        caseType,
      });
      setCaseTitle('');
      setCaseGoal('');
      setShowCaseForm(false);
    } catch (err: any) {
      setConcurrencyNotice(err.message || 'Failed to create case');
    }
  };

  const missionEvents = (eventsData?.items || []).filter(
    (ev) => ev.aggregate_id === mission.mission_id || ev.payload?.mission_id === mission.mission_id
  );

  return (
    <div className="space-y-12 font-interface text-jv-ink">
      {/* Concurrency / 412 Notification */}
      {concurrencyNotice && (
        <div className="p-4 border border-jv-rule-strong bg-jv-surface flex items-center gap-3 text-xs font-machine text-jv-ink">
          <AlertTriangle className="w-4 h-4 shrink-0 text-jv-ink" />
          <span>{concurrencyNotice}</span>
        </div>
      )}

      {/* Editorial Dossier Header */}
      <div className="border-b border-jv-rule pb-8 space-y-4">
        <div className="flex items-center justify-between font-machine text-xs text-jv-muted">
          <div className="flex items-center gap-2">
            <span>DOSSIER //</span>
            <span className="text-jv-ink font-semibold">{mission.mission_id.slice(0, 8)}</span>
          </div>
          <TextureBadge status={mission.status} />
        </div>

        <h1 className="font-display text-4xl sm:text-5xl text-jv-ink tracking-tight leading-[1.15]">
          {mission.title}
        </h1>

        <div className="flex flex-wrap items-center gap-4 sm:gap-6 font-machine text-xs text-jv-ink-soft pt-2">
          <span>KIND: {mission.kind.toUpperCase()}</span>
          <span className="text-jv-rule-strong">·</span>
          <span>STARTED: {new Date(mission.created_at).toLocaleDateString()}</span>
          <span className="text-jv-rule-strong">·</span>
          <span>VERSION: v{mission.version}</span>
        </div>

        {/* Operational Transitions */}
        <div className="flex flex-wrap items-center gap-3 pt-4 font-machine text-xs">
          {mission.status === 'draft' && (
            <TactileButton
              variant="primary"
              size="sm"
              loading={transitionMission.isPending}
              onClick={() => handleTransition('active')}
            >
              Activate Mission →
            </TactileButton>
          )}
          {mission.status === 'active' && (
            <TactileButton
              variant="outline"
              size="sm"
              loading={transitionMission.isPending}
              onClick={() => handleTransition('paused')}
            >
              Pause Mission
            </TactileButton>
          )}
          {mission.status === 'paused' && (
            <TactileButton
              variant="primary"
              size="sm"
              loading={transitionMission.isPending}
              onClick={() => handleTransition('active')}
            >
              Resume Mission →
            </TactileButton>
          )}
          {mission.status === 'active' && (
            <TactileButton
              variant="secondary"
              size="sm"
              loading={transitionMission.isPending}
              onClick={() => handleTransition('completed')}
            >
              Mark Completed ■
            </TactileButton>
          )}
        </div>
      </div>

      {/* Two-Column Asymmetric Document Layout */}
      <div className="grid grid-cols-1 lg:grid-cols-12 gap-10">
        {/* Left Column: Active Cases (8 cols) */}
        <div className="lg:col-span-8 space-y-8">
          <div className="flex items-baseline justify-between border-b border-jv-rule pb-2">
            <h2 className="font-display text-2xl text-jv-ink tracking-tight">
              Active Cases Dossier
            </h2>
            <TactileButton
              variant="outline"
              size="sm"
              onClick={() => setShowCaseForm(!showCaseForm)}
            >
              {showCaseForm ? 'Cancel' : '+ New Case'}
            </TactileButton>
          </div>

          {/* Inline Case Creation Form */}
          {showCaseForm && (
            <form onSubmit={handleCreateCase} className="border border-jv-rule bg-jv-surface p-6 space-y-4 animate-fadeIn">
              <div className="font-machine text-xs text-jv-muted uppercase tracking-widest border-b border-jv-rule pb-2">
                CREATE NEW CONCRETE CASE
              </div>

              <div className="space-y-1.5">
                <label className="font-machine text-xs uppercase text-jv-muted">Case Title</label>
                <input
                  type="text"
                  value={caseTitle}
                  onChange={(e) => setCaseTitle(e.target.value)}
                  placeholder="e.g. Forward-Deployed AI Application at Exxeta"
                  required
                  className="w-full bg-jv-bg border border-jv-rule px-3 py-2 text-sm text-jv-ink outline-none focus:border-jv-ink"
                />
              </div>

              <div className="space-y-1.5">
                <label className="font-machine text-xs uppercase text-jv-muted">Concrete Goal</label>
                <textarea
                  value={caseGoal}
                  onChange={(e) => setCaseGoal(e.target.value)}
                  placeholder="Specific measurable goal for this case..."
                  required
                  rows={2}
                  className="w-full bg-jv-bg border border-jv-rule px-3 py-2 text-sm text-jv-ink outline-none focus:border-jv-ink"
                />
              </div>

              <div className="space-y-1.5">
                <label className="font-machine text-xs uppercase text-jv-muted">Case Type</label>
                <select
                  value={caseType}
                  onChange={(e) => setCaseType(e.target.value)}
                  className="w-full bg-jv-bg border border-jv-rule px-3 py-2 text-sm text-jv-ink outline-none font-machine focus:border-jv-ink"
                >
                  <option value="job_application">Job Application</option>
                  <option value="housing_search">Housing Search</option>
                  <option value="grant_submission">Grant Submission</option>
                  <option value="dispute">Dispute Resolution</option>
                  <option value="general">General Objective</option>
                </select>
              </div>

              <div className="flex justify-end gap-3 pt-2">
                <TactileButton
                  type="button"
                  variant="outline"
                  size="sm"
                  onClick={() => setShowCaseForm(false)}
                >
                  Dismiss
                </TactileButton>
                <TactileButton
                  type="submit"
                  variant="primary"
                  size="sm"
                  loading={createCase.isPending}
                >
                  Create Case →
                </TactileButton>
              </div>
            </form>
          )}

          {/* Cases List */}
          {casesLoading ? (
            <div className="py-8 font-machine text-xs text-jv-muted">SYNCHRONIZING CASES...</div>
          ) : !cases || cases.length === 0 ? (
            <div className="py-12 text-center space-y-2 border-b border-jv-rule">
              <div className="font-machine text-xs text-jv-muted uppercase">ZERO ACTIVE CASES</div>
              <p className="text-xs text-jv-ink-soft">
                No concrete sub-cases registered under this mission yet.
              </p>
            </div>
          ) : (
            <div className="divide-y divide-jv-rule border-b border-jv-rule">
              {cases.map((c) => (
                <div
                  key={c.case_id}
                  onClick={() => {
                    startViewTransition(() => {
                      navigate(`/cases/${c.case_id}`);
                    });
                  }}
                  className="py-5 group flex items-baseline justify-between gap-4 px-3 -mx-3 hover:bg-jv-surface transition-colors cursor-pointer"
                >
                  <div className="space-y-1.5">
                    <div className="flex items-center gap-3">
                      <span className="font-machine text-xs text-jv-muted">
                        {c.case_id.slice(0, 8)}
                      </span>
                      <span className="font-interface font-medium text-base text-jv-ink group-hover:translate-x-1 transition-transform duration-fast ease-editorial">
                        {c.title}
                      </span>
                    </div>
                    <div className="font-machine text-xs text-jv-muted pl-11">
                      {c.case_type} · Goal: {c.goal}
                    </div>
                  </div>

                  <div className="flex items-center gap-4 shrink-0">
                    <TextureBadge status={c.status} />
                    <ArrowUpRight className="w-4 h-4 text-jv-muted group-hover:text-jv-ink transition-colors" />
                  </div>
                </div>
              ))}
            </div>
          )}

          {/* Mission Scope & Goal Description */}
          <div className="border border-jv-rule p-6 space-y-3 bg-jv-surface">
            <div className="font-machine text-xs tracking-widest uppercase text-jv-muted">
              OPERATIONAL GOAL & SCOPE
            </div>
            <p className="text-sm text-jv-ink-soft leading-relaxed">{mission.goal}</p>
          </div>
        </div>

        {/* Right Column: Mission Chronicle (4 cols) */}
        <div className="lg:col-span-4 space-y-6">
          <div className="border-b border-jv-rule pb-2">
            <h2 className="font-display text-xl text-jv-ink tracking-tight">
              Mission Chronicle
            </h2>
          </div>

          <div className="space-y-4 font-machine text-xs">
            {missionEvents.length === 0 ? (
              <div className="text-jv-muted">No activity recorded yet.</div>
            ) : (
              missionEvents.slice(0, 8).map((ev) => (
                <div key={ev.event_id} className="pb-3 border-b border-jv-rule space-y-1">
                  <div className="text-jv-muted">
                    {new Date(ev.occurred_at).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}
                  </div>
                  <div className="text-jv-ink font-medium">{humanizeEventType(ev.event_type)}</div>
                </div>
              ))
            )}
          </div>
        </div>
      </div>
    </div>
  );
};
