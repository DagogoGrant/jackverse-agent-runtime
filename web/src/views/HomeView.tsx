import React, { useState } from 'react';
import { useNavigate, Link } from 'react-router-dom';
import { ArrowUpRight } from 'lucide-react';
import { useMissions, useCreateMission, useApprovals } from '../hooks/useCaseworker';
import { TactileButton } from '../components/ui/TactileButton';
import { TextureBadge } from '../components/ui/TextureBadge';
import { NumberRoll } from '../components/ui/NumberRoll';
import { startViewTransition } from '../utils/transitions';

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
      startViewTransition(() => {
        navigate(`/missions/${created.mission_id}`);
      });
    } catch (err: any) {
      setErrorMsg(err.message || 'Failed to initialize mission');
    }
  };

  return (
    <div className="space-y-16 font-interface text-jv-ink">
      {/* Editorial Intent Dispatch Section */}
      <section className="space-y-6 pt-2">
        <div className="font-machine text-[11px] tracking-widest text-jv-muted uppercase flex items-center gap-2">
          <span>01 / NEW MISSION</span>
        </div>

        <form onSubmit={handleInitializeMission} className="space-y-6">
          <label
            htmlFor="home-prompt"
            className="block font-display text-4xl sm:text-5xl lg:text-6xl text-jv-ink tracking-tight leading-[1.1] select-none"
          >
            What do you want JackVerse to move forward?
          </label>

          <div className="relative border-b border-jv-rule-strong pb-3 pt-2 group focus-within:border-jv-ink transition-colors">
            <input
              id="home-prompt"
              type="text"
              value={promptText}
              onChange={(e) => handlePromptChange(e.target.value)}
              onFocus={() => setShowCreateForm(true)}
              placeholder="Describe what you want to accomplish…"
              className="w-full bg-transparent font-interface text-xl sm:text-2xl text-jv-ink placeholder:text-jv-muted/60 outline-none pr-12 tracking-wide"
            />
            <button
              type="button"
              onClick={() => setShowCreateForm(!showCreateForm)}
              className="absolute right-0 bottom-3 text-jv-muted hover:text-jv-ink transition-transform active:scale-tactile"
              aria-label="Toggle mission creation specification"
            >
              <ArrowUpRight className="w-7 h-7 stroke-[1.5]" />
            </button>
          </div>

          {errorMsg && (
            <div className="p-3 border border-jv-rule-strong bg-jv-surface text-xs font-machine text-jv-ink">
              ERROR // {errorMsg}
            </div>
          )}

          {/* Inline Editorial Parameter Expansion */}
          {showCreateForm && (
            <div className="border border-jv-rule bg-jv-surface p-6 space-y-6 mt-4 transition-all animate-fadeIn">
              <div className="flex items-center justify-between border-b border-jv-rule pb-3">
                <span className="font-interface font-medium text-base text-jv-ink">
                  Mission details
                </span>
                <button
                  type="button"
                  onClick={() => setShowCreateForm(false)}
                  className="font-interface text-xs text-jv-muted hover:text-jv-ink"
                >
                  Dismiss [Esc]
                </button>
              </div>

              <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
                <div className="space-y-1.5">
                  <label className="font-interface text-sm text-jv-muted">
                    Mission title
                  </label>
                  <input
                    type="text"
                    value={title}
                    onChange={(e) => setTitle(e.target.value)}
                    required
                    className="w-full bg-jv-bg border border-jv-rule px-3 py-2 text-sm text-jv-ink outline-none focus:border-jv-ink font-interface"
                  />
                </div>

                <div className="space-y-1.5">
                  <label className="font-interface text-sm text-jv-muted">
                    Mission type
                  </label>
                  <select
                    value={kind}
                    onChange={(e) => setKind(e.target.value)}
                    className="w-full bg-jv-bg border border-jv-rule px-3 py-2 text-sm text-jv-ink outline-none focus:border-jv-ink font-interface"
                  >
                    <option value="opportunity_pursuit">Opportunity Pursuit</option>
                    <option value="problem_resolution">Problem Resolution</option>
                    <option value="general_goal">General Goal</option>
                  </select>
                </div>
              </div>

              <div className="flex items-center justify-end gap-4 pt-2">
                <button
                  type="button"
                  onClick={() => setShowCreateForm(false)}
                  className="font-interface text-sm text-jv-muted hover:text-jv-ink"
                >
                  Cancel
                </button>
                <TactileButton
                  type="submit"
                  variant="primary"
                  size="md"
                  loading={createMission.isPending}
                >
                  Create mission →
                </TactileButton>
              </div>
            </div>
          )}
        </form>
      </section>

      {/* Flagship "Needs You" Highlight if Pending Approvals Exist */}
      {pendingApprovals.length > 0 && (
        <Link
          to="/approvals"
          onClick={(e) => {
            e.preventDefault();
            startViewTransition(() => {
              navigate('/approvals');
            });
          }}
          className="block border border-jv-rule-strong bg-jv-surface p-6 sm:p-8 cursor-pointer hover:bg-jv-ink hover:text-jv-bg focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-jv-ink transition-colors group space-y-3"
        >
          <div className="flex items-center justify-between font-machine text-xs">
            <span className="uppercase tracking-widest font-semibold text-jv-muted group-hover:text-jv-bg/80">
              Needs you
            </span>
            <span className="font-interface text-xs underline decoration-1 underline-offset-4 group-hover:text-jv-bg">
              REVIEW PENDING (<NumberRoll value={pendingApprovals.length} />) →
            </span>
          </div>
          <div className="font-interface font-semibold text-2xl sm:text-3xl tracking-tight">
            <NumberRoll value={pendingApprovals.length} />{' '}
            {pendingApprovals.length === 1 ? 'action requires' : 'actions require'}{' '}
            your authorization
          </div>
        </Link>
      )}

      {/* Editorial Index of Active Missions */}
      <section className="space-y-6">
        <div className="flex items-baseline justify-between border-b border-jv-rule pb-3">
          <h2 className="font-interface font-medium text-2xl text-jv-ink tracking-tight">
            Active missions
          </h2>
          <span className="font-machine text-xs text-jv-muted">
            <NumberRoll value={missions?.length || 0} /> MISSIONS
          </span>
        </div>

        {missionsLoading ? (
          <div className="py-12 text-center font-interface text-sm text-jv-muted">
            Loading missions…
          </div>
        ) : !missions || missions.length === 0 ? (
          <div className="py-16 text-center space-y-2">
            <p className="font-interface font-medium text-base text-jv-ink">
              Nothing tracked yet.
            </p>
            <p className="font-interface text-sm text-jv-ink-soft max-w-sm mx-auto">
              Describe a mission above to begin.
            </p>
          </div>
        ) : (
          <div className="divide-y divide-jv-rule border-b border-jv-rule">
            {missions.map((m, idx) => (
              <Link
                key={m.mission_id}
                to={`/missions/${m.mission_id}`}
                onClick={(e) => {
                  e.preventDefault();
                  startViewTransition(() => {
                    navigate(`/missions/${m.mission_id}`);
                  });
                }}
                className="group py-6 flex flex-col md:flex-row md:items-baseline justify-between gap-4 transition-colors hover:bg-jv-surface px-4 -mx-4 cursor-pointer focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-jv-ink focus-visible:bg-jv-surface"
              >
                <div className="space-y-1.5 max-w-2xl">
                  <div className="flex items-baseline gap-4">
                    <span className="font-machine text-xs text-jv-muted">
                      {String(idx + 1).padStart(2, '0')}
                    </span>
                    <h3 className="font-interface font-medium text-lg sm:text-xl text-jv-ink tracking-tight group-hover:translate-x-1 transition-transform duration-fast ease-editorial">
                      {m.title}
                    </h3>
                  </div>
                  <p className="font-interface text-sm text-jv-ink-soft pl-8 line-clamp-1">
                    {m.goal}
                  </p>
                </div>

                <div className="flex items-center gap-6 pl-8 md:pl-0 shrink-0">
                  <TextureBadge status={m.status} />
                  <ArrowUpRight className="w-5 h-5 text-jv-muted group-hover:text-jv-ink group-hover:translate-x-0.5 group-hover:-translate-y-0.5 transition-transform duration-fast ease-editorial" />
                </div>
              </Link>
            ))}
          </div>
        )}
      </section>
    </div>
  );
};
