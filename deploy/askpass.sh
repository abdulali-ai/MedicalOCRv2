#!/bin/sh
# sudo -A helper. Prints the password on stdout. Used only for this install.
exec zenity --password --title="Install Kubernetes" \
  --text="${1:-Administrator password required to install Kubernetes and deploy the services.}"
