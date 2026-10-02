import React, { useState, useEffect } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import {
  useContextFacts,
  useFactDetail,
  useProfileReadiness,
  useRecordFact,
  useVerifyFact,
  useRejectFact,
  useSupersedeFact,
} from '../hooks/useCaseworker';
import { TextureBadge } from '../components/ui/TextureBadge';
import { TactileButton } from '../components/ui/TactileButton';
import { Lock, Eye, EyeOff, X, Check, AlertTriangle } from 'lucide-react';
import type { FactSummary } from '../api/types';

interface FactRowProps {
  fact: FactSummary;
  onVerify: (factId: string, version: number) => Promise<void>;
  onReject: (factId: string, version: number) => Promise<void>;
  onSupersede: (factId: string, version: number, newValue: string, reason?: string) => Promise<void>;
}

const FactRow: React.FC<FactRowProps> = ({ fact, onVerify, onReject, onSupersede }) => {
  const queryClient = useQueryClient();
  const [detailOpen, setDetailOpen] = useState(false);
  const [isEditing, setIsEditing] = useState(false);
  const [newValue, setNewValue] = useState('');
  const [updateReason, setUpdateReason] = useState('');
  const [isSubmitting, setIsSubmitting] = useState(false);

  // On-demand detail fetching ONLY when explicitly opened/revealed
  const { data: detailData, isLoading } = useFactDetail(detailOpen ? fact.fact_id : null);
  const isSensitive = fact.sensitivity === 'sensitive';
  const isPersonal = fact.sensitivity === 'personal';

  const handleToggleDetail = () => {
    if (detailOpen) {
      // User is masking/hiding: immediately purge cached detail query to prevent retention
      queryClient.removeQueries({ queryKey: ['context', 'facts', 'detail', fact.fact_id] });
      setDetailOpen(false);
    } else {
      setDetailOpen(true);
    }
  };

  const handleSupersedeSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!newValue.trim()) return;
    setIsSubmitting(true);
    try {
      await onSupersede(fact.fact_id, fact.version, newValue.trim(), updateReason.trim() || undefined);
      setIsEditing(false);
      setNewValue('');
      setUpdateReason('');
    } catch {
      // Handled in ContextView
    } finally {
      setIsSubmitting(false);
    }
  };

  return (
    <div className="py-4 px-3 -mx-3 hover:bg-jv-surface transition-colors duration-fast ease-editorial">
      <div className="flex flex-col md:flex-row md:items-center justify-between gap-3">
        <div className="space-y-1 max-w-xl">
          <div className="flex flex-wrap items-center gap-3">
            <span className="font-interface text-sm font-medium text-jv-ink">
              {fact.namespace}.{fact.key}
            </span>
            {fact.verification_status === 'user_verified' || fact.verification_status === 'source_verified' ? (
              <span className="font-machine text-[10px] px-1.5 py-0.2 border border-jv-ink text-jv-ink uppercase">
                VERIFIED
              </span>
            ) : fact.verification_status === 'rejected' ? (
              <span className="font-machine text-[10px] px-1.5 py-0.2 border border-jv-rule-strong text-jv-muted uppercase">
                REJECTED
              </span>
            ) : (
              <span className="font-machine text-[10px] px-1.5 py-0.2 border border-jv-rule text-jv-muted uppercase">
                UNVERIFIED
              </span>
            )}
            <span className="font-machine text-[10px] text-jv-muted uppercase">
              v{fact.version}
            </span>
          </div>

          {/* Fact Value Representation */}
          <div className="font-interface text-sm text-jv-ink pt-0.5">
            {isSensitive ? (
              detailOpen ? (
                isLoading ? (
                  <span className="font-interface text-xs text-jv-muted">
                    Fetching protected detail...
                  </span>
                ) : (
                  <div className="flex items-center gap-2">
                    <Lock className="w-3.5 h-3.5 text-jv-ink shrink-0" />
                    <span className="font-interface text-sm bg-jv-bg px-2 py-0.5 border border-jv-rule">
                      {String(detailData?.detail?.value ?? '[EMPTY]')}
                    </span>
                  </div>
                )
              ) : (
                <span className="font-machine text-xs tracking-widest text-jv-muted select-none">
                  PRIVATE ••••••••••••
                </span>
              )
            ) : isPersonal ? (
              detailOpen ? (
                isLoading ? (
                  <span className="font-interface text-xs text-jv-muted">
                    Fetching personal detail...
                  </span>
                ) : (
                  <span className="font-interface text-sm text-jv-ink font-medium">
                    {String(detailData?.detail?.value ?? '[EMPTY]')}
                  </span>
                )
              ) : (
                <span className="font-interface text-xs text-jv-muted italic">
                  Personal record · Detail on demand
                </span>
              )
            ) : (
              <span className="font-medium font-interface">{fact.preview || 'Recorded Value'}</span>
            )}
          </div>
        </div>

        <div className="flex flex-wrap items-center gap-4 pl-0 md:pl-4">
          <span className="font-machine text-[10px] uppercase text-jv-muted">
            {fact.sensitivity}
          </span>

          {/* Detail Controls for Sensitive & Personal Facts */}
          {isSensitive && (
            <button
              type="button"
              onClick={handleToggleDetail}
              className="flex items-center gap-1.5 font-interface text-xs text-jv-ink-soft hover:text-jv-ink underline underline-offset-4"
            >
              {detailOpen ? (
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

          {isPersonal && (
            <button
              type="button"
              onClick={handleToggleDetail}
              className="flex items-center gap-1.5 font-interface text-xs text-jv-ink-soft hover:text-jv-ink underline underline-offset-4"
            >
              {detailOpen ? (
                <>
                  <EyeOff className="w-3.5 h-3.5" />
                  <span>Hide</span>
                </>
              ) : (
                <>
                  <Eye className="w-3.5 h-3.5" />
                  <span>View</span>
                </>
              )}
            </button>
          )}

          {/* Fact Update / Supersede Trigger */}
          <button
            type="button"
            onClick={() => setIsEditing(!isEditing)}
            className="font-interface text-xs text-jv-ink-soft hover:text-jv-ink underline underline-offset-4"
          >
            {isEditing ? 'Cancel' : 'Update'}
          </button>

          {/* Fact Lifecycle Controls */}
          {fact.verification_status === 'unverified' && (
            <div className="flex items-center gap-2">
              <button
                type="button"
                onClick={() => onVerify(fact.fact_id, fact.version)}
                className="p-1 border border-jv-rule hover:border-jv-ink text-jv-ink-soft hover:text-jv-ink text-xs font-interface"
                title="Verify fact assertion"
              >
                <Check className="w-3.5 h-3.5" />
              </button>
              <button
                type="button"
                onClick={() => onReject(fact.fact_id, fact.version)}
                className="p-1 border border-jv-rule hover:border-jv-ink text-jv-muted hover:text-jv-ink text-xs font-interface"
                title="Reject fact assertion"
              >
                <X className="w-3.5 h-3.5" />
              </button>
            </div>
          )}
        </div>
      </div>

      {/* Inline Supersede / Update Editor */}
      {isEditing && (
        <form
          onSubmit={handleSupersedeSubmit}
          className="mt-3 p-4 border border-jv-rule bg-jv-surface space-y-3 animate-fadeIn"
        >
          <div className="flex items-center justify-between border-b border-jv-rule pb-1.5">
            <span className="font-interface text-xs font-medium text-jv-muted">
              Supersede fact // Historical lineage preserved
            </span>
            <span className="font-machine text-[10px] text-jv-muted">
              v{fact.version}
            </span>
          </div>

          <div className="space-y-1">
            <label className="font-interface text-xs text-jv-muted">
              Replacement value
            </label>
            <input
              type="text"
              value={newValue}
              onChange={(e) => setNewValue(e.target.value)}
              placeholder="Enter updated replacement value"
              required
              className="w-full bg-jv-bg border border-jv-rule px-3 py-1.5 text-sm text-jv-ink font-interface outline-none focus:border-jv-ink"
            />
          </div>

          <div className="space-y-1">
            <label className="font-interface text-xs text-jv-muted">
              Reason for change (optional)
            </label>
            <input
              type="text"
              value={updateReason}
              onChange={(e) => setUpdateReason(e.target.value)}
              placeholder="e.g. Promotion, corrected title, renewed credential"
              className="w-full bg-jv-bg border border-jv-rule px-3 py-1.5 text-xs text-jv-ink font-interface outline-none focus:border-jv-ink"
            />
          </div>

          <div className="flex items-center justify-end gap-2 pt-1">
            <button
              type="button"
              onClick={() => {
                setIsEditing(false);
                setNewValue('');
                setUpdateReason('');
              }}
              className="px-2.5 py-1 text-xs font-interface border border-jv-rule hover:border-jv-ink text-jv-muted hover:text-jv-ink"
            >
              Cancel
            </button>
            <button
              type="submit"
              disabled={isSubmitting || !newValue.trim()}
              className="px-3 py-1 text-xs font-interface bg-jv-ink text-jv-bg font-medium hover:opacity-90 disabled:opacity-50"
            >
              {isSubmitting ? 'Updating...' : 'Save Update →'}
            </button>
          </div>
        </form>
      )}
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
  const supersedeFact = useSupersedeFact();

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

  const handleSupersede = async (factId: string, version: number, newValue: string, reason?: string) => {
    setConcurrencyNotice(null);
    try {
      const etag = `"fact:${factId}:v${version}"`;
      await supersedeFact.mutateAsync({
        factId,
        payload: {
          new_value: newValue,
          new_confidence: 1,
          reason: reason || 'Updated by user via Context Vault',
        },
        etag,
      });
    } catch (err: any) {
      if (err.name === 'PreconditionFailedError' || err.status === 412) {
        setConcurrencyNotice("This fact was modified elsewhere. We've loaded the latest version. Please review before updating.");
        refetch();
      } else {
        setConcurrencyNotice(err.message || 'Update failed');
      }
      throw err;
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
    <div className="space-y-16 font-interface text-jv-ink">
      {/* Concurrency Notification */}
      {concurrencyNotice && (
        <div className="p-4 border border-jv-rule-strong bg-jv-surface flex items-center gap-3 text-sm font-interface text-jv-ink">
          <AlertTriangle className="w-4 h-4 shrink-0 text-jv-ink" />
          <span>{concurrencyNotice}</span>
        </div>
      )}

      {/* Header */}
      <div className="border-b border-jv-rule pb-6 flex flex-col md:flex-row md:items-end justify-between gap-4">
        <div className="space-y-2">
          <div className="font-machine text-[11px] text-jv-muted uppercase tracking-widest">
            05 / FOLIO · PERSONAL INDEX
          </div>
          <h1 className="font-display text-4xl sm:text-5xl text-jv-ink tracking-tight">
            My Context
          </h1>
        </div>

        <TactileButton variant="primary" size="sm" onClick={() => setShowAddFact(!showAddFact)}>
          {showAddFact ? 'Cancel' : '+ Add Fact'}
        </TactileButton>
      </div>

      {/* Profile Readiness Dossier */}
      {jobReadiness && (
        <section className="p-6 sm:p-8 border border-jv-rule bg-jv-surface space-y-4">
          <div className="flex items-center justify-between">
            <span className="font-interface text-xs font-semibold uppercase tracking-wider text-jv-muted">
              Profile readiness · {jobReadiness.title}
            </span>
            <TextureBadge status={jobReadiness.is_ready ? 'complete' : 'waiting'} />
          </div>

          <div className="flex flex-col md:flex-row md:items-baseline gap-6">
            <div className="font-interface font-semibold text-4xl sm:text-5xl text-jv-ink tracking-tight">
              {Math.round(jobReadiness.completeness_ratio * 100)}%
            </div>
            <div className="font-interface text-xs text-jv-ink-soft space-y-1">
              <div>
                Satisfied: <span className="font-machine">{jobReadiness.satisfied_count}</span> · Missing: <span className="font-machine">{jobReadiness.missing_count}</span>
              </div>
              <div className="text-jv-muted">
                Authoritative evaluation against domain application requirements
              </div>
            </div>
          </div>

          {/* Missing Requirements List */}
          {(jobReadiness.missing?.length || 0) > 0 && (
            <div className="pt-3 border-t border-jv-rule space-y-2">
              <span className="font-interface text-xs text-jv-muted">
                Missing requirements:
              </span>
              <div className="grid grid-cols-1 sm:grid-cols-2 gap-2 font-interface text-xs">
                {jobReadiness.missing?.map((req) => (
                  <div key={req.requirement_id} className="flex items-center gap-2 text-jv-ink-soft">
                    <X className="w-3.5 h-3.5 text-jv-muted shrink-0" />
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
        <form onSubmit={handleRecord} className="p-6 border border-jv-rule bg-jv-surface space-y-4 animate-fadeIn">
          <div className="font-interface font-medium text-base text-jv-ink border-b border-jv-rule pb-2">
            Record context fact
          </div>

          <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
            <div className="space-y-1">
              <label className="font-interface text-xs text-jv-muted">Namespace</label>
              <select
                value={namespace}
                onChange={(e) => setNamespace(e.target.value)}
                className="w-full bg-jv-bg border border-jv-rule px-3 py-2 text-sm text-jv-ink font-interface outline-none focus:border-jv-ink"
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
              <label className="font-interface text-xs text-jv-muted">Fact key</label>
              <input
                type="text"
                value={key}
                onChange={(e) => setKey(e.target.value)}
                placeholder="e.g. legal_name, years_experience"
                required
                className="w-full bg-jv-bg border border-jv-rule px-3 py-2 text-sm text-jv-ink font-interface outline-none focus:border-jv-ink"
              />
            </div>

            <div className="space-y-1">
              <label className="font-interface text-xs text-jv-muted">Sensitivity</label>
              <select
                value={sensitivity}
                onChange={(e) => setSensitivity(e.target.value)}
                className="w-full bg-jv-bg border border-jv-rule px-3 py-2 text-sm text-jv-ink font-interface outline-none focus:border-jv-ink"
              >
                <option value="public">Public</option>
                <option value="personal">Personal</option>
                <option value="sensitive">Sensitive</option>
              </select>
            </div>
          </div>

          <div className="space-y-1">
            <label className="font-interface text-xs text-jv-muted">Fact value</label>
            <input
              type="text"
              value={value}
              onChange={(e) => setValue(e.target.value)}
              placeholder="e.g. John Doe, or salary target"
              required
              className="w-full bg-jv-bg border border-jv-rule px-3 py-2 text-sm text-jv-ink font-interface outline-none focus:border-jv-ink"
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
        <div className="py-24 text-center font-interface text-sm text-jv-muted">
          Indexing vault facts…
        </div>
      ) : Object.keys(groupedFacts).length === 0 ? (
        <div className="py-20 text-center space-y-4 max-w-md mx-auto">
          <div className="font-interface font-medium text-xl text-jv-ink tracking-tight">
            No context facts recorded yet
          </div>
          <p className="font-interface text-sm text-jv-ink-soft leading-relaxed">
            Your personal context vault is currently empty. Record verified identity, career,
            and preference items to ground all future agent proposals.
          </p>
        </div>
      ) : (
        <div className="space-y-12">
          {Object.entries(groupedFacts).map(([groupName, groupItems]) => (
            <section key={groupName} className="space-y-4">
              <div className="flex items-baseline justify-between border-b border-jv-rule pb-2">
                <h2 className="font-interface font-semibold text-xl capitalize text-jv-ink tracking-tight">
                  {groupName.toLowerCase()}
                </h2>
                <span className="font-machine text-xs text-jv-muted">
                  {groupItems.length} {groupItems.length === 1 ? 'fact' : 'facts'}
                </span>
              </div>

              <div className="divide-y divide-jv-rule border-b border-jv-rule">
                {groupItems.map((f) => (
                  <FactRow
                    key={f.fact_id}
                    fact={f}
                    onVerify={handleVerify}
                    onReject={handleReject}
                    onSupersede={handleSupersede}
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
