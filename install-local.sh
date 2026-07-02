#!/usr/bin/env sh
set -eu

repo=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)

if command -v python3 >/dev/null 2>&1; then
  python_bin=$(command -v python3)
elif command -v python >/dev/null 2>&1; then
  python_bin=$(command -v python)
else
  echo "install-local.sh: python3 or python must be on PATH" >&2
  exit 1
fi

bin_dir="${HOME}/.local/bin"
mkdir -p "$bin_dir"

wrapper="${bin_dir}/yt-scribe"
script="${repo}/yt_scribe.py"

if [ -n "${VIRTUAL_ENV:-}" ]; then
  "$python_bin" -m pip install -e "$repo"
else
  "$python_bin" -m pip install --user -e "$repo"
fi

cat >"$wrapper" <<EOF
#!/usr/bin/env sh
exec "$python_bin" "$script" "\$@"
EOF

chmod +x "$wrapper"

echo "Installed yt-scribe wrapper:"
echo "  $wrapper"
"$python_bin" -m yt_scribe setup
case ":${PATH:-}:" in
  *":$bin_dir:"*) ;;
  *)
    echo "Add $bin_dir to PATH if yt-scribe is not found."
    ;;
esac
