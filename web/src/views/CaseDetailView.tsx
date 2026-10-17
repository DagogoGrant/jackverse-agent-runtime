import React, { useState } from 'react';
import { useParams, useNavigate, Link } from 'react-router-dom';
import { ArrowLeft, AlertTriangle, CheckCircle2, XCircle, Search, ChevronDown, FileText, Copy, Check, ShieldCheck, X, Lock, Send, Eye, EyeOff, FileCode, Settings } from 'lucide-react';
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
  useCaseArtifacts,
  useDraftArtifacts,
  useLatestPreparationPackage,
  useVerifyPreparationPackage,
  useLatestExecutionPlan,
  useCreateExecutionPlan,
  useContextFacts,
} from '../hooks/useCaseworker';
import { apiRequest, PreconditionFailedError } from '../api/client';
import type { Claim, TargetFieldBinding, TargetSchemaField, TargetInteractionSchema, ExecutionDestination } from '../api/types';
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
  const { data: artifacts } = useCaseArtifacts(caseId);
  const { data: eventsData } = useEvents();

  const transitionCase = useTransitionCase();
  const resolveCase = useResolveCase();
  const proposeClaim = useProposeClaim();
  const evaluateClaim = useEvaluateClaim();
  const requestApproval = useRequestActionApproval();
  const generatePrep = useGeneratePreparation();
  const draftArtifacts = useDraftArtifacts();
  const { data: latestPackage, isLoading: _packageLoading } = useLatestPreparationPackage(caseId);
  const verifyPackage = useVerifyPreparationPackage();
  const [verificationError, setVerificationError] = useState<string | null>(null);
  const [copiedFingerprint, setCopiedFingerprint] = useState(false);

  const handleVerifyPackage = async () => {
    if (!caseId || !caseData || !artifacts || artifacts.length === 0) return;
    setVerificationError(null);
    setConcurrencyNotice(null);
    try {
      await verifyPackage.mutateAsync({
        caseId,
        artifactSetId: artifacts[0].artifact_set_id,
        etag: caseData.etag,
      });
    } catch (err: unknown) {
      if (err instanceof PreconditionFailedError) {
        setConcurrencyNotice(
          'Case was modified concurrently. Reloading current dossier state…'
        );
        await refetchCase();
      } else {
        setVerificationError(
          err instanceof Error ? err.message : 'Package pre-verification failed.'
        );
      }
    }
  };

  const { data: latestPlan, isLoading: _planLoading } = useLatestExecutionPlan(caseId);
  const createPlan = useCreateExecutionPlan();
  const [planError, setPlanError] = useState<string | null>(null);
  const [showPlanBinding, setShowPlanBinding] = useState(false);
  const [revealedFields, setRevealedFields] = useState<Record<string, boolean>>({});
  const [copiedPlanFingerprint, setCopiedPlanFingerprint] = useState(false);

  const toggleRevealField = (fieldId: string) => {
    setRevealedFields((prev) => ({
      ...prev,
      [fieldId]: !prev[fieldId],
    }));
  };

  const handleCompilePlan = async () => {
    if (!caseId || !caseData) return;
    setPlanError(null);
    setConcurrencyNotice(null);
    try {
      await createPlan.mutateAsync({
        caseId,
        packageId: latestPackage?.package_id,
        etag: caseData.etag,
      });
    } catch (err: unknown) {
      if (err instanceof PreconditionFailedError) {
        setConcurrencyNotice(
          'Case was modified concurrently. Reloading current dossier state…'
        );
        await refetchCase();
      } else {
        setPlanError(
          err instanceof Error ? err.message : 'Plan compilation failed.'
        );
      }
    }
  };

  // Phase 5.3.1.1 Plan Input Resolution State
  const { data: contextFacts } = useContextFacts();
  const [showResolvePlanModal, setShowResolvePlanModal] = useState(false);
  const [resolveDestinationUrl, setResolveDestinationUrl] = useState('');
  const [resolveDestinationOrg, setResolveDestinationOrg] = useState('');
  const [resolveFieldBindings, setResolveFieldBindings] = useState<Record<string, string>>({});
  const [resolveSubmitSelector, setResolveSubmitSelector] = useState('');
  const [resolveSubmitControlId, setResolveSubmitControlId] = useState('');
  const [resolveSubmitLabel, setResolveSubmitLabel] = useState('Submit Application');

  const defaultSchemaFields: TargetSchemaField[] = [
    { target_field_id: 'first_name', label: 'First Name', field_type: 'text', required: true },
    { target_field_id: 'last_name', label: 'Last Name', field_type: 'text', required: true },
    { target_field_id: 'email', label: 'Email Address', field_type: 'email', required: true },
    { target_field_id: 'phone', label: 'Phone Number', field_type: 'phone', required: true },
    { target_field_id: 'linkedin_url', label: 'LinkedIn Profile', field_type: 'url', required: false },
    { target_field_id: 'github_url', label: 'GitHub Profile', field_type: 'url', required: false },
    { target_field_id: 'work_authorization', label: 'Work Authorization', field_type: 'select', required: true },
  ];

  const openResolvePlanModal = () => {
    setResolveDestinationUrl(latestPlan?.destination?.canonical_url || '');
    setResolveDestinationOrg(latestPlan?.destination?.organization || '');

    const initialBindings: Record<string, string> = {};
    if (latestPlan?.field_mappings && latestPlan.field_mappings.length > 0) {
      for (const m of latestPlan.field_mappings) {
        if (m.target_field_id && m.source_fact_id) {
          initialBindings[m.target_field_id] = m.source_fact_id;
        }
      }
    }
    setResolveFieldBindings(initialBindings);

    setResolveSubmitSelector(latestPlan?.final_submit_control?.selector || '');
    setResolveSubmitControlId(latestPlan?.final_submit_control?.control_id || '');
    setResolveSubmitLabel(latestPlan?.final_submit_control?.semantic_target || 'Submit Application');

    setShowResolvePlanModal(true);
  };

  const handleSaveAndCompileResolvedPlan = async () => {
    if (!caseId || !caseData) return;
    setPlanError(null);
    setConcurrencyNotice(null);

    const bindings: TargetFieldBinding[] = Object.entries(resolveFieldBindings)
      .filter(([_, factId]) => Boolean(factId))
      .map(([fieldId, factId]) => ({
        target_field_id: fieldId,
        source_fact_id: factId,
      }));

    const activeFields = (latestPlan?.target_schema?.fields && latestPlan.target_schema.fields.length > 0)
      ? latestPlan.target_schema.fields
      : defaultSchemaFields;

    const targetSchema: TargetInteractionSchema = {
      schema_id: latestPlan?.target_schema?.schema_id || 'manual-resolved-v1',
      destination_type: 'ats_form',
      target_schema_provenance: 'USER_PROVIDED',
      fields: activeFields,
      upload_slots: latestPlan?.target_schema?.upload_slots || [
        { slot_id: 'resume', label: 'Resume', role: 'RESUME', required: true, accepted_mime_types: ['application/pdf'] },
      ],
      screening_questions: latestPlan?.target_schema?.screening_questions || [],
      final_submit_control: (resolveSubmitSelector || resolveSubmitControlId) ? {
        control_id: resolveSubmitControlId || '',
        selector: resolveSubmitSelector || '',
        semantic_target: resolveSubmitLabel || 'Submit Application',
        control_provenance: 'USER_PROVIDED',
        control_type: 'button_submit',
        method: 'POST',
        requires_double_confirmation: true,
      } : null,
      schema_hash: '',
    };

    let normOrigin = '';
    let normHost = '';
    if (resolveDestinationUrl) {
      try {
        const parsed = new URL(resolveDestinationUrl);
        normOrigin = parsed.origin;
        normHost = parsed.hostname;
      } catch {
        // ignore
      }
    }

    const destinationOverride: ExecutionDestination | null = resolveDestinationUrl ? {
      canonical_url: resolveDestinationUrl,
      organization: resolveDestinationOrg || '',
      destination_type: 'ats_form',
      destination_provenance: 'USER_PROVIDED',
      channel_metadata: {},
      is_exact_destination: true,
      normalized_origin: normOrigin,
      normalized_hostname: normHost,
    } : null;

    try {
      await createPlan.mutateAsync({
        caseId,
        packageId: latestPackage?.package_id,
        destinationOverride,
        targetSchema,
        fieldBindings: bindings,
        finalSubmitControl: targetSchema.final_submit_control,
        etag: caseData.etag,
      });
      setShowResolvePlanModal(false);
    } catch (err: unknown) {
      if (err instanceof PreconditionFailedError) {
        setConcurrencyNotice(
          'Case was modified concurrently. Reloading current dossier state…'
        );
        await refetchCase();
      } else {
        setPlanError(
          err instanceof Error ? err.message : 'Plan compilation failed.'
        );
      }
    }
  };

  const [concurrencyNotice, setConcurrencyNotice] = useState<string | null>(null);
  const [resolveOutcome, setResolveOutcome] = useState('');
  const [showResolveForm, setShowResolveForm] = useState(false);

  // Artifact selection & provenance drawer
  const [selectedArtifactId, setSelectedArtifactId] = useState<string | null>(null);
  const [showProvenanceDrawer, setShowProvenanceDrawer] = useState(false);
  const [copiedFormat, setCopiedFormat] = useState<string | null>(null);

  const handleCopyMarkdown = (content: string) => {
    navigator.clipboard.writeText(content);
    setCopiedFormat('markdown');
    setTimeout(() => setCopiedFormat(null), 2000);
  };

  const handleCopyPlainText = (content: string) => {
    const plain = content
      .replace(/^#{1,6}\s+/gm, '')
      .replace(/\*\*([^*]+)\*\*/g, '$1')
      .replace(/\*([^*]+)\*/g, '$1')
      .replace(/`([^`]+)`/g, '$1');
    navigator.clipboard.writeText(plain.trim());
    setCopiedFormat('plain');
    setTimeout(() => setCopiedFormat(null), 2000);
  };

  const handleDraftArtifacts = async () => {
    if (!caseId || !caseData) return;
    setConcurrencyNotice(null);
    try {
      await draftArtifacts.mutateAsync({
        caseId,
        etag: caseData.etag,
      });
    } catch (err: unknown) {
      if (err instanceof PreconditionFailedError) {
        setConcurrencyNotice(
          'Case was modified concurrently. Reloading current dossier state…'
        );
        await refetchCase();
      } else {
        setConcurrencyNotice(
          err instanceof Error ? err.message : 'Drafting application materials failed.'
        );
      }
    }
  };

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
      await generatePrep.mutateAsync({
        caseId,
        etag: caseData?.etag,
      });
      refetchCase();
    } catch (err: any) {
      if (err instanceof PreconditionFailedError || err?.status === 412) {
        setConcurrencyNotice("Case was modified concurrently. Loaded latest version.");
        refetchCase();
      } else {
        setConcurrencyNotice(err.message || 'Preparation intelligence analysis failed');
      }
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

      {caseData?.caseItem?.status?.toLowerCase() === 'awaiting_approval' && (
        <div className="p-4 border-2 border-jv-accent bg-jv-accent/10 flex flex-col sm:flex-row items-start sm:items-center justify-between gap-4 font-interface animate-fade-in">
          <div className="flex items-center gap-3">
            <Lock className="w-5 h-5 text-jv-accent shrink-0" />
            <div className="space-y-0.5">
              <div className="font-semibold text-sm text-jv-ink">
                Preparation Package Frozen · Human Review Required
              </div>
              <div className="text-xs text-jv-muted">
                Materials are frozen under content-addressed cryptographic fingerprint. Review and authorize in Needs You.
              </div>
            </div>
          </div>
          <Link
            to="/needs-you"
            className="px-4 py-2 bg-jv-ink text-jv-bg text-xs font-semibold hover:bg-jv-ink/90 transition-colors flex items-center gap-1.5 shrink-0"
          >
            Review in Needs You →
          </Link>
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
                  <Search className="w-3.5 h-3.5 mr-1.5 inline" />
                  Re-analyze Preparation
                </TactileButton>
              ) : (
                <TactileButton
                  variant="primary"
                  size="sm"
                  loading={generatePrep.isPending}
                  onClick={handleGeneratePrep}
                >
                  <Search className="w-3.5 h-3.5 mr-1.5 inline" />
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
                    <Search className="w-4 h-4 mr-2 inline" />
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

                {/* Report Metadata & Technical Disclosure */}
                <div className="border border-jv-rule bg-jv-surface/40 p-3">
                  <div className="flex items-center justify-between text-xs font-machine text-jv-muted">
                    <span>ANALYZED: {new Date(prepReport.created_at).toLocaleDateString()}</span>
                    <details className="group cursor-pointer">
                      <summary className="list-none text-[11px] uppercase tracking-wider text-jv-muted hover:text-jv-ink flex items-center gap-1 select-none">
                        <ChevronDown className="w-3.5 h-3.5 transition-transform group-open:rotate-180 inline" />
                        Inspect report technical metadata
                      </summary>
                      <div className="mt-2.5 pt-2.5 border-t border-jv-rule space-y-1 font-machine text-[11px] text-jv-muted">
                        <div><span className="text-jv-ink font-semibold">REPORT ID:</span> {prepReport.report_id}</div>
                        <div><span className="text-jv-ink font-semibold">ANALYZER:</span> {prepReport.analyzer_version} (schema: {prepReport.schema_version})</div>
                        <div><span className="text-jv-ink font-semibold">INPUT HASH:</span> {prepReport.input_hash}</div>
                        <div><span className="text-jv-ink font-semibold">CONTEXT SNAPSHOT:</span> {prepReport.context_snapshot_hash}</div>
                        {prepReport.source_evaluation_id && (
                          <div><span className="text-jv-ink font-semibold">SOURCE EVAL:</span> {prepReport.source_evaluation_id}</div>
                        )}
                      </div>
                    </details>
                  </div>
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
                        {(prepReport.evidence_map || []).filter((e) => (e.status || '').toLowerCase() === 'supported').length}
                      </div>
                      <div className="text-[10px] text-jv-muted uppercase">SUPPORTED</div>
                    </div>
                    <div className="p-2.5 border border-jv-rule bg-jv-bg">
                      <div className="text-lg font-bold text-jv-ink">
                        {(prepReport.evidence_map || []).filter((e) => (e.status || '').toLowerCase() === 'potential').length}
                      </div>
                      <div className="text-[10px] text-jv-muted uppercase">POTENTIAL</div>
                    </div>
                    <div className="p-2.5 border border-jv-rule bg-jv-bg">
                      <div className="text-lg font-bold text-jv-ink">
                        {(prepReport.evidence_map || []).filter((e) => (e.status || '').toLowerCase() === 'restricted').length}
                      </div>
                      <div className="text-[10px] text-jv-muted uppercase">RESTRICTED</div>
                    </div>
                    <div className="p-2.5 border border-jv-rule bg-jv-bg">
                      <div className="text-lg font-bold text-jv-ink">
                        {(prepReport.evidence_map || []).filter((e) => (e.status || '').toLowerCase() === 'contradicted').length}
                      </div>
                      <div className="text-[10px] text-jv-muted uppercase">CONTRADICTED</div>
                    </div>
                    <div className="p-2.5 border border-jv-rule bg-jv-bg">
                      <div className="text-lg font-bold text-jv-ink">
                        {(prepReport.evidence_map || []).filter((e) => (e.status || '').toLowerCase() === 'gap').length}
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
                        const catLower = (gap.category || '').toLowerCase();
                        const isConflict = catLower === 'hard_conflict';
                        const isMandatory = catLower === 'mandatory_evidence_gap';
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
                                    {gap.category.replace(/_/g, ' ').toUpperCase()}
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

                  {/* Materials Checklist & Pursuit Suite */}
                  <div className="space-y-4 pt-2">
                    <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-3">
                      <div>
                        <div className="font-machine text-xs text-jv-muted uppercase tracking-wider">
                          Materials Checklist & Pursuit Suite
                        </div>
                        <div className="font-interface text-xs text-jv-muted">
                          Structured, claim-bound materials compiled deterministically from verified vault facts.
                        </div>
                      </div>
                      {caseData?.caseItem?.status?.toLowerCase() === 'planning' && !prepReport.is_stale && (
                        <TactileButton
                          variant="primary"
                          size="sm"
                          loading={draftArtifacts.isPending}
                          onClick={handleDraftArtifacts}
                        >
                          <FileText className="w-3.5 h-3.5 mr-1.5 inline" />
                          {artifacts && artifacts.length > 0 ? 'Re-draft Suite →' : 'Draft Application Suite →'}
                        </TactileButton>
                      )}
                    </div>

                    <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
                      {(prepReport.strategy?.materials_checklist || []).map((mat: any, idx: number) => {
                        const type = mat.material_type || mat.type || 'DOCUMENT';
                        const isDrafted = artifacts?.some(
                          (a) => a.material_type === type && (a.status === 'draft' || a.status === 'frozen')
                        );
                        const displayStatus = isDrafted ? 'DRAFTED' : mat.status || 'PENDING_DRAFT';
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
                            <TextureBadge status={String(displayStatus)} />
                          </div>
                        );
                      })}
                    </div>
                  </div>

                  {/* Prepared Application Materials Suite (Phase 5.2.2 Multi-Tab Reader) */}
                  {artifacts && artifacts.length > 0 && (
                    <div className="mt-8 pt-6 border-t border-jv-rule space-y-4">
                      <div className="flex flex-col sm:flex-row sm:items-baseline justify-between gap-2">
                        <div>
                          <div className="font-machine text-xs text-jv-accent uppercase tracking-wider">
                            PHASE 5.2.2 // APPLICATION MATERIALS SUITE
                          </div>
                          <h3 className="font-display text-2xl text-jv-ink mt-0.5">
                            Prepared Application Materials
                          </h3>
                        </div>
                        <span className="font-machine text-xs text-jv-muted">
                          {artifacts.length} ARTIFACTS DRAFTED · ZERO SYNTHETIC CITATIONS
                        </span>
                      </div>

                      {/* Phase 5.2.3 Preparation Package Verification Gate */}
                      <div className="p-4 border border-jv-rule bg-jv-surface/50 space-y-4">
                        <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-3 pb-3 border-b border-jv-rule">
                          <div className="space-y-1">
                            <div className="font-machine text-xs text-jv-accent uppercase tracking-wider flex items-center gap-2">
                              <ShieldCheck className="w-4 h-4" />
                              PHASE 5.2.3 // PACKAGE VERIFICATION & REVIEW GATE
                            </div>
                            <div className="font-interface text-sm font-semibold text-jv-ink flex items-center gap-2">
                              <span>Verification Gate:</span>
                              <TextureBadge status={latestPackage ? latestPackage.status : 'UNVERIFIED'} />
                              {latestPackage?.status === 'approved' && (
                                <span className="font-machine text-[11px] text-jv-accent font-normal border border-jv-accent/30 px-2 py-0.5 bg-jv-accent/10">
                                  LOCAL APPROVAL ONLY
                                </span>
                              )}
                            </div>
                          </div>

                          {(!latestPackage || latestPackage.status === 'superseded' || latestPackage.status === 'rejected') && (
                            <TactileButton
                              variant="primary"
                              size="sm"
                              loading={verifyPackage.isPending}
                              onClick={handleVerifyPackage}
                            >
                              <ShieldCheck className="w-3.5 h-3.5 mr-1.5 inline" />
                              Verify Package & Request Review →
                            </TactileButton>
                          )}

                          {latestPackage?.status === 'frozen' && (
                            <Link
                              to="/needs-you"
                              className="px-3 py-1.5 bg-jv-ink text-jv-bg font-interface text-xs font-semibold hover:bg-jv-ink/90 transition-colors flex items-center gap-1.5"
                            >
                              Awaiting Authorization in Needs You →
                            </Link>
                          )}
                        </div>

                        {verificationError && (
                          <div className="p-3 border border-red-500/30 bg-red-500/10 text-red-600 font-interface text-xs flex items-center gap-2">
                            <AlertTriangle className="w-4 h-4 shrink-0" />
                            <span>{verificationError}</span>
                          </div>
                        )}

                        {latestPackage && (
                          <div className="space-y-3">
                            <div className="p-2.5 border border-jv-rule bg-jv-bg flex flex-col sm:flex-row sm:items-center justify-between gap-2 font-machine text-xs">
                              <div className="flex items-center gap-2 overflow-hidden">
                                <span className="text-jv-muted shrink-0">CONTENT FINGERPRINT:</span>
                                <span className="text-jv-ink font-mono truncate" title={latestPackage.package_fingerprint}>
                                  {latestPackage.package_fingerprint}
                                </span>
                              </div>
                              <button
                                type="button"
                                onClick={() => {
                                  navigator.clipboard.writeText(latestPackage.package_fingerprint);
                                  setCopiedFingerprint(true);
                                  setTimeout(() => setCopiedFingerprint(false), 2000);
                                }}
                                className="text-jv-muted hover:text-jv-ink px-2 py-1 border border-jv-rule bg-jv-surface shrink-0 flex items-center gap-1 text-[11px]"
                              >
                                {copiedFingerprint ? <Check className="w-3 h-3 text-jv-accent" /> : <Copy className="w-3 h-3" />}
                                <span>{copiedFingerprint ? 'COPIED' : 'COPY FP'}</span>
                              </button>
                            </div>

                            <div className="grid grid-cols-1 md:grid-cols-2 gap-2">
                              {(latestPackage.verification_findings || []).map((f: any, idx: number) => {
                                const isPassed = f.passed;
                                return (
                                  <div
                                    key={idx}
                                    className={`p-2.5 border text-xs flex items-start gap-2.5 ${
                                      isPassed
                                        ? 'border-jv-rule bg-jv-bg/50'
                                        : 'border-red-500/40 bg-red-500/5'
                                    }`}
                                  >
                                    {isPassed ? (
                                      <CheckCircle2 className="w-4 h-4 text-emerald-600 shrink-0 mt-0.5" />
                                    ) : (
                                      <XCircle className="w-4 h-4 text-red-600 shrink-0 mt-0.5" />
                                    )}
                                    <div className="space-y-0.5 min-w-0">
                                      <div className="font-machine uppercase text-[11px] font-semibold text-jv-ink">
                                        {(f.check_name || '').replace(/_/g, ' ')}
                                      </div>
                                      <div className="font-interface text-[11px] text-jv-muted leading-tight">
                                        {f.message}
                                      </div>
                                    </div>
                                  </div>
                                );
                              })}
                            </div>

                            {latestPackage.status === 'approved' && (
                              <div className="p-3 border border-emerald-600/40 bg-emerald-600/10 font-interface text-xs text-emerald-800 dark:text-emerald-300 space-y-1">
                                <div className="font-semibold flex items-center gap-2">
                                  <ShieldCheck className="w-4 h-4" />
                                  Approved Locally · Ready for Phase 5.3
                                </div>
                                <div>
                                  This package was approved by human operator for local compilation. In accordance with JackVerse policy, zero external submissions were performed in this phase.
                                </div>
                              </div>
                            )}
                          </div>
                        )}
                      </div>

                      {/* Phase 5.3.1 Exact External Action Planning Gate */}
                      <div className="p-4 border border-jv-rule bg-jv-surface/50 space-y-4">
                        <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-3 pb-3 border-b border-jv-rule">
                          <div className="space-y-1">
                            <div className="font-machine text-xs text-jv-accent uppercase tracking-wider flex items-center gap-2">
                              <Send className="w-4 h-4" />
                              PHASE 5.3.1 // EXACT EXTERNAL ACTION PLANNING
                            </div>
                            <div className="font-interface text-sm font-semibold text-jv-ink flex items-center gap-2">
                              <span>Plan Status:</span>
                              <TextureBadge status={latestPlan ? latestPlan.status : 'NO_PLAN'} />
                              {latestPlan?.status === 'ready_for_authorization' && (
                                <span className="font-machine text-[11px] text-emerald-600 font-normal border border-emerald-600/30 px-2 py-0.5 bg-emerald-600/10">
                                  READY FOR AUTHORIZATION · ZERO TRANSMISSION
                                </span>
                              )}
                              {latestPlan?.status === 'draft' && (
                                <span className="font-machine text-[11px] text-amber-600 font-normal border border-amber-600/30 px-2 py-0.5 bg-amber-600/10">
                                  DRAFT // INPUTS REQUIRED
                                </span>
                              )}
                            </div>
                          </div>

                          <div className="flex flex-wrap items-center gap-2">
                            {latestPackage?.status === 'approved' && (
                              <TactileButton
                                variant="outline"
                                size="sm"
                                onClick={openResolvePlanModal}
                              >
                                <Settings className="w-3.5 h-3.5 mr-1.5 inline" />
                                Resolve plan inputs →
                              </TactileButton>
                            )}
                            {latestPackage?.status === 'approved' && (!latestPlan || latestPlan.status === 'draft' || latestPlan.status === 'superseded') && (
                              <TactileButton
                                variant="primary"
                                size="sm"
                                loading={createPlan.isPending}
                                onClick={handleCompilePlan}
                              >
                                <FileCode className="w-3.5 h-3.5 mr-1.5 inline" />
                                {latestPlan ? 'Recompile Plan →' : 'Compile Action Plan →'}
                              </TactileButton>
                            )}
                          </div>
                        </div>

                        {latestPlan?.is_stale && (
                          <div className="p-3 border border-amber-500/30 bg-amber-500/10 text-amber-700 dark:text-amber-400 font-interface text-xs space-y-1">
                            <div className="font-semibold flex items-center gap-1.5">
                              <AlertTriangle className="w-4 h-4 text-amber-600 shrink-0" />
                              <span>Action Plan is Stale: Context facts, prepared artifacts, or destination integrity have changed.</span>
                            </div>
                            {latestPlan.staleness_reasons && latestPlan.staleness_reasons.length > 0 && (
                              <ul className="list-disc list-inside text-[11px] font-mono mt-1 space-y-0.5">
                                {latestPlan.staleness_reasons.map((r, i) => (
                                  <li key={i}>{r}</li>
                                ))}
                              </ul>
                            )}
                          </div>
                        )}

                        {planError && (
                          <div className="p-3 border border-red-500/30 bg-red-500/10 text-red-600 font-interface text-xs flex items-center gap-2">
                            <AlertTriangle className="w-4 h-4 shrink-0" />
                            <span>{planError}</span>
                          </div>
                        )}

                        {!latestPlan && latestPackage?.status !== 'approved' && (
                          <div className="p-3 border border-jv-rule bg-jv-bg/30 text-jv-muted font-interface text-xs">
                            Preparation Package must be verified and approved locally before compiling an external action plan.
                          </div>
                        )}

                        {latestPlan && (
                          <div className="space-y-4">
                            {/* Destination Card */}
                            <div className="p-3 border border-jv-rule bg-jv-bg space-y-2 font-interface text-xs">
                              <div className="font-machine text-[11px] text-jv-muted uppercase flex items-center justify-between">
                                <span>Target Destination & Provenance</span>
                                <span className={`px-1.5 py-0.5 border ${
                                  latestPlan.destination.is_exact_destination
                                    ? 'border-emerald-600/30 text-emerald-600 bg-emerald-600/10'
                                    : 'border-amber-600/30 text-amber-600 bg-amber-600/10'
                                }`}>
                                  {latestPlan.destination.is_exact_destination ? 'EXACT DESTINATION' : 'DESTINATION REQUIRED'}
                                </span>
                              </div>
                              <div className="grid grid-cols-1 sm:grid-cols-2 gap-2 text-jv-ink">
                                <div><span className="text-jv-muted">Organization:</span> <span className="font-medium">{latestPlan.destination.organization || '—'}</span></div>
                                <div><span className="text-jv-muted">Channel Type:</span> <span className="font-mono">{latestPlan.destination.destination_type}</span></div>
                                <div className="sm:col-span-2 flex items-center gap-1.5 overflow-hidden">
                                  <span className="text-jv-muted shrink-0">Endpoint URL:</span>
                                  <span className="font-mono text-jv-accent truncate" title={latestPlan.destination.canonical_url}>
                                    {latestPlan.destination.canonical_url}
                                  </span>
                                </div>
                                <div className="sm:col-span-2 text-[11px] text-jv-muted">
                                  Provenance: <span className="font-machine text-jv-ink">{latestPlan.destination.destination_provenance}</span>
                                </div>
                              </div>
                            </div>

                            {/* Field Mappings Table */}
                            {latestPlan.field_mappings && latestPlan.field_mappings.length > 0 && (
                              <div className="border border-jv-rule bg-jv-bg overflow-hidden">
                                <div className="p-2.5 border-b border-jv-rule bg-jv-surface/50 font-machine text-xs text-jv-muted uppercase">
                                  Exact Form Field Mappings ({latestPlan.field_mappings.length})
                                </div>
                                <div className="divide-y divide-jv-rule">
                                  {latestPlan.field_mappings.map((m) => {
                                    const isRevealed = revealedFields[m.target_field_id];
                                    const valStr = String(m.field_value ?? '');
                                    const displayVal = isRevealed ? valStr : '••••••••••••';
                                    return (
                                      <div key={m.target_field_id} className="p-2.5 flex flex-col sm:flex-row sm:items-center justify-between gap-2 text-xs">
                                        <div className="space-y-0.5 min-w-0">
                                          <div className="flex items-center gap-2">
                                            <span className="font-interface font-medium text-jv-ink">{m.field_name}</span>
                                            <span className="font-mono text-[10px] text-jv-muted">({m.target_field_id})</span>
                                            {m.required && <span className="text-[10px] text-red-500 font-bold">*</span>}
                                          </div>
                                          <div className="font-machine text-[10px] text-jv-muted flex items-center gap-2">
                                            <span>SRC: {m.source_fact_id} v{m.source_fact_version}</span>
                                            <span className="text-jv-accent">{m.verification_status}</span>
                                          </div>
                                        </div>
                                        <div className="flex items-center gap-2 shrink-0">
                                          <span className="font-mono text-jv-ink max-w-[200px] truncate" title={isRevealed ? valStr : undefined}>
                                            {displayVal}
                                          </span>
                                          <button
                                            type="button"
                                            onClick={() => toggleRevealField(m.target_field_id)}
                                            className="p-1 text-jv-muted hover:text-jv-ink border border-jv-rule bg-jv-surface"
                                            title={isRevealed ? 'Mask personal value' : 'Reveal personal value'}
                                          >
                                            {isRevealed ? <EyeOff className="w-3 h-3" /> : <Eye className="w-3 h-3" />}
                                          </button>
                                        </div>
                                      </div>
                                    );
                                  })}
                                </div>
                              </div>
                            )}

                            {/* Upload Assets */}
                            {latestPlan.upload_mappings && latestPlan.upload_mappings.length > 0 && (
                              <div className="border border-jv-rule bg-jv-bg overflow-hidden">
                                <div className="p-2.5 border-b border-jv-rule bg-jv-surface/50 font-machine text-xs text-jv-muted uppercase">
                                  Materialized Upload Assets ({latestPlan.upload_mappings.length})
                                </div>
                                <div className="divide-y divide-jv-rule">
                                  {latestPlan.upload_mappings.map((u) => (
                                    <div key={u.asset_id} className="p-2.5 flex flex-col sm:flex-row sm:items-center justify-between gap-2 text-xs">
                                      <div className="space-y-0.5 min-w-0">
                                        <div className="flex items-center gap-2">
                                          <span className="font-interface font-medium text-jv-ink">{u.filename}</span>
                                          <span className="font-machine text-[10px] uppercase text-jv-muted border border-jv-rule px-1.5 py-0.2 bg-jv-surface">
                                            {u.role}
                                          </span>
                                        </div>
                                        <div className="font-mono text-[10px] text-jv-muted flex items-center gap-2 truncate">
                                          <span>{u.mime_type}</span>
                                          <span>·</span>
                                          <span>{u.byte_size} bytes</span>
                                          <span>·</span>
                                          <span title={u.content_sha256}>SHA256: {u.content_sha256.slice(0, 12)}…</span>
                                        </div>
                                      </div>
                                      <span className={`px-2 py-0.5 border text-[11px] font-machine uppercase shrink-0 ${
                                        u.materialized
                                          ? 'border-emerald-600/30 text-emerald-600 bg-emerald-600/10'
                                          : 'border-red-500/30 text-red-600 bg-red-500/10'
                                      }`}>
                                        {u.materialized ? 'MATERIALIZED' : 'UNMATERIALIZED'}
                                      </span>
                                    </div>
                                  ))}
                                </div>
                              </div>
                            )}

                            {/* Screening Responses */}
                            {latestPlan.screening_responses && latestPlan.screening_responses.length > 0 && (
                              <div className="border border-jv-rule bg-jv-bg overflow-hidden">
                                <div className="p-2.5 border-b border-jv-rule bg-jv-surface/50 font-machine text-xs text-jv-muted uppercase">
                                  Grounded Screening Responses ({latestPlan.screening_responses.length})
                                </div>
                                <div className="divide-y divide-jv-rule">
                                  {latestPlan.screening_responses.map((s) => {
                                    const isRevealed = revealedFields[`screening_${s.question_id}`];
                                    return (
                                      <div key={s.question_id} className="p-2.5 space-y-1.5 text-xs">
                                        <div className="flex items-start justify-between gap-2">
                                          <span className="font-interface font-medium text-jv-ink">{s.question}</span>
                                          <button
                                            type="button"
                                            onClick={() => toggleRevealField(`screening_${s.question_id}`)}
                                            className="p-1 text-jv-muted hover:text-jv-ink border border-jv-rule bg-jv-surface shrink-0"
                                            title={isRevealed ? 'Mask answer' : 'Reveal answer'}
                                          >
                                            {isRevealed ? <EyeOff className="w-3 h-3" /> : <Eye className="w-3 h-3" />}
                                          </button>
                                        </div>
                                        <div className="font-interface text-jv-ink-soft bg-jv-surface/50 p-2 border border-jv-rule/50">
                                          {isRevealed ? s.answer : '•••••••••••• (click eye to reveal screening answer)'}
                                        </div>
                                        <div className="font-machine text-[10px] text-jv-muted flex items-center gap-2">
                                          <span>ARTIFACT: {s.source_artifact_id}</span>
                                          <span>·</span>
                                          <span>CLAIMS BOUND: {s.grounding_claim_ids?.length || 0}</span>
                                        </div>
                                      </div>
                                    );
                                  })}
                                </div>
                              </div>
                            )}

                            {/* Exact Final Submit Control Card */}
                            {latestPlan.final_submit_control && (
                              <div className="p-3 border border-jv-rule bg-jv-bg space-y-2 font-interface text-xs">
                                <div className="font-machine text-[11px] text-jv-muted uppercase flex items-center justify-between">
                                  <span>Exact Final Submit Control</span>
                                  <span className="px-1.5 py-0.5 border border-emerald-600/30 text-emerald-600 bg-emerald-600/10 font-machine text-[10px]">
                                    SEALED LOCATOR
                                  </span>
                                </div>
                                <div className="grid grid-cols-1 sm:grid-cols-2 gap-2 text-jv-ink font-interface">
                                  <div>
                                    <span className="text-jv-muted">Control Label:</span>{' '}
                                    <span className="font-medium">{latestPlan.final_submit_control.semantic_target || '—'}</span>
                                  </div>
                                  <div>
                                    <span className="text-jv-muted">Provenance:</span>{' '}
                                    <span className="font-machine">{latestPlan.final_submit_control.control_provenance}</span>
                                  </div>
                                  {latestPlan.final_submit_control.selector && (
                                    <div className="sm:col-span-2 flex items-center gap-1.5 overflow-hidden">
                                      <span className="text-jv-muted shrink-0">CSS Selector:</span>
                                      <span className="font-mono text-jv-ink text-[11px] bg-jv-surface px-1.5 py-0.5 border border-jv-rule truncate">
                                        {latestPlan.final_submit_control.selector}
                                      </span>
                                    </div>
                                  )}
                                  {latestPlan.final_submit_control.control_id && (
                                    <div className="sm:col-span-2 flex items-center gap-1.5 overflow-hidden">
                                      <span className="text-jv-muted shrink-0">Control ID:</span>
                                      <span className="font-mono text-jv-ink text-[11px] bg-jv-surface px-1.5 py-0.5 border border-jv-rule truncate">
                                        {latestPlan.final_submit_control.control_id}
                                      </span>
                                    </div>
                                  )}
                                </div>
                              </div>
                            )}

                            {/* Validation Findings */}
                            <div className="grid grid-cols-1 md:grid-cols-2 gap-2">
                              {(latestPlan.findings || []).map((f, idx) => {
                                const isPassed = f.passed;
                                return (
                                  <div
                                    key={idx}
                                    className={`p-2.5 border text-xs flex items-start gap-2.5 ${
                                      isPassed
                                        ? 'border-jv-rule bg-jv-bg/50'
                                        : 'border-red-500/40 bg-red-500/5'
                                    }`}
                                  >
                                    {isPassed ? (
                                      <CheckCircle2 className="w-4 h-4 text-emerald-600 shrink-0 mt-0.5" />
                                    ) : (
                                      <XCircle className="w-4 h-4 text-red-600 shrink-0 mt-0.5" />
                                    )}
                                    <div className="space-y-0.5 min-w-0">
                                      <div className="font-machine uppercase text-[11px] font-semibold text-jv-ink">
                                        {(f.check_name || '').replace(/_/g, ' ')}
                                      </div>
                                      <div className="font-interface text-[11px] text-jv-muted leading-tight">
                                        {f.message}
                                      </div>
                                    </div>
                                  </div>
                                );
                              })}
                            </div>

                            {/* Inspect Plan Binding Collapsible */}
                            <div className="border border-jv-rule bg-jv-bg p-3 space-y-2">
                              <button
                                type="button"
                                onClick={() => setShowPlanBinding(!showPlanBinding)}
                                className="w-full flex items-center justify-between text-left font-machine text-xs text-jv-ink font-semibold"
                              >
                                <span className="flex items-center gap-1.5">
                                  <Lock className="w-3.5 h-3.5 text-jv-accent" />
                                  INSPECT PLAN BINDING & CRYPTOGRAPHIC PROVENANCE
                                </span>
                                <ChevronDown className={`w-4 h-4 text-jv-muted transition-transform ${showPlanBinding ? 'rotate-180' : ''}`} />
                              </button>

                              {showPlanBinding && (
                                <div className="pt-2 space-y-2 border-t border-jv-rule font-machine text-xs">
                                  <div className="p-2 border border-jv-rule bg-jv-surface/50 flex flex-col sm:flex-row sm:items-center justify-between gap-2">
                                    <div className="flex items-center gap-2 overflow-hidden">
                                      <span className="text-jv-muted shrink-0">PLAN FINGERPRINT:</span>
                                      <span className="text-jv-ink font-mono truncate" title={latestPlan.execution_plan_fingerprint}>
                                        {latestPlan.execution_plan_fingerprint}
                                      </span>
                                    </div>
                                    <button
                                      type="button"
                                      onClick={() => {
                                        navigator.clipboard.writeText(latestPlan.execution_plan_fingerprint);
                                        setCopiedPlanFingerprint(true);
                                        setTimeout(() => setCopiedPlanFingerprint(false), 2000);
                                      }}
                                      className="text-jv-muted hover:text-jv-ink px-2 py-1 border border-jv-rule bg-jv-surface shrink-0 flex items-center gap-1 text-[11px]"
                                    >
                                      {copiedPlanFingerprint ? <Check className="w-3 h-3 text-jv-accent" /> : <Copy className="w-3 h-3" />}
                                      <span>{copiedPlanFingerprint ? 'COPIED' : 'COPY FP'}</span>
                                    </button>
                                  </div>
                                  <div className="grid grid-cols-1 sm:grid-cols-2 gap-2 text-[11px] text-jv-muted p-2">
                                    <div>INPUT HASH: <span className="font-mono text-jv-ink">{latestPlan.execution_input_hash.slice(0, 16)}…</span></div>
                                    <div>PACKAGE FP: <span className="font-mono text-jv-ink">{latestPlan.package_fingerprint.slice(0, 16)}…</span></div>
                                    <div>PLANNER VERSION: <span className="text-jv-ink">{latestPlan.planner_version}</span></div>
                                    <div>SCHEMA VERSION: <span className="text-jv-ink">{latestPlan.schema_version}</span></div>
                                  </div>
                                </div>
                              )}
                            </div>

                            {/* Constitutional Governance Notice (Zero Transmission) */}
                            <div className="p-3 border border-jv-rule bg-jv-surface/40 font-interface text-xs text-jv-muted space-y-1">
                              <div className="font-semibold text-jv-ink flex items-center gap-2">
                                <ShieldCheck className="w-4 h-4 text-jv-accent" />
                                Constitutional Zero-Transmission Policy
                              </div>
                              <div>
                                Planning authority is strictly decoupled from transmission and execution authority. This action plan is sealed for operator review. No browser connection, email, or external network transmission has been initiated.
                              </div>
                            </div>
                          </div>
                        )}
                      </div>

                      {/* Multi-Tab Selector */}
                      <div className="flex flex-wrap gap-2 border-b border-jv-rule pb-2">
                        {artifacts.map((art) => {
                          const isSelected = (selectedArtifactId || artifacts[0]?.artifact_id) === art.artifact_id;
                          return (
                            <button
                              key={art.artifact_id}
                              type="button"
                              onClick={() => {
                                setSelectedArtifactId(art.artifact_id);
                                setShowProvenanceDrawer(false);
                              }}
                              className={`px-3 py-1.5 font-machine text-xs uppercase transition-colors border ${
                                isSelected
                                  ? 'border-jv-ink bg-jv-ink text-jv-bg font-semibold'
                                  : 'border-jv-rule bg-jv-surface/40 text-jv-muted hover:text-jv-ink hover:border-jv-ink'
                              }`}
                            >
                              {art.material_type.replace(/_/g, ' ')}
                            </button>
                          );
                        })}
                      </div>

                      {/* Selected Artifact Reader */}
                      {(() => {
                        const activeArt = artifacts.find(
                          (a) => a.artifact_id === (selectedArtifactId || artifacts[0]?.artifact_id)
                        ) || artifacts[0];
                        if (!activeArt) return null;

                        return (
                          <div className="space-y-4">
                            {/* Artifact Header Toolbar */}
                            <div className="p-4 border border-jv-rule bg-jv-surface/30 flex flex-col md:flex-row md:items-center justify-between gap-3">
                              <div className="space-y-1">
                                <div className="flex items-center gap-2">
                                  <h4 className="font-interface font-medium text-base text-jv-ink">
                                    {activeArt.title}
                                  </h4>
                                  <TextureBadge status={activeArt.status} />
                                </div>
                                <div className="font-machine text-[11px] text-jv-muted flex items-center gap-3">
                                  <span>MATERIAL: {activeArt.material_type.toUpperCase()}</span>
                                  <span>VERSION: v{activeArt.version}</span>
                                  <span>BLOCKS: {activeArt.blocks?.length || 0}</span>
                                </div>
                              </div>

                              <div className="flex flex-wrap items-center gap-2">
                                <TactileButton
                                  variant="outline"
                                  size="sm"
                                  onClick={() => handleCopyMarkdown(activeArt.rendered_markdown)}
                                >
                                  {copiedFormat === 'markdown' ? (
                                    <>
                                      <Check className="w-3.5 h-3.5 mr-1 text-jv-accent inline" />
                                      Copied MD!
                                    </>
                                  ) : (
                                    <>
                                      <Copy className="w-3.5 h-3.5 mr-1 inline" />
                                      Copy Markdown
                                    </>
                                  )}
                                </TactileButton>

                                <TactileButton
                                  variant="outline"
                                  size="sm"
                                  onClick={() => handleCopyPlainText(activeArt.rendered_markdown)}
                                >
                                  {copiedFormat === 'plain' ? (
                                    <>
                                      <Check className="w-3.5 h-3.5 mr-1 text-jv-accent inline" />
                                      Copied Text!
                                    </>
                                  ) : (
                                    <>
                                      <Copy className="w-3.5 h-3.5 mr-1 inline" />
                                      Copy Plain Text
                                    </>
                                  )}
                                </TactileButton>

                                <TactileButton
                                  variant={showProvenanceDrawer ? 'primary' : 'outline'}
                                  size="sm"
                                  onClick={() => setShowProvenanceDrawer(!showProvenanceDrawer)}
                                >
                                  <ShieldCheck className="w-3.5 h-3.5 mr-1 inline" />
                                  {showProvenanceDrawer ? 'Hide Provenance' : 'Inspect Provenance'}
                                </TactileButton>
                              </div>
                            </div>

                            {/* Provenance Inspector Drawer */}
                            {showProvenanceDrawer && (
                              <div className="border border-jv-accent/40 bg-jv-surface/80 p-5 space-y-4 font-machine text-xs">
                                <div className="flex items-center justify-between border-b border-jv-rule pb-2">
                                  <div className="flex items-center gap-2">
                                    <ShieldCheck className="w-4 h-4 text-jv-accent" />
                                    <span className="font-semibold text-jv-ink uppercase">
                                      Grounding Provenance Map // {activeArt.title}
                                    </span>
                                  </div>
                                  <button
                                    type="button"
                                    onClick={() => setShowProvenanceDrawer(false)}
                                    className="text-jv-muted hover:text-jv-ink"
                                  >
                                    <X className="w-4 h-4" />
                                  </button>
                                </div>

                                <div className="grid grid-cols-1 sm:grid-cols-3 gap-2 text-[11px] text-jv-muted bg-jv-bg p-3 border border-jv-rule">
                                  <div><span className="text-jv-ink font-semibold">PLANNER:</span> {activeArt.planner_version}</div>
                                  <div><span className="text-jv-ink font-semibold">INPUT HASH:</span> {activeArt.input_hash.substring(0, 16)}…</div>
                                  <div><span className="text-jv-ink font-semibold">SNAPSHOT HASH:</span> {activeArt.grounding_snapshot_hash.substring(0, 16)}…</div>
                                </div>

                                <div className="space-y-2">
                                  <div className="text-jv-muted uppercase text-[11px]">
                                    AST Semantic Block Breakdown ({activeArt.blocks?.length || 0} nodes):
                                  </div>
                                  <div className="divide-y divide-jv-rule border border-jv-rule max-h-96 overflow-y-auto bg-jv-bg">
                                    {activeArt.blocks?.map((blk, bIdx) => (
                                      <div key={blk.block_id || bIdx} className="p-3 space-y-1.5">
                                        <div className="flex items-center justify-between gap-2">
                                          <div className="flex items-center gap-2">
                                            <span className="text-[10px] text-jv-muted uppercase font-mono">
                                              #{bIdx + 1} [{blk.block_type}]
                                            </span>
                                            <span
                                              className={`text-[9px] px-1.5 py-0.5 border uppercase font-mono ${
                                                blk.grounding_type === 'claim'
                                                  ? 'border-jv-ink bg-jv-ink text-jv-bg font-bold'
                                                  : blk.grounding_type === 'opportunity'
                                                  ? 'border-jv-accent text-jv-accent'
                                                  : blk.grounding_type === 'context_fact'
                                                  ? 'border-jv-rule text-jv-ink'
                                                  : 'border-jv-rule text-jv-muted'
                                              }`}
                                            >
                                              {blk.grounding_type}
                                            </span>
                                          </div>
                                          {blk.claim_ids && blk.claim_ids.length > 0 && (
                                            <span className="text-[10px] text-jv-muted">
                                              CLAIMS: {blk.claim_ids.join(', ')}
                                            </span>
                                          )}
                                          {blk.fact_ids && blk.fact_ids.length > 0 && (
                                            <span className="text-[10px] text-jv-muted">
                                              FACTS: {blk.fact_ids.join(', ')}
                                            </span>
                                          )}
                                        </div>
                                        <p className="font-interface text-xs text-jv-ink-soft line-clamp-2">
                                          {blk.content}
                                        </p>
                                      </div>
                                    ))}
                                  </div>
                                </div>
                              </div>
                            )}

                            {/* Clean Rendered Document Text Reader */}
                            <div className="border border-jv-rule bg-jv-bg p-8 shadow-sm">
                              <pre className="font-interface text-sm text-jv-ink whitespace-pre-wrap leading-relaxed select-text font-normal">
                                {activeArt.rendered_markdown}
                              </pre>
                            </div>
                          </div>
                        );
                      })()}
                    </div>
                  )}
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

      {/* Resolve Action Plan Inputs & Vault Fact Bindings Modal */}
      {showResolvePlanModal && (
        <div className="fixed inset-0 z-50 bg-jv-ink/40 flex items-center justify-center p-4">
          <div className="w-full max-w-3xl max-h-[90vh] overflow-y-auto border border-jv-rule bg-jv-bg p-6 space-y-6 font-interface">
            <div className="flex items-start justify-between border-b border-jv-rule pb-3">
              <div className="space-y-1">
                <div className="flex items-center gap-2">
                  <ShieldCheck className="w-5 h-5 text-jv-accent" />
                  <h3 className="font-display text-2xl text-jv-ink">
                    Resolve Action Plan Inputs
                  </h3>
                </div>
                <p className="text-xs text-jv-muted">
                  Configure target form schema fields, bind verified facts from your Context Vault, and seal the exact submit locator.
                </p>
              </div>
              <button
                type="button"
                onClick={() => setShowResolvePlanModal(false)}
                className="text-jv-muted hover:text-jv-ink p-1 border border-jv-rule bg-jv-surface"
              >
                <X className="w-4 h-4" />
              </button>
            </div>

            {/* Section 1: Exact Destination */}
            <div className="border border-jv-rule bg-jv-surface/40 p-4 space-y-3">
              <div className="font-machine text-xs font-semibold text-jv-ink uppercase flex items-center justify-between">
                <span>1. Exact Application Destination</span>
                <span className="text-[10px] text-jv-muted font-normal">USER_PROVIDED PROVENANCE</span>
              </div>
              <div className="grid grid-cols-1 sm:grid-cols-2 gap-3 text-xs">
                <div className="sm:col-span-2 space-y-1">
                  <label className="text-jv-muted font-medium">Canonical Application URL</label>
                  <input
                    type="url"
                    value={resolveDestinationUrl}
                    onChange={(e) => setResolveDestinationUrl(e.target.value)}
                    placeholder="https://company.greenhouse.io/jobs/123/apply"
                    className="w-full p-2 border border-jv-rule bg-jv-bg font-mono text-xs text-jv-ink rounded-none"
                  />
                </div>
                <div className="space-y-1">
                  <label className="text-jv-muted font-medium">Organization Name</label>
                  <input
                    type="text"
                    value={resolveDestinationOrg}
                    onChange={(e) => setResolveDestinationOrg(e.target.value)}
                    placeholder="e.g. Acme Corp"
                    className="w-full p-2 border border-jv-rule bg-jv-bg text-xs text-jv-ink rounded-none"
                  />
                </div>
                <div className="space-y-1">
                  <label className="text-jv-muted font-medium">Channel Type</label>
                  <input
                    type="text"
                    value="ats_form"
                    disabled
                    className="w-full p-2 border border-jv-rule bg-jv-surface font-mono text-xs text-jv-muted rounded-none cursor-not-allowed"
                  />
                </div>
              </div>
            </div>

            {/* Section 2: Target Fields & Fact Bindings */}
            <div className="border border-jv-rule bg-jv-surface/40 p-4 space-y-3">
              <div className="font-machine text-xs font-semibold text-jv-ink uppercase flex items-center justify-between">
                <span>2. External ATS Form Field Bindings</span>
                <span className="text-[10px] text-jv-muted font-normal">EXPLICIT VAULT BINDING ONLY</span>
              </div>
              <p className="text-[11px] text-jv-muted">
                Each target form field must be explicitly bound to an authorized Vault fact. Missing required bindings will keep the action plan in DRAFT status.
              </p>

              <div className="divide-y divide-jv-rule border border-jv-rule bg-jv-bg">
                {((latestPlan?.target_schema?.fields && latestPlan.target_schema.fields.length > 0)
                  ? latestPlan.target_schema.fields
                  : defaultSchemaFields
                ).map((field) => {
                  const currentBindingFactId = resolveFieldBindings[field.target_field_id] || '';
                  const boundFact = (contextFacts || []).find((f) => f.fact_id === currentBindingFactId);
                  const isBound = Boolean(currentBindingFactId);

                  return (
                    <div key={field.target_field_id} className="p-3 space-y-2 text-xs">
                      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-1">
                        <div className="flex items-center gap-2">
                          <span className="font-medium text-jv-ink">{field.label}</span>
                          <span className="font-mono text-[11px] text-jv-muted">({field.target_field_id})</span>
                          <span className="font-machine text-[10px] px-1.5 py-0.2 border border-jv-rule bg-jv-surface uppercase text-jv-muted">
                            {field.field_type}
                          </span>
                          {field.required && (
                            <span className="text-[10px] text-red-500 font-bold">*REQUIRED</span>
                          )}
                        </div>
                        <div>
                          {isBound ? (
                            <span className="font-machine text-[10px] text-emerald-600 bg-emerald-600/10 border border-emerald-600/30 px-1.5 py-0.5">
                              BOUND
                            </span>
                          ) : field.required ? (
                            <span className="font-machine text-[10px] text-amber-600 bg-amber-600/10 border border-amber-600/30 px-1.5 py-0.5">
                              UNBOUND (REQUIRED)
                            </span>
                          ) : (
                            <span className="font-machine text-[10px] text-jv-muted bg-jv-surface border border-jv-rule px-1.5 py-0.5">
                              OPTIONAL
                            </span>
                          )}
                        </div>
                      </div>

                      <div className="flex flex-col sm:flex-row items-stretch sm:items-center gap-2">
                        <select
                          aria-label={`Vault Fact for ${field.label}`}
                          value={currentBindingFactId}
                          onChange={(e) => {
                            const val = e.target.value;
                            setResolveFieldBindings((prev) => ({
                              ...prev,
                              [field.target_field_id]: val,
                            }));
                          }}
                          className="flex-1 p-2 border border-jv-rule bg-jv-surface text-xs font-interface text-jv-ink rounded-none"
                        >
                          <option value="">-- No Vault Fact Bound (Unbound) --</option>
                          {(contextFacts || []).map((fact) => (
                            <option key={fact.fact_id} value={fact.fact_id}>
                              [{fact.verification_status}] {fact.key} ({fact.namespace}) — {fact.preview ? fact.preview.slice(0, 40) : '(empty)'}
                            </option>
                          ))}
                        </select>
                      </div>

                      {boundFact && (
                        <div className="font-machine text-[11px] text-jv-muted flex items-center gap-2 bg-jv-surface/50 p-1.5 border border-jv-rule/50">
                          <span className="text-jv-ink font-semibold">FACT ID: {boundFact.fact_id}</span>
                          <span>·</span>
                          <span className="text-jv-accent">{boundFact.verification_status}</span>
                          {boundFact.preview && (
                            <>
                              <span>·</span>
                              <span className="truncate max-w-[250px] font-mono">{boundFact.preview}</span>
                            </>
                          )}
                        </div>
                      )}
                    </div>
                  );
                })}
              </div>
            </div>

            {/* Section 3: Materialized Upload Slots */}
            <div className="border border-jv-rule bg-jv-surface/40 p-4 space-y-3">
              <div className="font-machine text-xs font-semibold text-jv-ink uppercase flex items-center justify-between">
                <span>3. Materialized Upload Assets</span>
                <span className="text-[10px] text-jv-muted font-normal">DURABLE BINARY STORAGE</span>
              </div>
              <p className="text-[11px] text-jv-muted">
                Frozen package artifacts will be materialized into immutable byte assets with SHA-256 integrity verification.
              </p>
              <div className="border border-jv-rule bg-jv-bg p-3 space-y-1 font-machine text-xs">
                <div className="flex items-center justify-between text-jv-ink">
                  <span className="font-semibold">RESUME // slot: resume</span>
                  <span className="text-emerald-600 text-[10px] border border-emerald-600/30 px-1.5 py-0.5 bg-emerald-600/10">
                    APPROVED PACKAGE ARTIFACT
                  </span>
                </div>
                <div className="text-[11px] text-jv-muted">
                  Binds to approved package: <span className="font-mono text-jv-ink">{latestPackage?.package_id || 'none'}</span>
                </div>
              </div>
            </div>

            {/* Section 4: Exact Final Submit Control */}
            <div className="border border-jv-rule bg-jv-surface/40 p-4 space-y-3">
              <div className="font-machine text-xs font-semibold text-jv-ink uppercase flex items-center justify-between">
                <span>4. Exact Final Submit Control Locator</span>
                <span className="text-[10px] text-amber-600 font-semibold">REQUIRED FOR READY_FOR_AUTHORIZATION</span>
              </div>
              <p className="text-[11px] text-jv-muted">
                Specify the exact DOM selector or control ID that submits the form. Plans without an exact submit locator cannot be authorized and remain in DRAFT status.
              </p>

              <div className="grid grid-cols-1 sm:grid-cols-3 gap-3 text-xs">
                <div className="sm:col-span-2 space-y-1">
                  <label className="text-jv-muted font-medium">CSS Selector</label>
                  <input
                    type="text"
                    value={resolveSubmitSelector}
                    onChange={(e) => setResolveSubmitSelector(e.target.value)}
                    placeholder="e.g. button[data-automation-id='submit-button'] or #submit-application"
                    className="w-full p-2 border border-jv-rule bg-jv-bg font-mono text-xs text-jv-ink rounded-none"
                  />
                </div>
                <div className="space-y-1">
                  <label className="text-jv-muted font-medium">Control ID (Optional)</label>
                  <input
                    type="text"
                    value={resolveSubmitControlId}
                    onChange={(e) => setResolveSubmitControlId(e.target.value)}
                    placeholder="e.g. submit_btn"
                    className="w-full p-2 border border-jv-rule bg-jv-bg font-mono text-xs text-jv-ink rounded-none"
                  />
                </div>
                <div className="sm:col-span-3 space-y-1">
                  <label className="text-jv-muted font-medium">Submit Button Label</label>
                  <input
                    type="text"
                    value={resolveSubmitLabel}
                    onChange={(e) => setResolveSubmitLabel(e.target.value)}
                    placeholder="e.g. Submit Application"
                    className="w-full p-2 border border-jv-rule bg-jv-bg text-xs text-jv-ink rounded-none"
                  />
                </div>
              </div>
            </div>

            {/* Constitutional Notice */}
            <div className="p-3 border border-jv-rule bg-jv-surface/30 text-xs text-jv-muted font-interface flex items-start gap-2">
              <Lock className="w-4 h-4 text-jv-accent shrink-0 mt-0.5" />
              <div>
                <span className="font-semibold text-jv-ink">Decoupled Planning Authority:</span> Saving and compiling this plan seals an exact execution specification for user review. No browser connection, network request, or transmission is made.
              </div>
            </div>

            {/* Footer Buttons */}
            <div className="flex items-center justify-end gap-3 pt-3 border-t border-jv-rule">
              <TactileButton
                variant="outline"
                size="sm"
                onClick={() => setShowResolvePlanModal(false)}
              >
                Cancel
              </TactileButton>
              <TactileButton
                variant="primary"
                size="sm"
                loading={createPlan.isPending}
                onClick={handleSaveAndCompileResolvedPlan}
              >
                Compile Action Plan with Resolved Bindings →
              </TactileButton>
            </div>
          </div>
        </div>
      )}
    </div>
  );
};
