# Pi Swarm — Ansible

Single inventory file: **`config.yml`** (copy from `config.yml.example`). All hosts and variables live there—no `inventory.yaml`, `group_vars`, or separate secrets files.

## What the playbook does

1. **common** (all nodes): packages, `swarm` user, git clone of `swarm_repo` to `swarm_clone_path`, pip install from `{{ swarm_clone_path }}/{{ pi_swarm_python_subdir }}/requirements.txt` (default subdir `pi-swarm` for a monorepo with `ansible/` + `pi-swarm/`), llama-cpp-python, swap, CPU governor.
2. **lead**: Docker + **Gitea 1.25.5** via Compose (`/opt/gitea`), wait for HTTP, create **Gitea API token** (BasicAuth), write `/etc/pi-swarm/gitea.env`, ensure the **Gitea organization** `swarm` (override with `gitea_swarm_org`), deploy **orchestrator** systemd unit.
3. **workers**: copy token env from lead hostvars, deploy **worker** systemd unit.

Gitea image and ports match the Compose layout you specified (`docker.gitea.com/gitea:1.25.5`, `3000:3000`, `222:22`, bind-mount data under `gitea_data_host_path`).

## One-time setup

```bash
cd ansible
cp config.yml.example config.yml
# Edit config.yml: IPs, users, orchestrator_url, gitea_password, swarm_repo,
# swarm_clone_path, pi_swarm_python_subdir (see example), etc.
```

`config.yml` is **gitignored** so passwords stay local.

## Gitea first login

On a **fresh** Gitea data volume, open `http://<lead-ip>:3000` once and complete the **install wizard** (create the admin user matching `gitea_user` / password you will use). Then re-run the playbook (or run only from `gitea_token.yml` onward) so the token task can succeed.

If `/etc/pi-swarm/gitea.env` already exists on the lead, the token step reuses it and does not call the token API.

## Run

From the **`ansible/`** directory (so `ansible.cfg` picks up `inventory = config.yml`):

```bash
cd ansible
ansible-playbook playbook.yaml
```

If `config.yml` is missing, copy from `config.yml.example` first.

Optional extra vars:

```bash
ansible-playbook playbook.yaml -e "swarm_repo=https://github.com/you/PiSwarmAgents.git"
```

## Reference

- Gitea token API: [API Usage](https://docs.gitea.com/development/api-usage).
