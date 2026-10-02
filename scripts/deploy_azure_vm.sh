#!/usr/bin/env bash
# Deploy LabelVerify to one small Azure VM running the Docker image, with HTTPS from
# Caddy (automatic certificate) on <name>.<region>.cloudapp.azure.com. Always on, data on
# the VM's disk, no App Service quota involved. The B1s size is free for 750 hours a
# month in a free account's first year.
#
# Needs: az (signed in), Docker, and the image registry from scripts/deploy_azure.sh
# (this script builds and pushes the image the same way).
#
#   ANTHROPIC_API_KEY=sk-ant-... GEMINI_API_KEY=... scripts/deploy_azure_vm.sh
#
# Re-running pulls the newly pushed image on the VM and restarts the app in place.
# Tear down:  az group delete --name "$RG" --yes
set -euo pipefail

: "${ANTHROPIC_API_KEY:?set ANTHROPIC_API_KEY (the model key the deployment will use)}"
RG="${RG:-labelverify-rg}"
LOCATION="${LOCATION:-eastus}"
SUB8="$(az account show --query id --output tsv | cut -c1-8)"
ACR="${ACR:-labelverify$SUB8}"
VM="${VM:-labelverify-vm}"
DNS_LABEL="${DNS_LABEL:-labelverify-$SUB8}"
SIZE="${SIZE:-Standard_B1s}"
IMAGE="labelverify:$(git rev-parse --short HEAD 2>/dev/null || date +%s)"
SECRET_KEY="${SECRET_KEY:-$(openssl rand -hex 32)}"
FQDN="$DNS_LABEL.$LOCATION.cloudapp.azure.com"

echo "resource group $RG, vm $VM ($SIZE, $LOCATION), registry $ACR, image $IMAGE, https://$FQDN"
az group show --name "$RG" --output none 2>/dev/null \
  || az group create --name "$RG" --location "$LOCATION" --output none

# 1. Build and push the image.
az acr show --name "$ACR" --resource-group "$RG" --output none 2>/dev/null \
  || az acr create --name "$ACR" --resource-group "$RG" --sku Basic --admin-enabled true --output none
REGISTRY="$(az acr show --name "$ACR" --resource-group "$RG" --query loginServer --output tsv)"
ACR_USER="$(az acr credential show --name "$ACR" --resource-group "$RG" --query username --output tsv)"
ACR_PASS="$(az acr credential show --name "$ACR" --resource-group "$RG" --query 'passwords[0].value' --output tsv)"
DOCKER=docker
docker info >/dev/null 2>&1 || DOCKER="sudo docker"
printf '%s' "$ACR_PASS" | $DOCKER login "$REGISTRY" --username "$ACR_USER" --password-stdin
$DOCKER build -t "$REGISTRY/$IMAGE" .
$DOCKER push "$REGISTRY/$IMAGE"

# 2. The commands that (re)start the app on the VM: the app container on a private
#    network with its data on the VM disk, and Caddy in front terminating HTTPS.
START_APP=$(cat <<EOS
set -e
docker network inspect labelverify >/dev/null 2>&1 || docker network create labelverify
printf '%s' '$ACR_PASS' | docker login '$REGISTRY' --username '$ACR_USER' --password-stdin
docker pull '$REGISTRY/$IMAGE'
docker rm -f labelverify >/dev/null 2>&1 || true
mkdir -p /srv/labelverify
docker run -d --name labelverify --restart unless-stopped --network labelverify \\
  -v /srv/labelverify:/app/data \\
  -e ANTHROPIC_API_KEY='$ANTHROPIC_API_KEY' \\
  -e GEMINI_API_KEY='${GEMINI_API_KEY:-}' \\
  -e LABELVERIFY_NOTICE_PROVIDER='${GEMINI_API_KEY:+gemini}' \\
  -e SECRET_KEY='$SECRET_KEY' \\
  -e LABELVERIFY_DEMO_ACCOUNTS=false \\
  -e LABELVERIFY_SECURE_COOKIES=true \\
  -e LABELVERIFY_DAILY_READ_LIMIT='${LABELVERIFY_DAILY_READ_LIMIT:-150}' \\
  -e LABELVERIFY_FALLBACK=tesseract \\
  '$REGISTRY/$IMAGE'
if ! docker ps --format '{{.Names}}' | grep -qx caddy; then
  docker run -d --name caddy --restart unless-stopped --network labelverify \\
    -p 80:80 -p 443:443 -v caddy_data:/data -v caddy_config:/config \\
    caddy:2 caddy reverse-proxy --from 'https://$FQDN' --to labelverify:8000
fi
EOS
)

# 3. Create the VM on first run (cloud-init installs Docker and starts the app), or
#    push the new image into the running VM afterwards.
if ! az vm show --name "$VM" --resource-group "$RG" --output none 2>/dev/null; then
  CLOUD_INIT="$(mktemp)"
  {
    echo "#cloud-config"
    echo "package_update: true"
    echo "packages: [docker.io]"
    echo "runcmd:"
    echo "  - systemctl enable --now docker"
    echo "  - |"
    printf '%s\n' "$START_APP" | sed 's/^/    /'
  } > "$CLOUD_INIT"
  az vm create --name "$VM" --resource-group "$RG" --location "$LOCATION" --size "$SIZE" \
    --image Ubuntu2404 --admin-username azureuser --generate-ssh-keys \
    --public-ip-address-dns-name "$DNS_LABEL" --public-ip-sku Standard \
    --os-disk-size-gb 30 --custom-data "$CLOUD_INIT" --output none
  rm -f "$CLOUD_INIT"
  az vm open-port --name "$VM" --resource-group "$RG" --port 80,443 --priority 1001 --output none
else
  az vm run-command invoke --name "$VM" --resource-group "$RG" --command-id RunShellScript \
    --scripts "$START_APP" --query 'value[0].message' --output tsv | tail -5
fi

echo "waiting for https://$FQDN/healthz (first boot installs Docker and pulls the image: a few minutes)"
for _ in $(seq 1 60); do
  if curl -fsS "https://$FQDN/healthz" >/dev/null 2>&1; then
    echo "up: https://$FQDN"
    echo "sign in with the accounts in README.md (the dialog hides them on a public URL)"
    exit 0
  fi
  sleep 10
done
echo "not answering yet. Check on the VM:  ssh azureuser@$FQDN 'sudo docker ps; sudo docker logs labelverify | tail -20; sudo docker logs caddy | tail -20'" >&2
exit 1
