"""Deploy targets: rsync/ssh (0.1), then any static host via an artifact (GitHub Pages).

Public files are written under umask 022: CI often runs under 077 for key files, and a
0600 entry.jar answers 403 to every F-Droid client.
"""
