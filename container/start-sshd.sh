#!/bin/sh
set -eu

# Only the public client key is mounted. Preserve the previous root execution
# environment so existing development tools and workspace permissions still work.
install -d -m 700 /root/.ssh
install -m 600 /run/worker-keys/client_key.pub /root/.ssh/authorized_keys
install -d -m 755 /run/sshd
ssh-keygen -q -t ed25519 -N '' -f /run/sshd/host_key
exec /usr/sbin/sshd -D -q -f "${1:-/etc/ssh/sshd_config.harness}"
