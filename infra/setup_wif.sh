#!/usr/bin/env bash
# One-time GCP setup so GitHub Actions can reach BigQuery through
# Workload Identity Federation (short-lived tokens, no stored keys).
#
# Run in Cloud Shell from a clone of this repo, or paste the file there:
#   bash infra/setup_wif.sh <github_repository_id>
# The numeric repository_id is printed by the failing "Check repository variables"
# step of the dbt CI / Dashboard workflow when the repository variables are not set yet.
#
# Idempotent: existing resources are kept and grants are only added when missing.
# Least privilege:
#   dbt-ci (dbt CI workflow, any branch / PR of this repository)
#     - project: BigQuery Job User (run queries only)
#     - housing_bronze: READER; ci_housing_silver / ci_housing_gold: WRITER
#     - production housing_silver / housing_gold: no access at all
#   dashboard-reader (Dashboard workflow, main branch of this repository only)
#     - project: BigQuery Job User
#     - housing_gold: READER; nothing else
set -euo pipefail

PROJECT_ID="taiwan-household"
LOCATION="US"
POOL_ID="github"
PROVIDER_ID="github-actions"
CI_SA_NAME="dbt-ci"
DASHBOARD_SA_NAME="dashboard-reader"
CI_DATASETS=(ci_housing_silver ci_housing_gold)
REPO_ID="${1:-}"
# repo_ref = "<repository_id>:<git ref>", so a binding can be limited to one branch.
ATTRIBUTE_MAPPING="google.subject=assertion.sub,attribute.repository=assertion.repository,attribute.repository_id=assertion.repository_id,attribute.repo_ref=assertion.repository_id + ':' + assertion.ref"

if [[ ! "${REPO_ID}" =~ ^[0-9]+$ ]]; then
  echo "usage: bash infra/setup_wif.sh <github_repository_id>   (numeric id)" >&2
  exit 2
fi

gcloud config set project "${PROJECT_ID}" >/dev/null
PROJECT_NUMBER="$(gcloud projects describe "${PROJECT_ID}" --format='value(projectNumber)')"
CI_SA_EMAIL="${CI_SA_NAME}@${PROJECT_ID}.iam.gserviceaccount.com"
DASHBOARD_SA_EMAIL="${DASHBOARD_SA_NAME}@${PROJECT_ID}.iam.gserviceaccount.com"
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
  # update-oidc replaces the whole mapping, so the full mapping is always passed.
  # Re-running with another id re-points the provider to that repository only.
  gcloud iam workload-identity-pools providers update-oidc "${PROVIDER_ID}" \
    --location=global --workload-identity-pool="${POOL_ID}" \
    --attribute-mapping="${ATTRIBUTE_MAPPING}" \
    --attribute-condition="assertion.repository_id == '${REPO_ID}'"
else
  gcloud iam workload-identity-pools providers create-oidc "${PROVIDER_ID}" \
    --location=global --workload-identity-pool="${POOL_ID}" \
    --display-name="taiwan-housing-pipeline" \
    --issuer-uri="https://token.actions.githubusercontent.com" \
    --attribute-mapping="${ATTRIBUTE_MAPPING}" \
    --attribute-condition="assertion.repository_id == '${REPO_ID}'"
fi

# Create a service account if missing, let it run queries, and let the given
# workload identity principal set impersonate it.
ensure_service_account() {
  local name="$1" email="$2" display="$3" principal="$4"
  if ! gcloud iam service-accounts describe "${email}" >/dev/null 2>&1; then
    gcloud iam service-accounts create "${name}" --display-name="${display}"
    sleep 10  # let the new account propagate before granting roles
  fi
  gcloud projects add-iam-policy-binding "${PROJECT_ID}" \
    --member="serviceAccount:${email}" --role="roles/bigquery.jobUser" \
    --condition=None >/dev/null
  gcloud iam service-accounts add-iam-policy-binding "${email}" \
    --role="roles/iam.workloadIdentityUser" \
    --member="principalSet://iam.googleapis.com/${POOL_NAME}/${principal}" >/dev/null
}

echo "== Service accounts"
ensure_service_account "${CI_SA_NAME}" "${CI_SA_EMAIL}" "dbt CI (GitHub Actions)" \
  "attribute.repository_id/${REPO_ID}"
ensure_service_account "${DASHBOARD_SA_NAME}" "${DASHBOARD_SA_EMAIL}" \
  "Dashboard build (GitHub Actions, main only)" \
  "attribute.repo_ref/${REPO_ID}:refs/heads/main"

echo "== CI datasets"
for dataset in "${CI_DATASETS[@]}"; do
  if ! bq show "${PROJECT_ID}:${dataset}" >/dev/null 2>&1; then
    bq --location="${LOCATION}" mk --dataset \
      --description="dbt CI output (views only); safe to delete" "${PROJECT_ID}:${dataset}"
  fi
done

# Add one dataset-level access entry (READER or WRITER) for one account if missing.
grant_dataset() {
  local sa="$1" dataset="$2" role="$3" tmp
  tmp="$(mktemp)"
  bq show --format=prettyjson "${PROJECT_ID}:${dataset}" >"${tmp}"
  if jq -e --arg sa "${sa}" --arg role "${role}" \
    '.access | any(.userByEmail == $sa and .role == $role)' "${tmp}" >/dev/null; then
    echo "${dataset}: ${role} already granted to ${sa}"
  else
    jq --arg sa "${sa}" --arg role "${role}" \
      '.access += [{"role": $role, "userByEmail": $sa}]' "${tmp}" >"${tmp}.new"
    bq update --source "${tmp}.new" "${PROJECT_ID}:${dataset}" >/dev/null
    echo "${dataset}: ${role} granted to ${sa}"
  fi
  rm -f "${tmp}" "${tmp}.new"
}

echo "== Dataset access"
grant_dataset "${CI_SA_EMAIL}" housing_bronze READER
grant_dataset "${CI_SA_EMAIL}" ci_housing_silver WRITER
grant_dataset "${CI_SA_EMAIL}" ci_housing_gold WRITER
grant_dataset "${DASHBOARD_SA_EMAIL}" housing_gold READER

echo
echo "== Audit: everything each account can do (expect only the lines below)"
echo "   ${CI_SA_NAME}: project roles/bigquery.jobUser | housing_bronze: READER | ci_*: WRITER"
echo "   ${DASHBOARD_SA_NAME}: project roles/bigquery.jobUser | housing_gold: READER"
for email in "${CI_SA_EMAIL}" "${DASHBOARD_SA_EMAIL}"; do
  gcloud projects get-iam-policy "${PROJECT_ID}" --flatten="bindings[].members" \
    --filter="bindings.members:serviceAccount:${email}" --format="value(bindings.role)" |
    sed "s/^/${email%%@*} project: /"
  for dataset in housing_bronze housing_silver housing_gold "${CI_DATASETS[@]}"; do
    bq show --format=prettyjson "${PROJECT_ID}:${dataset}" |
      jq -r --arg sa "${email}" --arg ds "${dataset}" --arg who "${email%%@*}" \
        '.access[] | select(.userByEmail == $sa) | "\($who) \($ds): \(.role)"'
  done
done

echo
echo "== Set these GitHub repository variables (Settings > Secrets and variables > Actions > Variables)"
echo "GCP_WIF_PROVIDER=${POOL_NAME}/providers/${PROVIDER_ID}"
echo "GCP_CI_SERVICE_ACCOUNT=${CI_SA_EMAIL}"
echo "GCP_DASHBOARD_SERVICE_ACCOUNT=${DASHBOARD_SA_EMAIL}"
