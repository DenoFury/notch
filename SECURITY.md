# Security

## Reporting a problem

If you find a security issue, please use GitHub's **"Report a vulnerability"** button on the Security tab of this repository. That keeps the report private until it's fixed. Please don't open a public issue for it.

Notch is a hobby project provided **as is**, without warranty (see [LICENSE](LICENSE)). Reports are welcome and handled as time allows.

## What Notch does on your PC

So you can judge it yourself:

- **Runs as you, never as administrator.** It installs to `%LOCALAPPDATA%\Programs\Notch` and keeps its data in `%APPDATA%\Notch`.
- **Reads, never writes:** Windows' notification store (to mirror notifications), media sessions, the audio devices, the battery and the tray-icon list in the registry.
- **Keyboard:** while the Notch taskbar replaces Windows' taskbar, a keyboard hook handles **Win+1…9** for the bar's apps. It reacts only to Win plus a digit, records nothing, and every other key is passed on untouched.
- **Windows' taskbar** is hidden only if you turn that on. Its previous state is saved first and restored when Notch closes, by a small guard process if Notch crashes.
- **Clipboard history and snippets** are encrypted with your Windows account (DPAPI). Copies marked by password managers are skipped.

## Network

- No telemetry, accounts or ads. Outgoing requests go only to the services listed in the README's Privacy section, over HTTPS, with certificates checked by Windows.
- **Send to phone** starts a small web server on your local network only while you use the Phone tab, and stops 10 minutes after the last use. The address carries a random 72-bit code.
  - The connection is **not encrypted** (plain HTTP on your LAN), so use it on networks you trust.
  - Received files are saved with Windows' "downloaded from the internet" mark, so SmartScreen checks them before they can run. Notch never opens them by itself.
  - File names are cleaned: no folders, invisible direction characters or reserved names, and nothing is overwritten. There's a size limit, and 2 GB of disk is always kept free.
- The optional **Web** panel uses Qt WebEngine (Chromium) with its sandbox. Downloads, pop-up windows and permission requests (camera, location…) are not granted.

## Building

The installer is not code-signed, so Windows SmartScreen warns on first run. You can always build it yourself from this source with `packaging\build.py`.
