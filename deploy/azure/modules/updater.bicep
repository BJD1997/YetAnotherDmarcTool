// In-app updates ("Update now" in the admin console) — Azure's counterpart of
// the Docker Compose updater sidecar, which needs a Docker socket ACA doesn't
// have. Same split of trust:
//
// - The api's identity (triggerPrincipalId) may only START this job — no
//   overrides, so it can't change what the job runs.
// - The job reads the requested release back from the api
//   (GET /api/update-request), refuses anything but a real, newer release
//   tag, then updates the migrate job, worker, api and itself through Azure
//   Resource Manager (updater/azure_update.py) with its own identity, whose
//   custom role covers only container apps and jobs in this resource group:
//   read, update, start and read executions. No delete, no exec into
//   containers, no assigning identities.

param location string
param namePrefix string
param environmentId string
@description('Updater image (same tag as the app).')
param updaterImage string
@description('Comma-separated image repositories the updater may move to a new tag.')
param imageRepos string
@description('The api\'s base URL, for its version and the requested release.')
param apiUrl string
@description('Principal id of the api identity allowed to start this job.')
param triggerPrincipalId string

var jobName = '${namePrefix}-updater'

resource updaterIdentity 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' = {
  name: '${namePrefix}-updater-id'
  location: location
}

// Custom role names are unique per tenant, hence the resource group in them.
resource updaterRole 'Microsoft.Authorization/roleDefinitions@2022-04-01' = {
  name: guid(resourceGroup().id, 'yadt-updater-role')
  properties: {
    roleName: 'YetAnotherDmarcTool updater (${resourceGroup().name})'
    description: 'Moves this deployment\'s container apps and jobs to a new release image and runs its migrate job.'
    type: 'CustomRole'
    assignableScopes: [resourceGroup().id]
    permissions: [
      {
        actions: [
          'Microsoft.App/containerApps/read'
          'Microsoft.App/containerApps/write'
          'Microsoft.App/containerApps/*/read'
          'Microsoft.App/jobs/read'
          'Microsoft.App/jobs/write'
          'Microsoft.App/jobs/*/read'
          'Microsoft.App/jobs/start/action'
          'Microsoft.App/managedEnvironments/read'
          'Microsoft.App/managedEnvironments/join/action'
        ]
        notActions: []
      }
    ]
  }
}

resource updaterRoleAssignment 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(resourceGroup().id, updaterIdentity.id, updaterRole.id)
  properties: {
    roleDefinitionId: updaterRole.id
    principalId: updaterIdentity.properties.principalId
    principalType: 'ServicePrincipal'
  }
}

resource updaterJob 'Microsoft.App/jobs@2024-03-01' = {
  name: jobName
  location: location
  identity: {
    type: 'UserAssigned'
    userAssignedIdentities: {
      '${updaterIdentity.id}': {}
    }
  }
  properties: {
    environmentId: environmentId
    workloadProfileName: 'Consumption'
    configuration: {
      triggerType: 'Manual'
      // Migrations can take a while on a big database; the job waits for them.
      replicaTimeout: 3600
      // A failed update is reported, not retried behind the admin's back.
      replicaRetryLimit: 0
      manualTriggerConfig: {
        parallelism: 1
        replicaCompletionCount: 1
      }
    }
    template: {
      containers: [
        {
          name: 'updater'
          image: updaterImage
          command: ['python3', '/azure_update.py']
          resources: {
            cpu: json('0.25')
            memory: '0.5Gi'
          }
          env: [
            { name: 'APP_HEALTH_URL', value: '${apiUrl}/api/health' }
            { name: 'UPDATE_REQUEST_URL', value: '${apiUrl}/api/update-request' }
            { name: 'IMAGE_REPOS', value: imageRepos }
            { name: 'API_APP', value: '${namePrefix}-api' }
            { name: 'WORKER_APP', value: '${namePrefix}-worker' }
            { name: 'MIGRATE_JOB', value: '${namePrefix}-migrate' }
            { name: 'UPDATER_JOB', value: jobName }
            { name: 'SUBSCRIPTION_ID', value: subscription().subscriptionId }
            { name: 'RESOURCE_GROUP', value: resourceGroup().name }
            { name: 'AZURE_CLIENT_ID', value: updaterIdentity.properties.clientId }
          ]
        }
      ]
    }
  }
  dependsOn: [updaterRoleAssignment]
}

// The api may start this one job, and nothing else.
resource triggerRole 'Microsoft.Authorization/roleDefinitions@2022-04-01' = {
  name: guid(resourceGroup().id, 'yadt-update-trigger-role')
  properties: {
    roleName: 'YetAnotherDmarcTool update trigger (${resourceGroup().name})'
    description: 'Starts the updater job.'
    type: 'CustomRole'
    assignableScopes: [resourceGroup().id]
    permissions: [
      {
        actions: ['Microsoft.App/jobs/start/action']
        notActions: []
      }
    ]
  }
}

resource triggerRoleAssignment 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(updaterJob.id, triggerPrincipalId, triggerRole.id)
  scope: updaterJob
  properties: {
    roleDefinitionId: triggerRole.id
    principalId: triggerPrincipalId
    principalType: 'ServicePrincipal'
  }
}

output updaterJobId string = updaterJob.id
