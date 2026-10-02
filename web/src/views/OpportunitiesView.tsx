import React, { useState } from 'react';
import { useNavigate, Link } from 'react-router-dom';
import { ArrowUpRight } from 'lucide-react';
import { useOpportunities, useCreateOpportunity } from '../hooks/useCaseworker';
import { TextureBadge } from '../components/ui/TextureBadge';
import { TactileButton } from '../components/ui/TactileButton';
import { startViewTransition } from '../utils/transitions';
import type { OpportunityType } from '../api/types';

export const OpportunitiesView: React.FC = () => {
  const navigate = useNavigate();
  const { data: opportunities, isLoading } = useOpportunities();
  const createOpportunity = useCreateOpportunity();

  const [filter, setFilter] = useState<string>('all');
  const [showAddModal, setShowAddModal] = useState(false);

  // Form states
  const [title, setTitle] = useState('');
  const [oppType, setOppType] = useState<OpportunityType>('job');
  const [organization, setOrganization] = useState('');
  const [location, setLocation] = useState('');
  const [sourceName, setSourceName] = useState('');
  const [sourceUrl, setSourceUrl] = useState('');
  const [requirementsInput, setRequirementsInput] = useState('');
  const [errorMsg, setErrorMsg] = useState<string | null>(null);

  const filtered = (opportunities || []).filter((o) => {
    if (filter === 'all') return true;
    return o.status === filter;
  });

  const handleCreateOpportunity = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!title.trim()) return;
    setErrorMsg(null);

    const reqs = requirementsInput
      .split('\n')
      .map((r) => r.trim())
      .filter((r) => r.length > 0);

    try {
      const created = await createOpportunity.mutateAsync({
        title: title.trim(),
        opportunity_type: oppType,
        status: "discovered",
        organization: organization.trim(),
        location: location.trim(),
        source_name: sourceName.trim(),
        source_url: sourceUrl.trim(),
        requirements: reqs,
      });
      setShowAddModal(false);
      setTitle('');
      setOrganization('');
      setLocation('');
      setSourceName('');
      setSourceUrl('');
      setRequirementsInput('');
      navigate(`/opportunities/${created.opportunity.opportunity_id}`);
    } catch (err: any) {
      setErrorMsg(err.message || 'Failed to record opportunity');
    }
  };

  return (
    <div className="space-y-12 font-interface text-jv-ink">
      {/* Editorial Header */}
      <div className="border-b border-jv-rule pb-6 flex flex-col md:flex-row md:items-end justify-between gap-4">
        <div className="space-y-2">
          <div className="font-machine text-xs text-jv-muted uppercase">
            03 / FOLIO · OPPORTUNITY CATALOGUE
          </div>
          <h1 className="font-display font-normal text-4xl lg:text-5xl text-jv-ink tracking-tight">
            Opportunity Inbox
          </h1>
        </div>

        <div className="flex flex-wrap items-center gap-4">
          {/* Status Filters */}
          <div className="flex items-center gap-1.5 font-interface text-xs">
            {(['all', 'discovered', 'evaluating', 'shortlisted', 'archived'] as const).map((s) => (
              <button
                key={s}
                onClick={() => setFilter(s)}
                className={`px-3 py-1 capitalize border transition-colors ${
                  filter === s
                    ? 'border-jv-ink bg-jv-ink text-jv-bg font-semibold'
                    : 'border-jv-rule text-jv-muted hover:text-jv-ink hover:border-jv-rule-strong bg-jv-surface'
                }`}
              >
                {s}
              </button>
            ))}
          </div>

          <TactileButton variant="primary" size="sm" onClick={() => setShowAddModal(true)}>
            + Add Opportunity
          </TactileButton>
        </div>
      </div>

      {/* Manual Opportunity Ingress Form Modal */}
      {showAddModal && (
        <form onSubmit={handleCreateOpportunity} className="p-6 border border-jv-rule bg-jv-surface space-y-4">
          <div className="flex items-center justify-between border-b border-jv-rule pb-2">
            <span className="font-interface font-medium text-base text-jv-ink">
              Add opportunity
            </span>
            <button
              type="button"
              onClick={() => setShowAddModal(false)}
              className="font-interface text-xs text-jv-muted hover:text-jv-ink"
            >
              Dismiss [✕]
            </button>
          </div>

          {errorMsg && (
            <div className="p-3 border border-jv-rule bg-jv-bg text-xs font-machine text-jv-ink">
              ERROR // {errorMsg}
            </div>
          )}

          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
            <div className="space-y-1">
              <label className="font-interface text-sm text-jv-muted">Title</label>
              <input
                type="text"
                value={title}
                onChange={(e) => setTitle(e.target.value)}
                placeholder="e.g. Senior Machine Learning Engineer"
                required
                className="w-full bg-jv-bg border border-jv-rule px-3 py-2 text-sm text-jv-ink outline-none focus:border-jv-ink font-interface"
              />
            </div>

            <div className="space-y-1">
              <label className="font-interface text-sm text-jv-muted">Type</label>
              <select
                value={oppType}
                onChange={(e) => setOppType(e.target.value as OpportunityType)}
                className="w-full bg-jv-bg border border-jv-rule px-3 py-2 text-sm text-jv-ink font-interface outline-none focus:border-jv-ink"
              >
                <option value="job">Job</option>
                <option value="scholarship">Scholarship</option>
                <option value="grant">Grant</option>
                <option value="fellowship">Fellowship</option>
                <option value="research">Research</option>
                <option value="housing">Housing</option>
                <option value="freelance">Freelance</option>
                <option value="hackathon">Hackathon</option>
                <option value="conference">Conference</option>
                <option value="competition">Competition</option>
                <option value="other">Other</option>
              </select>
            </div>

            <div className="space-y-1">
              <label className="font-interface text-sm text-jv-muted">Organization</label>
              <input
                type="text"
                value={organization}
                onChange={(e) => setOrganization(e.target.value)}
                placeholder="e.g. Acme Research Labs"
                className="w-full bg-jv-bg border border-jv-rule px-3 py-2 text-sm text-jv-ink outline-none focus:border-jv-ink font-interface"
              />
            </div>

            <div className="space-y-1">
              <label className="font-interface text-sm text-jv-muted">Location</label>
              <input
                type="text"
                value={location}
                onChange={(e) => setLocation(e.target.value)}
                placeholder="e.g. Remote / Berlin, Germany"
                className="w-full bg-jv-bg border border-jv-rule px-3 py-2 text-sm text-jv-ink outline-none focus:border-jv-ink font-interface"
              />
            </div>

            <div className="space-y-1">
              <label className="font-interface text-sm text-jv-muted">Source name</label>
              <input
                type="text"
                value={sourceName}
                onChange={(e) => setSourceName(e.target.value)}
                placeholder="e.g. LinkedIn, Direct Board"
                className="w-full bg-jv-bg border border-jv-rule px-3 py-2 text-sm text-jv-ink outline-none focus:border-jv-ink font-interface"
              />
            </div>

            <div className="space-y-1">
              <label className="font-interface text-sm text-jv-muted">Source URL</label>
              <input
                type="url"
                value={sourceUrl}
                onChange={(e) => setSourceUrl(e.target.value)}
                placeholder="https://..."
                className="w-full bg-jv-bg border border-jv-rule px-3 py-2 text-sm text-jv-ink outline-none focus:border-jv-ink font-interface"
              />
            </div>
          </div>

          <div className="space-y-1">
            <label className="font-interface text-sm text-jv-muted">
              Requirements (one per line)
            </label>
            <textarea
              value={requirementsInput}
              onChange={(e) => setRequirementsInput(e.target.value)}
              placeholder="e.g. 3+ years experience with Python&#10;Work authorization in EU"
              rows={3}
              className="w-full bg-jv-bg border border-jv-rule px-3 py-2 text-sm text-jv-ink font-interface outline-none focus:border-jv-ink"
            />
          </div>

          <div className="flex justify-end gap-3 pt-2">
            <TactileButton type="button" variant="outline" size="sm" onClick={() => setShowAddModal(false)}>
              Cancel
            </TactileButton>
            <TactileButton type="submit" variant="primary" size="sm" loading={createOpportunity.isPending}>
              Register Opportunity →
            </TactileButton>
          </div>
        </form>
      )}

      {isLoading ? (
        <div className="py-16 text-center font-interface text-sm text-jv-muted">
          Loading opportunities…
        </div>
      ) : filtered.length === 0 ? (
        <div className="py-20 border border-jv-rule text-center space-y-3 bg-jv-surface/20">
          <div className="font-interface font-medium text-xl text-jv-ink tracking-tight">
            No opportunities found
          </div>
          <p className="text-sm text-jv-muted max-w-sm mx-auto">
            No opportunities registered in this view. Use "+ Add Opportunity" to manually record
            prospects or wait for active discovery monitors.
          </p>
        </div>
      ) : (
        <div className="divide-y divide-jv-rule border-y border-jv-rule">
          {filtered.map((opp, idx) => (
            <Link
              key={opp.opportunity_id}
              to={`/opportunities/${opp.opportunity_id}`}
              onClick={(e) => {
                e.preventDefault();
                startViewTransition(() => {
                  navigate(`/opportunities/${opp.opportunity_id}`);
                });
              }}
              className="py-6 px-4 -mx-4 group flex flex-col md:flex-row md:items-baseline justify-between gap-4 hover:bg-jv-surface transition-colors cursor-pointer focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-jv-ink focus-visible:bg-jv-surface"
            >
              <div className="space-y-2 max-w-2xl">
                <div className="flex items-center gap-3">
                  <span className="font-machine text-xs text-jv-muted">
                    {String(idx + 1).padStart(2, '0')}
                  </span>
                  <span className="font-interface text-xs text-jv-muted font-medium">
                    {opp.organization || 'Independent'}
                  </span>
                  <h2 className="font-interface font-semibold text-lg text-jv-ink group-hover:translate-x-1 transition-transform duration-fast ease-editorial">
                    {opp.title}
                  </h2>
                </div>
                <div className="font-interface text-xs text-jv-muted pl-10 md:pl-16 flex flex-wrap gap-4">
                  {opp.location && <span>Location: {opp.location}</span>}
                  {opp.source_name && <span>Source: {opp.source_name}</span>}
                  {opp.discovered_at && (
                    <span>
                      Discovered: {new Date(opp.discovered_at).toLocaleDateString()}
                    </span>
                  )}
                </div>
              </div>

              <div className="flex items-center gap-6 pl-10 md:pl-0">
                <TextureBadge status={opp.status} />
                <span className="font-machine text-xs text-jv-muted uppercase">
                  {opp.opportunity_type}
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
