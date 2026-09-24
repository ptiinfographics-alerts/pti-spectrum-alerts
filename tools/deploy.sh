#!/bin/zsh
# Puts the latest code live now, instead of at the next six-hourly hand-over.
#
#   tools/deploy.sh
#
# 1. Pushes any commits not yet on GitHub.
# 2. Cancels the queued run (it would start with the workflow file from when
#    it was queued) and then the running one, which saves what it has sent
#    before it stops.
# 3. Starts a fresh run from the latest code.
set -e
REPO=ptiinfographics-alerts/pti-spectrum-alerts
cd "${0:A:h:h}"

git pull -q --rebase origin main
git push -q origin main
echo "code on GitHub: $(git log -1 --format='%h %s')"

for state in pending queued waiting in_progress; do
  for id in $(gh run list -R $REPO --workflow alerts.yml --status $state --json databaseId --jq '.[].databaseId'); do
    gh run cancel $id -R $REPO >/dev/null && echo "stopped $state run $id"
  done
done

# The stopped run needs a few seconds to save and exit.
sleep 15
gh workflow run alerts.yml -R $REPO --ref main
echo "fresh run started; it will be checking within about a minute"
