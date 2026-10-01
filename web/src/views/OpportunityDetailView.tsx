import React, { useState } from 'react';
import { useParams, useNavigate, Link } from 'react-router-dom';
import { ArrowLeft, ExternalLink, Check, AlertTriangle } from 'lucide-react';
import { useOpportunity, useTransitionOpportunity } from '../hooks/useCaseworker';
import { TextureBadge } from '../components/ui/TextureBadge';
import { TactileButton } from '../components/ui/TactileButton';

export const OpportunityDetailView: React.FC = () => {
  const { id } = useParams<{ id: string }>();
  const navigate = useNavigate();

  const { data: oppData, isLoading, error } = useOpportunity(id);
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
      } else {
        setConcurrencyNotice(err.message || 'Transition failed');
      }
    }
  };

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
            {opp.organization}
          </div>
          <h1 className="font-display text-4xl lg:text-6xl text-pure tracking-tight leading-tight">
            {opp.title}
          </h1>
        </div>

        <div className="flex flex-wrap items-center gap-6 font-machine text-xs text-grey-300 pt-2">
          <span>STATUS: {opp.status.toUpperCase()}</span>
          <span className="text-grey-700">|</span>
          <span>DISCOVERED: {new Date(opp.created_at).toLocaleDateString()}</span>
          <span className="text-grey-700">|</span>
          <span>VERSION: v{opp.version}</span>
          {opp.deadline && (
            <>
              <span className="text-grey-700">|</span>
              <span>DEADLINE: {new Date(opp.deadline).toLocaleDateString()}</span>
            </>
          )}
        </div>

        {/* Transition Controls */}
        <div className="flex items-center gap-3 pt-4 font-machine text-xs">
          {opp.status !== 'shortlisted' && (
            <TactileButton
              variant="primary"
              size="sm"
              loading={transitionOpp.isPending}
              onClick={() => handleTransition('shortlisted')}
            >
              Shortlist Prospect ★
            </TactileButton>
          )}
          {opp.status !== 'archived' && (
            <TactileButton
              variant="outline"
              size="sm"
              loading={transitionOpp.isPending}
              onClick={() => handleTransition('archived')}
            >
              Archive
            </TactileButton>
          )}
          {opp.status !== 'rejected' && (
            <TactileButton
              variant="danger"
              size="sm"
              loading={transitionOpp.isPending}
              onClick={() => handleTransition('rejected')}
            >
              Decline / Reject ✕
            </TactileButton>
          )}
        </div>
      </div>

      {/* Asymmetric Magazine Body */}
      <div className="grid grid-cols-1 lg:grid-cols-12 gap-12">
        {/* Left Column: Narrative Description (7 cols) */}
        <div className="lg:col-span-7 space-y-6">
          <div className="border-b border-grey-700 pb-2">
            <h2 className="font-display text-2xl text-pure tracking-tight">
              Why It Exists
            </h2>
          </div>
          <p className="text-base text-grey-300 leading-relaxed whitespace-pre-wrap">
            {opp.description || 'No extended description provided.'}
          </p>

          {opp.source_url && (
            <div className="pt-4 border-t border-grey-700">
              <a
                href={opp.source_url}
                target="_blank"
                rel="noopener noreferrer"
                className="inline-flex items-center gap-2 font-machine text-xs text-pure hover:underline"
              >
                <span>OPEN SOURCE POSTING</span>
                <ExternalLink className="w-3.5 h-3.5" />
              </a>
            </div>
          )}
        </div>

        {/* Right Column: Requirements & Verification (5 cols) */}
        <div className="lg:col-span-5 space-y-8">
          <div className="border-b border-grey-700 pb-2">
            <h2 className="font-display text-xl text-pure tracking-tight">
              Identified Requirements
            </h2>
          </div>

          {opp.requirements.length === 0 ? (
            <div className="font-machine text-xs text-grey-500">
              No granular requirements isolated.
            </div>
          ) : (
            <div className="space-y-3 font-interface text-sm">
              {opp.requirements.map((req, idx) => (
                <div key={idx} className="flex items-start gap-3 p-3 border border-grey-700 bg-ink/40">
                  <Check className="w-4 h-4 text-paper shrink-0 mt-0.5" />
                  <span className="text-grey-300">{req}</span>
                </div>
              ))}
            </div>
          )}

          {/* Provenance Metadata */}
          <div className="border border-grey-700 p-5 space-y-3 bg-ink/20 font-machine text-xs">
            <div className="text-grey-500 uppercase tracking-widest">PROVENANCE FINGERPRINT</div>
            <div className="text-grey-300 break-all">{opp.fingerprint}</div>
          </div>
        </div>
      </div>
    </div>
  );
};
