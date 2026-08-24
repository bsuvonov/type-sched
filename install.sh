#!/usr/bin/env bash
set -euo pipefail

script_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
data_root=${XDG_DATA_HOME:-"${HOME}/.local/share"}
app_dir="${data_root}/type-sched"
desktop_dir="${data_root}/applications"
icon_dir="${data_root}/icons/hicolor/scalable/apps"
bin_dir=${XDG_BIN_HOME:-"${HOME}/.local/bin"}
launcher="${bin_dir}/type-sched"

for command_name in python3 xdotool; do
  if ! command -v "${command_name}" >/dev/null 2>&1; then
    echo "Missing dependency: ${command_name}" >&2
    echo "On Fedora, run: sudo dnf install python3-gobject gtk3 xdotool" >&2
    exit 1
  fi
done

python3 -c 'import gi; gi.require_version("Gtk", "3.0"); from gi.repository import Gtk' \
  >/dev/null 2>&1 || {
    echo "Missing GTK Python bindings." >&2
    echo "On Fedora, run: sudo dnf install python3-gobject gtk3" >&2
    exit 1
  }

install -d "${app_dir}/typesched" "${desktop_dir}" "${icon_dir}" "${bin_dir}"
install -m 755 "${script_dir}/type-sched" "${app_dir}/type-sched"
install -m 644 "${script_dir}"/typesched/*.py "${app_dir}/typesched/"
install -m 644 \
  "${script_dir}/data/io.github.typesched.TypeSched.svg" \
  "${icon_dir}/io.github.typesched.TypeSched.svg"

if [[ -e "${launcher}" || -L "${launcher}" ]]; then
  existing_target=$(readlink -f -- "${launcher}" 2>/dev/null || true)
  if [[ "${existing_target}" != "${app_dir}/type-sched" ]]; then
    echo "Refusing to replace existing ${launcher}" >&2
    exit 1
  fi
fi
ln -sfn "${app_dir}/type-sched" "${launcher}"

sed "s|@EXEC@|${app_dir}/type-sched|g" \
  "${script_dir}/data/io.github.typesched.TypeSched.desktop.in" \
  > "${desktop_dir}/io.github.typesched.TypeSched.desktop"
chmod 644 "${desktop_dir}/io.github.typesched.TypeSched.desktop"

if command -v gtk-update-icon-cache >/dev/null 2>&1; then
  gtk-update-icon-cache -q "${data_root}/icons/hicolor" 2>/dev/null || true
fi

echo "TypeSched is installed. Open it from your application menu or run: type-sched"
