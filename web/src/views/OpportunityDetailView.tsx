import React, { useState } from 'react';
import { useParams, useNavigate, Link } from 'react-router-dom';
import { ArrowLeft, ExternalLink, AlertTriangle, CheckCircle2, XCircle, HelpCircle, Sparkles, FolderPlus, ArrowRight } from 'lucide-react';
import {
  useOpportunity,
  useOpportunityEvaluation,
  useNormalizeOpportunity,
  useEvaluateOpportunity,
  useShortlistOpportunity,
  useConvertOpportunityToCase,
  useTransitionOpportunity,
  useMissions,
} from '../hooks/useCaseworker';
import { TextureBadge } from '../components/ui/TextureBadge';
import { TactileButton } from '../components/ui/TactileButton';

export const OpportunityDetailView: React.FC = () => {
  const { id } = useParams<{ id: string }>();
  const navigate = useNavigate();

  const { data: oppData, isLoading, error, refetch } = useOpportunity(id);
  const { data: evalSnapshot } = useOpportunityEvaluation(id);
  const { data: missionsData } = useMissions({ archived: false });
  const activeMissions = (missionsData ?? []).filter((m) => m.status === 'active' || m.status === 'draft');

  const normalizeOpp = useNormalizeOpportunity();
  const evaluateOpp = useEvaluateOpportunity();
  const shortlistOpp = useShortlistOpportunity();
  const convertOpp = useConvertOpportunityToCase();
  const transitionOpp = useTransitionOpportunity();

  const [concurrencyNotice, setConcurrencyNotice] = useState<string | null>(null);
  const [shortlistModalOpen, setShortlistModalOpen] = useState(false);
  const [shortlistReason, setShortlistReason] = useState('');
  const [overrideIneligible, setOverrideIneligible] = useState(false);

  const [convertModalOpen, setConvertModalOpen] = useState(false);
  const [caseTitle, setCaseTitle] = useState('');
  const [caseGoal, setCaseGoal] = useState('');
  const [selectedMissionId, setSelectedMissionId] = useState('');

  if (isLoading) {
    return (
      <div className="py-24 text-center font-interface text-sm text-jv-muted">
        Loading opportunity dossier…
      </div>
    );
  }

  if (error || !oppData?.opportunity) {
    return (
      <div className="py-24 text-center space-y-4 font-interface">
        <div className="font-machine text-xs text-jv-muted uppercase">404 // NOT FOUND</div>
        <h1 className="font-display text-4xl text-jv-ink">Opportunity Not Found</h1>
        <p className="text-sm text-jv-muted">
          This prospect record does not exist or belongs to another user scope.
        </p>
        <TactileButton variant="primary" size="md" onClick={() => navigate('/opportunities')}>
          Back to Opportunities →
        </TactileButton>
      </div>
    );
  }

  const { opportunity: opp, etag } = oppData;
  const summary = opp.evaluation_summary;

  const handleNormalize = async () => {
    if (!etag || !id) return;
    setConcurrencyNotice(null);
    try {
      await normalizeOpp.mutateAsync({ id, etag });
    } catch (err: any) {
      if (err.name === 'PreconditionFailedError' || err.status === 412) {
        setConcurrencyNotice("This changed elsewhere. We've loaded the latest version.");
        refetch();
      } else {
        setConcurrencyNotice(err.message || 'Normalization failed');
      }
    }
  };

  const handleEvaluate = async () => {
    if (!etag || !id) return;
    setConcurrencyNotice(null);
    try {
      await evaluateOpp.mutateAsync({ id, etag });
    } catch (err: any) {
      if (err.name === 'PreconditionFailedError' || err.status === 412) {
        setConcurrencyNotice("This changed elsewhere. We've loaded the latest version.");
        refetch();
      } else {
        setConcurrencyNotice(err.message || 'Evaluation failed');
      }
    }
  };

  const handleShortlistSubmit = async () => {
    if (!etag || !id) return;
    setConcurrencyNotice(null);
    try {
      await shortlistOpp.mutateAsync({
        id,
        etag,
        reason: shortlistReason.trim() || undefined,
        override_ineligible: overrideIneligible,
      });
      setShortlistModalOpen(false);
      setShortlistReason('');
      setOverrideIneligible(false);
    } catch (err: any) {
      if (err.name === 'PreconditionFailedError' || err.status === 412) {
        setConcurrencyNotice("This changed elsewhere. We've loaded the latest version.");
        refetch();
      } else {
        setConcurrencyNotice(err.message || 'Shortlisting failed');
      }
    }
  };

  const handleConvertSubmit = async () => {
    if (!id) return;
    const targetMissionId = opp.mission_id || selectedMissionId || (activeMissions[0]?.mission_id ?? '');
    if (!targetMissionId) {
      setConcurrencyNotice('An active mission is required to convert an opportunity into a case.');
      return;
    }
    setConcurrencyNotice(null);
    try {
      const res = await convertOpp.mutateAsync({
        id,
        etag: etag || undefined,
        mission_id: targetMissionId,
        title: caseTitle.trim() || undefined,
        goal: caseGoal.trim() || undefined,
      });
      setConvertModalOpen(false);
      navigate(`/cases/${res.case_id}`);
    } catch (err: any) {
      if (err.name === 'PreconditionFailedError' || err.status === 412) {
        setConcurrencyNotice("This changed elsewhere. We've loaded the latest version.");
        refetch();
      } else {
        setConcurrencyNotice(err.message || 'Case conversion failed');
      }
    }
  };

  const handleArchive = async () => {
    if (!etag || !id) return;
    setConcurrencyNotice(null);
    try {
      await transitionOpp.mutateAsync({ id, newStatus: 'archived', etag, reason: 'Archived from dossier' });
    } catch (err: any) {
      setConcurrencyNotice(err.message || 'Archival failed');
    }
  };

  const handleReject = async () => {
    if (!etag || !id) return;
    setConcurrencyNotice(null);
    try {
      await transitionOpp.mutateAsync({ id, newStatus: 'rejected', etag, reason: 'Rejected from dossier' });
    } catch (err: any) {
      setConcurrencyNotice(err.message || 'Rejection failed');
    }
  };

  return (
    <div className="space-y-12 font-interface text-jv-ink">
      {/* Return Navigation */}
      <div>
        <Link
          to="/opportunities"
          className="inline-flex items-center gap-2 font-interface text-sm text-jv-muted hover:text-jv-ink transition-colors"
        >
          <ArrowLeft className="w-3.5 h-3.5" />
          <span>Return to opportunities</span>
        </Link>
      </div>

      {/* Concurrency Notification */}
      {concurrencyNotice && (
        <div className="p-4 border border-jv-rule bg-jv-surface flex items-center gap-3 text-xs font-machine text-jv-ink">
          <AlertTriangle className="w-4 h-4 shrink-0 text-jv-ink" />
          <span>{concurrencyNotice}</span>
        </div>
      )}

      {/* Editorial Header */}
      <div className="border-b border-jv-rule pb-8 space-y-4">
        <div className="flex items-center justify-between font-machine text-xs text-jv-muted">
          <div>OPPORTUNITY / {opp.opportunity_id.slice(0, 8)}</div>
          <div className="flex items-center gap-2">
            {summary && (
              <span
                className={`px-2 py-0.5 text-[11px] font-machine uppercase border ${
                  summary.eligibility_status === 'eligible'
                    ? 'border-jv-ink/20 bg-jv-ink/5 text-jv-ink'
                    : summary.eligibility_status === 'ineligible'
                    ? 'border-jv-rule bg-jv-surface text-jv-muted'
                    : 'border-dashed border-jv-rule text-jv-muted'
                }`}
              >
                {summary.eligibility_status}
              </span>
            )}
            <TextureBadge status={opp.status} />
          </div>
        </div>

        <div className="space-y-1">
          <div className="font-interface text-sm font-medium text-jv-muted">
            {opp.organization || 'Direct Prospect'}
          </div>
          <h1 className="font-display font-normal text-4xl lg:text-6xl text-jv-ink tracking-tight leading-tight">
            {opp.title}
          </h1>
        </div>

        <div className="flex flex-wrap items-center gap-4 sm:gap-6 font-machine text-xs text-jv-muted pt-2">
          <span>Status: {opp.status.replace(/_/g, ' ')}</span>
          <span className="text-jv-rule">·</span>
          <span>Type: {opp.opportunity_type.replace(/_/g, ' ')}</span>
          <span className="text-jv-rule">·</span>
          <span>Discovered: {new Date(opp.discovered_at).toLocaleDateString()}</span>
          {opp.location && (
            <>
              <span className="text-jv-rule">·</span>
              <span>Location: {opp.location}</span>
            </>
          )}
          {opp.deadline && (
            <>
              <span className="text-jv-rule">·</span>
              <span>Deadline: {new Date(opp.deadline).toLocaleDateString()}</span>
            </>
          )}
        </div>
      </div>

      {/* Main Dossier Content */}
      <div className="grid grid-cols-1 lg:grid-cols-12 gap-8">
        <div className="lg:col-span-8 space-y-8">
          {/* Fit Assessment & Eligibility Meter */}
          {summary ? (
            <section className="p-6 border border-jv-rule bg-jv-surface space-y-6">
              {evalSnapshot?.is_stale && (
                <div className="p-3 border border-jv-accent/40 bg-jv-accent/10 flex items-center justify-between text-xs font-interface text-jv-ink">
                  <div className="flex items-center gap-2">
                    <AlertTriangle className="w-4 h-4 text-jv-accent shrink-0" />
                    <span>Evaluation is stale: Context vault or mission parameters have changed since last evaluation.</span>
                  </div>
                  <TactileButton
                    variant="outline"
                    size="sm"
                    loading={evaluateOpp.isPending}
                    onClick={handleEvaluate}
                  >
                    Re-evaluate Now
                  </TactileButton>
                </div>
              )}

              <div className="flex items-start justify-between border-b border-jv-rule pb-4">
                <div>
                  <div className="font-machine text-[11px] text-jv-muted uppercase tracking-wider">
                    Context Vault Assessment
                  </div>
                  <h2 className="font-display text-2xl text-jv-ink mt-0.5">
                    {summary.fit_tier === 'strong'
                      ? 'Strong Fit'
                      : summary.fit_tier === 'moderate'
                      ? 'Moderate Fit'
                      : summary.fit_tier === 'insufficient_evidence'
                      ? 'Insufficient Evidence'
                      : 'Low Fit'}
                  </h2>
                </div>
                <div className="text-right">
                  <div className="font-display text-4xl text-jv-ink">{Math.round(summary.fit_score)}</div>
                  <div className="font-machine text-[10px] text-jv-muted">/ 100 PTS</div>
                </div>
              </div>

              {/* Fit Breakdown Components */}
              {evalSnapshot?.fit && (
                <div className="space-y-4">
                  <div className="grid grid-cols-2 sm:grid-cols-4 gap-3 text-xs">
                    <div className="p-3 border border-jv-rule bg-jv-bg space-y-1">
                      <span className="font-interface text-jv-muted text-[11px]">Skills</span>
                      <div className="font-machine text-jv-ink font-medium">
                        {evalSnapshot.fit.skills_score} / 50
                      </div>
                    </div>
                    <div className="p-3 border border-jv-rule bg-jv-bg space-y-1">
                      <span className="font-interface text-jv-muted text-[11px]">Preferred</span>
                      <div className="font-machine text-jv-ink font-medium">
                        {evalSnapshot.fit.preferred_reqs_score} / 20
                      </div>
                    </div>
                    <div className="p-3 border border-jv-rule bg-jv-bg space-y-1">
                      <span className="font-interface text-jv-muted text-[11px]">Location</span>
                      <div className="font-machine text-jv-ink font-medium">
                        {evalSnapshot.fit.location_score} / 15
                      </div>
                    </div>
                    <div className="p-3 border border-jv-rule bg-jv-bg space-y-1">
                      <span className="font-interface text-jv-muted text-[11px]">Seniority</span>
                      <div className="font-machine text-jv-ink font-medium">
                        {evalSnapshot.fit.seniority_score} / 15
                      </div>
                    </div>
                  </div>

                  {Boolean(evalSnapshot.fit.explanations && evalSnapshot.fit.explanations.length > 0) && (
                    <ul className="space-y-1.5 font-interface text-xs text-jv-muted pt-2 border-t border-jv-rule">
                      {(evalSnapshot.fit.explanations || []).map((exp, idx) => (
                        <li key={idx} className="flex items-center gap-2">
                          <span className="w-1 h-1 bg-jv-muted rounded-full shrink-0" />
                          <span>{exp}</span>
                        </li>
                      ))}
                    </ul>
                  )}
                </div>
              )}

              {/* Eligibility Summary Line */}
              <div className="pt-4 border-t border-jv-rule flex items-center justify-between text-xs font-machine">
                <span className="text-jv-muted">
                  Evidence Coverage: {Math.round(summary.coverage * 100)}%
                </span>
                <span className="text-jv-muted">
                  Policy: {evalSnapshot?.evaluation_policy || 'opp_eval_v1'}
                </span>
              </div>
            </section>
          ) : (
            <div className="p-6 border border-dashed border-jv-rule bg-jv-surface/50 text-center space-y-3">
              <Sparkles className="w-6 h-6 mx-auto text-jv-muted" />
              <div className="font-display text-xl text-jv-ink">Unassessed Prospect</div>
              <p className="font-interface text-xs text-jv-muted max-w-md mx-auto">
                Requirements have not yet been evaluated against the personal context vault. Normalize and evaluate to compute explainable fit and tri-state eligibility.
              </p>
            </div>
          )}

          {/* Structured Requirements & Evidence */}
          <section className="space-y-4">
            <h2 className="font-interface font-medium text-xl text-jv-ink tracking-tight border-b border-jv-rule pb-2">
              Requirements & Evidence ({(opp.requirements || []).length})
            </h2>

            {(!opp.requirements || opp.requirements.length === 0) ? (
              <p className="font-interface text-sm text-jv-muted">
                No formal eligibility requirements recorded for this prospect.
              </p>
            ) : (
              <div className="space-y-3">
                {evalSnapshot?.evidence?.length ? (
                  evalSnapshot.evidence.map((ev, idx) => (
                    <div
                      key={ev.requirement_id || idx}
                      className="p-4 border border-jv-rule bg-jv-surface/50 space-y-2 text-xs font-interface"
                    >
                      <div className="flex items-center justify-between">
                        <div className="flex items-center gap-2">
                          {ev.match_result === 'met' ? (
                            <CheckCircle2 className="w-4 h-4 text-jv-ink" />
                          ) : ev.match_result === 'not_met' ? (
                            <XCircle className="w-4 h-4 text-jv-muted" />
                          ) : (
                            <HelpCircle className="w-4 h-4 text-jv-muted" />
                          )}
                          <span className="font-medium text-jv-ink">
                            {(opp.requirements || [])[idx] || `Requirement ${idx + 1}`}
                          </span>
                        </div>
                        <span className="font-machine text-[10px] uppercase text-jv-muted">
                          {ev.match_result}
                        </span>
                      </div>
                      {ev.notes && (
                        <p className="text-jv-muted pl-6 text-[11px] font-machine">{ev.notes}</p>
                      )}
                    </div>
                  ))
                ) : (
                  <ul className="space-y-2">
                    {(opp.requirements || []).map((req, idx) => (
                      <li
                        key={idx}
                        className="flex items-start gap-3 p-3.5 border border-jv-rule bg-jv-surface/50 text-sm text-jv-ink font-interface"
                      >
                        <span className="font-machine text-xs text-jv-muted shrink-0 pt-0.5">
                          {String(idx + 1).padStart(2, '0')}.
                        </span>
                        <span className="leading-relaxed">{req}</span>
                      </li>
                    ))}
                  </ul>
                )}
              </div>
            )}
          </section>

          {/* Provenance & Cryptographic Identifiers */}
          <section className="space-y-4">
            <h2 className="font-interface font-medium text-xl text-jv-ink tracking-tight border-b border-jv-rule pb-2">
              Provenance & identifiers
            </h2>
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-4 text-xs">
              <div className="p-4 border border-jv-rule bg-jv-surface space-y-1">
                <span className="font-interface text-xs text-jv-muted">Provider / Organization</span>
                <div className="font-interface text-jv-ink text-sm font-semibold">
                  {opp.organization || 'Not Specified'}
                </div>
              </div>
              <div className="p-4 border border-jv-rule bg-jv-surface space-y-1">
                <span className="font-interface text-xs text-jv-muted">Geographic Scope</span>
                <div className="font-interface text-jv-ink text-sm font-semibold">
                  {opp.location || 'Remote / Unspecified'}
                </div>
              </div>
              <div className="p-4 border border-jv-rule bg-jv-surface space-y-1">
                <span className="font-interface text-xs text-jv-muted">Source Platform</span>
                <div className="font-interface text-jv-ink text-sm font-semibold">
                  {opp.source_name || 'Direct Manual Ingress'}
                </div>
              </div>
              <div className="p-4 border border-jv-rule bg-jv-surface space-y-1">
                <span className="font-interface text-xs text-jv-muted">Cross-Source Fingerprint (v2)</span>
                <div className="font-machine text-jv-ink text-[11px] break-all">
                  {opp.fingerprint_v2 || opp.fingerprint}
                </div>
              </div>
              <div className="p-4 border border-jv-rule bg-jv-surface space-y-1">
                <span className="font-interface text-xs text-jv-muted">Conversion Record</span>
                <div className="font-machine text-jv-ink text-[11px]">
                  {opp.converted_case_id ? (
                    <Link
                      to={`/cases/${opp.converted_case_id}`}
                      className="underline underline-offset-2 hover:text-jv-muted"
                    >
                      Case #{opp.converted_case_id.slice(0, 8)} →
                    </Link>
                  ) : (
                    'Not converted to case'
                  )}
                </div>
              </div>
            </div>
          </section>
        </div>

        {/* Right Sidebar: Operational Actions */}
        <div className="lg:col-span-4 space-y-6">
          <div className="border border-jv-rule bg-jv-surface p-6 space-y-4">
            <div className="font-interface font-medium text-sm text-jv-ink border-b border-jv-rule pb-2">
              Editorial Workflow
            </div>

            <div className="space-y-3">
              {/* Step 1: Normalize */}
              {opp.status === 'discovered' && (
                <TactileButton
                  variant="primary"
                  size="sm"
                  className="w-full justify-center"
                  loading={normalizeOpp.isPending}
                  onClick={handleNormalize}
                >
                  Structure Requirements →
                </TactileButton>
              )}

              {/* Step 2: Evaluate */}
              {opp.status === 'normalized' && (
                <TactileButton
                  variant="primary"
                  size="sm"
                  className="w-full justify-center"
                  loading={evaluateOpp.isPending}
                  onClick={handleEvaluate}
                >
                  Evaluate Eligibility & Fit ✦
                </TactileButton>
              )}

              {/* Step 3: Shortlist */}
              {opp.status === 'evaluating' && (
                <>
                  <TactileButton
                    variant="primary"
                    size="sm"
                    className="w-full justify-center"
                    onClick={() => setShortlistModalOpen(true)}
                  >
                    Shortlist Opportunity ★
                  </TactileButton>
                  <TactileButton
                    variant="outline"
                    size="sm"
                    className="w-full justify-center"
                    loading={evaluateOpp.isPending}
                    onClick={handleEvaluate}
                  >
                    Re-evaluate Fit
                  </TactileButton>
                </>
              )}

              {/* Step 4: Convert to Case */}
              {opp.status === 'shortlisted' && (
                <>
                  <TactileButton
                    variant="primary"
                    size="sm"
                    className="w-full justify-center"
                    onClick={() => setConvertModalOpen(true)}
                  >
                    <FolderPlus className="w-4 h-4 mr-1.5" />
                    Open Application Case →
                  </TactileButton>
                  <TactileButton
                    variant="outline"
                    size="sm"
                    className="w-full justify-center"
                    loading={evaluateOpp.isPending}
                    onClick={handleEvaluate}
                  >
                    Re-evaluate Fit
                  </TactileButton>
                </>
              )}

              {/* Converted State */}
              {opp.status === 'converted_to_case' && opp.converted_case_id && (
                <div className="p-3 border border-jv-rule bg-jv-bg space-y-2">
                  <div className="font-machine text-[10px] text-jv-muted uppercase">Case Opened</div>
                  <TactileButton
                    variant="outline"
                    size="sm"
                    className="w-full justify-center"
                    onClick={() => navigate(`/cases/${opp.converted_case_id}`)}
                  >
                    View Active Case Dossier <ArrowRight className="w-3.5 h-3.5 ml-1" />
                  </TactileButton>
                </div>
              )}

              {/* Non-destructive Archival & Reject */}
              {opp.status !== 'archived' && opp.status !== 'rejected' && opp.status !== 'converted_to_case' && (
                <div className="pt-2 border-t border-jv-rule space-y-2">
                  <TactileButton
                    variant="outline"
                    size="sm"
                    className="w-full justify-center text-jv-muted hover:text-jv-ink"
                    loading={transitionOpp.isPending}
                    onClick={handleArchive}
                  >
                    Archive Prospect
                  </TactileButton>
                  <TactileButton
                    variant="outline"
                    size="sm"
                    className="w-full justify-center text-jv-muted hover:text-jv-ink"
                    loading={transitionOpp.isPending}
                    onClick={handleReject}
                  >
                    Reject Prospect ✕
                  </TactileButton>
                </div>
              )}
            </div>

            {opp.source_url && (
              <div className="pt-4 border-t border-jv-rule">
                <a
                  href={opp.source_url}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="flex items-center justify-between text-xs font-interface text-jv-muted hover:text-jv-ink p-2 border border-jv-rule bg-jv-bg hover:border-jv-ink transition-colors"
                >
                  <span>Visit source listing</span>
                  <ExternalLink className="w-3.5 h-3.5" />
                </a>
              </div>
            )}
          </div>
        </div>
      </div>

      {/* Shortlist Confirmation Modal */}
      {shortlistModalOpen && (
        <div className="fixed inset-0 z-50 bg-jv-ink/40 flex items-center justify-center p-4">
          <div className="w-full max-w-md border border-jv-rule bg-jv-bg p-6 space-y-4 font-interface">
            <h3 className="font-display text-2xl text-jv-ink">Shortlist Opportunity</h3>
            <p className="text-xs text-jv-muted">
              Add this prospect to your active shortlist. Only shortlisted opportunities can be converted into active application cases.
            </p>

            {summary?.eligibility_status === 'ineligible' && (
              <div className="p-3 border border-jv-rule bg-jv-surface space-y-2 text-xs">
                <div className="font-medium text-jv-ink flex items-center gap-1.5">
                  <AlertTriangle className="w-3.5 h-3.5" />
                  <span>Opportunity is marked Ineligible</span>
                </div>
                <p className="text-jv-muted text-[11px]">
                  At least one mandatory requirement is not satisfied. You must explicitly override with rationale to shortlist.
                </p>
                <label className="flex items-center gap-2 pt-1 font-machine text-xs cursor-pointer">
                  <input
                    type="checkbox"
                    checked={overrideIneligible}
                    onChange={(e) => setOverrideIneligible(e.target.checked)}
                    className="rounded border-jv-rule text-jv-ink"
                  />
                  <span>Override ineligibility</span>
                </label>
              </div>
            )}

            <div className="space-y-1">
              <label className="text-xs font-interface text-jv-muted">
                {summary?.eligibility_status === 'ineligible' ? 'Override Rationale (Required)' : 'Shortlisting Note (Optional)'}
              </label>
              <textarea
                value={shortlistReason}
                onChange={(e) => setShortlistReason(e.target.value)}
                placeholder="Document your reasoning for shortlisting this prospect..."
                className="w-full p-2 border border-jv-rule bg-jv-surface text-xs font-interface text-jv-ink rounded-none h-20"
              />
            </div>

            <div className="flex items-center justify-end gap-2 pt-2 border-t border-jv-rule">
              <TactileButton variant="outline" size="sm" onClick={() => setShortlistModalOpen(false)}>
                Cancel
              </TactileButton>
              <TactileButton
                variant="primary"
                size="sm"
                loading={shortlistOpp.isPending}
                disabled={summary?.eligibility_status === 'ineligible' && (!overrideIneligible || !shortlistReason.trim())}
                onClick={handleShortlistSubmit}
              >
                Confirm Shortlist
              </TactileButton>
            </div>
          </div>
        </div>
      )}

      {/* Convert to Case Modal */}
      {convertModalOpen && (
        <div className="fixed inset-0 z-50 bg-jv-ink/40 flex items-center justify-center p-4">
          <div className="w-full max-w-md border border-jv-rule bg-jv-bg p-6 space-y-4 font-interface">
            <h3 className="font-display text-2xl text-jv-ink">Open Application Case</h3>
            <p className="text-xs text-jv-muted">
              Convert this prospect into an active real-world Case. The runtime will open a tracking dossier linked to this opportunity.
            </p>

            <div className="space-y-3">
              {!opp.mission_id && (
                <div className="space-y-1">
                  <label className="text-xs font-interface text-jv-muted">Target Mission</label>
                  {activeMissions.length > 0 ? (
                    <select
                      value={selectedMissionId || (activeMissions[0]?.mission_id ?? '')}
                      onChange={(e) => setSelectedMissionId(e.target.value)}
                      className="w-full p-2 border border-jv-rule bg-jv-surface text-xs font-interface text-jv-ink rounded-none"
                    >
                      {activeMissions.map((m) => (
                        <option key={m.mission_id} value={m.mission_id}>
                          {m.title}
                        </option>
                      ))}
                    </select>
                  ) : (
                    <div className="p-2 border border-jv-accent/40 bg-jv-accent/5 text-xs text-jv-ink font-interface">
                      No active mission found. Please create or activate a mission before converting.
                    </div>
                  )}
                </div>
              )}

              <div className="space-y-1">
                <label className="text-xs font-interface text-jv-muted">Case Title</label>
                <input
                  type="text"
                  value={caseTitle}
                  onChange={(e) => setCaseTitle(e.target.value)}
                  placeholder={`Application: ${opp.title}`}
                  className="w-full p-2 border border-jv-rule bg-jv-surface text-xs font-interface text-jv-ink rounded-none"
                />
              </div>

              <div className="space-y-1">
                <label className="text-xs font-interface text-jv-muted">Operational Goal</label>
                <textarea
                  value={caseGoal}
                  onChange={(e) => setCaseGoal(e.target.value)}
                  placeholder={`Pursue and apply for ${opp.title} at ${opp.organization || 'target organization'}`}
                  className="w-full p-2 border border-jv-rule bg-jv-surface text-xs font-interface text-jv-ink rounded-none h-16"
                />
              </div>
            </div>

            <div className="flex items-center justify-end gap-2 pt-2 border-t border-jv-rule">
              <TactileButton variant="outline" size="sm" onClick={() => setConvertModalOpen(false)}>
                Cancel
              </TactileButton>
              <TactileButton
                variant="primary"
                size="sm"
                loading={convertOpp.isPending}
                onClick={handleConvertSubmit}
              >
                Open Case Dossier →
              </TactileButton>
            </div>
          </div>
        </div>
      )}
    </div>
  );
};
