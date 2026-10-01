import React, { useState } from 'react';
import { useParams, useNavigate, Link } from 'react-router-dom';
import { ArrowLeft, ExternalLink, AlertTriangle } from 'lucide-react';
import { useOpportunity, useTransitionOpportunity } from '../hooks/useCaseworker';
import { TextureBadge } from '../components/ui/TextureBadge';
import { TactileButton } from '../components/ui/TactileButton';

export const OpportunityDetailView: React.FC = () => {
  const { id } = useParams<{ id: string }>();
  const navigate = useNavigate();

  const { data: oppData, isLoading, error, refetch } = useOpportunity(id);
  const transitionOpp = useTransitionOpportunity();
  const [concurrencyNotice, setConcurrencyNotice] = useState<string | null>(null);

  if (isLoading) {
    return (
      <div className="py-24 text-center font-machine text-xs text-grey-500">
        RETRIEVING OPPORTUNITY SPECIFICATION...
      </div>
    );
  }

  if (error || !oppData?.opportunity) {
    return (
      <div className="py-24 text-center space-y-4 font-interface">
        <div className="font-machine text-xs text-grey-500 uppercase">HTTP 404 // NOT FOUND</div>
        <h1 className="font-display text-4xl text-pure">Opportunity Not Found</h1>
        <p className="text-sm text-grey-300">
          This prospect record does not exist or belongs to another user scope.
        </p>
        <TactileButton variant="primary" size="md" onClick={() => navigate('/opportunities')}>
          Back to Opportunities →
        </TactileButton>
      </div>
    );
  }

  const { opportunity: opp, etag } = oppData;

  const handleTransition = async (newStatus: string) => {
    if (!etag || !id) return;
    setConcurrencyNotice(null);
    try {
      await transitionOpp.mutateAsync({
        id,
        newStatus,
        etag,
      });
    } catch (err: any) {
      if (err.name === 'PreconditionFailedError' || err.status === 412) {
        setConcurrencyNotice("This changed elsewhere. We've loaded the latest version.");
        refetch();
      } else {
        setConcurrencyNotice(err.message || 'Transition failed');
      }
    }
  };

  const reqs = opp.requirements || [];

  return (
    <div className="space-y-12 font-interface text-paper">
      {/* Return Navigation */}
      <div>
        <Link
          to="/opportunities"
          className="inline-flex items-center gap-2 font-machine text-xs text-grey-500 hover:text-pure transition-colors"
        >
          <ArrowLeft className="w-3.5 h-3.5" />
          <span>RETURN TO OPPORTUNITY INBOX</span>
        </Link>
      </div>

      {/* Concurrency Notification */}
      {concurrencyNotice && (
        <div className="p-4 border border-grey-500 bg-ink flex items-center gap-3 text-xs font-machine text-pure">
          <AlertTriangle className="w-4 h-4 shrink-0 text-paper" />
          <span>{concurrencyNotice}</span>
        </div>
      )}

      {/* Magazine Editorial Header */}
      <div className="border-b border-grey-700 pb-8 space-y-4">
        <div className="flex items-center justify-between font-machine text-xs text-grey-500">
          <div>{opp.opportunity_type.toUpperCase()} // {opp.opportunity_id}</div>
          <TextureBadge status={opp.status} />
        </div>

        <div className="space-y-1">
          <div className="font-machine text-sm text-grey-300 uppercase tracking-widest">
            {opp.organization || 'Direct Prospect'}
          </div>
          <h1 className="font-display text-4xl lg:text-6xl text-pure tracking-tight leading-tight">
            {opp.title}
          </h1>
        </div>

        <div className="flex flex-wrap items-center gap-6 font-machine text-xs text-grey-300 pt-2">
          <span>STATUS: {opp.status.toUpperCase()}</span>
          <span className="text-grey-700">|</span>
          <span>DISCOVERED: {new Date(opp.discovered_at).toLocaleDateString()}</span>
          {opp.location && (
            <>
              <span className="text-grey-700">|</span>
              <span>LOCATION: {opp.location}</span>
            </>
          )}
          {opp.deadline && (
            <>
              <span className="text-grey-700">|</span>
              <span>DEADLINE: {new Date(opp.deadline).toLocaleDateString()}</span>
            </>
          )}
        </div>
      </div>

      {/* Structured Overview Dossier */}
      <div className="grid grid-cols-1 lg:grid-cols-12 gap-8">
        <div className="lg:col-span-8 space-y-8">
          {/* Metadata attributes */}
          <section className="space-y-4">
            <h2 className="font-machine text-xs text-grey-500 uppercase tracking-widest border-b border-grey-700 pb-2">
              PROSPECT ATTRIBUTES & PROVENANCE
            </h2>
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-4 font-machine text-xs">
              <div className="p-4 border border-grey-700 bg-ink space-y-1">
                <span className="text-grey-500 uppercase">Provider / Organization</span>
                <div className="text-pure text-sm">{opp.organization || 'Not Specified'}</div>
              </div>
              <div className="p-4 border border-grey-700 bg-ink space-y-1">
                <span className="text-grey-500 uppercase">Geographic Scope</span>
                <div className="text-pure text-sm">{opp.location || 'Remote / Unspecified'}</div>
              </div>
              <div className="p-4 border border-grey-700 bg-ink space-y-1">
                <span className="text-grey-500 uppercase">Source Platform</span>
                <div className="text-pure text-sm">{opp.source_name || 'Direct Manual Ingress'}</div>
              </div>
              <div className="p-4 border border-grey-700 bg-ink space-y-1">
                <span className="text-grey-500 uppercase">Cryptographic Fingerprint</span>
                <div className="text-pure text-[11px] break-all">{opp.fingerprint}</div>
              </div>
            </div>
          </section>

          {/* Requirements List */}
          <section className="space-y-4">
            <h2 className="font-machine text-xs text-grey-500 uppercase tracking-widest border-b border-grey-700 pb-2">
              REQUIREMENTS & ELIGIBILITY ({reqs.length})
            </h2>
            {reqs.length === 0 ? (
              <p className="font-machine text-xs text-grey-500">
                No formal eligibility requirements recorded for this prospect.
              </p>
            ) : (
              <ul className="space-y-2">
                {reqs.map((req, idx) => (
                  <li
                    key={idx}
                    className="flex items-start gap-3 p-3 border border-grey-700/60 bg-ink/40 text-sm text-grey-300"
                  >
                    <span className="font-machine text-xs text-grey-500 shrink-0 pt-0.5">
                      {String(idx + 1).padStart(2, '0')}.
                    </span>
                    <span>{req}</span>
                  </li>
                ))}
              </ul>
            )}
          </section>
        </div>

        {/* Right Sidebar: Lifecycle Operations */}
        <div className="lg:col-span-4 space-y-6">
          <div className="border border-grey-700 bg-ink p-6 space-y-4">
            <div className="font-machine text-xs text-grey-500 uppercase tracking-widest border-b border-grey-700 pb-2">
              LIFECYCLE TRANSITIONS
            </div>

            <div className="space-y-2">
              {opp.status === 'discovered' && (
                <>
                  <TactileButton
                    variant="primary"
                    size="sm"
                    className="w-full justify-center"
                    loading={transitionOpp.isPending}
                    onClick={() => handleTransition('evaluating')}
                  >
                    Mark Evaluating →
                  </TactileButton>
                  <TactileButton
                    variant="outline"
                    size="sm"
                    className="w-full justify-center"
                    loading={transitionOpp.isPending}
                    onClick={() => handleTransition('normalized')}
                  >
                    Normalize Prospect
                  </TactileButton>
                </>
              )}

              {(opp.status === 'evaluating' || opp.status === 'normalized') && (
                <TactileButton
                  variant="primary"
                  size="sm"
                  className="w-full justify-center"
                  loading={transitionOpp.isPending}
                  onClick={() => handleTransition('shortlisted')}
                >
                  Shortlist Opportunity ★
                </TactileButton>
              )}

              {opp.status !== 'archived' && opp.status !== 'rejected' && (
                <TactileButton
                  variant="outline"
                  size="sm"
                  className="w-full justify-center text-grey-300"
                  loading={transitionOpp.isPending}
                  onClick={() => handleTransition('archived')}
                >
                  Archive Opportunity
                </TactileButton>
              )}

              {opp.status !== 'rejected' && opp.status !== 'archived' && (
                <TactileButton
                  variant="danger"
                  size="sm"
                  className="w-full justify-center"
                  loading={transitionOpp.isPending}
                  onClick={() => handleTransition('rejected')}
                >
                  Reject Opportunity ✕
                </TactileButton>
              )}
            </div>

            {opp.source_url && (
              <div className="pt-4 border-t border-grey-700">
                <a
                  href={opp.source_url}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="flex items-center justify-between text-xs font-machine text-grey-300 hover:text-pure p-2 border border-grey-700 bg-canvas"
                >
                  <span>VISIT SOURCE LISTING</span>
                  <ExternalLink className="w-3.5 h-3.5" />
                </a>
              </div>
            )}
          </div>
        </div>
      </div>
    </div>
  );
};
