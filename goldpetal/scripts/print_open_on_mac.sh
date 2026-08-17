# Sourced by VM start scripts. Prints how to open the desk from the Mac.
# Do not run gcloud on the VM — that is what prints "insufficient authentication scopes".

print_open_on_mac() {
  local ip=""
  ip="$(curl -s -m 1 -H "Metadata-Flavor: Google" \
    http://metadata.google.internal/computeMetadata/v1/instance/network-interfaces/0/access-configs/0/external-ip 2>/dev/null || true)"

  echo
  echo "======== THIS WINDOW IS THE VM ========"
  echo "Do not run gcloud compute ssh here. The VM cannot open Chrome,"
  echo "and gcloud here fails with: insufficient authentication scopes."
  echo
  echo "Open a NEW Terminal on your Mac (not this SSH session) and paste:"
  echo
  echo "  gcloud compute ssh sampreeth1608@sampreeth-love-story --zone=asia-south1-c -- -N -L 8501:127.0.0.1:8501"
  echo
  if [[ -n "${ip}" ]]; then
    echo "Or if gcloud is not installed on the Mac:"
    echo
    echo "  ssh -N -L 8501:127.0.0.1:8501 sampreeth1608@${ip}"
    echo
  else
    echo "Or if gcloud is not installed on the Mac (use the VM public IP):"
    echo
    echo "  ssh -N -L 8501:127.0.0.1:8501 sampreeth1608@<VM_EXTERNAL_IP>"
    echo
  fi
  echo "Leave that Mac window running. Then Chrome:"
  echo "  http://127.0.0.1:8501/   → first tab Desk"
  echo "Hard-refresh: Cmd+Shift+R"
  echo "========================================"
  echo
}
