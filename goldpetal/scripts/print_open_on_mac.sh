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
  echo "  Copy ONE command at a time. scp does not open the desk."
  echo "  ImportError HARD_LIVE_MAX_LOTS = live_orders.py on the VM is old. Sync matching desk files, then restart:"
  echo "    gcloud compute ssh sampreeth1608@sampreeth-love-story --project=sampreethlovestory --zone=asia-south1-c --tunnel-through-iap --command 'cd ~/goldpetal-repo && git fetch origin && git show origin/cursor/live-1lot-test-a4b2:goldpetal/live_orders.py > ~/goldpetal/live_orders.py && git show origin/cursor/live-1lot-test-a4b2:goldpetal/live_readiness.py > ~/goldpetal/live_readiness.py && git show origin/cursor/live-1lot-test-a4b2:goldpetal/scripts/sync_desk_runtime.sh > ~/goldpetal/scripts/sync_desk_runtime.sh && git show origin/cursor/live-1lot-test-a4b2:goldpetal/scripts/run_desk_vm.sh > ~/goldpetal/scripts/run_desk_vm.sh && chmod +x ~/goldpetal/scripts/sync_desk_runtime.sh ~/goldpetal/scripts/run_desk_vm.sh && ~/goldpetal/scripts/sync_desk_runtime.sh && cd ~/goldpetal && ./scripts/run_desk_vm.sh --restart'"
  echo "  Connection refused on the tunnel = 8501 is down. After the files match, restart only:"
  echo "    gcloud compute ssh sampreeth1608@sampreeth-love-story --project=sampreethlovestory --zone=asia-south1-c --tunnel-through-iap --command 'cd ~/goldpetal && ./scripts/run_desk_vm.sh --restart'"
  echo "  Login is off until DESK_PASSWORD is set. IAP + localhost bind stay."
  echo "  1) Open the desk (leave this running, then Chrome http://127.0.0.1:8501/ ):"
  echo
  echo "gcloud compute ssh sampreeth1608@sampreeth-love-story --project=sampreethlovestory --zone=asia-south1-c --tunnel-through-iap -- -N -L 8501:127.0.0.1:8501"
  echo
  echo "  2) Replace the Desktop file with v39 (~ window says Gold Petal v39):"
  echo "     gcloud compute ssh sampreeth1608@sampreeth-love-story --project=sampreethlovestory --zone=asia-south1-c --tunnel-through-iap --command 'cd ~/goldpetal-repo && git fetch origin && git show origin/cursor/live-1lot-test-a4b2:goldpetal/scripts/GoldPetal.command' > ~/Desktop/GoldPetal.command"
  echo "     chmod +x ~/Desktop/GoldPetal.command"
  echo "     open ~/Desktop/GoldPetal.command"
  echo "  Do not put chmod on the gcloud line. The window must say Gold Petal v39, not v38."
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
