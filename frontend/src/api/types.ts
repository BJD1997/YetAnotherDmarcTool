export interface CurrentUser {
  id: string;
  email: string;
  display_name: string | null;
  role: "org_admin" | "member";
  organization_id: string;
}

export interface EntraConsentUrls {
  mail_access_consent_url: string;
  sso_consent_url: string;
}

export interface Organization {
  id: string;
  name: string;
  status: "pending_setup" | "active" | "suspended";
  entra_tenant_id: string | null;
  is_operator: boolean;
  entra_consent_urls: EntraConsentUrls | null;
}

export interface Domain {
  id: string;
  name: string;
  parent_domain_id: string | null;
  notes: string | null;
  is_active: boolean;
  created_at: string;
  verification_status: "pending" | "verified";
  verified_at: string | null;
  verification_token: string;
  verification_record_name: string;
}

export interface VerifyDomainResponse {
  verified: boolean;
  domain: Domain;
}

export interface DetectedDomain {
  name: string;
  report_count: number;
  message_volume: number;
  relationship: "apex" | "subdomain_of_registered" | "subdomain_of_detected";
  suggested_parent_id: string | null;
  suggested_parent_name: string | null;
}

export interface AdminMe {
  id: string;
  email: string;
  auth_type: "local" | "operator_org";
}

export interface TeamMember {
  id: string;
  email: string;
  display_name: string | null;
  role: "org_admin" | "member";
  status: "active" | "disabled";
  last_login_at: string | null;
}
