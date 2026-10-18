import React, { useState } from 'react';
import {
  useApprovals,
  useApproval,
  useAction,
  useApproveApproval,
  useRejectApproval,
  useCaseArtifacts,
  useLatestPreparationPackage,
  useExecutionPlan,
} from '../hooks/useCaseworker';
import { DragToAuthorize } from '../components/ui/DragToAuthorize';
import { TactileButton } from '../components/ui/TactileButton';
import { TextureBadge } from '../components/ui/TextureBadge';
import {
  ShieldCheck,
  AlertTriangle,
  ChevronDown,
  ChevronUp,
  Lock,
  FileText,
  Eye,
  EyeOff,
  Send,
  Globe,
  UploadCloud,
  CheckCircle2,
} from 'lucide-react';
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
  const [selectedPreviewArtifactId, setSelectedPreviewArtifactId] = useState<string | null>(null);

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

  const { data: caseArtifacts } = useCaseArtifacts(activeApproval?.case_id);
  const { data: prepPackage } = useLatestPreparationPackage(activeApproval?.case_id);
  const isPrepPackageApproval = action?.action_type === 'approve_preparation_package';
  const isSubmitFormApproval = action?.action_type === 'submit_form';
  const planId = (action?.parameters as Record<string, any> | undefined)?.plan_id as string | undefined;
  const { data: executionPlan } = useExecutionPlan(activeApproval?.case_id, planId);

  const [revealedValues, setRevealedValues] = useState<Record<string, boolean>>({});
  const toggleRevealValue = (key: string) => {
    setRevealedValues((prev) => ({
      ...prev,
      [key]: !prev[key],
    }));
  };

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
        <div className="flex items-center gap-2 font-interface text-xs">
          <button
            type="button"
            onClick={() => setFilter('pending')}
            className={`px-3 py-1 capitalize border text-xs transition-colors duration-fast ease-editorial ${
              filter === 'pending'
                ? 'border-jv-ink bg-jv-ink text-jv-bg font-semibold'
                : 'border-jv-rule bg-jv-surface text-jv-muted hover:text-jv-ink hover:border-jv-rule-strong'
            }`}
          >
            Pending ({pendingCount})
          </button>
          <button
            type="button"
            onClick={() => setFilter('all')}
            className={`px-3 py-1 capitalize border text-xs transition-colors duration-fast ease-editorial ${
              filter === 'all'
                ? 'border-jv-ink bg-jv-ink text-jv-bg font-semibold'
                : 'border-jv-rule bg-jv-surface text-jv-muted hover:text-jv-ink hover:border-jv-rule-strong'
            }`}
          >
            All decisions ({allApprovals.length})
          </button>
        </div>
      </div>

      {listLoading ? (
        <div className="py-24 text-center font-interface text-sm text-jv-muted">
          Loading approval queue…
        </div>
      ) : filtered.length === 0 ? (
        <div className="py-24 text-center space-y-3 max-w-md mx-auto">
          <div className="font-interface font-medium text-xl text-jv-ink tracking-tight">
            No pending approvals
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
                    <span className={`truncate max-w-[180px] ${isSelected ? 'text-jv-bg/70' : 'text-jv-muted'}`} title={app.case_id}>
                      {app.case_id}
                    </span>
                    <TextureBadge status={app.status} />
                  </div>

                  <div className="font-interface font-medium text-sm line-clamp-1">
                    Approval #{app.approval_id.slice(0, 8)}
                  </div>

                  <div
                    className={`font-interface text-xs ${
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
                <div className="py-16 text-center font-interface text-sm text-jv-muted">
                  Loading action metadata…
                </div>
              ) : (
                <>
                  <div className="space-y-3">
                    <div className="flex flex-wrap items-center gap-3">
                      <span className="font-machine text-xs text-jv-muted">
                        Decision #{activeApproval.approval_id.slice(0, 8)}
                      </span>
                      <TextureBadge status={activeApproval.status} />
                      <span className="font-machine text-xs text-jv-ink-soft uppercase font-semibold">
                        RISK: {action?.risk_level?.toUpperCase() || 'STANDARD'}
                      </span>
                    </div>

                    <h2 className="font-interface font-semibold text-2xl sm:text-3xl text-jv-ink tracking-tight leading-snug">
                      {action?.description || 'Consequential Action Decision'}
                    </h2>

                    <div className="font-machine text-xs text-jv-muted">
                      Case: {activeApproval.case_id.slice(0, 8)} · Action: {activeApproval.action_id.slice(0, 8)} · v{activeApproval.version}
                    </div>
                  </div>

                  {/* Constitutional Rationale / Guardrail Notice */}
                  {isSubmitFormApproval ? (
                    <div className="border-l-4 border-jv-accent bg-jv-accent/10 p-4 text-xs font-interface text-jv-ink space-y-2">
                      <div className="font-semibold uppercase tracking-wider font-machine text-[11px] text-jv-accent flex items-center gap-2">
                        <ShieldCheck className="w-4 h-4 text-jv-accent" />
                        Constitutional Boundary // External Transmission Authorization
                      </div>
                      <p className="leading-relaxed font-semibold text-sm text-jv-ink">
                        “I authorize these exact materials and values to be transmitted to this exact destination.”
                      </p>
                      <p className="leading-relaxed text-jv-muted">
                        Authorizing this decision provides your cryptographic consent for external transmission of the sealed plan. Actual transmission execution remains strictly deferred to Phase 5.3.3. Zero outbound network calls or form submissions will occur at this stage.
                      </p>
                    </div>
                  ) : isPrepPackageApproval ? (
                    <div className="border-l-4 border-amber-600 bg-amber-500/10 p-4 text-xs font-interface text-amber-900 dark:text-amber-200 space-y-1">
                      <div className="font-semibold uppercase tracking-wider font-machine text-[11px] text-amber-700 dark:text-amber-400 flex items-center gap-2">
                        <Lock className="w-3.5 h-3.5" />
                        Constitutional Boundary // Local Verification Only
                      </div>
                      <p className="leading-relaxed">
                        Authorizing this decision approves these exact frozen materials <strong>locally</strong> for preparation. This does <strong>NOT</strong> grant authorization to submit or transmit them externally. Zero external transmission will occur in this phase.
                      </p>
                    </div>
                  ) : (
                    <div className="border-l-2 border-jv-ink pl-4 py-1 text-sm text-jv-ink-soft leading-relaxed space-y-1 font-interface">
                      <div className="font-medium text-jv-ink">Constitutional boundary:</div>
                      <div>
                        This action was categorized with risk level{' '}
                        <span className="font-machine text-xs text-jv-ink uppercase font-semibold">
                          {action?.risk_level || 'standard'}
                        </span>
                        . Under JackVerse security policy, consequential mutations and third-party
                        transmissions require explicit human authorization before execution.
                      </div>
                    </div>
                  )}

                  {/* Specialized Human Review for Preparation Packages: Exact Material Previews */}
                  {isPrepPackageApproval && caseArtifacts && caseArtifacts.length > 0 && (
                    <div className="space-y-3 border border-jv-rule bg-jv-bg/50 p-4">
                      <div className="flex items-center justify-between pb-2 border-b border-jv-rule">
                        <div className="font-machine text-xs uppercase font-semibold text-jv-ink flex items-center gap-2">
                          <FileText className="w-4 h-4 text-jv-accent" />
                          Exact Frozen Materials Previews ({caseArtifacts.length})
                        </div>
                        <span className="font-machine text-[11px] text-jv-muted">
                          FROZEN FOR REVIEW
                        </span>
                      </div>

                      {/* Preview Tabs */}
                      <div className="flex flex-wrap gap-2">
                        {caseArtifacts.map((art) => {
                          const activeArtId = selectedPreviewArtifactId || caseArtifacts[0]?.artifact_id;
                          const isSelected = activeArtId === art.artifact_id;
                          return (
                            <button
                              key={art.artifact_id}
                              type="button"
                              onClick={() => setSelectedPreviewArtifactId(art.artifact_id)}
                              className={`px-3 py-1 font-machine text-xs uppercase border transition-colors ${
                                isSelected
                                  ? 'border-jv-ink bg-jv-ink text-jv-bg font-semibold'
                                  : 'border-jv-rule bg-jv-surface text-jv-muted hover:text-jv-ink'
                              }`}
                            >
                              {art.material_type.replace(/_/g, ' ')}
                            </button>
                          );
                        })}
                      </div>

                      {/* Active Material Text Preview */}
                      {(() => {
                        const activeArtId = selectedPreviewArtifactId || caseArtifacts[0]?.artifact_id;
                        const art = caseArtifacts.find((a) => a.artifact_id === activeArtId) || caseArtifacts[0];
                        if (!art) return null;
                        return (
                          <div className="space-y-2">
                            <div className="flex items-center justify-between font-machine text-[11px] text-jv-muted">
                              <span>TITLE: {art.title}</span>
                              <span>STATUS: {art.status.toUpperCase()} (v{art.version})</span>
                            </div>
                            <div className="p-3 border border-jv-rule bg-jv-surface text-xs font-interface text-jv-ink max-h-72 overflow-y-auto whitespace-pre-wrap leading-relaxed">
                              {art.rendered_markdown}
                            </div>
                          </div>
                        );
                      })()}
                    </div>
                  )}

                  {/* Specialized Human Review for External Transmission: Exact Transmission Dossier */}
                  {isSubmitFormApproval && executionPlan && (
                    <div className="space-y-4 border border-jv-rule bg-jv-bg/50 p-4">
                      <div className="flex items-center justify-between pb-2 border-b border-jv-rule">
                        <div className="font-machine text-xs uppercase font-semibold text-jv-ink flex items-center gap-2">
                          <Send className="w-4 h-4 text-jv-accent" />
                          Exact External Transmission Dossier
                        </div>
                        <span className="font-machine text-[11px] text-emerald-600 border border-emerald-600/30 px-2 py-0.5 bg-emerald-600/10">
                          SEALED PLAN v{executionPlan.version}
                        </span>
                      </div>

                      {/* Destination Summary */}
                      {executionPlan.destination && (
                        <div className="p-3 border border-jv-rule bg-jv-surface text-xs space-y-2">
                          <div className="flex items-center justify-between font-machine text-[11px] text-jv-muted">
                            <span className="flex items-center gap-1.5 text-jv-ink font-semibold">
                              <Globe className="w-3.5 h-3.5 text-jv-accent" />
                              Target Destination
                            </span>
                            <span className="text-[10px] uppercase font-mono px-1.5 py-0.5 border border-jv-rule">
                              {executionPlan.destination.is_exact_destination ? 'EXACT DESTINATION' : 'DESTINATION REQUIRED'}
                            </span>
                          </div>
                          <div className="grid grid-cols-1 sm:grid-cols-2 gap-2 font-interface text-xs">
                            <div><span className="text-jv-muted">Organization:</span> <span className="font-medium text-jv-ink">{executionPlan.destination.organization || '—'}</span></div>
                            <div><span className="text-jv-muted">Channel Type:</span> <span className="font-mono text-jv-ink">{executionPlan.destination.destination_type}</span></div>
                          </div>
                          <div className="pt-1 border-t border-jv-rule/60 text-[11px] font-mono break-all text-jv-accent">
                            {executionPlan.destination.canonical_url}
                          </div>
                          <div className="text-[10px] font-machine text-jv-muted">
                            Provenance: <span className="text-jv-ink">{executionPlan.destination.destination_provenance}</span>
                          </div>
                        </div>
                      )}

                      {/* Materialized Upload Assets */}
                      {executionPlan.upload_mappings && executionPlan.upload_mappings.length > 0 && (
                        <div className="p-3 border border-jv-rule bg-jv-surface text-xs space-y-2">
                          <div className="flex items-center justify-between font-machine text-[11px] text-jv-muted">
                            <span className="flex items-center gap-1.5 text-jv-ink font-semibold">
                              <UploadCloud className="w-3.5 h-3.5 text-jv-accent" />
                              Materialized Upload Assets ({executionPlan.upload_mappings.length})
                            </span>
                          </div>
                          <div className="space-y-2">
                            {executionPlan.upload_mappings.map((u) => (
                              <div key={u.asset_id} className="p-2 border border-jv-rule/80 bg-jv-bg/60 text-xs font-interface space-y-1">
                                <div className="flex items-center justify-between font-medium text-jv-ink">
                                  <span>{u.filename}</span>
                                  <span className="text-[10px] font-mono text-jv-muted">{(u.byte_size / 1024).toFixed(1)} KB</span>
                                </div>
                                <div className="flex items-center justify-between text-[11px] text-jv-muted">
                                  <span>Role: <span className="font-mono text-jv-ink">{u.role}</span></span>
                                  <span className="font-mono">{u.mime_type}</span>
                                </div>
                                <div className="text-[10px] font-mono text-jv-muted truncate" title={u.content_sha256}>
                                  SHA-256: {u.content_sha256}
                                </div>
                              </div>
                            ))}
                          </div>
                        </div>
                      )}

                      {/* Form Field Mappings with Masking */}
                      {executionPlan.field_mappings && executionPlan.field_mappings.length > 0 && (
                        <div className="p-3 border border-jv-rule bg-jv-surface text-xs space-y-2">
                          <div className="flex items-center justify-between font-machine text-[11px] text-jv-muted">
                            <span className="flex items-center gap-1.5 text-jv-ink font-semibold">
                              <FileText className="w-3.5 h-3.5 text-jv-accent" />
                              Exact Form Field Values ({executionPlan.field_mappings.length})
                            </span>
                            <span className="text-[10px] text-jv-muted">PII MASKED BY DEFAULT</span>
                          </div>
                          <div className="space-y-1.5 max-h-60 overflow-y-auto">
                            {executionPlan.field_mappings.map((m) => {
                              const isRevealed = Boolean(revealedValues[`field_${m.target_field_id}`]);
                              const displayVal = String(m.field_value ?? '');
                              return (
                                <div key={m.target_field_id} className="p-2 border border-jv-rule/80 bg-jv-bg/60 text-xs flex items-center justify-between gap-2">
                                  <div className="space-y-0.5 min-w-0">
                                    <div className="font-mono text-[11px] font-medium text-jv-ink truncate">
                                      {m.field_name}
                                    </div>
                                    <div className="text-[10px] text-jv-muted font-mono truncate">
                                      ID: {m.target_field_id} · Fact: {m.source_fact_id} v{m.source_fact_version}
                                    </div>
                                  </div>
                                  <div className="flex items-center gap-2 shrink-0">
                                    <span className="font-mono text-xs px-2 py-0.5 border border-jv-rule bg-jv-surface text-jv-ink max-w-[180px] truncate">
                                      {isRevealed ? displayVal : '••••••••'}
                                    </span>
                                    <button
                                      type="button"
                                      onClick={() => toggleRevealValue(`field_${m.target_field_id}`)}
                                      className="p-1 border border-jv-rule text-jv-muted hover:text-jv-ink text-xs transition-colors"
                                      title={isRevealed ? 'Hide value' : 'Reveal value'}
                                    >
                                      {isRevealed ? <EyeOff className="w-3.5 h-3.5" /> : <Eye className="w-3.5 h-3.5" />}
                                    </button>
                                  </div>
                                </div>
                              );
                            })}
                          </div>
                        </div>
                      )}

                      {/* Grounded Screening Responses with Masking */}
                      {executionPlan.screening_responses && executionPlan.screening_responses.length > 0 && (
                        <div className="p-3 border border-jv-rule bg-jv-surface text-xs space-y-2">
                          <div className="flex items-center justify-between font-machine text-[11px] text-jv-muted">
                            <span className="flex items-center gap-1.5 text-jv-ink font-semibold">
                              <CheckCircle2 className="w-3.5 h-3.5 text-jv-accent" />
                              Grounded Screening Responses ({executionPlan.screening_responses.length})
                            </span>
                            <span className="text-[10px] text-jv-muted">ANSWERS MASKED BY DEFAULT</span>
                          </div>
                          <div className="space-y-1.5 max-h-48 overflow-y-auto">
                            {executionPlan.screening_responses.map((s) => {
                              const isRevealed = Boolean(revealedValues[`screen_${s.question_id}`]);
                              const displayAns = String(s.answer ?? '');
                              return (
                                <div key={s.question_id} className="p-2 border border-jv-rule/80 bg-jv-bg/60 text-xs flex items-center justify-between gap-2">
                                  <div className="space-y-0.5 min-w-0">
                                    <div className="font-interface text-xs text-jv-ink truncate">
                                      {s.question}
                                    </div>
                                    <div className="text-[10px] text-jv-muted font-mono">
                                      ID: {s.question_id}
                                    </div>
                                  </div>
                                  <div className="flex items-center gap-2 shrink-0">
                                    <span className="font-mono text-xs px-2 py-0.5 border border-jv-rule bg-jv-surface text-jv-ink max-w-[180px] truncate">
                                      {isRevealed ? displayAns : '••••••••'}
                                    </span>
                                    <button
                                      type="button"
                                      onClick={() => toggleRevealValue(`screen_${s.question_id}`)}
                                      className="p-1 border border-jv-rule text-jv-muted hover:text-jv-ink text-xs transition-colors"
                                      title={isRevealed ? 'Hide answer' : 'Reveal answer'}
                                    >
                                      {isRevealed ? <EyeOff className="w-3.5 h-3.5" /> : <Eye className="w-3.5 h-3.5" />}
                                    </button>
                                  </div>
                                </div>
                              );
                            })}
                          </div>
                        </div>
                      )}

                      {/* Final Submit Control */}
                      {executionPlan.final_submit_control && (
                        <div className="p-3 border border-jv-rule bg-jv-surface text-xs space-y-1">
                          <div className="font-machine text-[11px] text-jv-muted uppercase">Final Submit Control</div>
                          <div className="flex items-center justify-between font-interface">
                            <span className="font-medium text-jv-ink">{executionPlan.final_submit_control.semantic_target}</span>
                            <span className="font-machine text-[10px] text-jv-muted">Provenance: {executionPlan.final_submit_control.control_provenance}</span>
                          </div>
                          {executionPlan.final_submit_control.selector && (
                            <div className="text-[11px] font-mono text-jv-muted truncate">
                              Selector: {executionPlan.final_submit_control.selector}
                            </div>
                          )}
                        </div>
                      )}
                    </div>
                  )}

                  {/* Action Parameters & Cryptographic Binding Inspector */}
                  <div className="border border-jv-rule">
                    <button
                      type="button"
                      onClick={() => setShowParameters(!showParameters)}
                      className="w-full flex items-center justify-between px-4 py-3 bg-jv-bg text-sm font-interface text-jv-ink hover:text-jv-ink-soft border-b border-jv-rule"
                    >
                      <span>
                        {isSubmitFormApproval
                          ? 'Inspect cryptographic plan binding & fingerprints'
                          : isPrepPackageApproval
                          ? 'Inspect cryptographic binding & fingerprints'
                          : 'Inspect action parameters & fingerprint'}
                      </span>
                      {showParameters ? (
                        <ChevronUp className="w-4 h-4" />
                      ) : (
                        <ChevronDown className="w-4 h-4" />
                      )}
                    </button>

                    {showParameters && (
                      <div className="p-4 space-y-3 font-machine text-xs bg-jv-bg/60">
                        {isSubmitFormApproval && executionPlan && (
                          <>
                            <div className="space-y-1">
                              <span className="font-interface text-xs text-jv-muted">Plan Content Fingerprint:</span>
                              <div className="p-2 border border-jv-rule bg-jv-surface text-jv-ink break-all text-[11px] font-machine">
                                {executionPlan.execution_plan_fingerprint}
                              </div>
                            </div>
                            <div className="space-y-1">
                              <span className="font-interface text-xs text-jv-muted">Package Fingerprint:</span>
                              <div className="p-2 border border-jv-rule bg-jv-surface text-jv-ink break-all text-[11px] font-machine">
                                {executionPlan.package_fingerprint}
                              </div>
                            </div>
                            <div className="space-y-1">
                              <span className="font-interface text-xs text-jv-muted">Target Schema Hash:</span>
                              <div className="p-2 border border-jv-rule bg-jv-surface text-jv-ink break-all text-[11px] font-machine">
                                {executionPlan.target_schema_hash}
                              </div>
                            </div>
                            <div className="space-y-1">
                              <span className="font-interface text-xs text-jv-muted">Execution Input Hash:</span>
                              <div className="p-2 border border-jv-rule bg-jv-surface text-jv-ink break-all text-[11px] font-machine">
                                {executionPlan.execution_input_hash}
                              </div>
                            </div>
                          </>
                        )}

                        {isPrepPackageApproval && prepPackage && (
                          <div className="space-y-1">
                            <span className="font-interface text-xs text-jv-muted">Package Content Fingerprint:</span>
                            <div className="p-2 border border-jv-rule bg-jv-surface text-jv-ink break-all text-[11px] font-machine">
                              {prepPackage.package_fingerprint}
                            </div>
                          </div>
                        )}

                        <div className="space-y-1">
                          <span className="font-interface text-xs text-jv-muted">Action Fingerprint:</span>
                          <div className="p-2 border border-jv-rule bg-jv-surface text-jv-ink break-all text-[11px] font-machine">
                            {activeApproval.action_fingerprint}
                          </div>
                        </div>

                        <div className="space-y-1">
                          <span className="font-interface text-xs text-jv-muted">Action Parameters:</span>
                          <pre className="p-2 border border-jv-rule bg-jv-surface text-jv-ink text-[11px] font-machine overflow-x-auto max-h-28 overflow-y-auto leading-relaxed">
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
                            label={
                              isSubmitFormApproval
                                ? "Slide to authorize transmission"
                                : isPrepPackageApproval
                                ? "Slide to approve preparation package locally"
                                : "Slide to authorize action"
                            }
                            consequentialDescription={
                              isSubmitFormApproval
                                ? "Transmission authorization: Approving provides explicit consent to transmit these exact values to the declared destination. Execution is deferred to Phase 5.3.3."
                                : isPrepPackageApproval
                                ? "Local compilation authorization: Approving binds your explicit consent of these exact materials. No external submission will be initiated."
                                : "Consequential operation: Authorizing binds your explicit consent under cryptographic fingerprint verification."
                            }
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
                        <div className="text-jv-ink font-medium uppercase">
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
