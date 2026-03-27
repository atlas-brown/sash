#!/bin/bash

# Set qt theme with kvantum manager

THEME_NAME=$1

if [ -z "$THEME_NAME" ]; then
  echo "Warning: QT theme name missing"
  exit 1
fi

if command -v kvantummanager &>/dev/null; then
  echo "Error: Kvantum manager not installed"
  exit 1
fi

kvantummanager --set "$THEME_NAME"
