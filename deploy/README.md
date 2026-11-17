# Deployment

Every push to `main` is tested, built into `ghcr.io/gaggimate/update-server:sha-<commit>` and then started
on the OTA host by the `deploy` job in `.github/workflows/docker-image.yml`.

## How a deployment works

1. The job connects to the host over SSH as the user `deploy` and passes one word: `sha-<commit>`.
2. That SSH key is bound to a forced command, so the only thing it can run is
   `/usr/local/sbin/ota-deploy` (this directory's `ota-deploy`). No shell, no file copy, no forwarding.
3. `ota-deploy` pulls that exact image, writes it to `/app/.env` as `APP_IMAGE`, restarts the `app`
   service and waits until the container is healthy and the site answers through Traefik.
4. If that does not happen within 90 seconds it puts the previous build back and exits non-zero, which
   fails the job.

To roll back by hand, run the deploy job again for an older commit, or on the host:
`ota-deploy sha-<older commit>`.

`ota-deploy` on the host is installed by hand and is deliberately not updated by the pipeline: a change
to this repository must not be able to change what the deploy key is allowed to do.

## GitHub configuration

Environment `production`, restricted to the `main` branch, with these secrets:

| Secret | Value |
| --- | --- |
| `DEPLOY_HOST` | Address of the OTA host. A secret because the host only accepts web traffic from Cloudflare and its address should not be published. |
| `DEPLOY_PORT` | SSH port |
| `DEPLOY_SSH_KEY` | Private half of the deploy key |
| `DEPLOY_KNOWN_HOSTS` | The host's SSH host key line, so the job only talks to the real server |

Adding yourself as a required reviewer on the environment turns every deployment into a one-click approval.

## Host setup (one time)

```bash
install -m 755 deploy/ota-deploy /usr/local/sbin/ota-deploy
useradd --system --home-dir /var/lib/deploy --shell /bin/sh deploy && passwd -l deploy
install -d -o root -g root -m 755 /var/lib/deploy/.ssh
echo 'command="sudo -n /usr/local/sbin/ota-deploy \"$SSH_ORIGINAL_COMMAND\"",restrict <deploy public key>' \
  > /var/lib/deploy/.ssh/authorized_keys
echo 'deploy ALL=(root) NOPASSWD: /usr/local/sbin/ota-deploy' > /etc/sudoers.d/ota-deploy
chmod 440 /etc/sudoers.d/ota-deploy && visudo -cf /etc/sudoers.d/ota-deploy
```

The compose file on the host uses `image: ${APP_IMAGE:-ghcr.io/gaggimate/update-server:main}` and mounts
the public release signing keys as `release-signing-keys.pub.pem` (see `docker-compose.prod.yml`).
