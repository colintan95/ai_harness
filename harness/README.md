# macOS UTM harness

Runs an agent in a macOS UTM guest, using a remote Ollama server. The terminal UI runs on the host. Model tools execute in the worker.

Run the host commands below from the repository root.

## macOS VM: one-time setup

Requires an Apple Silicon Mac and UTM with the Apple Virtualization backend.
Use macOS 13 or newer on both host and guest for VirtioFS sharing.

1. Create a dedicated macOS VM and finish account setup. Set networking so the
   host can reach the guest; Shared Network is a starting point.
2. Install Python 3.12+ in the guest. Install any additional development tools
   you need separately; bootstrap installs only the Python dependencies.
3. Configure the VM's share from the host. With the VM shut down, run this from
   the repository root, replacing the VM name with its name or UUID if needed:

   ```bash
   python3 setup/setup.py "Harness macOS"
   ```

   The script creates `~/work` on the host and sets the VM's two shared
   directories to the repository's `harness` folder and `~/work`. It requires
   UTM's Apple backend scripting support (UTM 5.0.6 or newer) and may prompt for
   permission to control UTM. Do not share the repository root or its `host/`
   folder. Start the VM after the script completes.

   Current UTM versions automatically mount shared folders inside
   `/Volumes/My Shared Files` in the guest. Open Terminal **inside the guest**
   and check:

   ```bash
   ls "/Volumes/My Shared Files"
   ls "/Volumes/My Shared Files/harness"
   ```

   The first command should show `harness` and `work`; the second should show
   files such as `harness.py`, `service.py`, and `bootstrap.sh`. UTM mounts the
   shares automatically.

   If the volume is missing, fully shut down the VM and start it again after
   checking the sharing settings. A guest reboot may not apply sharing changes.
   If it still does not appear, check the UTM sharing configuration and guest
   version before continuing.

   The bootstrap script creates an empty `~/work` directory for normal
   sessions. The harness links it to the automounted `work` folder.

4. Run bootstrap as the guest account, without sudo, substituting the installed
   Python executable:

   ```bash
   bash "/Volumes/My Shared Files/harness/bootstrap.sh" \
     /opt/homebrew/bin/python3.12
   ```

   This creates a virtual environment and installs Python dependencies, copies
   the harness onto the guest disk, and creates an empty workspace mount
   directory. When the GUI LaunchAgent starts, it requests Screen Recording,
   Accessibility, and mouse and keyboard event access for its venv Python.
   It waits up to 90 seconds for each request to be approved before moving to
   the next one. Approve the prompts in the guest desktop. If macOS doesn't
   show a prompt, enable that Python executable in System Settings.
   Bootstrap does not configure SSH. It is rerunnable.
5. Set up SSH after bootstrap so the host can log into the guest automatically,
   without prompting. On the host, copy `env/local.env.example` to
   `env/local.env` if needed. Set `VM_ID` to the VM name or UUID,
   `VM_SSH_TARGET` to the guest hostname/address or an alias from your normal
   `~/.ssh/config`, and `VM_SSH_KEY` to the host private key. SSH uses your
   normal configuration and `known_hosts`, with that key explicitly selected
   for authentication. Values are literal: use `~` for host home paths or
   absolute paths; shell `$VARIABLE` expansion is not supported. Bootstrap
   fixes the guest install and workspace paths, which the host discovers
   automatically.

   From the host, connect once over SSH using the configured `VM_SSH_TARGET`,
   verify and accept the guest host key, and confirm you can log in. This
   records the key in your normal `known_hosts`; the harness uses SSH
   non-interactively and requires this first connection to be trusted.
6. Keep the VM running and unlocked after bootstrap. Leave both UTM shares
   pointing at the repository's `harness` folder and host `~/work` directory.

On the host, install dependencies with
`python3 -m pip install -r host/requirements.txt`, and configure the remote
Ollama `SSH_TARGET` in `env/local.env` along with the VM settings. No model
server is installed in the guest.

## macOS VM: each session

```bash
./prompt.sh
```

Start and unlock the VM yourself before running the host command. The host
connects to the already-running VM, checks its two existing shared directories,
waits up to 300 seconds for SSH and updates the guest harness code through SSH.
It does not start, stop, or repoint the VM.

The worker uses the host's `~/work` directory, mounted at `~/work` in the guest.
macOS may ask for permission to control UTM on first use.

The harness exposes the `work` share at `~/work` using a symlink to the
automounted folder. The harness installation and virtual environment remain on
the guest disk, outside the workspace. Changes to files affect the originals on
the host immediately. Keep SSH private keys outside the shared workspace.

On normal exit, the supervisor stops the harness process group and removes the
workspace symlink. The host closes its SSH
connections but leaves the VM running, including when startup fails. It never
requests VM shutdown. The automatic share remains mounted while the VM runs.
A host crash or forced termination can bypass worker cleanup.

Only one UTM session is supported at a time. Consecutive sessions reuse the
running VM and its existing shares.

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
bash -n harness/bootstrap.sh
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
