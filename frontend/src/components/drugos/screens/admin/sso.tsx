'use client';

import { EmptyState } from '../../use-api-data';
import { DemoDataBanner } from '@/components/ui/DemoDataBanner';
import { FadeIn, PageHeader } from '../_remaining-shared';

// ═══════════════════════════════════════════
// 16. SSO SCREEN
// ═══════════════════════════════════════════
/**
 * FE-009 ROOT FIX (Team Member 15, v108): The previous SSOScreen
 * rendered 3 fabricated SSO providers ("Okta SAML 2.0 active 18
 * users", "Azure AD OIDC active 8 users", "Google Workspace OIDC
 * inactive") and a fabricated SCIM endpoint
 * "https://api.drugos.com/scim/v2" with a fabricated bearer token
 * "sk-drugos-scim-xxxx" rendered as a `defaultValue` in a password
 * input. No API call. No banner. An admin believed Okta and Azure
 * AD were configured and syncing. The fake SCIM token was readable
 * via DevTools — if a real token had ever been placed there, it
 * would leak.
 *
 * ROOT FIX: SSO/SCIM is not implemented anywhere in the codebase.
 * Per the issue spec we render an honest EmptyState. We NEVER
 * render real or fake bearer tokens in the DOM. The screen tells
 * the admin honestly that SSO is not configured and points them
 * at support to enable it.
 */
export function SSOScreen() {
  // Issue 311 (audit 301-320): There is no /api/auth/sso endpoint, no
  // SAML/OIDC provider integration, and no SCIM user-provisioning endpoint.
  // The DemoDataBanner makes it 100% visible that this screen is non-
  // functional — a user cannot mistake the EmptyState for a working SSO
  // config surface.
  return (
    <FadeIn>
      <div className="space-y-6">
        <PageHeader title="Single Sign-On (SSO)" desc="Configure SAML or OIDC identity provider" />
        <DemoDataBanner
          reason="SSO provider configuration is not implemented in this deployment. There is no /api/auth/sso endpoint, no SAML/OIDC integration, and no SCIM user-provisioning endpoint. Any SSO configuration shown below would be fabricated."
        />
        <EmptyState
          title="SSO is not configured"
          description="SSO/SCIM is not implemented in this deployment. There is no /api/auth/sso endpoint, no SAML/OIDC provider integration, and no SCIM user-provisioning endpoint. Contact support to enable SAML or OIDC for your organization. No provider configuration, user counts, or bearer tokens are shown because none exist."
        />
      </div>
    </FadeIn>
  );
}
