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

  return (
    <div className="space-y-12 font-interface text-paper">
      {/* Concurrency Notification */}
      {concurrencyNotice && (
        <div className="p-4 border border-grey-500 bg-ink flex items-center gap-3 text-xs font-machine text-pure">
          <AlertTriangle className="w-4 h-4 shrink-0 text-paper" />
          <span>{concurrencyNotice}</span>
        </div>
      )}

      {/* Authorization Success Notice */}
      {authorizedNotice && (
        <div className="p-4 border border-pure bg-pure text-canvas flex items-center gap-3 text-xs font-machine font-bold">
          <ShieldCheck className="w-4 h-4 shrink-0" />
          <span>{authorizedNotice}</span>
        </div>
      )}

      {/* Header */}
      <div className="border-b border-grey-700 pb-6 flex flex-col md:flex-row md:items-end justify-between gap-4">
        <div className="space-y-2">
          <div className="font-machine text-xs text-grey-500 uppercase">
            CONSTITUTIONAL GATE // HUMAN DECISION QUEUE
          </div>
          <h1 className="font-display text-4xl text-pure tracking-tight">
            Needs You
          </h1>
        </div>

        {/* Filter Toggle */}
        <div className="flex items-center gap-2 font-machine text-xs">
          <button
            type="button"
            onClick={() => setFilter('pending')}
            className={`px-3 py-1 uppercase border transition-colors ${
              filter === 'pending'
                ? 'border-paper bg-paper text-canvas font-bold'
                : 'border-grey-700 text-grey-300 hover:text-pure'
            }`}
          >
            Pending ({allApprovals.filter((a) => a.status === 'pending').length})
          </button>
          <button
            type="button"
            onClick={() => setFilter('all')}
            className={`px-3 py-1 uppercase border transition-colors ${
              filter === 'all'
                ? 'border-paper bg-paper text-canvas font-bold'
                : 'border-grey-700 text-grey-300 hover:text-pure'
            }`}
          >
            All Decisions ({allApprovals.length})
          </button>
        </div>
      </div>

      {listLoading ? (
        <div className="py-24 text-center font-machine text-xs text-grey-500">
          INDEXING APPROVAL QUEUE...
        </div>
      ) : filtered.length === 0 ? (
        <div className="py-20 border border-grey-700 text-center space-y-4 bg-ink/20">
          <div className="font-machine text-xs text-grey-500 uppercase tracking-widest">
            ZERO PENDING APPROVALS
          </div>
          <p className="text-sm text-grey-300 max-w-sm mx-auto">
            Nothing currently requires your approval. Consequential agent actions will pause
            here for verification before external effect.
          </p>
        </div>
      ) : (
        <div className="grid grid-cols-1 lg:grid-cols-12 gap-8">
          {/* Left Column: Decision Selection List */}
          <div className="lg:col-span-4 divide-y divide-grey-700 border-y border-grey-700">
            {filtered.map((app) => {
              const isSelected = app.approval_id === effectiveApprovalId;
              return (
                <div
                  key={app.approval_id}
                  onClick={() => {
                    setSelectedApprovalId(app.approval_id);
                    setConcurrencyNotice(null);
                    setAuthorizedNotice(null);
                  }}
                  className={`p-4 cursor-pointer transition-colors space-y-2 ${
                    isSelected
                      ? 'bg-paper text-canvas'
                      : 'hover:bg-ink text-paper'
                  }`}
                >
                  <div className="flex items-center justify-between font-machine text-xs">
                    <span className={isSelected ? 'text-canvas/70' : 'text-grey-500'}>
                      {app.case_id}
                    </span>
                    <TextureBadge status={app.status} />
                  </div>

                  <div className="font-medium text-sm line-clamp-1">
                    Approval #{app.approval_id.slice(0, 8)}
                  </div>

                  <div
                    className={`font-machine text-[11px] ${
                      isSelected ? 'text-canvas/80' : 'text-grey-500'
                    }`}
                  >
                    Requested: {new Date(app.requested_at).toLocaleDateString()}
                  </div>
                </div>
              );
            })}
          </div>

          {/* Right Column: Editorial Approval Dossier */}
          {activeApproval && (
            <div className="lg:col-span-8 border border-grey-700 bg-ink p-6 md:p-8 space-y-6">
              {approvalLoading || actionLoading ? (
                <div className="py-16 text-center font-machine text-xs text-grey-500">
                  LOADING ACTION METADATA & CONCURRENCY CONTEXT...
                </div>
              ) : (
                <>
                  <div className="space-y-3">
                    <div className="flex flex-wrap items-center gap-3">
                      <span className="font-machine text-xs text-grey-500">
                        DECISION #{activeApproval.approval_id.slice(0, 8)} //
                      </span>
                      <TextureBadge status={activeApproval.status} />
                      <span className="font-machine text-xs text-grey-300 uppercase">
                        RISK: {action?.risk_level?.toUpperCase() || 'UNRATED'}
                      </span>
                    </div>

                    <h2 className="font-display text-3xl text-pure tracking-tight">
                      {action?.description || 'Consequential Action Decision'}
                    </h2>

                    <div className="font-machine text-xs text-grey-300">
                      CASE: {activeApproval.case_id} · ACTION: {activeApproval.action_id} · VERSION: v{activeApproval.version}
                    </div>
                  </div>

                  {/* Constitutional Rationale */}
                  <div className="border-l-2 border-paper pl-4 py-1 text-sm text-grey-300 leading-relaxed space-y-1">
                    <div className="font-medium text-pure">Constitutional Boundary:</div>
                    <div>
                      This action was categorized with risk level{' '}
                      <span className="font-machine text-xs text-pure uppercase">
                        {action?.risk_level || 'standard'}
                      </span>
                      . Under JackVerse security policy, consequential mutations and third-party
                      transmissions require explicit human authorization before execution.
                    </div>
                  </div>

                  {/* Action Parameters Inspector */}
                  <div className="border border-grey-700">
                    <button
                      type="button"
                      onClick={() => setShowParameters(!showParameters)}
                      className="w-full flex items-center justify-between px-4 py-3 bg-canvas text-xs font-machine text-grey-300 hover:text-pure border-b border-grey-700"
                    >
                      <span>INSPECT ACTION PARAMETERS & FINGERPRINT</span>
                      {showParameters ? (
                        <ChevronUp className="w-4 h-4" />
                      ) : (
                        <ChevronDown className="w-4 h-4" />
                      )}
                    </button>

                    {showParameters && (
                      <div className="p-4 space-y-3 font-machine text-xs bg-canvas/60">
                        <div className="space-y-1">
                          <span className="text-grey-500 uppercase">Cryptographic Fingerprint:</span>
                          <div className="p-2 border border-grey-700 bg-ink text-paper break-all text-[11px]">
                            {activeApproval.action_fingerprint}
                          </div>
                        </div>

                        <div className="space-y-1">
                          <span className="text-grey-500 uppercase">Parameters:</span>
                          <pre className="p-3 border border-grey-700 bg-ink text-paper text-[11px] overflow-x-auto">
                            {JSON.stringify(action?.parameters || {}, null, 2)}
                          </pre>
                        </div>
                      </div>
                    )}
                  </div>

                  {/* Decision Controls */}
                  <div className="pt-4 border-t border-grey-700 space-y-4">
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
                      <div className="p-4 border border-grey-700 bg-canvas text-xs font-machine space-y-1">
                        <div className="text-pure uppercase">
                          DECISION RECORDED // STATUS: {activeApproval.status.toUpperCase()}
                        </div>
                        {activeApproval.reason && (
                          <div className="text-grey-300">
                            Reason: {activeApproval.reason}
                          </div>
                        )}
                        {activeApproval.decided_at && (
                          <div className="text-grey-500">
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
