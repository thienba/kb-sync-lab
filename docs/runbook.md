# Runbook: kbsync on the server

The sync job, the status web server and the Cloudflare Tunnel run as one compose stack in
`/opt/kbsync` on the human's AWS server (arm64 Ubuntu 24.04, Docker, Compose). Public URL:
`https://kbsync.bahuynh.com/status/`. Design: `context/part1-design.md` §5.6, §5.8, §8.

**Never touch bs-kara (the other project on this server): not its repo, compose stack, Caddyfile, deploy or security group (D-0000-12).**
This stack publishes no host port; the only way in is the Cloudflare Tunnel.

How to read the steps: **[human]** is done by the human alone (dashboard, secrets).
**[agent, ask first]** is run by the agent only after the human says go for that exact step.
The agent never runs a server command without the human's go-ahead for that step.
Run server commands as user `ubuntu` over SSH (`ssh -i <your server key> ubuntu@54.251.27.240`).

## Layout

```
/opt/kbsync/
  compose.yaml  Caddyfile  run-job.sh   copied from src/deploy by deploy.sh
  .env          secrets, mode 600, owner root (never in git, never in chat)
  public/       status page + runs/*.json, owner UID 10001 (job writes, web reads)
  data/         kbsync.lock, cron.log, owner UID 10001
  src/          git clone of https://github.com/thienba/kb-sync-lab (owner ubuntu)
```

## First-time setup

1. **[agent, ask first]** Record the other containers' start times, to prove nothing else was restarted:
   `docker inspect -f '{{.Name}} {{.State.StartedAt}}' $(docker ps -q) | sort > ~/containers-before.txt; ss -ltn > ~/ports-before.txt`
2. **[agent, ask first]** Create the layout. The repo is public, so it is cloned over https and needs no deploy key:
   ```
   sudo install -d -o root -g root -m 755 /opt/kbsync
   sudo install -d -o ubuntu -g ubuntu /opt/kbsync/src
   sudo install -d -o 10001 -g 10001 /opt/kbsync/public /opt/kbsync/data
   git clone https://github.com/thienba/kb-sync-lab.git /opt/kbsync/src
   ```
   `/opt/kbsync` is root-owned, so the files root's cron runs (`run-job.sh`, `compose.yaml`) cannot be edited by `ubuntu`; `deploy.sh` installs them with `sudo`.
3. **[agent, ask first]** Build the image and install the stack files: `/opt/kbsync/src/deploy/deploy.sh`
4. **[human]** Create the healthchecks.io check: project, "Add check", name `kbsync`, schedule
   cron `17 2 * * *`, timezone UTC, grace 1 hour. Copy its ping URL (`https://hc-ping.com/<uuid>`).
5. **[human]** Create the tunnel in the Cloudflare dashboard (Zero Trust, Networks, Tunnels, Create a tunnel,
   Cloudflared). Copy the tunnel token. Never paste it into chat.
6. **[human]** On the server write the secrets file. Start from `src/deploy/env.example`, fill `API_KEY`
   (a dedicated OpenAI project key with a spend limit), `HEALTHCHECK_URL` and `TUNNEL_TOKEN`:
   ```
   sudo install -m 600 -o root -g root /opt/kbsync/src/deploy/env.example /opt/kbsync/.env
   sudo nano /opt/kbsync/.env
   stat -c '%a %u' /opt/kbsync/.env      # must print: 600 0
   ```
7. **[human]** In the tunnel's "Public hostname" tab add hostname `kbsync.bahuynh.com`, service type HTTP,
   URL `http://web:8080`. Cloudflare creates the DNS record itself.
   Do not create a manual DNS record for kbsync.
8. **[agent, ask first]** Start the web server and the tunnel (root is needed to read `.env`):
   `sudo docker compose -f /opt/kbsync/compose.yaml up -d web cloudflared`
   then `sudo docker compose -f /opt/kbsync/compose.yaml ps` shows both `running`, and `ss -ltn > ~/ports-after.txt; diff ~/ports-before.txt ~/ports-after.txt` shows no new listening port.
9. **[agent, ask first]** First run by hand with the production key (about 8 minutes the first time):
   `sudo /opt/kbsync/run-job.sh; echo exit=$?` then `tail -20 /opt/kbsync/data/cron.log`.
   Check `jq '{status,added,files}' /opt/kbsync/public/runs/latest.json`: `"status": "ok"`.
10. **[agent, ask first]** Check from the internet (run from your laptop):
    ```
    curl -sI https://kbsync.bahuynh.com/runs/latest.json            # 200, cache-control: no-store
    curl -s -o /dev/null -w '%{http_code}\n' https://kbsync.bahuynh.com/runs/   # not 200
    curl -sI https://kbsync.bahuynh.com/ | grep -i '^location'      # /status/
    curl -m 5 http://54.251.27.240:8080                            # fails to connect
    ```
11. **[agent, ask first]** Failure drill: run once with a bad key, expect exit 2 and a red healthchecks check:
    `sudo API_KEY=sk-invalid /opt/kbsync/run-job.sh; echo exit=$?`
    (a shell variable overrides the `.env` value for compose). Then **[human]** confirms the check is red in
    the healthchecks dashboard. Run step 9 again and the human confirms it turns green.
12. **[agent, ask first]** Install the schedule in root's crontab (`.env` is root-only, Q-0010-1). Never overwrite it:
    first look at what is there, `sudo crontab -l`, then append only if the job line is missing:
    ```
    sudo crontab -l 2>/dev/null | grep -qF '/opt/kbsync/run-job.sh' || \
      (sudo crontab -l 2>/dev/null; cat /opt/kbsync/src/deploy/crontab.example) | sudo crontab -
    sudo crontab -l
    ```
    `sudo crontab -l` must show `CRON_TZ=UTC` and `17 2 * * * /opt/kbsync/run-job.sh`. `CRON_TZ=UTC` only affects the
    lines after it, so any existing root job above it keeps the server's timezone; it is appended at the end on purpose.
    A second run of the command changes nothing.
13. **[agent, ask first]** Rotate the job log: `sudo install -m 644 -o root -g root /opt/kbsync/src/deploy/logrotate.example /etc/logrotate.d/kbsync`,
    then `sudo logrotate -d /etc/logrotate.d/kbsync` (dry run) reports no error.
14. **[agent, ask first]** Prove nothing else restarted:
    `docker inspect -f '{{.Name}} {{.State.StartedAt}}' $(docker ps -q) | grep -v kbsync | sort | diff ~/containers-before.txt -`
    must print nothing.
15. **[agent, ask first]** The next morning confirm the scheduled run: `jq .started_at /opt/kbsync/public/runs/latest.json` is within 02:17 UTC plus 10 minutes.

## Everyday operations

- **Deploy a new version** **[agent, ask first]**: `/opt/kbsync/src/deploy/deploy.sh`. It runs `git pull --ff-only`, builds `kbsync:local` (tagged with the commit), and refreshes the stack files. Pick up Caddyfile/compose changes with `sudo docker compose -f /opt/kbsync/compose.yaml up -d web cloudflared`.
- **Run the job by hand** **[agent, ask first]**: `sudo /opt/kbsync/run-job.sh`. If a run is going it prints `kbsync: previous run still in progress, skipping.` and exits 0.
- **Read the log**: `tail -100 /opt/kbsync/data/cron.log`; stack logs: `sudo docker compose -f /opt/kbsync/compose.yaml logs --tail 50 web cloudflared`.
- **Status**: `https://kbsync.bahuynh.com/status/`, or `jq . /opt/kbsync/public/runs/latest.json`.
- **Roll back** **[agent, ask first]**: `cd /opt/kbsync/src && git log --oneline -5`, then
  `git checkout <good-sha> && docker build -t kbsync:local --build-arg GIT_SHA=$(git rev-parse --short HEAD) . && sudo install -m 644 deploy/compose.yaml deploy/Caddyfile /opt/kbsync/ && sudo install -m 755 deploy/run-job.sh /opt/kbsync/`,
  then `git checkout main` (so the next `deploy.sh` can `git pull`). The vector store is idempotent: the next run re-syncs it.
- **Stop everything** **[agent, ask first]**: `sudo docker compose -f /opt/kbsync/compose.yaml down`, and `sudo crontab -r` removes the schedule (only if root has no other jobs: check `sudo crontab -l` first).
