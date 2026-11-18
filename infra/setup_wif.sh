#!/usr/bin/env bash
# One-time GCP setup so the dbt CI workflow can reach BigQuery through
# Workload Identity Federation (short-lived tokens, no stored keys).
#
# Run in Cloud Shell from a clone of this repo, or paste the file there:
#   bash infra/setup_wif.sh <github_repository_id>
# The numeric repository_id is printed by the failing "Check repository variables"
# step of the dbt CI workflow when the repository variables are not set yet.
#
# Idempotent: existing resources are kept and grants are only added when missing.
# Least privilege for the CI service account:
#   - project: BigQuery Job User (run queries only)
#   - housing_bronze: READER; ci_housing_silver / ci_housing_gold: WRITER
#   - production housing_silver / housing_gold: no access at all
set -euo pipefail

PROJECT_ID="taiwan-household"
LOCATION="US"
POOL_ID="github"
PROVIDER_ID="github-actions"
SA_NAME="dbt-ci"
CI_DATASETS=(ci_housing_silver ci_housing_gold)
REPO_ID="${1:-}"

if [[ ! "${REPO_ID}" =~ ^[0-9]+$ ]]; then
  echo "usage: bash infra/setup_wif.sh <github_repository_id>   (numeric id)" >&2
  exit 2
fi

gcloud config set project "${PROJECT_ID}" >/dev/null
PROJECT_NUMBER="$(gcloud projects describe "${PROJECT_ID}" --format='value(projectNumber)')"
SA_EMAIL="${SA_NAME}@${PROJECT_ID}.iam.gserviceaccount.com"
POOL_NAME="projects/${PROJECT_NUMBER}/locations/global/workloadIdentityPools/${POOL_ID}"

echo "== APIs"
gcloud services enable iam.googleapis.com iamcredentials.googleapis.com sts.googleapis.com \
  cloudresourcemanager.googleapis.com bigquery.googleapis.com

echo "== Workload identity pool and GitHub OIDC provider"
if ! gcloud iam workload-identity-pools describe "${POOL_ID}" --location=global >/dev/null 2>&1; then
  gcloud iam workload-identity-pools create "${POOL_ID}" --location=global \
    --display-name="GitHub Actions"
fi
if gcloud iam workload-identity-pools providers describe "${PROVIDER_ID}" --location=global \
  --workload-identity-pool="${POOL_ID}" >/dev/null 2>&1; then
  # Re-running with another id re-points the provider to that repository only.
  gcloud iam workload-identity-pools providers update-oidc "${PROVIDER_ID}" \
    --location=global --workload-identity-pool="${POOL_ID}" \
    --attribute-condition="assertion.repository_id == '${REPO_ID}'"
else
  gcloud iam workload-identity-pools providers create-oidc "${PROVIDER_ID}" \
    --location=global --workload-identity-pool="${POOL_ID}" \
    --display-name="taiwan-housing-pipeline" \
    --issuer-uri="https://token.actions.githubusercontent.com" \
    --attribute-mapping="google.subject=assertion.sub,attribute.repository=assertion.repository,attribute.repository_id=assertion.repository_id" \
    --attribute-condition="assertion.repository_id == '${REPO_ID}'"
fi

echo "== CI service account"
if ! gcloud iam service-accounts describe "${SA_EMAIL}" >/dev/null 2>&1; then
  gcloud iam service-accounts create "${SA_NAME}" --display-name="dbt CI (GitHub Actions)"
  sleep 10  # let the new account propagate before granting roles
fi
gcloud projects add-iam-policy-binding "${PROJECT_ID}" \
  --member="serviceAccount:${SA_EMAIL}" --role="roles/bigquery.jobUser" \
  --condition=None >/dev/null
gcloud iam service-accounts add-iam-policy-binding "${SA_EMAIL}" \
  --role="roles/iam.workloadIdentityUser" \
  --member="principalSet://iam.googleapis.com/${POOL_NAME}/attribute.repository_id/${REPO_ID}" \
  >/dev/null

echo "== CI datasets"
for dataset in "${CI_DATASETS[@]}"; do
  if ! bq show "${PROJECT_ID}:${dataset}" >/dev/null 2>&1; then
    bq --location="${LOCATION}" mk --dataset \
      --description="dbt CI output (views only); safe to delete" "${PROJECT_ID}:${dataset}"
  fi
done

# Add one dataset-level access entry (READER or WRITER) for the CI account if missing.
grant_dataset() {
  local dataset="$1" role="$2" tmp
  tmp="$(mktemp)"
  bq show --format=prettyjson "${PROJECT_ID}:${dataset}" >"${tmp}"
  if jq -e --arg sa "${SA_EMAIL}" --arg role "${role}" \
    '.access | any(.userByEmail == $sa and .role == $role)' "${tmp}" >/dev/null; then
    echo "${dataset}: ${role} already granted"
  else
    jq --arg sa "${SA_EMAIL}" --arg role "${role}" \
      '.access += [{"role": $role, "userByEmail": $sa}]' "${tmp}" >"${tmp}.new"
    bq update --source "${tmp}.new" "${PROJECT_ID}:${dataset}" >/dev/null
    echo "${dataset}: ${role} granted"
  fi
  rm -f "${tmp}" "${tmp}.new"
}

echo "== Dataset access"
grant_dataset housing_bronze READER
grant_dataset ci_housing_silver WRITER
grant_dataset ci_housing_gold WRITER

echo
echo "== Audit: everything the CI account can do (expect only the lines below)"
echo "   project: roles/bigquery.jobUser | housing_bronze: READER | ci_*: WRITER"
gcloud projects get-iam-policy "${PROJECT_ID}" --flatten="bindings[].members" \
  --filter="bindings.members:serviceAccount:${SA_EMAIL}" --format="value(bindings.role)" |
  sed 's/^/project: /'
for dataset in housing_bronze housing_silver housing_gold "${CI_DATASETS[@]}"; do
  bq show --format=prettyjson "${PROJECT_ID}:${dataset}" |
    jq -r --arg sa "${SA_EMAIL}" --arg ds "${dataset}" \
      '.access[] | select(.userByEmail == $sa) | "\($ds): \(.role)"'
done

echo
echo "== Set these GitHub repository variables (Settings > Secrets and variables > Actions > Variables)"
echo "GCP_WIF_PROVIDER=${POOL_NAME}/providers/${PROVIDER_ID}"
echo "GCP_CI_SERVICE_ACCOUNT=${SA_EMAIL}"
