#!/usr/bin/env bash
set -euo pipefail
: "${PROJECT_ID:?Set PROJECT_ID}"; : "${ZONE:=asia-south1-a}"
VM_NAME="${VM_NAME:-cultiiq-vm}"
SA="cultiiq-vm-sa@${PROJECT_ID}.iam.gserviceaccount.com"
gcloud config set project "$PROJECT_ID"
gcloud services enable compute.googleapis.com aiplatform.googleapis.com iam.googleapis.com
if ! gcloud iam service-accounts describe "$SA" >/dev/null 2>&1; then
  gcloud iam service-accounts create cultiiq-vm-sa --display-name='CultiIQ VM Vertex AI runtime'
fi
gcloud projects add-iam-policy-binding "$PROJECT_ID" --member="serviceAccount:$SA" --role='roles/aiplatform.user' --quiet >/dev/null
# VM gets an external IP for demo use. Firewall is limited to the caller's IPv4 CIDR.
gcloud compute instances create "$VM_NAME" --zone "$ZONE" --machine-type e2-standard-2 \
  --image-family debian-12 --image-project debian-cloud --boot-disk-size 30GB \
  --service-account "$SA" --scopes cloud-platform --tags cultiiq-demo
printf '\nVM created. Install Docker using scripts/setup_vm.sh over SSH.\n'
printf 'To restrict access, create a firewall rule with --source-ranges=YOUR_PUBLIC_IP/32 (see README).\n'
