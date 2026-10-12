import React, { useState } from 'react';
import { useParams, useNavigate, Link } from 'react-router-dom';
import { ArrowLeft, AlertTriangle, CheckCircle2, Sparkles } from 'lucide-react';
import {
  useCase,
  useCaseActions,
  useClaims,
  useEvents,
  useTransitionCase,
  useResolveCase,
  useProposeClaim,
  useEvaluateClaim,
  useRequestActionApproval,
  usePreparationReport,
  useGeneratePreparation,
} from '../hooks/useCaseworker';
import { apiRequest } from '../api/client';
import type { Claim } from '../api/types';
import { TextureBadge } from '../components/ui/TextureBadge';
import { TactileButton } from '../components/ui/TactileButton';
import { humanizeEvent, formatEventTime } from '../lib/eventPresentation';

export const CaseDetailView: React.FC = () => {
  const { caseId } = useParams<{ caseId: string }>();
  const navigate = useNavigate();

  const { data: caseData, isLoading, error, refetch: refetchCase } = useCase(caseId);
  const { data: actions, isLoading: actionsLoading } = useCaseActions(caseId);
  const { data: claims, isLoading: claimsLoading } = useClaims({ case_id: caseId });
  const { data: prepReport, isLoading: prepLoading } = usePreparationReport(caseId);
  const { data: eventsData } = useEvents();

  const transitionCase = useTransitionCase();
  const resolveCase = useResolveCase();
  const proposeClaim = useProposeClaim();
  const evaluateClaim = useEvaluateClaim();
  const requestApproval = useRequestActionApproval();
  const generatePrep = useGeneratePreparation();

  const [concurrencyNotice, setConcurrencyNotice] = useState<string | null>(null);
  const [resolveOutcome, setResolveOutcome] = useState('');
  const [showResolveForm, setShowResolveForm] = useState(false);

  // Quick claim creation
  const [showClaimForm, setShowClaimForm] = useState(false);
  const [claimText, setClaimText] = useState('');
  const [claimPurpose, setClaimPurpose] = useState('job_application');
  const [supportingFactsInput, setSupportingFactsInput] = useState('');

  if (isLoading) {
    return (
      <div className="py-24 text-center font-interface text-sm text-jv-muted">
        Loading case dossier…
      </div>
    );
  }

  if (error || !caseData?.caseItem) {
    return (
      <div className="py-24 text-center space-y-4 font-interface">
        <div className="font-machine text-xs text-jv-muted uppercase">HTTP 404 // NOT FOUND</div>
        <h1 className="font-display text-4xl text-jv-ink">Case Dossier Not Found</h1>
        <p className="text-sm text-jv-muted">
          This case does not exist or belongs to another user scope.
        </p>
        <TactileButton variant="primary" size="md" onClick={() => navigate('/missions')}>
          Back to Missions →
        </TactileButton>
      </div>
    );
  }

  const { caseItem, etag } = caseData;

  const handleTransition = async (newStatus: string) => {
    if (!etag || !caseId) return;
    setConcurrencyNotice(null);
    try {
      await transitionCase.mutateAsync({
        caseId,
        newStatus,
        etag,
      });
    } catch (err: any) {
      if (err.name === 'PreconditionFailedError' || err.status === 412) {
        setConcurrencyNotice("This changed elsewhere. We've loaded the latest version.");
        refetchCase();
      } else {
        setConcurrencyNotice(err.message || 'Transition failed');
      }
    }
  };

  const handleResolve = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!etag || !caseId || !resolveOutcome.trim()) return;
    setConcurrencyNotice(null);
    try {
      await resolveCase.mutateAsync({
        caseId,
        outcome: resolveOutcome.trim(),
        etag,
      });
      setShowResolveForm(false);
      setResolveOutcome('');
    } catch (err: any) {
      if (err.name === 'PreconditionFailedError' || err.status === 412) {
        setConcurrencyNotice("This changed elsewhere. We've loaded the latest version.");
      } else {
        setConcurrencyNotice(err.message || 'Resolution failed');
      }
    }
  };

  const handleProposeClaim = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!caseId || !claimText.trim()) return;
    setConcurrencyNotice(null);
    const factIds = supportingFactsInput
      .split(/[\s,]+/)
      .map((s) => s.trim())
      .filter((s) => s.length > 0);

    try {
      await proposeClaim.mutateAsync({
        purpose: claimPurpose,
        text: claimText.trim(),
        case_id: caseId,
        mission_id: caseItem.mission_id || undefined,
        supporting_fact_ids: factIds,
        auto_evaluate: true,
      });
      setClaimText('');
      setSupportingFactsInput('');
      setShowClaimForm(false);
    } catch (err: any) {
      setConcurrencyNotice(err.message || 'Failed to propose claim');
    }
  };

  const handleEvaluateClaim = async (claimId: string) => {
    setConcurrencyNotice(null);
    try {
      const res = await apiRequest<Claim>(`/claims/${claimId}`);
      if (!res.etag) {
        throw new Error('Claim response missing ETag header');
      }
      await evaluateClaim.mutateAsync({
        claimId,
        etag: res.etag,
      });
    } catch (err: any) {
      if (err.name === 'PreconditionFailedError' || err.status === 412) {
        setConcurrencyNotice("This changed elsewhere. We've loaded the latest version.");
      } else {
        setConcurrencyNotice(err.message || 'Claim evaluation failed');
      }
    }
  };

  const handleGeneratePrep = async () => {
    if (!caseId) return;
    setConcurrencyNotice(null);
    try {
      await generatePrep.mutateAsync({ caseId });
      refetchCase();
    } catch (err: any) {
      setConcurrencyNotice(err.message || 'Preparation intelligence analysis failed');
    }
  };

  const caseEvents = (eventsData?.items || []).filter(
    (ev) => ev.aggregate_id === caseItem.case_id || ev.payload?.case_id === caseItem.case_id
  );

  return (
    <div className="space-y-12 font-interface text-jv-ink">
      {/* Return link */}
      <div>
        {caseItem.mission_id ? (
          <Link
            to={`/missions/${caseItem.mission_id}`}
            className="inline-flex items-center gap-2 font-interface text-sm text-jv-muted hover:text-jv-ink transition-colors"
          >
            <ArrowLeft className="w-3.5 h-3.5" />
            <span>Return to parent mission // <span className="font-machine text-xs">{caseItem.mission_id.slice(0, 8)}</span></span>
          </Link>
        ) : (
          <Link
            to="/missions"
            className="inline-flex items-center gap-2 font-interface text-sm text-jv-muted hover:text-jv-ink transition-colors"
          >
            <ArrowLeft className="w-3.5 h-3.5" />
            <span>Return to missions</span>
          </Link>
        )}
      </div>

      {/* Concurrency Notification */}
      {concurrencyNotice && (
        <div className="p-4 border border-jv-rule bg-jv-surface flex items-center gap-3 text-xs font-machine text-jv-ink">
          <AlertTriangle className="w-4 h-4 shrink-0 text-jv-ink" />
          <span>{concurrencyNotice}</span>
        </div>
      )}

      {/* Case Header */}
      <div className="border-b border-jv-rule pb-8 space-y-4">
        <div className="flex items-center justify-between font-machine text-xs text-jv-muted">
          <div>CASE / {caseItem.case_id.slice(0, 8)}</div>
          <TextureBadge status={caseItem.status} />
        </div>

        <h1 className="font-display font-normal text-4xl lg:text-5xl text-jv-ink tracking-tight leading-tight">
          {caseItem.title}
        </h1>

        <div className="flex flex-wrap items-center gap-4 sm:gap-6 font-machine text-xs text-jv-muted pt-1">
          <span>TYPE: {caseItem.case_type.toUpperCase()}</span>
          <span className="text-jv-rule">·</span>
          <span>STATUS: {caseItem.status.toUpperCase()}</span>
          <span className="text-jv-rule">·</span>
          <span>VERSION: v{caseItem.version}</span>
          {caseItem.deadline && (
            <>
              <span className="text-jv-rule">·</span>
              <span>DEADLINE: {new Date(caseItem.deadline).toLocaleDateString()}</span>
            </>
          )}
        </div>

        {/* State Machine Transition Controls */}
        <div className="flex flex-wrap items-center gap-3 pt-4">
          {caseItem.status === 'new' && (
            <TactileButton
              variant="primary"
              size="sm"
              loading={transitionCase.isPending}
              onClick={() => handleTransition('intake')}
            >
              Initiate Intake →
            </TactileButton>
          )}
          {caseItem.status === 'intake' && (
            <TactileButton
              variant="primary"
              size="sm"
              loading={transitionCase.isPending}
              onClick={() => handleTransition('investigating')}
            >
              Begin Investigation →
            </TactileButton>
          )}
          {caseItem.status === 'investigating' && (
            <>
              <TactileButton
                variant="outline"
                size="sm"
                loading={transitionCase.isPending}
                onClick={() => handleTransition('planning')}
              >
                Move to Planning
              </TactileButton>
              <TactileButton
                variant="secondary"
                size="sm"
                onClick={() => setShowResolveForm(true)}
              >
                Resolve Case ■
              </TactileButton>
            </>
          )}
          {caseItem.status === 'planning' && (
            <TactileButton
              variant="primary"
              size="sm"
              loading={transitionCase.isPending}
              onClick={() => handleTransition('action_required')}
            >
              Declare Action Required →
            </TactileButton>
          )}
          {caseItem.status === 'action_required' && (
            <TactileButton
              variant="outline"
              size="sm"
              loading={transitionCase.isPending}
              onClick={() => handleTransition('action_in_progress')}
            >
              Dispatch Action In Progress
            </TactileButton>
          )}
          {caseItem.status === 'action_in_progress' && (
            <TactileButton
              variant="outline"
              size="sm"
              loading={transitionCase.isPending}
              onClick={() => handleTransition('waiting_external')}
            >
              Wait External Response
            </TactileButton>
          )}
        </div>

        {/* Inline Resolve Form */}
        {showResolveForm && (
          <form onSubmit={handleResolve} className="mt-4 p-4 border border-jv-rule bg-jv-surface space-y-3">
            <div className="font-interface font-medium text-sm text-jv-ink">
              Specify operational outcome
            </div>
            <input
              type="text"
              value={resolveOutcome}
              onChange={(e) => setResolveOutcome(e.target.value)}
              placeholder="e.g. Accepted offer; contract executed"
              required
              className="w-full bg-jv-bg border border-jv-rule px-3 py-2 text-sm text-jv-ink outline-none focus:border-jv-ink font-interface"
            />
            <div className="flex justify-end gap-3">
              <TactileButton type="button" variant="outline" size="sm" onClick={() => setShowResolveForm(false)}>
                Cancel
              </TactileButton>
              <TactileButton type="submit" variant="primary" size="sm" loading={resolveCase.isPending}>
                Confirm Resolution ■
              </TactileButton>
            </div>
          </form>
        )}
      </div>

      {/* Grid: Overview, Claims, Actions, Timeline */}
      <div className="grid grid-cols-1 lg:grid-cols-12 gap-12">
        {/* Left Column: Overview, Claims, Actions (8 cols) */}
        <div className="lg:col-span-8 space-y-12">
          {/* Section 1: Overview */}
          <section className="space-y-4">
            <h2 className="font-interface font-medium text-xl text-jv-ink tracking-tight border-b border-jv-rule pb-2">
              Case parameters
            </h2>
            <div className="p-6 border border-jv-rule bg-jv-surface/50 space-y-4">
              <div>
                <span className="font-interface text-xs text-jv-muted block mb-1">
                  Primary objective:
                </span>
                <p className="text-sm text-jv-ink leading-relaxed">{caseItem.goal}</p>
              </div>

              {caseItem.outcome && (
                <div className="border-t border-jv-rule pt-3">
                  <span className="font-interface text-xs text-jv-ink flex items-center gap-2 mb-1">
                    <CheckCircle2 className="w-4 h-4 text-jv-ink" />
                    Operational outcome:
                  </span>
                  <p className="text-sm text-jv-ink font-medium">{caseItem.outcome}</p>
                </div>
              )}
            </div>
          </section>

          {/* Section 2: Preparation Intelligence */}
          <section className="space-y-4">
            <div className="flex items-baseline justify-between border-b border-jv-rule pb-2">
              <div className="flex items-center gap-3">
                <h2 className="font-interface font-medium text-xl text-jv-ink tracking-tight">
                  Preparation intelligence
                </h2>
                {prepReport && (
                  <span className="font-machine text-xs px-2 py-0.5 border border-jv-rule bg-jv-surface text-jv-muted">
                    v{prepReport.version}
                  </span>
                )}
              </div>
              {prepReport ? (
                <TactileButton
                  variant="outline"
                  size="sm"
                  loading={generatePrep.isPending}
                  onClick={handleGeneratePrep}
                >
                  <Sparkles className="w-3.5 h-3.5 mr-1.5 inline" />
                  Re-analyze Preparation
                </TactileButton>
              ) : (
                <TactileButton
                  variant="primary"
                  size="sm"
                  loading={generatePrep.isPending}
                  onClick={handleGeneratePrep}
                >
                  <Sparkles className="w-3.5 h-3.5 mr-1.5 inline" />
                  Analyze Preparation →
                </TactileButton>
              )}
            </div>

            {prepLoading ? (
              <div className="py-6 font-interface text-sm text-jv-muted">
                Loading preparation intelligence dossier…
              </div>
            ) : !prepReport ? (
              <div className="p-8 border border-dashed border-jv-rule bg-jv-surface/20 text-center space-y-4">
                <div className="font-machine text-xs text-jv-muted uppercase">
                  PREPARATION INTELLIGENCE // UNINITIALIZED
                </div>
                <h3 className="font-display text-2xl text-jv-ink">
                  No Preparation Intelligence Generated
                </h3>
                <p className="font-interface text-sm text-jv-muted max-w-xl mx-auto">
                  Analyze the converted prospect requirements against your verified context vault to map evidence alignments, identify actionable gaps, and synthesize strategic claim intents.
                </p>
                <div className="pt-2">
                  <TactileButton
                    variant="primary"
                    size="md"
                    loading={generatePrep.isPending}
                    onClick={handleGeneratePrep}
                  >
                    <Sparkles className="w-4 h-4 mr-2 inline" />
                    Analyze Preparation Intelligence →
                  </TactileButton>
                </div>
              </div>
            ) : (
              <div className="space-y-6">
                {/* Staleness Banner */}
                {prepReport.is_stale && (
                  <div className="p-4 border border-jv-rule bg-jv-surface flex flex-col sm:flex-row sm:items-center justify-between gap-3 text-xs font-interface text-jv-ink">
                    <div className="flex items-center gap-2.5">
                      <AlertTriangle className="w-4 h-4 text-jv-accent shrink-0" />
                      <span>
                        <strong>Intelligence Report is Stale:</strong> Source opportunity requirements or context vault facts have changed since this report was generated.
                      </span>
                    </div>
                    <TactileButton
                      variant="outline"
                      size="sm"
                      loading={generatePrep.isPending}
                      onClick={handleGeneratePrep}
                    >
                      Re-analyze Now →
                    </TactileButton>
                  </div>
                )}

                {/* Report Metadata Bar */}
                <div className="flex flex-wrap items-center gap-4 text-xs font-machine text-jv-muted p-3 border border-jv-rule bg-jv-surface/40">
                  <span>REPORT: {prepReport.report_id.slice(0, 8)}</span>
                  <span className="text-jv-rule">·</span>
                  <span>ANALYZER: {prepReport.analyzer_version}</span>
                  <span className="text-jv-rule">·</span>
                  <span>INPUT: {prepReport.input_hash.slice(0, 8)}</span>
                  <span className="text-jv-rule">·</span>
                  <span>SNAPSHOT: {prepReport.context_snapshot_hash.slice(0, 8)}</span>
                  <span className="text-jv-rule">·</span>
                  <span>DATE: {new Date(prepReport.created_at).toLocaleDateString()}</span>
                </div>

                {/* Evidence Alignment Matrix (5 States) */}
                <div className="space-y-3">
                  <div className="flex items-center justify-between">
                    <h3 className="font-machine text-xs text-jv-muted uppercase tracking-wider">
                      Evidence Alignment Matrix
                    </h3>
                    <span className="font-machine text-xs text-jv-muted">
                      {(prepReport.evidence_map || []).length} CRITERIA EVALUATED
                    </span>
                  </div>

                  {/* 5-State Summary Counters */}
                  <div className="grid grid-cols-2 sm:grid-cols-5 gap-2 text-center font-machine">
                    <div className="p-2.5 border border-jv-rule bg-jv-bg">
                      <div className="text-lg font-bold text-jv-ink">
                        {(prepReport.evidence_map || []).filter((e) => e.status === 'SUPPORTED').length}
                      </div>
                      <div className="text-[10px] text-jv-muted uppercase">SUPPORTED</div>
                    </div>
                    <div className="p-2.5 border border-jv-rule bg-jv-bg">
                      <div className="text-lg font-bold text-jv-ink">
                        {(prepReport.evidence_map || []).filter((e) => e.status === 'POTENTIAL').length}
                      </div>
                      <div className="text-[10px] text-jv-muted uppercase">POTENTIAL</div>
                    </div>
                    <div className="p-2.5 border border-jv-rule bg-jv-bg">
                      <div className="text-lg font-bold text-jv-ink">
                        {(prepReport.evidence_map || []).filter((e) => e.status === 'RESTRICTED').length}
                      </div>
                      <div className="text-[10px] text-jv-muted uppercase">RESTRICTED</div>
                    </div>
                    <div className="p-2.5 border border-jv-rule bg-jv-bg">
                      <div className="text-lg font-bold text-jv-ink">
                        {(prepReport.evidence_map || []).filter((e) => e.status === 'CONTRADICTED').length}
                      </div>
                      <div className="text-[10px] text-jv-muted uppercase">CONTRADICTED</div>
                    </div>
                    <div className="p-2.5 border border-jv-rule bg-jv-bg">
                      <div className="text-lg font-bold text-jv-ink">
                        {(prepReport.evidence_map || []).filter((e) => e.status === 'GAP').length}
                      </div>
                      <div className="text-[10px] text-jv-muted uppercase">GAP</div>
                    </div>
                  </div>

                  {/* Criteria Items List */}
                  <div className="divide-y divide-jv-rule border border-jv-rule">
                    {(prepReport.evidence_map || []).map((item) => (
                      <div key={item.requirement_id} className="p-4 space-y-2 bg-jv-surface/20">
                        <div className="flex items-start justify-between gap-3">
                          <div className="space-y-1">
                            <div className="flex items-center gap-2 font-machine text-xs">
                              <span className={item.mandatory ? 'font-bold text-jv-ink' : 'text-jv-muted'}>
                                {item.mandatory ? '[MANDATORY]' : '[PREFERRED]'}
                              </span>
                              <span className="uppercase text-jv-muted">{item.requirement_type}</span>
                            </div>
                            <p className="font-interface text-sm text-jv-ink font-medium">{item.description}</p>
                          </div>
                          <TextureBadge status={item.status} />
                        </div>

                        <div className="flex flex-wrap items-center gap-x-4 gap-y-1 font-machine text-xs text-jv-muted pt-1">
                          {item.matched_fact_id && (
                            <span>
                              FACT: {item.fact_namespace ? `${item.fact_namespace}.${item.fact_key || ''}` : item.matched_fact_id.slice(0, 8)}
                              {item.verification_status && ` (${item.verification_status.toUpperCase()})`}
                            </span>
                          )}
                          {item.notes && (
                            <span className="font-interface text-xs text-jv-muted italic">
                              Note: {item.notes}
                            </span>
                          )}
                        </div>
                      </div>
                    ))}
                  </div>
                </div>

                {/* Gap Analysis (3 Classes) */}
                <div className="space-y-3">
                  <div className="flex items-center justify-between">
                    <h3 className="font-machine text-xs text-jv-muted uppercase tracking-wider">
                      Identified Gaps & Conflicts
                    </h3>
                    <span className="font-machine text-xs text-jv-muted">
                      {(prepReport.gaps || []).length} ISSUE{(prepReport.gaps || []).length === 1 ? '' : 'S'}
                    </span>
                  </div>

                  {(prepReport.gaps || []).length === 0 ? (
                    <div className="p-4 border border-jv-rule bg-jv-surface/30 flex items-center gap-2.5 text-xs font-interface text-jv-ink">
                      <CheckCircle2 className="w-4 h-4 text-jv-ink shrink-0" />
                      <span>Zero eligibility or evidence gaps identified. Profile fully satisfies requirements.</span>
                    </div>
                  ) : (
                    <div className="divide-y divide-jv-rule border border-jv-rule">
                      {(prepReport.gaps || []).map((gap) => {
                        const isConflict = gap.category === 'HARD_CONFLICT';
                        const isMandatory = gap.category === 'MANDATORY_EVIDENCE_GAP';
                        return (
                          <div key={gap.gap_id} className="p-4 space-y-2 bg-jv-surface/20">
                            <div className="flex items-start justify-between gap-3">
                              <div className="space-y-1">
                                <div className="flex items-center gap-2 font-machine text-xs">
                                  <span
                                    className={`px-1.5 py-0.5 border uppercase text-[10px] ${
                                      isConflict
                                        ? 'border-jv-ink bg-jv-ink text-jv-bg font-bold'
                                        : isMandatory
                                        ? 'border-jv-ink font-medium text-jv-ink'
                                        : 'border-jv-rule text-jv-muted'
                                    }`}
                                  >
                                    {gap.category.replace(/_/g, ' ')}
                                  </span>
                                  <span className="text-jv-muted uppercase">SEVERITY: {gap.severity}</span>
                                </div>
                                <p className="font-interface text-sm text-jv-ink font-medium">{gap.description}</p>
                              </div>
                            </div>
                            <div className="p-2.5 border border-jv-rule/60 bg-jv-bg font-interface text-xs text-jv-ink-soft">
                              <span className="font-machine text-[10px] uppercase text-jv-muted block mb-0.5">
                                Actionable Mitigation Guidance:
                              </span>
                              {gap.mitigation_guidance}
                            </div>
                          </div>
                        );
                      })}
                    </div>
                  )}
                </div>

                {/* Strategic Direction */}
                <div className="space-y-4">
                  <h3 className="font-machine text-xs text-jv-muted uppercase tracking-wider">
                    Strategic Direction
                  </h3>

                  <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                    {/* Key Strengths */}
                    <div className="p-4 border border-jv-rule bg-jv-surface/20 space-y-2">
                      <div className="font-machine text-xs text-jv-muted uppercase tracking-wider">
                        Key Strengths to Highlight
                      </div>
                      {(prepReport.strategy?.strengths || []).length === 0 ? (
                        <p className="font-interface text-xs text-jv-muted">No explicit strengths derived.</p>
                      ) : (
                        <ul className="space-y-1.5 font-interface text-xs text-jv-ink">
                          {(prepReport.strategy?.strengths || []).map((s, idx) => (
                            <li key={idx} className="flex items-start gap-2">
                              <span className="font-machine text-xs text-jv-muted">▸</span>
                              <span>{s}</span>
                            </li>
                          ))}
                        </ul>
                      )}
                    </div>

                    {/* Strategic Mitigations */}
                    <div className="p-4 border border-jv-rule bg-jv-surface/20 space-y-2">
                      <div className="font-machine text-xs text-jv-muted uppercase tracking-wider">
                        Strategic Mitigations
                      </div>
                      {(prepReport.strategy?.mitigations || []).length === 0 ? (
                        <p className="font-interface text-xs text-jv-muted">No mitigations necessary.</p>
                      ) : (
                        <ul className="space-y-1.5 font-interface text-xs text-jv-ink">
                          {(prepReport.strategy?.mitigations || []).map((m, idx) => (
                            <li key={idx} className="flex items-start gap-2">
                              <span className="font-machine text-xs text-jv-muted">▸</span>
                              <span>{m}</span>
                            </li>
                          ))}
                        </ul>
                      )}
                    </div>
                  </div>

                  {/* Claim Intents */}
                  <div className="space-y-3 pt-2">
                    <div className="font-machine text-xs text-jv-muted uppercase tracking-wider">
                      Claim Intents ({(prepReport.strategy?.claim_intents || []).length})
                    </div>
                    {(prepReport.strategy?.claim_intents || []).length === 0 ? (
                      <div className="p-4 border border-jv-rule text-center font-interface text-xs text-jv-muted bg-jv-surface/20">
                        No claim intents formulated yet.
                      </div>
                    ) : (
                      <div className="divide-y divide-jv-rule border border-jv-rule">
                        {(prepReport.strategy?.claim_intents || []).map((intent) => (
                          <div key={intent.intent_id} className="p-4 space-y-2 bg-jv-surface/20">
                            <div className="flex items-center justify-between gap-2">
                              <span className="font-machine text-xs font-semibold text-jv-ink">
                                {intent.claim_topic}
                              </span>
                              <span className="font-machine text-[10px] px-2 py-0.5 border border-jv-rule uppercase text-jv-muted">
                                TARGET: {intent.target_material}
                              </span>
                            </div>
                            <div className="font-interface text-xs text-jv-ink-soft">
                              <span className="font-machine text-[10px] uppercase text-jv-muted block mb-0.5">
                                Suggested Angle:
                              </span>
                              {intent.suggested_angle}
                            </div>
                            <div className="font-machine text-[10px] text-jv-muted">
                              SUPPORTING FACTS: {intent.supporting_fact_ids?.length || 0} LINKED
                            </div>
                          </div>
                        ))}
                      </div>
                    )}
                  </div>

                  {/* Materials Checklist */}
                  <div className="space-y-3 pt-2">
                    <div className="flex items-center justify-between">
                      <div className="font-machine text-xs text-jv-muted uppercase tracking-wider">
                        Materials Checklist
                      </div>
                      <span className="font-machine text-[10px] text-jv-muted">
                        PHASE 5.2.1 // DRAFTING COMMENCES IN 5.2.2
                      </span>
                    </div>
                    <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
                      {(prepReport.strategy?.materials_checklist || []).map((mat: any, idx: number) => {
                        const type = mat.material_type || mat.type || 'DOCUMENT';
                        const status = mat.status || 'PENDING_DRAFT';
                        const count = mat.claim_intents_count ?? 0;
                        return (
                          <div key={idx} className="p-3 border border-jv-rule bg-jv-bg flex items-center justify-between">
                            <div className="space-y-0.5">
                              <div className="font-machine text-xs uppercase font-medium text-jv-ink">
                                {String(type).replace(/_/g, ' ')}
                              </div>
                              <div className="font-interface text-[11px] text-jv-muted">
                                {count} claim intent{count === 1 ? '' : 's'} assigned
                              </div>
                            </div>
                            <TextureBadge status={String(status)} />
                          </div>
                        );
                      })}
                    </div>
                  </div>
                </div>
              </div>
            )}
          </section>

          {/* Section 3: Associated Claims */}
          <section className="space-y-4">
            <div className="flex items-baseline justify-between border-b border-jv-rule pb-2">
              <h2 className="font-interface font-medium text-xl text-jv-ink tracking-tight">
                Claims
              </h2>
              <TactileButton variant="outline" size="sm" onClick={() => setShowClaimForm(!showClaimForm)}>
                {showClaimForm ? 'Cancel' : '+ Propose Claim'}
              </TactileButton>
            </div>

            {/* Inline Propose Claim Form */}
            {showClaimForm && (
              <form onSubmit={handleProposeClaim} className="border border-jv-rule bg-jv-surface p-4 space-y-3">
                <div className="font-interface font-medium text-sm text-jv-ink">
                  Propose claim
                </div>
                <div className="space-y-1">
                  <label className="font-interface text-sm text-jv-muted">Claim purpose</label>
                  <select
                    value={claimPurpose}
                    onChange={(e) => setClaimPurpose(e.target.value)}
                    className="w-full bg-jv-bg border border-jv-rule px-3 py-1.5 text-sm text-jv-ink font-interface outline-none focus:border-jv-ink"
                  >
                    <option value="job_application">Job Application</option>
                    <option value="housing_search">Housing Search</option>
                    <option value="general">General</option>
                  </select>
                </div>
                <div className="space-y-1">
                  <label className="font-interface text-sm text-jv-muted">
                    Supporting Fact IDs (optional, space or comma-separated)
                  </label>
                  <input
                    type="text"
                    value={supportingFactsInput}
                    onChange={(e) => setSupportingFactsInput(e.target.value)}
                    placeholder="e.g. fact_abc123 fact_xyz789"
                    className="w-full bg-jv-bg border border-jv-rule px-3 py-1.5 text-sm text-jv-ink font-interface outline-none focus:border-jv-ink"
                  />
                </div>
                <textarea
                  value={claimText}
                  onChange={(e) => setClaimText(e.target.value)}
                  placeholder="State claim assertion (e.g. 5 years Python engineering experience)..."
                  required
                  rows={2}
                  className="w-full bg-jv-bg border border-jv-rule px-3 py-2 text-sm text-jv-ink font-interface outline-none focus:border-jv-ink"
                />
                <div className="flex justify-end gap-3">
                  <TactileButton type="button" variant="outline" size="sm" onClick={() => setShowClaimForm(false)}>
                    Cancel
                  </TactileButton>
                  <TactileButton type="submit" variant="primary" size="sm" loading={proposeClaim.isPending}>
                    Propose Claim →
                  </TactileButton>
                </div>
              </form>
            )}

            {claimsLoading ? (
              <div className="py-6 font-interface text-sm text-jv-muted">Loading claims…</div>
            ) : !claims || claims.length === 0 ? (
              <div className="p-8 border border-jv-rule text-center font-interface text-sm text-jv-muted bg-jv-surface/20">
                No claims bound to this case yet.
              </div>
            ) : (
              <div className="divide-y divide-jv-rule border-y border-jv-rule">
                {claims.map((cl) => (
                  <div key={cl.claim_id} className="py-4 space-y-2">
                    <div className="flex items-center justify-between font-machine text-xs">
                      <span className="text-jv-muted">{cl.claim_id.slice(0, 8)}</span>
                      <div className="flex items-center gap-2">
                        <TextureBadge status={cl.status} />
                        {cl.status !== 'supported' && (
                          <TactileButton
                            variant="outline"
                            size="sm"
                            loading={evaluateClaim.isPending}
                            onClick={() => handleEvaluateClaim(cl.claim_id)}
                          >
                            Evaluate Claim →
                          </TactileButton>
                        )}
                      </div>
                    </div>
                    <p className="text-sm text-jv-ink font-interface">{cl.text}</p>
                    <div className="font-machine text-[11px] text-jv-muted">
                      PURPOSE: {cl.purpose} · FACTS LINKED: {cl.supporting_fact_ids?.length || 0}
                    </div>
                  </div>
                ))}
              </div>
            )}
          </section>

          {/* Section 4: Case Actions */}
          <section className="space-y-4">
            <div className="border-b border-jv-rule pb-2">
              <h2 className="font-interface font-medium text-xl text-jv-ink tracking-tight">
                Actions
              </h2>
            </div>

            {actionsLoading ? (
              <div className="py-6 font-interface text-sm text-jv-muted">Loading actions…</div>
            ) : !actions || actions.length === 0 ? (
              <div className="p-8 border border-jv-rule text-center font-interface text-sm text-jv-muted bg-jv-surface/20">
                No actions recorded yet.
              </div>
            ) : (
              <div className="divide-y divide-jv-rule border-y border-jv-rule">
                {actions.map((act) => (
                  <div key={act.action_id} className="py-5 space-y-2">
                    <div className="flex items-center justify-between">
                      <div className="flex items-center gap-3">
                        <span className="font-machine text-xs text-jv-muted">{act.action_id.slice(0, 8)}</span>
                        <span className="font-interface font-medium text-jv-ink">{act.description}</span>
                      </div>
                      <div className="flex items-center gap-3">
                        <span className="font-machine text-xs uppercase px-2 py-0.5 border border-jv-rule text-jv-muted">
                          {act.risk_level} RISK
                        </span>
                        <TextureBadge status={act.status} />
                      </div>
                    </div>
                    <div className="flex items-center justify-between pt-1">
                      <div className="font-interface text-xs text-jv-muted">
                        Type: {act.action_type} · Approval required: {act.requires_approval ? 'Yes' : 'No'}
                      </div>
                      {act.status === 'proposed' && act.requires_approval && (
                        <TactileButton
                          variant="secondary"
                          size="sm"
                          loading={requestApproval.isPending}
                          onClick={async () => {
                            setConcurrencyNotice(null);
                            try {
                              const etag = `"action:${act.action_id}:v${act.version}"`;
                              await requestApproval.mutateAsync({ actionId: act.action_id, etag });
                            } catch (err: any) {
                              if (err.name === 'PreconditionFailedError' || err.status === 412) {
                                setConcurrencyNotice("This action was modified elsewhere. We've loaded the latest version.");
                                refetchCase();
                              } else {
                                setConcurrencyNotice(err.message || 'Approval request failed');
                              }
                            }
                          }}
                        >
                          Request Approval →
                        </TactileButton>
                      )}
                    </div>
                  </div>
                ))}
              </div>
            )}
          </section>
        </div>

        {/* Right Column: Case Timeline (4 cols) */}
        <div className="lg:col-span-4 space-y-6">
          <div className="border-b border-jv-rule pb-2">
            <h2 className="font-interface font-medium text-xl text-jv-ink tracking-tight">
              Case chronicle
            </h2>
          </div>

          <div className="space-y-4">
            {caseEvents.length === 0 ? (
              <div className="text-jv-muted font-interface text-sm">No activity recorded yet.</div>
            ) : (
              caseEvents.map((ev) => (
                <div key={ev.event_id} className="pb-3 border-b border-jv-rule/50 space-y-1">
                  <div className="text-jv-muted font-machine text-xs">
                    {formatEventTime(ev.occurred_at)}
                  </div>
                  <div className="text-jv-ink font-medium font-interface text-sm">{humanizeEvent(ev)}</div>
                </div>
              ))
            )}
          </div>
        </div>
      </div>
    </div>
  );
};
