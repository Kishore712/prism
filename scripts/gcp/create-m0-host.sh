#!/bin/bash
# Run only in an explicitly authorized GCP account with trial credit available.
# Existing resources are never reused, edited, or deleted by this script.
set -euo pipefail
if [[ $# != 1 || ! $1 =~ ^[a-z][a-z0-9-]+$ ]]; then
  echo 'Usage: bash create-m0-host.sh PROJECT_ID' >&2
  exit 2
fi
prism_project=$1
prism_script_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
prism_image=$(gcloud compute images describe-from-family ubuntu-2404-lts-amd64 \
  --project=ubuntu-os-cloud --format='value(selfLink)')
test -n "$prism_image"
test -f "$prism_script_dir/m0-preflight.sh"

gcloud compute networks create prism-m0-isolated \
  --project="$prism_project" --subnet-mode=custom \
  --description='Prism M0 synthetic host; no ingress allow rules'
gcloud compute networks subnets create prism-m0-subnet \
  --project="$prism_project" --network=prism-m0-isolated \
  --region=us-central1 --range=10.77.0.0/24
gcloud compute instances create prism-m0-kvm \
  --project="$prism_project" --zone=us-central1-a \
  --machine-type=n2-standard-2 --enable-nested-virtualization \
  --image="$prism_image" --boot-disk-size=30GB --boot-disk-type=pd-balanced \
  --network=prism-m0-isolated --subnet=prism-m0-subnet --network-tier=STANDARD \
  --no-service-account --no-scopes --no-restart-on-failure \
  --maintenance-policy=TERMINATE --max-run-duration=4h \
  --instance-termination-action=STOP --reservation-affinity=none \
  --shielded-secure-boot --shielded-vtpm --shielded-integrity-monitoring \
  --metadata=block-project-ssh-keys=TRUE,enable-oslogin=TRUE,disable-legacy-endpoints=TRUE,serial-port-enable=FALSE \
  --metadata-from-file="startup-script=$prism_script_dir/m0-preflight.sh" \
  --labels=project=prism,stage=m0,data=synthetic \
  --description='Synthetic M0 KVM validation only; stop after four hours; no ingress or service account' \
  --format='table(name,zone,machineType,status,scheduling.maxRunDuration.seconds)'
