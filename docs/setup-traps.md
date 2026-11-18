# Setup traps

Each of these cost real time on a production repo. fdroidkit handles them; this page says why.

- **`fdroid init` ignores a passphrase on stdin.** Pass keystore passphrases through config
  (`{env: NAME}`), not through a prompt.
- **setuptools 81+ breaks fdroidserver 2.4.x** (`pkg_resources` gone). Install fdroidserver with
  `setuptools<81` in the same environment.
- **Placeholder repo icon.** `fdroid init` points at a default icon that clients show as broken;
  set your own `repo_icon`, and the archive one too when `archive_older` is on.
- **umask 077 in CI makes `entry.jar` mode 0600**, rsync carries the mode to the web root, and every
  client gets 403. Write public files under umask 022.
- **rsync deploy over a non-standard ssh port**: use an ssh config alias in `serverwebroot`, the
  `user@host:port` form does not work there.
- **Client intent filter**: serve the repo at `/fdroid/repo` so "add repo" links open the F-Droid
  client. See [examples/nginx-fdroid.conf](../examples/nginx-fdroid.conf).
- **Cache**: `index*` and `entry*` must be `no-cache`, or clients see the new APK only hours later.
