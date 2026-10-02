// deploy/azure/modules/apps.bicep
// The api and worker container apps. Each runs the DNSSEC-validating Unbound
// resolver as a sidecar (ACA has no UDP ingress, so DNS_RESOLVER_HOST=127.0.0.1
// over the shared localhost). Secrets come from Key Vault via the user-assigned
// identity. main.bicep makes this module depend on the migrate deploymentScript,
// so the schema + dmarc_app role exist before these start.

@description('Azure region.')
param location string

@description('Address range of the Container Apps environment subnet — trusted, with ACA ingress, to set X-Forwarded-For.')
param acaSubnetPrefix string
param namePrefix string
param environmentId string
@description('Managed environment default domain (to compute the api FQDN without a self-reference).')
param envDefaultDomain string
param identityId string
param vaultUri string

param appImage string
param resolverImage string

@description('Override the api public URL (e.g. a custom domain). Empty = use the ACA FQDN.')
param publicBaseUrlOverride string = ''

@description('Tier-resolved by the caller (main.bicep) from deploymentSize.')
param apiMinReplicas int
param apiMaxReplicas int
param workerMinReplicas int
param workerMaxReplicas int
@description('Target pending-job count per worker replica for the KEDA Postgres scaler.')
param workerQueueTarget string = '5'
param apiConcurrentRequests string = '50'

// Optional Entra (Azure AD). Client IDs are plain; secrets come from Key Vault
// and are only wired when the corresponding secret was created (see main.bicep).
param entraSsoClientId string = ''
#disable-next-line no-hardcoded-env-urls
param entraSsoAuthority string = 'https://login.microsoftonline.com/organizations'
param deployEntraSsoSecret bool = false
param entraMailClientId string = ''
param deployEntraMailSecret bool = false

// Optional hosted reporting mailbox (api issues the addresses, worker polls
// the mailbox) and Cloudflare (api creates/removes the DNS authorization
// records — the token goes to the api only).
param hostedReportsTenantId string = ''
param hostedReportsMailboxAddress string = ''
param cloudflareZoneId string = ''
param deployCloudflareSecret bool = false

// In-app updates: the api's second identity may only start the updater job
// (see updater.bicep); the job's resource id is fixed by naming.
param triggerIdentityId string
param triggerClientId string
@description('Show "Run test update" in the admin console.')
param enableTestUpdate bool = false
@description('Run the STARTTLS check (needs outbound port 25).')
param enableStarttlsCheck bool = false

var starttlsEnv = [
  {
    name: 'STARTTLS_CHECK_ENABLED'
    value: enableStarttlsCheck ? 'true' : 'false'
  }
]

var hostedReportsEnv = empty(hostedReportsMailboxAddress) ? [] : [
  {
    name: 'HOSTED_REPORTS_TENANT_ID'
    value: hostedReportsTenantId
  }
  {
    name: 'HOSTED_REPORTS_MAILBOX_ADDRESS'
    value: hostedReportsMailboxAddress
  }
]

var apiFqdn = '${namePrefix}-api.${envDefaultDomain}'
var publicBaseUrl = empty(publicBaseUrlOverride) ? 'https://${apiFqdn}' : publicBaseUrlOverride

// resolver sidecar shared by both apps.
var resolverContainer = {
  name: 'resolver'
  image: resolverImage
  resources: {
    cpu: json('0.25')
    memory: '0.5Gi'
  }
}

// --- api ---

var apiSecrets = concat(
  [
    {
      name: 'app-database-url'
      keyVaultUrl: '${vaultUri}secrets/app-database-url'
      identity: identityId
    }
    {
      name: 'fernet-key'
      keyVaultUrl: '${vaultUri}secrets/fernet-key'
      identity: identityId
    }
  ],
  deployEntraSsoSecret ? [
    {
      name: 'entra-sso-client-secret'
      keyVaultUrl: '${vaultUri}secrets/entra-sso-client-secret'
      identity: identityId
    }
  ] : [],
  deployCloudflareSecret ? [
    {
      name: 'cloudflare-api-token'
      keyVaultUrl: '${vaultUri}secrets/cloudflare-api-token'
      identity: identityId
    }
  ] : []
)

var apiEnv = concat(
  [
    {
      name: 'DATABASE_URL'
      secretRef: 'app-database-url'
    }
    {
      name: 'FERNET_KEY'
      secretRef: 'fernet-key'
    }
    {
      name: 'PUBLIC_BASE_URL'
      value: publicBaseUrl
    }
    {
      name: 'DNS_RESOLVER_HOST'
      value: '127.0.0.1'
    }
    {
      name: 'RATE_LIMIT_BACKEND'
      value: 'postgres'
    }
    {
      // Every request reaches the api through ACA's ingress proxy (peer
      // addresses in 100.100.0.0/16, or the environment's own subnet), which
      // puts the real client in X-Forwarded-For. uvicorn only honours that
      // header from peers listed here — without it every visitor looks like
      // the proxy, in the sign-in log and to the per-IP rate limits.
      name: 'FORWARDED_ALLOW_IPS'
      value: '100.100.0.0/16,${acaSubnetPrefix}'
    }
    {
      // No updater sidecar on ACA: "Update now" starts the updater job
      // instead (updater.bicep); the redeploy command stays the fallback.
      name: 'DEPLOYMENT_PLATFORM'
      value: 'azure-container-apps'
    }
    {
      name: 'AZURE_RESOURCE_GROUP'
      value: resourceGroup().name
    }
    {
      name: 'AZURE_UPDATER_JOB_ID'
      value: resourceId('Microsoft.App/jobs', '${namePrefix}-updater')
    }
    {
      name: 'AZURE_UPDATE_CLIENT_ID'
      value: triggerClientId
    }
    {
      name: 'UPDATE_REHEARSAL_ENABLED'
      value: enableTestUpdate ? 'true' : 'false'
    }
  ],
  deployEntraSsoSecret ? [
    {
      name: 'ENTRA_SSO_CLIENT_ID'
      value: entraSsoClientId
    }
    {
      name: 'ENTRA_SSO_CLIENT_SECRET'
      secretRef: 'entra-sso-client-secret'
    }
    {
      name: 'ENTRA_SSO_AUTHORITY'
      value: entraSsoAuthority
    }
  ] : [],
  // The api only needs the mail app's client ID (for the admin-consent
  // links in onboarding); the secret stays with the worker, which does the
  // mailbox syncs.
  deployEntraMailSecret ? [
    {
      name: 'ENTRA_MAIL_CLIENT_ID'
      value: entraMailClientId
    }
  ] : [],
  hostedReportsEnv,
  starttlsEnv,
  deployCloudflareSecret ? [
    {
      name: 'CLOUDFLARE_API_TOKEN'
      secretRef: 'cloudflare-api-token'
    }
    {
      name: 'CLOUDFLARE_ZONE_ID'
      value: cloudflareZoneId
    }
  ] : []
)

resource apiApp 'Microsoft.App/containerApps@2024-03-01' = {
  name: '${namePrefix}-api'
  location: location
  identity: {
    type: 'UserAssigned'
    userAssignedIdentities: {
      '${identityId}': {}
      '${triggerIdentityId}': {}
    }
  }
  properties: {
    environmentId: environmentId
    workloadProfileName: 'Consumption'
    configuration: {
      activeRevisionsMode: 'Single'
      ingress: {
        external: true
        targetPort: 8000
        transport: 'auto'
        allowInsecure: false
        traffic: [
          {
            latestRevision: true
            weight: 100
          }
        ]
      }
      secrets: apiSecrets
    }
    template: {
      containers: [
        {
          name: 'api'
          image: appImage
          resources: {
            cpu: json('0.5')
            memory: '1Gi'
          }
          env: apiEnv
          probes: [
            {
              type: 'Liveness'
              httpGet: {
                path: '/api/health'
                port: 8000
              }
              initialDelaySeconds: 10
              periodSeconds: 30
            }
          ]
        }
        resolverContainer
      ]
      scale: {
        minReplicas: apiMinReplicas
        maxReplicas: apiMaxReplicas
        rules: [
          {
            name: 'http-concurrency'
            http: {
              metadata: {
                concurrentRequests: apiConcurrentRequests
              }
            }
          }
        ]
      }
    }
  }
}

// --- worker ---

var workerSecrets = concat(
  [
    {
      name: 'app-database-url'
      keyVaultUrl: '${vaultUri}secrets/app-database-url'
      identity: identityId
    }
    {
      name: 'fernet-key'
      keyVaultUrl: '${vaultUri}secrets/fernet-key'
      identity: identityId
    }
    {
      name: 'keda-pg-connection'
      keyVaultUrl: '${vaultUri}secrets/keda-pg-connection'
      identity: identityId
    }
  ],
  deployEntraMailSecret ? [
    {
      name: 'entra-mail-client-secret'
      keyVaultUrl: '${vaultUri}secrets/entra-mail-client-secret'
      identity: identityId
    }
  ] : []
)

var workerEnv = concat(
  [
    {
      name: 'DATABASE_URL'
      secretRef: 'app-database-url'
    }
    {
      name: 'FERNET_KEY'
      secretRef: 'fernet-key'
    }
    {
      name: 'DNS_RESOLVER_HOST'
      value: '127.0.0.1'
    }
    {
      name: 'RATE_LIMIT_BACKEND'
      value: 'postgres'
    }
  ],
  deployEntraMailSecret ? [
    {
      name: 'ENTRA_MAIL_CLIENT_ID'
      value: entraMailClientId
    }
    {
      name: 'ENTRA_MAIL_CLIENT_SECRET'
      secretRef: 'entra-mail-client-secret'
    }
  ] : [],
  hostedReportsEnv,
  starttlsEnv
)

resource workerApp 'Microsoft.App/containerApps@2024-03-01' = {
  name: '${namePrefix}-worker'
  location: location
  identity: {
    type: 'UserAssigned'
    userAssignedIdentities: {
      '${identityId}': {}
    }
  }
  properties: {
    environmentId: environmentId
    workloadProfileName: 'Consumption'
    configuration: {
      activeRevisionsMode: 'Single'
      // no ingress — background worker
      secrets: workerSecrets
    }
    template: {
      containers: [
        {
          name: 'worker'
          image: appImage
          command: [
            'python'
            '-m'
            'app.workers.scheduler'
          ]
          resources: {
            cpu: json('0.5')
            memory: '1Gi'
          }
          env: workerEnv
          probes: [
            {
              type: 'Liveness'
              httpGet: {
                path: '/health'
                port: 8080
              }
              initialDelaySeconds: 15
              periodSeconds: 30
            }
            {
              type: 'Startup'
              httpGet: {
                path: '/health'
                port: 8080
              }
              initialDelaySeconds: 5
              periodSeconds: 5
              failureThreshold: 30
            }
          ]
        }
        resolverContainer
      ]
      scale: {
        // min replicas is tier-resolved but always >= 1 so a leader always exists.
        minReplicas: workerMinReplicas
        maxReplicas: workerMaxReplicas
        rules: [
          {
            name: 'pg-queue-depth'
            custom: {
              type: 'postgresql'
              metadata: {
                query: 'SELECT count(*) FROM background_jobs WHERE status = \'pending\' AND run_after <= now()'
                targetQueryValue: workerQueueTarget
              }
              auth: [
                {
                  secretRef: 'keda-pg-connection'
                  triggerParameter: 'connection'
                }
              ]
            }
          }
        ]
      }
    }
  }
}

output apiFqdn string = apiApp.properties.configuration.ingress.fqdn
output apiUrl string = 'https://${apiApp.properties.configuration.ingress.fqdn}'
