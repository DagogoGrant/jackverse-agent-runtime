import React, { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { ArrowUpRight } from 'lucide-react';
import { useMissions, useCreateMission, useApprovals } from '../hooks/useCaseworker';
import { TactileButton } from '../components/ui/TactileButton';
import { TextureBadge } from '../components/ui/TextureBadge';

export const HomeView: React.FC = () => {
  const navigate = useNavigate();
  const { data: missions, isLoading: missionsLoading } = useMissions();
  const { data: approvals } = useApprovals();
  const createMission = useCreateMission();

  const [promptText, setPromptText] = useState('');
  const [showCreateForm, setShowCreateForm] = useState(false);
  const [title, setTitle] = useState('');
  const [kind, setKind] = useState('opportunity_pursuit');
  const [errorMsg, setErrorMsg] = useState<string | null>(null);

  const pendingApprovals = approvals?.filter((a) => a.status === 'pending') || [];

  const handlePromptChange = (val: string) => {
    setPromptText(val);
    if (!title || title === promptText) {
      setTitle(val);
    }
  };

  const handleInitializeMission = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!promptText.trim()) return;
    setErrorMsg(null);

    try {
      const created = await createMission.mutateAsync({
        goal: promptText.trim(),
        title: (title || promptText).trim().slice(0, 80),
        kind,
      });
      navigate(`/missions/${created.mission_id}`);
    } catch (err: any) {
      setErrorMsg(err.message || 'Failed to initialize mission');
    }
  };

  return (
    <div className="space-y-16 font-interface text-paper">
      {/* Dramatic Functional Home Prompt */}
      <section className="space-y-6 pt-4">
        <div className="font-machine text-xs tracking-widest text-grey-500 uppercase">
          OPERATIONAL INGRESS // INTENT DISPATCH
        </div>

        <form onSubmit={handleInitializeMission} className="space-y-4">
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
              onChange={(e) => handlePromptChange(e.target.value)}
              onFocus={() => setShowCreateForm(true)}
              placeholder="State a clear operational mission goal..."
              className="w-full bg-transparent font-interface text-xl lg:text-2xl text-pure placeholder-grey-700 outline-none pr-12"
            />
            <button
              type="button"
              onClick={() => setShowCreateForm(!showCreateForm)}
              className="absolute right-0 bottom-3 text-paper hover:text-pure transition-transform active:scale-95"
              aria-label="Toggle mission creation specification"
            >
              <ArrowUpRight className="w-8 h-8 stroke-[1.5]" />
            </button>
          </div>

          {errorMsg && (
            <div className="p-3 border border-grey-500 bg-ink text-xs font-machine text-pure">
              ERROR // {errorMsg}
            </div>
          )}

          {/* Inline Confirmation & Parameter Panel */}
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
                    required
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
                <TactileButton
                  type="submit"
                  variant="primary"
                  size="md"
                  loading={createMission.isPending}
                >
                  Initialize Mission →
                </TactileButton>
              </div>
            </div>
          )}
        </form>
      </section>

      {/* Flagship "Needs You" Highlight if Pending Approvals Exist */}
      {pendingApprovals.length > 0 && (
        <section
          onClick={() => navigate('/approvals')}
          className="border-2 border-paper bg-ink p-6 cursor-pointer hover:bg-paper hover:text-canvas transition-colors group space-y-2"
        >
          <div className="flex items-center justify-between font-machine text-xs">
            <span className="uppercase tracking-widest font-bold">
              CONSTITUTIONAL GATE // ACTION REQUIRED
            </span>
            <span className="underline decoration-1 underline-offset-4">
              REVIEW PENDING ({pendingApprovals.length}) →
            </span>
          </div>
          <div className="font-display text-2xl lg:text-3xl tracking-tight">
            {pendingApprovals.length}{' '}
            {pendingApprovals.length === 1 ? 'action requires' : 'actions require'}{' '}
            your authorization
          </div>
        </section>
      )}

      {/* Editorial Index of Active Missions */}
      <section className="space-y-6">
        <div className="flex items-baseline justify-between border-b border-grey-700 pb-2">
          <h2 className="font-display text-2xl text-pure tracking-tight">
            Active Missions Index
          </h2>
          <span className="font-machine text-xs text-grey-500">
            {missions?.length || 0} TRACKED OBJECTS
          </span>
        </div>

        {missionsLoading ? (
          <div className="py-12 text-center font-machine text-xs text-grey-500">
            SYNCHRONIZING REPOSITORY STATE...
          </div>
        ) : !missions || missions.length === 0 ? (
          <div className="py-16 border border-grey-700 text-center space-y-4 bg-ink/20">
            <div className="font-machine text-xs text-grey-500 uppercase tracking-widest">
              ZERO ACTIVE MISSIONS
            </div>
            <p className="text-sm text-grey-300 max-w-sm mx-auto">
              No operational missions registered under this profile. Type an objective
              above to initialize a case file.
            </p>
          </div>
        ) : (
          <div className="divide-y divide-grey-700 border-y border-grey-700">
            {missions.map((m, idx) => (
              <div
                key={m.mission_id}
                onClick={() => navigate(`/missions/${m.mission_id}`)}
                className="group py-6 flex flex-col md:flex-row md:items-baseline justify-between gap-4 transition-colors hover:bg-ink/60 px-4 -mx-4 cursor-pointer"
              >
                <div className="space-y-2 max-w-2xl">
                  <div className="flex items-center gap-4">
                    <span className="font-machine text-xs text-grey-500">
                      {String(idx + 1).padStart(2, '0')}
                    </span>
                    <h3 className="font-interface font-semibold text-lg lg:text-xl text-pure tracking-wide group-hover:translate-x-1 transition-transform">
                      {m.title}
                    </h3>
                  </div>
                  <p className="font-interface text-sm text-grey-300 pl-8 line-clamp-1">
                    {m.goal}
                  </p>
                </div>

                <div className="flex items-center gap-6 pl-8 md:pl-0">
                  <TextureBadge status={m.status} />
                  <ArrowUpRight className="w-5 h-5 text-grey-500 group-hover:text-pure group-hover:translate-x-0.5 group-hover:-translate-y-0.5 transition-all" />
                </div>
              </div>
            ))}
          </div>
        )}
      </section>
    </div>
  );
};
