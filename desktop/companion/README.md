# EmberOS Connect for Windows

EmberOS Connect is a small Windows app for using the EmberOS GUI on a headless
Raspberry Pi. The Pi still runs EmberOS; this companion discovers it on the
local network, retrieves the current web token over SSH, and opens the GUI in a
standalone Edge window through an encrypted SSH tunnel.

## First use

1. Download and unzip `emberos-companion-windows` from a successful GitHub
   Actions run, then start `EmberOS-Connect.exe` on the Windows laptop.
2. On the Pi, run `emberos --lan` as the same ordinary Linux account you use
   for SSH, and leave it running.
3. Select the discovered Pi and click **Pair / Open EmberOS**. Enter the Pi's
   SSH username and password. For SSH-key login, leave the password blank.
4. Compare the SSH fingerprint shown by the app against the output of this
   command on the Pi before accepting:

   ```sh
   for key in /etc/ssh/ssh_host_*_key.pub; do sudo ssh-keygen -lf "$key"; done
   ```

The password is stored in Windows Credential Manager. The app stores the
device ID, username, and verified host fingerprint locally. It does not save
the web token; it reads each new session token over SSH. On later runs, the
companion opens the GUI when it discovers the paired Pi. Turn on **Run
companion when Windows signs in** if you want discovery to stay active in the
background.

Windows and the Pi must share a network that permits multicast DNS discovery
and SSH on port 22. Allow the app on Private networks if Windows Firewall asks.
The companion currently supports IPv4 discovery and SSH password or key-based
login.

## Build locally

From the repository root on Windows:

```powershell
python -m pip install -r desktop/companion/requirements.txt
python -m PyInstaller --noconfirm --clean --onefile --windowed `
  --name EmberOS-Connect --collect-all zeroconf `
  --hidden-import keyring.backends.Windows `
  --distpath dist/companion --workpath build/companion/work `
  --specpath build/companion desktop/companion/emberos_connect.py
dist/companion/EmberOS-Connect.exe --self-test
```
