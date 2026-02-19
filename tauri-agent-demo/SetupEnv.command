#!/bin/bash

# macOS environment setup mirroring SetupEnv.ps1 behavior.

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT" || exit 1

missing=()
installed=()

write_section() {
  echo ""
  echo "== $1 =="
}

prompt_yes_no() {
  local message="$1"
  local resp=""
  while true; do
    read -r -p "$message (y/n) " resp </dev/tty
    case "$resp" in
      [Yy]) return 0 ;;
      [Nn]) return 1 ;;
    esac
  done
}

add_session_path() {
  local path="$1"
  [[ -z "$path" ]] && return
  case ":$PATH:" in
    *":$path:"*) return ;;
  esac
  export PATH="$path:$PATH"
}

add_user_path() {
  local path="$1"
  local profile="$HOME/.zprofile"
  [[ -z "$path" ]] && return
  local line="export PATH=\"$path:\$PATH\""
  if ! grep -Fqs "$line" "$profile" 2>/dev/null; then
    printf '\n%s\n' "$line" >> "$profile"
  fi
}

version_ge() {
  local v1="$1"
  local v2="$2"
  local IFS=.
  read -r a1 b1 c1 <<< "$v1"
  read -r a2 b2 c2 <<< "$v2"
  a1=${a1:-0}; b1=${b1:-0}; c1=${c1:-0}
  a2=${a2:-0}; b2=${b2:-0}; c2=${c2:-0}
  if (( a1 > a2 )); then return 0; fi
  if (( a1 < a2 )); then return 1; fi
  if (( b1 > b2 )); then return 0; fi
  if (( b1 < b2 )); then return 1; fi
  if (( c1 >= c2 )); then return 0; fi
  return 1
}

get_node_info() {
  local node_home="$1"
  local node_bin="$node_home/bin/node"
  if [[ -x "$node_bin" ]]; then
    echo "$node_bin|tools|$node_home"
    return 0
  fi
  local node_cmd
  node_cmd="$(command -v node 2>/dev/null || true)"
  if [[ -n "$node_cmd" ]]; then
    local node_root
    node_root="$(cd "$(dirname "$node_cmd")/.." && pwd)"
    echo "$node_cmd|path|$node_root"
    return 0
  fi
  return 1
}

get_node_version() {
  local node_bin="$1"
  local raw
  raw="$("$node_bin" --version 2>/dev/null || true)"
  raw="${raw#v}"
  if [[ "$raw" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
    echo "$raw"
  fi
}

install_node() {
  local node_version_tag="$1"
  local tools_root="$2"
  local node_arch="$3"
  local node_dir="node-$node_version_tag-darwin-$node_arch"
  local node_home="$tools_root/$node_dir"
  local node_bin="$node_home/bin/node"

  if [[ -x "$node_bin" ]]; then
    echo "$node_home"
    return 0
  fi

  mkdir -p "$tools_root"
  local url="https://nodejs.org/dist/$node_version_tag/$node_dir.tar.gz"
  local tmp="/tmp/$node_dir.tar.gz"

  echo "Downloading Node.js $node_version_tag..."
  if ! curl -fsSL "$url" -o "$tmp"; then
    return 1
  fi
  echo "Extracting Node.js..."
  if ! tar -xzf "$tmp" -C "$tools_root"; then
    rm -f "$tmp"
    return 1
  fi
  rm -f "$tmp"

  [[ -x "$node_bin" ]] || return 1
  echo "$node_home"
}

get_python_cmd() {
  if command -v python3 >/dev/null 2>&1; then
    echo "python3"
    return 0
  fi
  if command -v python >/dev/null 2>&1; then
    echo "python"
    return 0
  fi
  return 1
}

get_python_version() {
  local cmd="$1"
  local raw
  raw="$("$cmd" -c 'import sys; print(f"{sys.version_info[0]}.{sys.version_info[1]}.{sys.version_info[2]}")' 2>/dev/null || true)"
  if [[ "$raw" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
    echo "$raw"
  fi
}

install_python() {
  if command -v brew >/dev/null 2>&1; then
    echo "Installing Python via Homebrew..."
    brew install python@3.12
    return $?
  fi
  return 1
}

install_rust() {
  echo "Downloading rustup..."
  curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh -s -- -y
}

# ==================== System dependencies ====================

write_section "System dependencies"

required_node_version="20.19.0"
node_version_tag="v20.19.0"
tools_root="$(cd "$ROOT/.." && pwd)/.tools"
arch="$(uname -m)"
case "$arch" in
  arm64) node_arch="arm64" ;;
  x86_64) node_arch="x64" ;;
  *) node_arch="x64" ;;
esac
node_home="$tools_root/node-$node_version_tag-darwin-$node_arch"

node_ok=false
node_info="$(get_node_info "$node_home" || true)"
if [[ -n "$node_info" ]]; then
  IFS='|' read -r node_bin node_source node_home_found <<< "$node_info"
  node_ver="$(get_node_version "$node_bin")"
  if [[ -n "$node_ver" ]] && version_ge "$node_ver" "$required_node_version"; then
    node_ok=true
    add_session_path "$(dirname "$node_bin")"
    export NODE_HOME="$node_home_found"
    echo "Node.js found: v$node_ver ($node_source)"
  else
    echo "Node.js found but version is too old or unknown."
  fi
fi

if [[ "$node_ok" != true ]]; then
  if prompt_yes_no "Node.js $node_version_tag not found. Install it to $node_home?"; then
    installed_home="$(install_node "$node_version_tag" "$tools_root" "$node_arch" || true)"
    if [[ -n "$installed_home" ]]; then
      add_session_path "$installed_home/bin"
      export NODE_HOME="$installed_home"
      add_user_path "$installed_home/bin"
      installed+=("Node.js $node_version_tag")
      node_ok=true
    else
      echo "Node.js install failed."
      missing+=("Node.js $node_version_tag (or newer)")
    fi
  else
    missing+=("Node.js $node_version_tag (or newer)")
  fi
fi

python_min_version="3.9.0"
venv_root="$ROOT/python-backend/venv"
venv_python="$venv_root/bin/python"
venv_cfg="$venv_root/pyvenv.cfg"
venv_ok=false

if [[ -x "$venv_python" && -f "$venv_cfg" ]]; then
  venv_ok=true
else
  if [[ -d "$venv_root" ]]; then
    ts="$(date +%Y%m%d%H%M%S)"
    echo "Existing venv looks invalid. Moving to venv.invalid.$ts"
    mv "$venv_root" "$venv_root.invalid.$ts"
  fi
  python_ok=false
  python_cmd="$(get_python_cmd || true)"
  if [[ -n "$python_cmd" ]]; then
    py_ver="$(get_python_version "$python_cmd")"
    if [[ -n "$py_ver" ]] && version_ge "$py_ver" "$python_min_version"; then
      echo "System Python found: v$py_ver"
      python_ok=true
      echo "Creating venv..."
      "$python_cmd" -m venv "$venv_root"
    else
      echo "System Python version is too old or unknown."
    fi
  fi

  if [[ "$python_ok" != true ]]; then
    if prompt_yes_no "Python 3.12 not found. Install it now (Homebrew)?"; then
      if install_python; then
        python_cmd="$(get_python_cmd || true)"
        if [[ -n "$python_cmd" ]]; then
          echo "Creating venv..."
          "$python_cmd" -m venv "$venv_root"
          python_ok=true
          installed+=("Python 3.12")
        else
          missing+=("Python $python_min_version (or newer)")
        fi
      else
        echo "Python install failed (Homebrew missing or install failed)."
        missing+=("Python $python_min_version (or newer)")
      fi
    else
      missing+=("Python $python_min_version (or newer)")
    fi
  fi

  if [[ -x "$venv_python" && -f "$venv_cfg" ]]; then
    venv_ok=true
  fi
fi

cargo_path="$(command -v cargo 2>/dev/null || true)"
if [[ -z "$cargo_path" && -x "$HOME/.cargo/bin/cargo" ]]; then
  cargo_path="$HOME/.cargo/bin/cargo"
fi

rust_ok=false
if [[ -n "$cargo_path" && -x "$cargo_path" ]]; then
  rust_ok=true
  add_session_path "$(dirname "$cargo_path")"
  add_user_path "$(dirname "$cargo_path")"
  echo "Rust (cargo) found."
else
  if prompt_yes_no "Rust (cargo) not found. Install via rustup?"; then
    if install_rust; then
      if [[ -f "$HOME/.cargo/env" ]]; then
        # shellcheck disable=SC1090
        source "$HOME/.cargo/env"
      fi
      cargo_path="$HOME/.cargo/bin/cargo"
      if [[ -x "$cargo_path" ]]; then
        add_session_path "$(dirname "$cargo_path")"
        add_user_path "$(dirname "$cargo_path")"
        installed+=("Rust toolchain")
        rust_ok=true
      else
        missing+=("Rust toolchain (cargo)")
      fi
    else
      echo "Rust install failed."
      missing+=("Rust toolchain (cargo)")
    fi
  else
    missing+=("Rust toolchain (cargo)")
  fi
fi

rg_home="$ROOT/tools/rg"
rg_bin="$rg_home/rg"
if [[ -x "$rg_bin" ]]; then
  add_session_path "$rg_home"
  echo "ripgrep found: $rg_bin"
else
  rg_cmd="$(command -v rg 2>/dev/null || true)"
  if [[ -n "$rg_cmd" ]]; then
    echo "ripgrep found in PATH: $rg_cmd"
  else
    echo "ripgrep not found at $rg_bin (optional)."
  fi
fi

# ==================== Project dependencies ====================

write_section "Project dependencies"

if [[ "$node_ok" == true ]]; then
  if [[ ! -d "$ROOT/node_modules" ]]; then
    echo "Installing npm dependencies..."
    npm install
  else
    echo "node_modules already exists. Skipping npm install."
  fi
else
  echo "Skipping npm install (Node.js missing)."
fi

if [[ "$venv_ok" == true ]]; then
  echo "Installing Python requirements..."
  PIP_CONFIG_FILE=/dev/null "$venv_python" -m pip install --upgrade pip
  PIP_CONFIG_FILE=/dev/null "$venv_python" -m pip install --no-user -r "$ROOT/python-backend/requirements.txt"
else
  echo "Skipping Python requirements (venv missing)."
fi

write_section "Summary"
if (( ${#installed[@]} > 0 )); then
  echo "Installed:"
  for item in "${installed[@]}"; do
    echo " - $item"
  done
fi

if (( ${#missing[@]} > 0 )); then
  echo "Missing:"
  for item in "${missing[@]}"; do
    echo " - $item"
  done
  echo ""
  echo "Please install missing items and re-run setup."
else
  echo "All required dependencies are ready."
fi
