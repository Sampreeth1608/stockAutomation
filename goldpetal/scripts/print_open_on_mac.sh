# Sourced by VM start scripts. Prints how to open the desk from the Mac.
# Do not run gcloud or ssh-to-the-VM-IP here.

print_open_on_mac() {
  local ip=""
  ip="$(curl -s -m 1 -H "Metadata-Flavor: Google" \
    http://metadata.google.internal/computeMetadata/v1/instance/network-interfaces/0/access-configs/0/external-ip 2>/dev/null || true)"

  echo
  echo "======== STOP — this window is the VM ========"
  echo "Prompt looks like:  sampreeth1608@sampreeth-love-story"
  echo "gcloud here → insufficient authentication scopes"
  echo "ssh to ${ip:-8.231.125.120} here → Permission denied (publickey)"
  echo "Both are the VM talking to itself. That cannot open Chrome."
  echo
  echo "The Google Cloud Console SSH button is also the VM."
  echo
  echo "On the Mac laptop (not this SSH tab):"
  echo "  Easiest on the Mac (not this SSH tab):"
  echo "    gcloud compute scp sampreeth1608@sampreeth-love-story:/home/sampreeth1608/goldpetal-repo/goldpetal/scripts/GoldPetal.command ~/Desktop/GoldPetal.command --project=sampreethlovestory --zone=asia-south1-c --tunnel-through-iap"
  echo "    chmod +x ~/Desktop/GoldPetal.command && open ~/Desktop/GoldPetal.command"
  echo "  Or Terminal (IAP — not ssh to 8.231.125.120:22):"
  echo
  echo "gcloud compute ssh sampreeth1608@sampreeth-love-story --project=sampreethlovestory --zone=asia-south1-c --tunnel-through-iap -- -N -L 8501:127.0.0.1:8501"
  echo
  echo "  If ssh times out on port 22, you missed --tunnel-through-iap."
  echo "  Do not open port 22 or 8501 to the internet."
  echo
  echo "  Then Chrome on the Mac (not this SSH tab):"
  echo "     http://127.0.0.1:8501/          Gold Petal desk"
  echo "     http://127.0.0.1:8501/lite     compact controls"
  echo "     http://127.0.0.1:8501/full     archive / downloads"
  echo "     Hard-refresh: Cmd+Shift+R"
  echo "     This is a desktop trading station (not Streamlit)."
  echo
  echo "If a tunnel from earlier is still on the Mac, just open Chrome."
  echo "=============================================="
  echo
}
