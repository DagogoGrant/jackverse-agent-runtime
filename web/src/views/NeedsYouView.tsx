import React, { useState } from 'react';
import {
  useApprovals,
  useApproval,
  useAction,
  useApproveApproval,
  useRejectApproval,
} from '../hooks/useCaseworker';
import { DragToAuthorize } from '../components/ui/DragToAuthorize';
import { TactileButton } from '../components/ui/TactileButton';
import { TextureBadge } from '../components/ui/TextureBadge';
import { ShieldCheck, AlertTriangle, ChevronDown, ChevronUp } from 'lucide-react';
import type { Approval } from '../api/types';

export const NeedsYouView: React.FC = () => {
  const { data: approvals, isLoading: listLoading, refetch: refetchApprovals } = useApprovals();
  const approveApproval = useApproveApproval();
  const rejectApproval = useRejectApproval();

  const [selectedApprovalId, setSelectedApprovalId] = useState<string | null>(null);
  const [showParameters, setShowParameters] = useState(true);
  const [concurrencyNotice, setConcurrencyNotice] = useState<string | null>(null);
  const [filter, setFilter] = useState<'pending' | 'all'>('pending');
  const [authorizedNotice, setAuthorizedNotice] = useState<string | null>(null);

  const allApprovals = approvals || [];
  const filtered = allApprovals.filter((a) => {
    if (filter === 'pending') return a.status === 'pending';
    return true;
  });

  // Pick first item if none selected or selection not in filtered list
  const activeSummary: Approval | undefined =
    filtered.find((a) => a.approval_id === selectedApprovalId) || filtered[0];

  const effectiveApprovalId = activeSummary?.approval_id || null;

  // Fetch full Approval (with authoritative ETag)
  const {
    data: approvalData,
    isLoading: approvalLoading,
    refetch: refetchCurrentApproval,
  } = useApproval(effectiveApprovalId);

  // Fetch associated Action details
  const activeApproval = approvalData?.approval || activeSummary;
  const approvalEtag = approvalData?.etag;
  const {
    data: actionData,
    isLoading: actionLoading,
    refetch: refetchAction,
  } = useAction(activeApproval?.action_id);
  const action = actionData?.action;

  const handleApprove = async () => {
    if (!activeApproval || !approvalEtag) return;
    setConcurrencyNotice(null);
    setAuthorizedNotice(null);
    try {
      await approveApproval.mutateAsync({
        approvalId: activeApproval.approval_id,
        etag: approvalEtag,
      });
      setAuthorizedNotice('Authorization recorded. Execution is not connected in this phase.');
    } catch (err: any) {
      if (err.name === 'PreconditionFailedError' || err.status === 412) {
        setConcurrencyNotice("This changed elsewhere. We've loaded the latest version.");
        refetchCurrentApproval();
        refetchAction();
        refetchApprovals();
      } else {
        setConcurrencyNotice(err.message || 'Authorization failed');
      }
    }
  };

  const handleReject = async () => {
    if (!activeApproval || !approvalEtag) return;
    setConcurrencyNotice(null);
    setAuthorizedNotice(null);
    try {
      await rejectApproval.mutateAsync({
        approvalId: activeApproval.approval_id,
        etag: approvalEtag,
        reason: 'Declined by human operator via Needs You surface',
      });
      setAuthorizedNotice('Action proposal rejected.');
    } catch (err: any) {
      if (err.name === 'PreconditionFailedError' || err.status === 412) {
        setConcurrencyNotice("This changed elsewhere. We've loaded the latest version.");
        refetchCurrentApproval();
        refetchAction();
        refetchApprovals();
      } else {
        setConcurrencyNotice(err.message || 'Rejection failed');
      }
    }
  };

  const isPending = activeApproval?.status === 'pending';
  const isHighConsequential =
    action?.risk_level === 'high' ||
    action?.risk_level === 'critical' ||
    action?.requires_approval === true;

  const pendingCount = allApprovals.filter((a) => a.status === 'pending').length;

  return (
    <div className="space-y-12 font-interface text-jv-ink">
      {/* Concurrency Notification */}
      {concurrencyNotice && (
        <div className="p-4 border border-jv-rule-strong bg-jv-surface flex items-center gap-3 text-xs font-machine text-jv-ink">
          <AlertTriangle className="w-4 h-4 shrink-0 text-jv-ink" />
          <span>{concurrencyNotice}</span>
        </div>
      )}

      {/* Authorization Success Notice */}
      {authorizedNotice && (
        <div className="p-4 border border-jv-ink bg-jv-ink text-jv-bg flex items-center gap-3 text-xs font-machine font-bold">
          <ShieldCheck className="w-4 h-4 shrink-0" />
          <span>{authorizedNotice}</span>
        </div>
      )}

      {/* Editorial Header */}
      <div className="border-b border-jv-rule pb-6 flex flex-col md:flex-row md:items-end justify-between gap-4">
        <div className="space-y-2">
          <div className="font-machine text-[11px] text-jv-muted uppercase tracking-widest">
            04 / FOLIO · HUMAN GOVERNANCE GATE
          </div>
          <h1 className="font-display text-4xl sm:text-5xl text-jv-ink tracking-tight">
            Needs You
          </h1>
        </div>

        {/* Filter Toggle */}
        <div className="flex items-center gap-2 font-machine text-xs">
          <button
            type="button"
            onClick={() => setFilter('pending')}
            className={`px-3 py-1 uppercase border text-xs tracking-wider transition-colors duration-fast ease-editorial ${
              filter === 'pending'
                ? 'border-jv-ink bg-jv-ink text-jv-bg font-bold'
                : 'border-jv-rule bg-jv-surface text-jv-muted hover:text-jv-ink hover:border-jv-rule-strong'
            }`}
          >
            Pending ({pendingCount})
          </button>
          <button
            type="button"
            onClick={() => setFilter('all')}
            className={`px-3 py-1 uppercase border text-xs tracking-wider transition-colors duration-fast ease-editorial ${
              filter === 'all'
                ? 'border-jv-ink bg-jv-ink text-jv-bg font-bold'
                : 'border-jv-rule bg-jv-surface text-jv-muted hover:text-jv-ink hover:border-jv-rule-strong'
            }`}
          >
            All Decisions ({allApprovals.length})
          </button>
        </div>
      </div>

      {listLoading ? (
        <div className="py-24 text-center font-machine text-xs text-jv-muted">
          INDEXING APPROVAL QUEUE...
        </div>
      ) : filtered.length === 0 ? (
        <div className="py-24 text-center space-y-3 max-w-md mx-auto">
          <div className="font-display text-2xl text-jv-ink tracking-tight">
            ZERO PENDING APPROVALS
          </div>
          <p className="text-sm text-jv-ink-soft leading-relaxed">
            Nothing currently requires your approval. Consequential agent actions will pause
            here for verification before external effect.
          </p>
        </div>
      ) : (
        <div className="grid grid-cols-1 lg:grid-cols-12 gap-8">
          {/* Left Column: Decision Selection List */}
          <div className="lg:col-span-4 divide-y divide-jv-rule border-y border-jv-rule">
            {filtered.map((app) => {
              const isSelected = app.approval_id === effectiveApprovalId;
              return (
                <button
                  type="button"
                  key={app.approval_id}
                  onClick={() => {
                    setSelectedApprovalId(app.approval_id);
                    setConcurrencyNotice(null);
                    setAuthorizedNotice(null);
                  }}
                  className={`w-full text-left p-4 cursor-pointer transition-colors duration-fast ease-editorial space-y-2 focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-jv-ink ${
                    isSelected
                      ? 'bg-jv-ink text-jv-bg'
                      : 'hover:bg-jv-surface text-jv-ink'
                  }`}
                >
                  <div className="flex items-center justify-between font-machine text-xs">
                    <span className={isSelected ? 'text-jv-bg/70' : 'text-jv-muted'}>
                      {app.case_id.slice(0, 8)}
                    </span>
                    <TextureBadge status={app.status} />
                  </div>

                  <div className="font-medium text-sm line-clamp-1">
                    Approval #{app.approval_id.slice(0, 8)}
                  </div>

                  <div
                    className={`font-machine text-[11px] ${
                      isSelected ? 'text-jv-bg/80' : 'text-jv-muted'
                    }`}
                  >
                    Requested: {new Date(app.requested_at).toLocaleDateString()}
                  </div>
                </button>
              );
            })}
          </div>

          {/* Right Column: Editorial Approval Dossier */}
          {activeApproval && (
            <div className="lg:col-span-8 border border-jv-rule bg-jv-surface p-6 md:p-8 space-y-6">
              {approvalLoading || actionLoading ? (
                <div className="py-16 text-center font-machine text-xs text-jv-muted">
                  LOADING ACTION METADATA & CONCURRENCY CONTEXT...
                </div>
              ) : (
                <>
                  <div className="space-y-3">
                    <div className="flex flex-wrap items-center gap-3">
                      <span className="font-machine text-xs text-jv-muted">
                        DECISION #{activeApproval.approval_id.slice(0, 8)} //
                      </span>
                      <TextureBadge status={activeApproval.status} />
                      <span className="font-machine text-xs text-jv-ink-soft uppercase font-semibold">
                        RISK: {action?.risk_level?.toUpperCase() || 'STANDARD'}
                      </span>
                    </div>

                    <h2 className="font-display text-3xl sm:text-4xl text-jv-ink tracking-tight leading-tight">
                      {action?.description || 'Consequential Action Decision'}
                    </h2>

                    <div className="font-machine text-xs text-jv-muted">
                      CASE: {activeApproval.case_id} · ACTION: {activeApproval.action_id} · VERSION: v{activeApproval.version}
                    </div>
                  </div>

                  {/* Constitutional Rationale */}
                  <div className="border-l-2 border-jv-ink pl-4 py-1 text-sm text-jv-ink-soft leading-relaxed space-y-1">
                    <div className="font-medium text-jv-ink">Constitutional Boundary:</div>
                    <div>
                      This action was categorized with risk level{' '}
                      <span className="font-machine text-xs text-jv-ink uppercase font-semibold">
                        {action?.risk_level || 'standard'}
                      </span>
                      . Under JackVerse security policy, consequential mutations and third-party
                      transmissions require explicit human authorization before execution.
                    </div>
                  </div>

                  {/* Action Parameters Inspector */}
                  <div className="border border-jv-rule">
                    <button
                      type="button"
                      onClick={() => setShowParameters(!showParameters)}
                      className="w-full flex items-center justify-between px-4 py-3 bg-jv-bg text-xs font-machine text-jv-ink hover:text-jv-ink-soft border-b border-jv-rule"
                    >
                      <span>INSPECT ACTION PARAMETERS & FINGERPRINT</span>
                      {showParameters ? (
                        <ChevronUp className="w-4 h-4" />
                      ) : (
                        <ChevronDown className="w-4 h-4" />
                      )}
                    </button>

                    {showParameters && (
                      <div className="p-4 space-y-3 font-machine text-xs bg-jv-bg/60">
                        <div className="space-y-1">
                          <span className="text-jv-muted uppercase">Cryptographic Fingerprint:</span>
                          <div className="p-2 border border-jv-rule bg-jv-surface text-jv-ink break-all text-[11px]">
                            {activeApproval.action_fingerprint}
                          </div>
                        </div>

                        <div className="space-y-1">
                          <span className="text-jv-muted uppercase">Parameters:</span>
                          <pre className="p-2 border border-jv-rule bg-jv-surface text-jv-ink text-[11px] overflow-x-auto max-h-28 overflow-y-auto leading-relaxed">
                            {JSON.stringify(action?.parameters || {}, null, 2)}
                          </pre>
                        </div>
                      </div>
                    )}
                  </div>

                  {/* Decision Controls */}
                  <div className="pt-4 border-t border-jv-rule space-y-4">
                    {isPending ? (
                      <div className="space-y-4">
                        {isHighConsequential ? (
                          <DragToAuthorize
                            label="Slide to authorize action"
                            consequentialDescription="Consequential operation: Authorizing binds your explicit consent under cryptographic fingerprint verification."
                            onAuthorize={handleApprove}
                          />
                        ) : (
                          <TactileButton
                            variant="primary"
                            size="md"
                            loading={approveApproval.isPending}
                            onClick={handleApprove}
                          >
                            Approve Action →
                          </TactileButton>
                        )}

                        <div className="flex items-center justify-end gap-4 pt-2">
                          <TactileButton
                            variant="danger"
                            size="md"
                            loading={rejectApproval.isPending}
                            onClick={handleReject}
                          >
                            Reject Proposal ✕
                          </TactileButton>
                        </div>
                      </div>
                    ) : (
                      <div className="p-4 border border-jv-rule bg-jv-bg text-xs font-machine space-y-1">
                        <div className="text-jv-ink uppercase font-bold">
                          DECISION RECORDED // STATUS: {activeApproval.status.toUpperCase()}
                        </div>
                        {activeApproval.reason && (
                          <div className="text-jv-ink-soft">
                            Reason: {activeApproval.reason}
                          </div>
                        )}
                        {activeApproval.decided_at && (
                          <div className="text-jv-muted">
                            Decided at: {new Date(activeApproval.decided_at).toLocaleString()}
                          </div>
                        )}
                      </div>
                    )}
                  </div>
                </>
              )}
            </div>
          )}
        </div>
      )}
    </div>
  );
};
