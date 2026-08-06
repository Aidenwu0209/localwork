# Radeon Cloud quick start

This page covers the first connection from Radeon Cloud to a local DejaView checkout. It uses the platform's SSH option. You do not need to publish a model endpoint or expose the DejaView gateway to the internet.

## 1. Create an instance

1. Open [Radeon Cloud](https://radeon-global.anruicloud.com/) and sign in.
2. Open your profile and create a template under **My Templates**.
3. Choose a container image with a working Linux shell. Enable **Persistent (PVC)** if you want files under the persistent volume to survive instance recreation.
4. Enable **SSH Access** in the advanced options and launch the template.
5. Wait until the instance reports that the workspace is ready.

The platform shows the SSH username, host, and port in the ready dialog and in the active instance view. Copy those values from the current instance. Do not put them in a commit or a public issue.

## 2. Add the local SSH alias

On macOS or Linux, add an entry like this to `~/.ssh/config` and replace the placeholders with the values shown by Radeon Cloud:

```sshconfig
Host radeon-cloud
    HostName <host shown by Radeon Cloud>
    Port <port shown by Radeon Cloud>
    User <user shown by Radeon Cloud>
    IdentityFile ~/.ssh/id_ed25519
    IdentitiesOnly yes
```

On Windows, put the same entry in `%USERPROFILE%\\.ssh\\config`. The Windows wrapper also accepts a separate SSH config path through `DEJAVIEW_SSH_CONFIG`.

Test the alias before touching the DejaView stack:

```bash
ssh radeon-cloud 'hostname && rocm-smi --showmeminfo vram --showuse'
```

If the image does not contain `sshd`, open the JupyterLab terminal and install/start it there, following the commands in the [official Radeon Cloud guide](https://github.com/AMD-DEV-CONTEST/Radeon-hackathon-2026-07/blob/main/Radeon-Cloud-User%20Guide/README.md#option-b--ssh).

## 3. Check the machine before starting models

The DejaView server stack expects the llama.cpp binary, model files, and launch scripts under `/root/dejaview-launch` and `/root/dejaview-models`. The exact bootstrap commands are in [deploy/server/DEPLOY.md](../deploy/server/DEPLOY.md).

Run this read-only check first:

```bash
ssh radeon-cloud \
  'rocm-smi --showmeminfo vram --showuse; echo "---"; \
   rocm-smi --showpids verbose; echo "---"; \
   cd /root/dejaview-launch && ./server-stack.sh status'
```

Do not start a brain model while an unknown KFD process is using the GPU. Check the process identity and available VRAM first. The accepted P3.1 measurements were made on a Radeon PRO W7900D with `gfx1100` and ROCm 7.2.1, but a new instance may have different capacity or resident processes.

## 4. Start the roles used by the split topology

From the local DejaView clone:

```bash
ssh radeon-cloud \
  'cd /root/dejaview-launch && ./server-stack.sh up embed fast perceive'
ssh radeon-cloud \
  'cd /root/dejaview-launch && ./server-stack.sh status'
ssh -f -N -L 14000:127.0.0.1:4000 radeon-cloud
```

The local machine uses `http://127.0.0.1:14000/v1` for allowed Radeon requests. The device-local Sentinel uses its own local gateway and must not be routed through this tunnel.

Start the local services as described in the root README:

```bash
./deploy/mac/llama-launch/dev-stack.sh up sentinel
make product-up
make product-status
```

## 5. Stop cleanly

Stop DejaView-managed roles and the SSH tunnel when the test is over:

```bash
ssh radeon-cloud \
  'cd /root/dejaview-launch && ./server-stack.sh down embed fast perceive'
pkill -f 'ssh -f -N -L 14000:127.0.0.1:4000 radeon-cloud' || true
make product-down
```

Destroy the Radeon Cloud instance from the profile page when you are finished. A running instance continues to consume credits.

## What not to expose

- Do not bind the LiteLLM gateway to `0.0.0.0`.
- Do not publish the SSH host, port, instance ID, private key, model API key, or tunnel state in this repository.
- Do not send raw capture frames through the Radeon tunnel. The local Sentinel must decide first.
- Do not use `rc-tunnel` to expose the product page unless you add application authentication and have a specific reason to make the page public.
