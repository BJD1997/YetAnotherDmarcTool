import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../api/client";
import { queryKeys } from "./queryKeys";

export type SignInProvider = "none" | "microsoft" | "google" | "oidc";

export interface SignInSettings {
  provider: SignInProvider;
  slug: string;
  login_link: string;
  // What to register at the organization's own OIDC provider.
  redirect_uri: string;
  entra_tenant_id: string | null;
  google_workspace_domain: string | null;
  oidc: { issuer: string; client_id: string; secret_set: boolean } | null;
  // Which providers this server offers.
  available: { microsoft: boolean; google: boolean; oidc: boolean };
  // Only the break-glass account may change the provider.
  can_change: boolean;
}

export interface ProviderIn {
  provider: Exclude<SignInProvider, "none">;
  // OIDC only; a left-out secret keeps the stored one (same issuer and client).
  issuer?: string;
  client_id?: string;
  client_secret?: string;
}

export function useSignInSettings() {
  return useQuery({
    queryKey: queryKeys.organization.signIn,
    queryFn: () => api.get<SignInSettings>("/organizations/current/sign-in"),
  });
}

export function useStartSignInTest() {
  return useMutation({
    mutationFn: (body: ProviderIn) => api.post<{ start_url: string }>("/organizations/current/sign-in/test", body),
  });
}

function useStoreSignIn() {
  const queryClient = useQueryClient();
  return (data: SignInSettings) => {
    queryClient.setQueryData(queryKeys.organization.signIn, data);
    void queryClient.invalidateQueries({ queryKey: queryKeys.organization.current });
    void queryClient.invalidateQueries({ queryKey: queryKeys.users.all });
  };
}

export function useSaveProvider() {
  const store = useStoreSignIn();
  return useMutation({
    mutationFn: (body: ProviderIn & { ticket: string }) => api.put<SignInSettings>("/organizations/current/sign-in", body),
    onSuccess: store,
  });
}

export function useRemoveProvider() {
  const store = useStoreSignIn();
  return useMutation({
    mutationFn: () => api.delete<SignInSettings>("/organizations/current/sign-in"),
    onSuccess: store,
  });
}
