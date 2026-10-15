#!/usr/bin/env bash
set -uo pipefail
shopt -s nullglob

if [[ -z ${BASE:-} ]]; then
  BASE=$(git merge-base origin/develop HEAD) || {
    printf '%s\n' 'cannot determine BASE from origin/develop and HEAD'
    exit 1
  }
fi

failed=0
fail() {
  printf '%s\n' "$1"
  failed=1
}

for tool in git python3 jq yq xmllint openapi-spec-validator oasdiff; do
  command -v "$tool" >/dev/null 2>&1 || fail "missing contract tool: $tool"
done
if ((failed)); then
  exit 1
fi

# Registry and route checks share one parse so malformed ownership cannot make
# a later check silently reason from a partial registry.
if ! python3 - <<'PY'
from __future__ import annotations

import pathlib
import re
import sys

import yaml

META = {"/health/live", "/health/ready", "/openapi.yaml", "/schema.xsd"}
METHODS = {"get", "post", "put", "patch", "delete", "head", "options", "trace"}
errors: list[str] = []

try:
    registry = yaml.safe_load(pathlib.Path("contracts/registry.yaml").read_text())
    entries = registry["services"]
except (OSError, TypeError, KeyError, yaml.YAMLError) as error:
    print(f"invalid contracts/registry.yaml: {error}")
    raise SystemExit(1) from None

if not isinstance(entries, list):
    print("invalid contracts/registry.yaml: services is not a list")
    raise SystemExit(1)

names: list[str] = []
owner: dict[str, str] = {}
for entry in entries:
    if not isinstance(entry, dict) or not isinstance(entry.get("name"), str):
        errors.append("registry entry without a name")
        continue
    name = entry["name"]
    names.append(name)
    required = {"name", "port", "rule", "rn", "wave", "resources"}
    missing = sorted(required - entry.keys())
    if missing:
        errors.append(f"{name}: missing registry fields: {missing}")
    resources = entry.get("resources")
    if not isinstance(resources, list) or not resources:
        errors.append(f"{name}: entry without resources")
        resources = []
    for resource in resources:
        if resource in owner:
            errors.append(f"/v1/{resource}: registered to {owner[resource]} and {name}")
        else:
            owner[resource] = name

if len(set(names)) != len(names):
    errors.append("a service is registered twice")

core = sum(
    1
    for entry in entries
    if isinstance(entry, dict) and entry.get("wave") == "core"
)
if core < 10:
    errors.append(f"{core} core services: the floor is ten without the second wave")

not_one_rule = []
for entry in entries:
    if not isinstance(entry, dict) or not isinstance(entry.get("name"), str):
        continue
    rule = entry.get("rule")
    rn = entry.get("rn")
    if (
        not isinstance(rule, str)
        or not rule.strip()
        or not isinstance(rn, list)
        or not rn
    ):
        not_one_rule.append(entry["name"])
if not_one_rule:
    errors.append(f"not exactly one rule: {not_one_rule}")

service_root = pathlib.Path("services")
service_dirs = sorted(path.name for path in service_root.glob("*") if path.is_dir())
unregistered = [name for name in service_dirs if name not in names]
if unregistered:
    errors.append(f"services/ without a registry entry: {unregistered}")

try:
    rules_text = pathlib.Path("docs/business-rules.md").read_text()
except OSError as error:
    errors.append(f"cannot read docs/business-rules.md: {error}")
    rules_text = ""
known_rn = set(re.findall(r"^### (RN-\d+[a-z]?) ", rules_text, re.MULTILINE))
unknown_rn = [
    (entry["name"], rn)
    for entry in entries
    if isinstance(entry, dict)
    and isinstance(entry.get("name"), str)
    and isinstance(entry.get("rn"), list)
    for rn in entry["rn"]
    if not isinstance(rn, str) or rn not in known_rn
]
if unknown_rn:
    errors.append(f"unknown rule references: {unknown_rn}")

for entry in entries:
    if not isinstance(entry, dict) or not isinstance(entry.get("name"), str):
        continue
    name = entry["name"]
    openapi = pathlib.Path("contracts") / name / "openapi.yaml"
    if not openapi.is_file():
        continue
    try:
        document = yaml.safe_load(openapi.read_text())
        paths = document["paths"]
    except (OSError, TypeError, KeyError, yaml.YAMLError) as error:
        errors.append(f"invalid OpenAPI YAML: {openapi}: {error}")
        continue
    if not isinstance(paths, dict):
        errors.append(f"invalid OpenAPI paths: {openapi}")
        continue
    for path, path_item in paths.items():
        if path in META or re.fullmatch(r"/schema\.v[2-9]\d*\.xsd", path):
            continue
        match = re.fullmatch(r"/v[1-9]\d*/([^/]+)((?:/[^/]+)*)", path)
        if not match:
            errors.append(f"{name}: {path} is neither versioned nor a meta route")
            continue
        first = match.group(1)
        if owner.get(first) != name:
            errors.append(f"{name}: {path} starts with a segment not registered to it")
        for segment in match.group(2).split("/")[1:]:
            other = owner.get(segment)
            if other is not None and other != name:
                errors.append(
                    f"{name}: {path} nests {segment}, registered to {other}"
                )
        if not isinstance(path_item, dict):
            continue
        for method, operation in path_item.items():
            if method.lower() not in METHODS or not isinstance(operation, dict):
                continue
            # Token-free operations say security: []; every other operation is
            # protected and must make its local authorization policy visible.
            if operation.get("security") != [] and operation.get("x-mosaiq-roles") is None:
                errors.append(f"{openapi}: {path} {method}: missing x-mosaiq-roles")

if errors:
    print("\n".join(errors))
    raise SystemExit(1)
PY
then
  failed=1
fi

work_dir=$(mktemp -d)
trap 'rm -rf "$work_dir"' EXIT
probe="$work_dir/compile-probe.xml"
printf '%s\n' '<compile-probe/>' > "$probe"

compile_xsd() {
  local xsd=$1
  xmllint --noout --nonet --schema "$xsd" "$probe" >/dev/null 2>&1
  local status=$?
  if [[ $status -ne 3 ]]; then
    fail "does not compile standalone: $xsd"
  fi
}

compile_xsd contracts/common/v1/common.xsd

# A failed read must stop the run: an empty list would skip every per-contract
# check below and still exit 0.
if ! names_output=$(yq -r '.services[].name' contracts/registry.yaml) \
  || [[ -z $names_output ]]; then
  printf '%s\n' 'cannot list services from contracts/registry.yaml'
  exit 1
fi
mapfile -t service_names <<<"$names_output"
for service in "${service_names[@]}"; do
  openapi="contracts/$service/openapi.yaml"
  [[ -f $openapi ]] || continue

  primary_xsd="contracts/$service/$service.xsd"
  if [[ ! -f $primary_xsd ]]; then
    fail "missing XSD: $primary_xsd"
  fi

  for xsd in contracts/"$service"/*.xsd; do
    compile_xsd "$xsd"
    filename=${xsd##*/}
    if [[ $filename == "$service.xsd" ]]; then
      major=1
    elif [[ $filename =~ ^${service}\.v([2-9][0-9]*)\.xsd$ ]]; then
      major=${BASH_REMATCH[1]}
    else
      fail "unexpected XSD name: $xsd"
      continue
    fi
    namespace="https://mosaiq.maxthecoder.online/xml/$service/v$major"
    grep -qF "targetNamespace=\"$namespace\"" "$xsd" \
      || fail "wrong namespace: $xsd"

    if ! python3 - "$xsd" <<'PY'
import sys
import xml.etree.ElementTree as ET

schema = ET.parse(sys.argv[1]).getroot()
xsd = "{http://www.w3.org/2001/XMLSchema}"
allowed = (
    "https://mosaiq.maxthecoder.online/xml/common/v1",
    "../common/v1/common.xsd",
)
bad = any(
    (child.tag == f"{xsd}import" and (
        child.get("namespace"), child.get("schemaLocation")
    ) != allowed)
    or child.tag in {f"{xsd}include", f"{xsd}redefine", f"{xsd}override"}
    for child in schema
)
raise SystemExit(1 if bad else 0)
PY
    then
      fail "imports more than common/v1: $service"
    fi
  done

  for example in contracts/"$service"/examples/*.xml; do
    if [[ $example == *.invalid.xml ]]; then
      if xmllint --noout --nonet --schema "$primary_xsd" "$example" \
        >/dev/null 2>&1; then
        fail "invalid example accepted: $example"
      fi
    elif ! xmllint --noout --nonet --schema "$primary_xsd" "$example" \
      >/dev/null 2>&1; then
      fail "invalid XML example: $example"
    fi
  done

  if ! openapi-spec-validator "$openapi" >/dev/null 2>&1; then
    fail "invalid OpenAPI: $openapi"
  fi
done

for example in contracts/common/v1/examples/*.xml; do
  if [[ $example == *.invalid.xml ]]; then
    if xmllint --noout --nonet --schema contracts/common/v1/common.xsd "$example" \
      >/dev/null 2>&1; then
      fail "invalid example accepted: $example"
    fi
  elif ! xmllint --noout --nonet --schema contracts/common/v1/common.xsd "$example" \
    >/dev/null 2>&1; then
    fail "invalid XML example: $example"
  fi
done

base_tree="$work_dir/base"
mkdir -p "$base_tree"
if git cat-file -e "$BASE:contracts" 2>/dev/null; then
  if ! git archive "$BASE" -- contracts 2>/dev/null | tar -x -C "$base_tree"; then
    fail "cannot extract contracts from BASE: $BASE"
  fi
fi

for service in "${service_names[@]}"; do
  openapi="contracts/$service/openapi.yaml"
  base_openapi="$base_tree/contracts/$service/openapi.yaml"
  [[ -f $openapi && -f $base_openapi ]] || continue

  if ! oasdiff breaking --fail-on ERR "$base_openapi" "$openapi" \
    >/dev/null 2>&1; then
    fail "breaking OpenAPI change in v1: $service"
  fi

  base_xsd="$base_tree/contracts/$service/$service.xsd"
  head_xsd="contracts/$service/$service.xsd"
  for example in contracts/"$service"/examples/*.xml; do
    [[ $example == *.invalid.xml || $example == *.request.xml ]] && continue
    if ! xmllint --noout --nonet --schema "$base_xsd" "$example" \
      >/dev/null 2>&1; then
      fail "XML response changed in v1: $example"
    fi
  done
  for example in "$base_tree"/contracts/"$service"/examples/*.request.xml; do
    if ! xmllint --noout --nonet --schema "$head_xsd" "$example" \
      >/dev/null 2>&1; then
      relative=${example#"$base_tree/"}
      fail "XML request changed in v1: $relative"
    fi
  done
done

if git cat-file -e "$BASE:contracts/common/v1/common.xsd" 2>/dev/null; then
  if ! git diff --quiet "$BASE" -- contracts/common/v1/; then
    fail "contracts/common/v1 is frozen"
  fi
fi

mapfile -t changed_contracts < <(
  git diff --name-only "$BASE" -- contracts/ | cut -d/ -f2 | sort -u
)
for service in "${changed_contracts[@]}"; do
  openapi="contracts/$service/openapi.yaml"
  [[ -f $openapi ]] || continue
  git cat-file -e "$BASE:$openapi" 2>/dev/null || continue
  base_version=$(git show "$BASE:$openapi" | yq -r '.info.version' 2>/dev/null)
  head_version=$(yq -r '.info.version' "$openapi" 2>/dev/null)
  if [[ $base_version == "$head_version" ]]; then
    fail "info.version not bumped: $service"
  fi
done

mapfile -t changed_services < <(
  git diff --name-only "$BASE" -- services/ | cut -d/ -f2 | sort -u
)
for service in "${changed_services[@]}"; do
  if git diff --quiet "$BASE" -- "services/$service/VERSION"; then
    fail "VERSION not bumped: $service"
  fi
done

for service in "${service_names[@]}"; do
  contract_commit=$(
    git log --diff-filter=A --format=%H -- "contracts/$service/openapi.yaml" \
      | tail -1
  )
  implementation_commit=$(
    git log --diff-filter=A --format=%H -- "services/$service/" | tail -1
  )
  [[ -n $implementation_commit ]] || continue
  if [[ -z $contract_commit || $contract_commit == "$implementation_commit" ]] \
    || ! git merge-base --is-ancestor "$contract_commit" "$implementation_commit"; then
    fail "implemented before its contract: $service"
  fi
done

exit "$failed"
