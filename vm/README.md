# macOS UTM harness

Runs an agent in a macOS UTM guest, using a remote Ollama server. The terminal UI runs on the host. Model tools execute in the worker.

Run the host commands below from the repository root.

## macOS VM: one-time setup

Requires an Apple Silicon Mac and UTM with the Apple Virtualization backend.
Use macOS 13 or newer on both host and guest for VirtioFS sharing.

1. Create a dedicated macOS VM, finish account setup, and enable **Remote Login**
   for the guest account under System Settings → General → Sharing. Set networking
   so the host can reach guest SSH; Shared Network is a starting point.
2. Install Python 3.12+ in the guest. Install any additional development tools you
   need separately; bootstrap does not translate the Linux Dockerfile packages.
3. Configure passwordless SSH access to the guest using the host private key
   you will set as `VM_SSH_KEY`. Make sure `ssh` can connect to the guest with
   that key. A passphrase-protected key can be unlocked in `ssh-agent` before
   running the harness.
4. Configure **exactly one writable directory share** in UTM, initially pointing
   at this repository. You do not need to create another directory on the host:
   use the existing repository folder, such as `/Users/colintan/ai_harness`.

   With the VM shut down, open its settings in UTM, go to **Sharing**, and add
   the repository folder as the single shared directory. Ensure **Read Only**
   is disabled, save the settings, and start the VM.

   Current UTM versions automatically mount shared folders inside
   `/Volumes/My Shared Files` in the guest. Open Terminal **inside the guest**
   and check:

   ```bash
   ls "/Volumes/My Shared Files"
   ls "/Volumes/My Shared Files/ai_harness"
   ```

   Substitute your repository folder's name if it is not `ai_harness`. You
   should see repository entries such as `harness`, `host`, and `vm`. No extra
   host directory or guest mount directory is needed for this automatic mount.

   If the volume is missing, fully shut down the VM and start it again after
   checking the sharing settings. A guest reboot may not apply sharing changes.
   If it still does not appear, mount the modern share tag manually:

   ```bash
   mkdir -p "$HOME/bootstrap-share"
   /sbin/mount_virtiofs com.apple.virtio-fs.automount "$HOME/bootstrap-share"
   ls "$HOME/bootstrap-share"
   ```

   The repository will be inside `~/bootstrap-share/ai_harness`, rather than
   directly inside `~/bootstrap-share`. If macOS reports a permission error,
   retry that mount command with `sudo`. Older UTM releases may instead use
   the tag `share` and expose the repository contents directly at the mount.

   The bootstrap script creates a separate empty `~/harness-work` directory
   for normal sessions. The worker links it to the single automounted project
   on modern UTM, or mounts the legacy share there on older versions.

5. Run bootstrap as the guest account, without sudo, substituting the installed
   Python executable:

   ```bash
   bash "/Volumes/My Shared Files/ai_harness/vm/bootstrap-macos.sh" \
     /opt/homebrew/bin/python3.12
   ```

   If you mounted manually, substitute the repository path from step 4.

   This creates a virtual environment and installs Python dependencies, copies
   the harness onto the guest disk, and creates an empty workspace mount
   directory. It does not configure SSH. It is rerunnable.
6. Copy bootstrap's guest paths into `env/local.env` on the host and set `VM_ID`
   to the VM name or UUID. Set `VM_SSH_TARGET` to a hostname/address or an alias
   from your normal `~/.ssh/config`, and set `VM_SSH_KEY` to the host private key
   configured in step 3. SSH uses your normal configuration and `known_hosts`, with
   that key explicitly selected for VM authentication. Values are literal: use
   `~` for host home paths or absolute paths; shell `$VARIABLE` expansion is not
   supported.
7. Establish host-key trust using your normal SSH setup. Connect once with
   `ssh VM_SSH_TARGET` and verify/accept the guest host key so it is recorded in
   your normal `known_hosts`. The harness runs SSH non-interactively, so this
   first-time trust step must be completed before starting a session.
8. Keep the VM running and unlocked after bootstrap. If you used a manual
   bootstrap mount, unmount it before normal sessions:

   ```bash
   /sbin/umount "$HOME/bootstrap-share"
   ```

   For normal sessions on current UTM, use the automatic volume at
   `/Volumes/My Shared Files`. Leave the single shared directory pointing at
   the project you want to use, or a parent directory containing your projects.

On the host, install dependencies with
`python3 -m pip install -r host/requirements.txt`, copy `env/local.env.example`
to `env/local.env` if it does not exist, and configure the remote Ollama
`SSH_TARGET` along with the VM settings. No model server is installed in the guest.

## macOS VM: each session

```bash
./prompt.sh --backend utm /path/to/project
```

Start and unlock the VM yourself before running the host command. The host
connects to the already-running VM, checks its existing single shared directory,
waits up to 300 seconds for SSH (configurable with `VM_SSH_TIMEOUT`), and updates
the guest harness code through SSH. It does not start, stop, or repoint the VM.

The supplied host project path must be the shared directory or a subdirectory
inside it. For example, share `/Users/colintan/projects` once and select
`/Users/colintan/projects/my-app` when running the command. Switching between
projects under that same share does not require restarting the VM. Selecting a
folder outside the share reports an error; changing the UTM share itself may
require restarting the VM. macOS may ask for permission to control UTM on first
use.

The guest exposes the project at `VM_WORK_DIR` using a symlink to the
automounted folder on modern UTM, or a direct mount on older releases. The harness installation and
virtual environment remain on the guest disk, outside the workspace. Changes
to project files affect the originals on the host immediately. Keep SSH private
keys outside the shared project.

On normal exit, the supervisor stops the worker process group and removes the
workspace symlink or unmounts a legacy workspace. The host closes its SSH
connections but leaves the VM running, including when startup fails. It never
requests VM shutdown. The automatic share remains mounted while the VM runs.
A host crash or forced termination can bypass worker cleanup.

Only one UTM session is supported at a time. Consecutive sessions reuse the
running VM and its existing share. If you select a subdirectory, the guest
project path is `VM_WORK_DIR/<relative-project-path>`.

Code updates happen every session. When Python dependencies change, rerun
bootstrap (or explicitly install the updated requirements in the guest venv).
The supervisor sets a PATH covering Homebrew and standard macOS command paths;
non-interactive shell commands do not load your interactive shell configuration.

The VM is not a network isolation policy: reachable host services and the shared
workspace remain intentional access paths. Both SSH reverse forwards bind guest
loopback and expose only the host callback and remote Ollama tunnel.

UTM references: [macOS automount documentation correction](https://github.com/utmapp/UTM/issues/7677),
[macOS guest sharing](https://docs.getutm.app/guest-support/macos/)
and [scripting reference](https://docs.getutm.app/scripting/reference/).

## Checks

```bash
bash -n vm/bootstrap-macos.sh
```

## Troubleshooting startup

The interactive `>>>` prompt appears in the **host terminal**, not inside the
VM. Unlocking the guest permits startup to continue, but does not itself launch
an application on the guest desktop.

The host prints each startup stage. While waiting for SSH, it reports the latest
connection error every ten seconds. `Could not resolve hostname` means the guest
hostname in `VM_SSH_TARGET` cannot be resolved; `Connection refused` usually means
Remote Login is not listening at that address. A connection timeout can indicate
an incorrect address or a networking/firewall issue. Authentication and host-key
verification failures are reported immediately.

Confirm Remote Login is enabled inside the guest. Check its username and local
hostname there with `id -un` and `scutil --get LocalHostName`, then compare them
with the host's `VM_SSH_TARGET`. If using a new hostname or IP, connect to it
once with your normal `ssh` command and verify/accept its host key before running
the harness.
