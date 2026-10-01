import React, { useState, useEffect } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import {
  useContextFacts,
  useFactDetail,
  useProfileReadiness,
  useRecordFact,
  useVerifyFact,
  useRejectFact,
} from '../hooks/useCaseworker';
import { TextureBadge } from '../components/ui/TextureBadge';
import { TactileButton } from '../components/ui/TactileButton';
import { Lock, Eye, EyeOff, X, Check, AlertTriangle } from 'lucide-react';
import type { FactSummary } from '../api/types';

interface FactRowProps {
  fact: FactSummary;
  onVerify: (factId: string, version: number) => Promise<void>;
  onReject: (factId: string, version: number) => Promise<void>;
}

const FactRow: React.FC<FactRowProps> = ({ fact, onVerify, onReject }) => {
  const queryClient = useQueryClient();
  const [revealed, setRevealed] = useState(false);

  // On-demand detail fetching ONLY when explicitly revealed
  const { data: detailData, isLoading } = useFactDetail(revealed ? fact.fact_id : null);
  const isSensitive = fact.sensitivity === 'sensitive';

  const handleToggleReveal = () => {
    if (revealed) {
      // User is masking: immediately remove cached detail query to prevent retention
      queryClient.removeQueries({ queryKey: ['context', 'facts', 'detail', fact.fact_id] });
      setRevealed(false);
    } else {
      setRevealed(true);
    }
  };

  return (
    <div className="py-4 flex flex-col md:flex-row md:items-center justify-between gap-3 px-3 -mx-3 hover:bg-ink/50 transition-colors">
      <div className="space-y-1 max-w-xl">
        <div className="flex flex-wrap items-center gap-3">
          <span className="font-machine text-xs text-grey-500 uppercase">
            {fact.namespace}.{fact.key}
          </span>
          {fact.verification_status === 'user_verified' || fact.verification_status === 'source_verified' ? (
            <span className="font-machine text-[10px] px-1.5 py-0.2 border border-paper text-pure uppercase">
              VERIFIED
            </span>
          ) : fact.verification_status === 'rejected' ? (
            <span className="font-machine text-[10px] px-1.5 py-0.2 border border-grey-500 text-grey-500 uppercase">
              REJECTED
            </span>
          ) : (
            <span className="font-machine text-[10px] px-1.5 py-0.2 border border-grey-700 text-grey-500 uppercase">
              UNVERIFIED
            </span>
          )}
          <span className="font-machine text-[10px] text-grey-500 uppercase">
            v{fact.version}
          </span>
        </div>

        {/* Fact Value Representation */}
        <div className="font-interface text-sm text-pure pt-0.5">
          {isSensitive ? (
            revealed ? (
              isLoading ? (
                <span className="font-machine text-xs text-grey-500">
                  Fetching protected detail...
                </span>
              ) : (
                <div className="flex items-center gap-2">
                  <Lock className="w-3.5 h-3.5 text-pure shrink-0" />
                  <span className="font-machine text-xs bg-canvas px-2 py-0.5 border border-grey-500">
                    {String(detailData?.detail?.value ?? '[EMPTY]')}
                  </span>
                </div>
              )
            ) : (
              <span className="font-machine text-xs tracking-widest text-grey-500 select-none">
                PRIVATE ••••••••••••
              </span>
            )
          ) : (
            <span>{fact.preview || 'Recorded Value'}</span>
          )}
        </div>
      </div>

      <div className="flex flex-wrap items-center gap-4 pl-0 md:pl-4">
        <span className="font-machine text-xs uppercase text-grey-500">
          {fact.sensitivity}
        </span>

        {isSensitive && (
          <button
            type="button"
            onClick={handleToggleReveal}
            className="flex items-center gap-1.5 font-machine text-xs text-grey-300 hover:text-pure underline underline-offset-4"
          >
            {revealed ? (
              <>
                <EyeOff className="w-3.5 h-3.5" />
                <span>Mask</span>
              </>
            ) : (
              <>
                <Eye className="w-3.5 h-3.5" />
                <span>Reveal</span>
              </>
            )}
          </button>
        )}

        {/* Fact Lifecycle Controls (operates strictly from summary metadata without revealing value) */}
        {fact.verification_status === 'unverified' && (
          <div className="flex items-center gap-2">
            <button
              type="button"
              onClick={() => onVerify(fact.fact_id, fact.version)}
              className="p-1 border border-grey-700 hover:border-paper text-grey-300 hover:text-pure text-xs font-machine"
              title="Verify fact assertion"
            >
              <Check className="w-3.5 h-3.5" />
            </button>
            <button
              type="button"
              onClick={() => onReject(fact.fact_id, fact.version)}
              className="p-1 border border-grey-700 hover:border-paper text-grey-500 hover:text-pure text-xs font-machine"
              title="Reject fact assertion"
            >
              <X className="w-3.5 h-3.5" />
            </button>
          </div>
        )}
      </div>
    </div>
  );
};

export const ContextView: React.FC = () => {
  const queryClient = useQueryClient();
  const { data: facts, isLoading, refetch } = useContextFacts();
  const { data: jobReadiness } = useProfileReadiness('job_application');
  const recordFact = useRecordFact();
  const verifyFact = useVerifyFact();
  const rejectFact = useRejectFact();

  const [showAddFact, setShowAddFact] = useState(false);
  const [namespace, setNamespace] = useState('identity');
  const [key, setKey] = useState('');
  const [value, setValue] = useState('');
  const [sensitivity, setSensitivity] = useState('personal');
  const [concurrencyNotice, setConcurrencyNotice] = useState<string | null>(null);

  // Clean up all revealed sensitive queries on page unmount
  useEffect(() => {
    return () => {
      queryClient.removeQueries({ queryKey: ['context', 'facts', 'detail'] });
    };
  }, [queryClient]);

  const handleRecord = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!key.trim() || !value.trim()) return;
    setConcurrencyNotice(null);

    await recordFact.mutateAsync({
      namespace,
      key: key.trim(),
      value: value.trim(),
      sensitivity,
      allowed_purposes: ['job_application', 'housing_application'],
    });
    setKey('');
    setValue('');
    setShowAddFact(false);
  };

  const handleVerify = async (factId: string, version: number) => {
    setConcurrencyNotice(null);
    try {
      const etag = `"fact:${factId}:v${version}"`;
      await verifyFact.mutateAsync({
        factId,
        status: 'user_verified',
        etag,
      });
    } catch (err: any) {
      if (err.name === 'PreconditionFailedError' || err.status === 412) {
        setConcurrencyNotice("This fact was modified elsewhere. We've loaded the latest version.");
        refetch();
      } else {
        setConcurrencyNotice(err.message || 'Verification failed');
      }
    }
  };

  const handleReject = async (factId: string, version: number) => {
    setConcurrencyNotice(null);
    try {
      const etag = `"fact:${factId}:v${version}"`;
      await rejectFact.mutateAsync({
        factId,
        reason: 'Rejected by user from Context Vault',
        etag,
      });
    } catch (err: any) {
      if (err.name === 'PreconditionFailedError' || err.status === 412) {
        setConcurrencyNotice("This fact was modified elsewhere. We've loaded the latest version.");
        refetch();
      } else {
        setConcurrencyNotice(err.message || 'Rejection failed');
      }
    }
  };

  // Group facts by namespace
  const groupedFacts = (facts || []).reduce<Record<string, FactSummary[]>>((acc, f) => {
    const ns = f.namespace.toUpperCase();
    if (!acc[ns]) acc[ns] = [];
    acc[ns].push(f);
    return acc;
  }, {});

  return (
    <div className="space-y-16 font-interface text-paper">
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
            PERSONAL CONTEXT VAULT // VERIFIABLE PROFILE INDEX
          </div>
          <h1 className="font-display text-4xl text-pure tracking-tight">
            My Context
          </h1>
        </div>

        <TactileButton variant="primary" size="sm" onClick={() => setShowAddFact(!showAddFact)}>
          {showAddFact ? 'Cancel' : '+ Add Fact'}
        </TactileButton>
      </div>

      {/* Profile Readiness Dossier */}
      {jobReadiness && (
        <section className="p-6 border border-grey-700 bg-ink space-y-4">
          <div className="flex items-center justify-between font-machine text-xs">
            <span className="text-grey-500 uppercase tracking-widest">
              PROFILE READINESS // {jobReadiness.title}
            </span>
            <TextureBadge status={jobReadiness.is_ready ? 'complete' : 'waiting'} />
          </div>

          <div className="flex flex-col md:flex-row md:items-baseline gap-6">
            <div className="font-display text-4xl text-pure">
              {Math.round(jobReadiness.completeness_ratio * 100)}%
            </div>
            <div className="font-machine text-xs text-grey-300 space-y-1">
              <div>
                Satisfied: {jobReadiness.satisfied_count} · Missing: {jobReadiness.missing_count}
              </div>
              <div className="text-grey-500">
                Evaluation against authoritative Job Application RequirementSet
              </div>
            </div>
          </div>

          {/* Missing Requirements List */}
          {(jobReadiness.missing?.length || 0) > 0 && (
            <div className="pt-2 border-t border-grey-700 space-y-2">
              <span className="font-machine text-xs text-grey-500 uppercase">
                Missing Requirements:
              </span>
              <div className="grid grid-cols-1 sm:grid-cols-2 gap-2 font-machine text-xs">
                {jobReadiness.missing?.map((req) => (
                  <div key={req.requirement_id} className="flex items-center gap-2 text-grey-300">
                    <X className="w-3.5 h-3.5 text-grey-500 shrink-0" />
                    <span>{req.label}</span>
                  </div>
                ))}
              </div>
            </div>
          )}
        </section>
      )}

      {/* Inline Add Fact Form */}
      {showAddFact && (
        <form onSubmit={handleRecord} className="p-6 border border-grey-700 bg-ink space-y-4">
          <div className="font-machine text-xs text-grey-500 uppercase tracking-widest border-b border-grey-700 pb-2">
            RECORD CONTEXT FACT
          </div>

          <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
            <div className="space-y-1">
              <label className="font-machine text-xs text-grey-500 uppercase">Namespace</label>
              <select
                value={namespace}
                onChange={(e) => setNamespace(e.target.value)}
                className="w-full bg-canvas border border-grey-700 px-3 py-2 text-sm text-pure font-machine outline-none"
              >
                <option value="identity">Identity</option>
                <option value="contact">Contact</option>
                <option value="career">Career</option>
                <option value="skills">Skills</option>
                <option value="education">Education</option>
                <option value="preferences">Preferences</option>
              </select>
            </div>

            <div className="space-y-1">
              <label className="font-machine text-xs text-grey-500 uppercase">Fact Key</label>
              <input
                type="text"
                value={key}
                onChange={(e) => setKey(e.target.value)}
                placeholder="e.g. legal_name, years_experience"
                required
                className="w-full bg-canvas border border-grey-700 px-3 py-2 text-sm text-pure outline-none focus:border-paper"
              />
            </div>

            <div className="space-y-1">
              <label className="font-machine text-xs text-grey-500 uppercase">Sensitivity</label>
              <select
                value={sensitivity}
                onChange={(e) => setSensitivity(e.target.value)}
                className="w-full bg-canvas border border-grey-700 px-3 py-2 text-sm text-pure font-machine outline-none"
              >
                <option value="public">Public</option>
                <option value="personal">Personal</option>
                <option value="sensitive">Sensitive</option>
              </select>
            </div>
          </div>

          <div className="space-y-1">
            <label className="font-machine text-xs text-grey-500 uppercase">Fact Value</label>
            <input
              type="text"
              value={value}
              onChange={(e) => setValue(e.target.value)}
              placeholder="e.g. John Doe, or salary target"
              required
              className="w-full bg-canvas border border-grey-700 px-3 py-2 text-sm text-pure outline-none focus:border-paper"
            />
          </div>

          <div className="flex justify-end gap-3 pt-2">
            <TactileButton type="button" variant="outline" size="sm" onClick={() => setShowAddFact(false)}>
              Cancel
            </TactileButton>
            <TactileButton type="submit" variant="primary" size="sm" loading={recordFact.isPending}>
              Record Fact →
            </TactileButton>
          </div>
        </form>
      )}

      {/* Fact Groupings */}
      {isLoading ? (
        <div className="py-24 text-center font-machine text-xs text-grey-500">
          INDEXING VAULT FACTS...
        </div>
      ) : Object.keys(groupedFacts).length === 0 ? (
        <div className="py-20 border border-grey-700 text-center space-y-4 bg-ink/20">
          <div className="font-machine text-xs text-grey-500 uppercase tracking-widest">
            ZERO RECORDED CONTEXT FACTS
          </div>
          <p className="text-sm text-grey-300 max-w-sm mx-auto">
            Your personal context vault is currently empty. Record verified identity, career,
            and preference items to ground all future agent proposals.
          </p>
        </div>
      ) : (
        <div className="space-y-12">
          {Object.entries(groupedFacts).map(([groupName, groupItems]) => (
            <section key={groupName} className="space-y-4">
              <div className="flex items-baseline justify-between border-b border-grey-700 pb-2">
                <h2 className="font-display text-2xl text-pure tracking-tight">
                  {groupName}
                </h2>
                <span className="font-machine text-xs text-grey-500">
                  {groupItems.length} FACTS
                </span>
              </div>

              <div className="divide-y divide-grey-700 border-y border-grey-700">
                {groupItems.map((f) => (
                  <FactRow
                    key={f.fact_id}
                    fact={f}
                    onVerify={handleVerify}
                    onReject={handleReject}
                  />
                ))}
              </div>
            </section>
          ))}
        </div>
      )}
    </div>
  );
};
