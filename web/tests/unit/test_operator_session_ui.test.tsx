import React from 'react';
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { OperatorSessionControl } from '../../src/components/ui/OperatorSessionControl';
import * as useCaseworkerHooks from '../../src/hooks/useCaseworker';

describe('OperatorSessionControl Component (Phase 5.3.3.1)', () => {
  let queryClient: QueryClient;

  beforeEach(() => {
    queryClient = new QueryClient({
      defaultOptions: {
        queries: { retry: false },
      },
    });
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  const renderComponent = () => {
    return render(
      <QueryClientProvider client={queryClient}>
        <OperatorSessionControl />
      </QueryClientProvider>
    );
  };

  it('renders unauthenticated trigger button when no operator session exists', () => {
    vi.spyOn(useCaseworkerHooks, 'useGoogleAuthStatus').mockReturnValue({
      data: undefined,
      isError: true,
      isLoading: false,
      refetch: vi.fn(),
    } as any);

    renderComponent();

    expect(screen.getByRole('button', { name: /operator session/i })).toBeInTheDocument();
    expect(screen.queryByText(/operator active/i)).not.toBeInTheDocument();
  });

  it('opens bootstrap modal with password input when trigger button is clicked', () => {
    vi.spyOn(useCaseworkerHooks, 'useGoogleAuthStatus').mockReturnValue({
      data: undefined,
      isError: true,
      isLoading: false,
      refetch: vi.fn(),
    } as any);

    renderComponent();

    const trigger = screen.getByRole('button', { name: /operator session/i });
    fireEvent.click(trigger);

    expect(screen.getByRole('dialog')).toBeInTheDocument();
    expect(screen.getByText(/operator session bootstrap/i)).toBeInTheDocument();
    const input = screen.getByLabelText(/signed operator token/i);
    expect(input).toHaveAttribute('type', 'password');
    expect(screen.getByText(/credentials are kept out of urls, localstorage/i)).toBeInTheDocument();
  });

  it('submits operator token to mutation and handles success', async () => {
    const mutateAsyncMock = vi.fn().mockResolvedValue({ success: true, operator_id: 'operator-1' });
    vi.spyOn(useCaseworkerHooks, 'useGoogleAuthStatus').mockReturnValue({
      data: undefined,
      isError: true,
      isLoading: false,
      refetch: vi.fn(),
    } as any);
    vi.spyOn(useCaseworkerHooks, 'useCreateOperatorSession').mockReturnValue({
      mutateAsync: mutateAsyncMock,
      isPending: false,
    } as any);

    renderComponent();

    fireEvent.click(screen.getByRole('button', { name: /operator session/i }));

    const input = screen.getByLabelText(/signed operator token/i);
    fireEvent.change(input, { target: { value: 'op.operator-1.12345.abcdef' } });

    const submitBtn = screen.getByRole('button', { name: /authenticate session/i });
    fireEvent.click(submitBtn);

    await waitFor(() => {
      expect(mutateAsyncMock).toHaveBeenCalledWith({
        operator_token: 'op.operator-1.12345.abcdef',
      });
      expect(screen.getByText(/session established for operator 'operator-1'/i)).toBeInTheDocument();
    });
  });

  it('renders authenticated badge and sign out when session is active', () => {
    vi.spyOn(useCaseworkerHooks, 'useGoogleAuthStatus').mockReturnValue({
      data: { connected: false, authenticated_email: null, scopes: [], expires_at: null },
      isError: false,
      isLoading: false,
      refetch: vi.fn(),
    } as any);

    renderComponent();

    expect(screen.getByText(/operator active/i)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /sign out/i })).toBeInTheDocument();
  });
});
