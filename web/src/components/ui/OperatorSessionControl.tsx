import React, { useState } from 'react';
import { useCreateOperatorSession, useDeleteOperatorSession, useGoogleAuthStatus } from '../../hooks/useCaseworker';
import { TactileButton } from './TactileButton';

export const OperatorSessionControl: React.FC = () => {
  const { data: googleAuth, isError, isLoading, refetch } = useGoogleAuthStatus();
  const createOperatorSession = useCreateOperatorSession();
  const deleteOperatorSession = useDeleteOperatorSession();

  const [isOpen, setIsOpen] = useState(false);
  const [tokenInput, setTokenInput] = useState('');
  const [errorMsg, setErrorMsg] = useState<string | null>(null);
  const [successMsg, setSuccessMsg] = useState<string | null>(null);

  // Authenticated if googleAuth endpoint returned 200 (even if Google itself is not connected)
  const isAuthenticated = !isError && !isLoading && googleAuth !== undefined;

  const handleOpen = () => {
    setErrorMsg(null);
    setSuccessMsg(null);
    setTokenInput('');
    setIsOpen(true);
  };

  const handleClose = () => {
    setTokenInput('');
    setErrorMsg(null);
    setSuccessMsg(null);
    setIsOpen(false);
  };

  const handleAuthenticate = async (e: React.FormEvent) => {
    e.preventDefault();
    setErrorMsg(null);
    setSuccessMsg(null);

    const cleanToken = tokenInput.trim();
    if (!cleanToken) {
      setErrorMsg('Please enter a signed operator token.');
      return;
    }

    try {
      const res = await createOperatorSession.mutateAsync({ operator_token: cleanToken });
      setTokenInput(''); // Immediately clear credentials from memory
      setSuccessMsg(`Session established for operator '${res.operator_id}'.`);
      await refetch();
      setTimeout(() => {
        setIsOpen(false);
        setSuccessMsg(null);
      }, 1200);
    } catch (err: unknown) {
      setErrorMsg(err instanceof Error ? err.message : 'Failed to establish operator session.');
    }
  };

  const handleLogout = async () => {
    try {
      await deleteOperatorSession.mutateAsync();
      await refetch();
    } catch (err: unknown) {
      console.error('Logout error:', err);
    }
  };

  return (
    <div className="relative inline-flex items-center">
      {isAuthenticated ? (
        <div className="flex items-center gap-2 px-2 py-0.5 border border-emerald-500/40 bg-emerald-950/20 text-emerald-400 text-[10px] sm:text-[11px] font-machine">
          <span className="w-1.5 h-1.5 rounded-full bg-emerald-400 inline-block animate-pulse" />
          <span className="hidden sm:inline">OPERATOR ACTIVE</span>
          <button
            type="button"
            onClick={handleLogout}
            disabled={deleteOperatorSession.isPending}
            className="ml-1 text-jv-muted hover:text-jv-ink underline cursor-pointer"
            title="Terminate operator session"
          >
            {deleteOperatorSession.isPending ? '…' : 'Sign out'}
          </button>
        </div>
      ) : (
        <button
          type="button"
          onClick={handleOpen}
          className="flex items-center gap-1.5 px-2 py-0.5 border border-jv-rule hover:border-jv-ink text-jv-ink text-[10px] sm:text-[11px] font-machine uppercase bg-jv-surface transition-colors cursor-pointer"
        >
          <span className="w-1.5 h-1.5 rounded-full bg-amber-400 inline-block" />
          <span>Operator Session</span>
        </button>
      )}

      {/* Modal Dialog */}
      {isOpen && (
        <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/60 backdrop-blur-sm">
          <div
            role="dialog"
            aria-modal="true"
            aria-labelledby="modal-title"
            className="w-full max-w-md border border-jv-rule bg-jv-bg p-6 text-jv-ink shadow-2xl relative"
          >
            <div className="flex items-center justify-between border-b border-jv-rule pb-3 mb-4">
              <h3 id="modal-title" className="font-machine text-xs uppercase tracking-widest text-jv-ink font-bold flex items-center gap-2">
                <span className="w-2 h-2 bg-jv-ink inline-block" />
                Operator Session Bootstrap
              </h3>
              <button
                type="button"
                onClick={handleClose}
                className="text-jv-muted hover:text-jv-ink text-sm font-machine cursor-pointer"
                aria-label="Close dialog"
              >
                ✕
              </button>
            </div>

            <p className="text-xs text-jv-muted mb-4 font-interface leading-relaxed">
              Authenticate this browser session via <code className="bg-jv-surface px-1 py-0.5 text-jv-ink">POST /api/v1/auth/operator/session</code>.
              Sets an HTTP-only secure session cookie at <code className="bg-jv-surface px-1 py-0.5 text-jv-ink">https://localhost:3443</code>.
            </p>

            <form onSubmit={handleAuthenticate} className="space-y-4">
              <div>
                <label htmlFor="operator-token-input" className="block text-[11px] font-machine uppercase tracking-wider text-jv-muted mb-1.5">
                  Signed Operator Token
                </label>
                <input
                  id="operator-token-input"
                  type="password"
                  autoComplete="off"
                  spellCheck="false"
                  value={tokenInput}
                  onChange={(e) => setTokenInput(e.target.value)}
                  placeholder="op.<operator_id>.<expires>.<signature>"
                  className="w-full px-3 py-2 bg-jv-surface border border-jv-rule focus:border-jv-ink focus:outline-none text-xs font-mono text-jv-ink"
                  disabled={createOperatorSession.isPending}
                />
                <span className="block mt-1 text-[10px] text-jv-muted font-machine">
                  Credentials are kept out of URLs, localStorage, and browser logs.
                </span>
              </div>

              {errorMsg && (
                <div className="p-2 border border-rose-500/40 bg-rose-950/20 text-rose-400 text-xs font-machine">
                  {errorMsg}
                </div>
              )}

              {successMsg && (
                <div className="p-2 border border-emerald-500/40 bg-emerald-950/20 text-emerald-400 text-xs font-machine">
                  {successMsg}
                </div>
              )}

              <div className="flex items-center justify-end gap-2 pt-2 border-t border-jv-rule">
                <TactileButton
                  type="button"
                  variant="outline"
                  size="sm"
                  onClick={handleClose}
                  disabled={createOperatorSession.isPending}
                >
                  Cancel
                </TactileButton>
                <TactileButton
                  type="submit"
                  variant="primary"
                  size="sm"
                  loading={createOperatorSession.isPending}
                  disabled={!tokenInput.trim() || createOperatorSession.isPending}
                >
                  Authenticate Session
                </TactileButton>
              </div>
            </form>
          </div>
        </div>
      )}
    </div>
  );
};
