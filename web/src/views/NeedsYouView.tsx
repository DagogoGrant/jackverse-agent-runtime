import React, { useState } from 'react';
import { useApprovals, useApproveAction, useRejectAction } from '../hooks/useCaseworker';
import { DragToAuthorize } from '../components/ui/DragToAuthorize';
import { TactileButton } from '../components/ui/TactileButton';
import { TextureBadge } from '../components/ui/TextureBadge';
import { ShieldCheck, AlertTriangle, ChevronDown, ChevronUp } from 'lucide-react';
import { Approval } from '../api/types';

export const NeedsYouView: React.FC = () => {
  const { data: approvals, isLoading } = useApprovals();
  const approveAction = useApproveAction();
  const rejectAction = useRejectAction();

  const [selectedApprovalId, setSelectedApprovalId] = useState<string | null>(null);
  const [showParameters, setShowParameters] = useState(true);
  const [concurrencyNotice, setConcurrencyNotice] = useState<string | null>(null);
  const [filter, setFilter] = useState<'pending' | 'all'>('pending');

  const allApprovals = approvals || [];
  const filtered = allApprovals.filter((a) => {
    if (filter === 'pending') return a.status === 'pending';
    return true;
  });

  // Pick first pending if none selected
  const activeApproval: Approval | undefined =
    filtered.find((a) => a.approval_id === selectedApprovalId) || filtered[0];

  const handleApprove = async (approval: Approval) => {
    setConcurrencyNotice(null);
    try {
      // ETag for approval or action
      const etag = `"action:${approval.action_id}:v${approval.action?.version || 1}"`;
      await approveAction.mutateAsync({
        actionId: approval.action_id,
        etag,
      });
    } catch (err: any) {
      if (err.name === 'PreconditionFailedError' || err.status === 412) {
        setConcurrencyNotice("This changed elsewhere. We've loaded the latest version.");
      } else {
        setConcurrencyNotice(err.message || 'Authorization failed');
      }
    }
  };

  const handleReject = async (approval: Approval) => {
    setConcurrencyNotice(null);
    try {
      const etag = `"action:${approval.action_id}:v${approval.action?.version || 1}"`;
      await rejectAction.mutateAsync({
        actionId: approval.action_id,
        etag,
        reason: 'Declined by human operator via Needs You surface',
      });
    } catch (err: any) {
      if (err.name === 'PreconditionFailedError' || err.status === 412) {
        setConcurrencyNotice("This changed elsewhere. We've loaded the latest version.");
      } else {
        setConcurrencyNotice(err.message || 'Rejection failed');
      }
    }
  };

  return (
    <div className="space-y-12 font-interface text-paper">
      {/* Concurrency Notification */}
      {concurrencyNotice && (
        <div className="p-4 border border-grey-500 bg-ink flex items-center gap-3 text-xs font-machine text-pure">
          <AlertTriangle className="w-4 h-4 shrink-0 text-paper" />
          <span>{concurrencyNotice}</span>
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
            onClick={() => setFilter('all')}
            className={`px-3 py-1 uppercase border transition-colors ${
              filter === 'all'
                ? 'border-paper bg-paper text-canvas font-bold'
                : 'border-grey-700 text-grey-300 hover:text-pure'
            }`}
          >
            All History ({allApprovals.length})
          </button>
        </div>
      </div>

      {isLoading ? (
        <div className="py-24 text-center font-machine text-xs text-grey-500">
          INSPECTING APPROVAL INBOX...
        </div>
      ) : filtered.length === 0 ? (
        <div className="py-24 border border-grey-700 text-center space-y-4 bg-ink/20">
          <div className="font-machine text-xs text-grey-500 uppercase tracking-widest">
            ZERO ACTIONS AWAITING AUTHORIZATION
          </div>
          <p className="text-sm text-grey-300 max-w-sm mx-auto">
            All proposed actions have been resolved or JackVerse is operating autonomously
            within authorized boundaries.
          </p>
        </div>
      ) : (
        <div className="grid grid-cols-1 lg:grid-cols-12 gap-8 items-start">
          {/* Left Column: Approvals Queue List (4 cols) */}
          <div className="lg:col-span-4 border border-grey-700 divide-y divide-grey-700 bg-canvas">
            {filtered.map((a) => {
              const isSelected = activeApproval?.approval_id === a.approval_id;
              return (
                <div
                  key={a.approval_id}
                  onClick={() => setSelectedApprovalId(a.approval_id)}
                  className={`p-4 space-y-2 cursor-pointer transition-colors ${
                    isSelected ? 'bg-ink border-l-4 border-l-paper' : 'hover:bg-ink/50'
                  }`}
                >
                  <div className="flex items-center justify-between font-machine text-xs">
                    <span className="text-grey-500">{a.approval_id.slice(0, 8)}</span>
                    <TextureBadge status={a.status} />
                  </div>
                  <div className="font-interface font-medium text-sm text-pure line-clamp-1">
                    {a.action?.description || `Action ${a.action_id.slice(0, 8)}`}
                  </div>
                  <div className="font-machine text-[11px] text-grey-500 uppercase">
                    RISK: {a.action?.risk_level || 'CRITICAL'}
                  </div>
                </div>
              );
            })}
          </div>

          {/* Right Column: Active Approval Dossier (8 cols) */}
          {activeApproval && (
            <div className="lg:col-span-8 border border-grey-700 bg-ink p-8 space-y-8">
              <div className="space-y-3">
                <div className="flex items-center gap-3">
                  <span className="font-machine text-xs text-grey-500">
                    GATE ID // {activeApproval.approval_id}
                  </span>
                  <TextureBadge status={activeApproval.status} />
                </div>

                <h2 className="font-display text-3xl text-pure tracking-tight leading-tight">
                  {activeApproval.action?.description || 'Operational Action Proposal'}
                </h2>

                <div className="font-machine text-xs text-grey-300">
                  ACTION: {activeApproval.action_id} · RISK LEVEL:{' '}
                  {activeApproval.action?.risk_level?.toUpperCase() || 'CRITICAL'}
                </div>
              </div>

              {/* Consequential Notice */}
              <div className="border-l-2 border-paper pl-4 py-1 text-sm text-grey-300 leading-relaxed space-y-1">
                <div className="font-medium text-pure">Operational Intent:</div>
                <div>
                  This action involves irreversible side-effects, sensitive data disclosure, or
                  external service mutation. JackVerse requires human consent before executing.
                </div>
              </div>

              {/* Action Parameters Inspection */}
              <div className="border border-grey-700">
                <button
                  type="button"
                  onClick={() => setShowParameters(!showParameters)}
                  className="w-full flex items-center justify-between px-4 py-3 bg-canvas text-xs font-machine text-grey-300 hover:text-pure border-b border-grey-700"
                >
                  <span>INSPECT ACTION PARAMETERS & BOUND DATA</span>
                  {showParameters ? <ChevronUp className="w-4 h-4" /> : <ChevronDown className="w-4 h-4" />}
                </button>

                {showParameters && (
                  <div className="p-4 space-y-3 font-machine text-xs bg-ink/80">
                    <div className="text-grey-500 uppercase">Parameters:</div>
                    <pre className="p-3 bg-canvas border border-grey-700 overflow-x-auto text-grey-300 whitespace-pre-wrap">
                      {JSON.stringify(activeApproval.action?.parameters || {}, null, 2)}
                    </pre>

                    <div className="text-grey-500 uppercase pt-2">Cryptographic Fingerprint:</div>
                    <div className="text-grey-300 break-all text-[11px]">
                      {activeApproval.action_fingerprint}
                    </div>
                  </div>
                )}
              </div>

              {/* Decision Section */}
              <div className="pt-4 border-t border-grey-700">
                {activeApproval.status === 'approved' ? (
                  <div className="flex items-center gap-3 p-4 border border-pure bg-pure text-canvas font-interface font-medium">
                    <ShieldCheck className="w-5 h-5 shrink-0" />
                    <span>Action has been authorized and queued for execution.</span>
                  </div>
                ) : activeApproval.status === 'rejected' ? (
                  <div className="p-4 border border-grey-700 bg-canvas text-grey-300 font-interface text-sm">
                    Action rejected: {activeApproval.decision_reason || 'Declined by human operator.'}
                  </div>
                ) : (
                  <div className="space-y-4">
                    {/* For high or critical risk: Drag to Authorize */}
                    {activeApproval.action?.risk_level === 'high' ||
                    activeApproval.action?.risk_level === 'critical' ? (
                      <DragToAuthorize
                        label="Slide to authorize consequential action"
                        consequentialDescription="Consequential Action: Authorizing will trigger external execution under the current case policy."
                        onAuthorize={() => handleApprove(activeApproval)}
                        disabled={approveAction.isPending || rejectAction.isPending}
                      />
                    ) : (
                      /* For low or medium risk: Direct tactile button */
                      <div className="flex items-center gap-4">
                        <TactileButton
                          variant="primary"
                          size="lg"
                          className="flex-1 uppercase font-machine tracking-widest"
                          loading={approveAction.isPending}
                          disabled={rejectAction.isPending}
                          onClick={() => handleApprove(activeApproval)}
                        >
                          Authorize Action →
                        </TactileButton>
                      </div>
                    )}

                    <div className="flex justify-end pt-2">
                      <TactileButton
                        variant="danger"
                        size="md"
                        loading={rejectAction.isPending}
                        disabled={approveAction.isPending}
                        onClick={() => handleReject(activeApproval)}
                      >
                        Decline Action ✕
                      </TactileButton>
                    </div>
                  </div>
                )}
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  );
};
