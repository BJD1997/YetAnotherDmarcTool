import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../api/client";
import { queryKeys } from "./queryKeys";

export type SignInProvider = "none" | "microsoft" | "google" | "oidc";

export interface SignInSettings {
  provider: SignInProvider;
  slug: string;
  login_link: string;
  // What to register at the organization's own provider.
  redirect_uri: string;
  google_workspace_domain: string | null;
  oidc: { issuer: string; client_id: string; secret_set: boolean } | null;
}

export interface OidcSettingsIn {
  issuer: string;
  client_id: string;
  // Left out: keep the stored secret (same issuer and client only).
  client_secret?: string;
}

export function useSignInSettings() {
  return useQuery({
    queryKey: queryKeys.organization.signIn,
    queryFn: () => api.get<SignInSettings>("/organizations/current/sign-in"),
  });
}

export function useStartOidcTest() {
  return useMutation({
    mutationFn: (body: OidcSettingsIn) => api.post<{ start_url: string }>("/organizations/current/sign-in/oidc/test", body),
  });
}

function useStoreSignIn() {
  const queryClient = useQueryClient();
  return (data: SignInSettings) => {
    queryClient.setQueryData(queryKeys.organization.signIn, data);
    void queryClient.invalidateQueries({ queryKey: queryKeys.organization.current });
  };
}

export function useSaveOidc() {
  const store = useStoreSignIn();
  return useMutation({
    mutationFn: (body: OidcSettingsIn & { ticket: string }) =>
      api.put<SignInSettings>("/organizations/current/sign-in/oidc", body),
    onSuccess: store,
  });
}

export function useRemoveOidc() {
  const store = useStoreSignIn();
  return useMutation({
    mutationFn: () => api.delete<SignInSettings>("/organizations/current/sign-in/oidc"),
    onSuccess: store,
  });
}
